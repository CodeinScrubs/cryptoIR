"""Bounded public WebSocket recording with explicit discontinuity events.

No recovery is claimed across disconnects. Full books replace previous books;
Wallex publishes separate full sides and has no source clock/sequence guarantee.
"""

import asyncio
import gzip
import hashlib
import json
import shutil
import time
import uuid
from collections import Counter
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

from . import __version__
from .domain import Book, DataError, Level, number
from .feeds import endpoint, native_symbol

URLS = {
    "nobitex": "wss://ws.nobitex.ir/connection/websocket",
    "wallex": "wss://api.wallex.ir/connection/websocket",
    "tabdeal": "wss://api1.tabdeal.org/stream/",
}


def dumps(value):
    return json.dumps(value, default=str, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def messages(raw: str) -> list[dict]:
    if not isinstance(raw, str):
        raise DataError("expected JSON text frame")
    result = [json.loads(line, parse_float=Decimal) for line in raw.splitlines() if line.strip()]
    if not result or any(not isinstance(value, dict) for value in result):
        raise DataError("invalid JSON message envelope")
    return result


def millis(value):
    value = number(value)
    if value <= 0 or value != value.to_integral_value():
        raise DataError("invalid source milliseconds")
    return int(value) * 1_000_000


class StreamParser:
    def __init__(self, venue: str, pair: str, max_side_skew_ns=2_000_000_000):
        endpoint(venue, pair)  # validated explicit USDT pair; no symbol guessing
        self.venue, self.pair = venue, pair
        self.symbol = native_symbol(venue, pair)
        self.max_side_skew_ns = max_side_skew_ns
        if venue == "nobitex":
            self.channels = ["public:orderbook-" + self.symbol]
        elif venue == "wallex":
            self.channels = [f"orderbook:{self.symbol}@{side}Depth" for side in ("buy", "sell")]
        else:
            self.channels = [self.symbol.lower() + "@depth@2000ms"]
        self.offsets, self.sides = {}, {}

    def reset(self):
        self.offsets.clear()
        self.sides.clear()

    def subscriptions(self):
        if self.venue == "tabdeal":
            return [{"id": 1, "method": "SUBSCRIBE", "params": self.channels}]
        return [{"id": i, "subscribe": {"channel": channel}}
                for i, channel in enumerate(self.channels, start=2)]

    def parse(self, message: dict, received_ns: int) -> list[dict]:
        """Pure protocol-to-event transform; state is reset on every reconnect."""
        result = []
        if "error" in message or "code" in message:
            raise DataError("server rejected a stream command")
        if "subscribe" in message and self.venue != "tabdeal":
            index = message["id"] - 2
            if not 0 <= index < len(self.channels):
                raise DataError("unknown subscription acknowledgement")
            position = message["subscribe"].get("offset")
            if position is not None:
                if type(position) is not int or position < 0:
                    raise DataError("invalid subscription offset")
                self.offsets[self.channels[index]] = position
        if self.venue == "tabdeal":
            if "stream" not in message:
                return []
            channel = message["stream"]
            if channel not in self.channels:
                raise DataError("unexpected stream symbol")
            data = message["data"]
            if isinstance(data, str):
                data = json.loads(data, parse_float=Decimal)
            if data["s"] != self.symbol or data["e"] != "depthUpdate":
                raise DataError("unexpected depth payload")
            bids, asks = data["b"], data["a"]
            exchange_ns = millis(data["E"])
            continuity = "no_exchange_sequence"
        else:
            push = message.get("push")
            if not push:
                return []
            if "disconnect" in push or "unsubscribe" in push:
                raise DataError("server invalidated subscription")
            if "pub" not in push:
                return []
            channel, pub = push["channel"], push["pub"]
            if channel not in self.channels or pub.get("delta"):
                raise DataError("unexpected channel or unsupported delta")
            offset = pub.get("offset")
            continuity = "no_exchange_sequence"
            if offset is not None:
                if type(offset) is not int or offset < 0:
                    raise DataError("invalid stream offset")
                previous = self.offsets.get(channel)
                if previous is not None and offset <= previous:
                    raise DataError("duplicate or regressed stream offset")
                if previous is not None and offset != previous + 1:
                    self.sides.clear()
                    result.append({"kind": "gap", "reason": "offset_jump", "previous": previous, "current": offset})
                self.offsets[channel] = offset
                continuity = "offset_observed_no_cross_connection_recovery"
            data = pub["data"]
            if isinstance(data, str):
                data = json.loads(data, parse_float=Decimal)
            if self.venue == "nobitex":
                bids, asks = data["bids"], data["asks"]
                exchange_ns = millis(data["lastUpdate"])
            else:
                side = "bids" if channel.endswith("@buyDepth") else "asks"
                # Observed v2 production payload wraps the documented list.
                if isinstance(data, dict):
                    data = data["message"]
                rows = [[str(number(row["price"])), str(number(row["quantity"]))] for row in data]
                self.sides[side] = (rows, received_ns)
                result.append({"kind": "book_side", "side": side, "levels": rows,
                               "source_ns": None, "continuity": continuity})
                if len(self.sides) < 2:
                    return result
                skew = abs(self.sides["bids"][1] - self.sides["asks"][1])
                if skew > self.max_side_skew_ns:
                    result.append({"kind": "book_unavailable", "reason": "side_receive_skew", "skew_ns": skew})
                    return result
                bids, asks = self.sides["bids"][0], self.sides["asks"][0]
                if bids and asks and number(bids[0][0]) >= number(asks[0][0]):
                    result.append({"kind": "book_unavailable", "reason": "asynchronous_sides_crossed"})
                    return result
                exchange_ns = None
        levels = lambda rows: tuple(Level(number(row[0]), number(row[1])) for row in rows)
        book = Book(self.venue, self.pair, levels(bids), levels(asks), received_ns, 0, exchange_ns)
        result.append({"kind": "book", "book": asdict(book), "continuity": continuity,
                       "source_time_known": exchange_ns is not None,
                       "atomic_exchange_snapshot": self.venue != "wallex"})
        return result


class RecordingLimit(Exception):
    pass


class RecordingIOError(Exception):
    pass


class ConsumerFailure(Exception):
    """A downstream paper/accounting failure must stop, never reconnect/retry it."""
    pass


class EventWriter:
    def __init__(self, path: Path, max_bytes: int):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path, self.max_bytes = path, max_bytes
        self.file = (gzip.open(path, "xt", encoding="utf-8", newline="\n", compresslevel=3)
                     if path.suffix == ".gz" else path.open("x", encoding="utf-8", newline="\n"))
        self.run_id = str(uuid.uuid4())
        self.seq, self.size, self.counts = 0, 0, Counter()
        self.start_wall, self.start_mono = time.time_ns(), time.monotonic_ns()

    def write(self, venue: str, event: dict):
        if {"schema","run_id","seq","venue","observed_ns","monotonic_ns"}.intersection(event):
            raise DataError("event must not replace recording envelope fields")
        mono = time.monotonic_ns()
        wall = time.time_ns()
        record = {"schema": 2, "run_id": self.run_id, "seq": self.seq + 1,
                  "venue": venue, "observed_ns": wall, "monotonic_ns": mono,
                  "clock_error_ns": wall - self.start_wall - (mono - self.start_mono), **event}
        line = dumps(record) + "\n"
        size = len(line.encode("utf-8"))
        if self.size + size > self.max_bytes:
            raise RecordingLimit("configured file size reached")
        if self.seq % 100 == 0 and shutil.disk_usage(self.path.parent).free < 512 * 1024 * 1024:
            raise RecordingLimit("disk reserve reached")
        try:
            self.file.write(line)
            self.file.flush()
        except OSError as exc:
            raise RecordingIOError("unable to persist stream record") from exc
        self.seq += 1
        self.size += size
        self.counts[venue + ":" + event["kind"]] += 1
        if abs(record["clock_error_ns"]) > 1_000_000_000:
            # The affected frame remains flagged; later observations have a new
            # reference clock instead of an everlasting stale offset.
            self.start_wall, self.start_mono = wall, mono
        return record

    def close(self):
        self.file.close()


async def capture_venue(venue: str, pair: str, writer: EventWriter):
    from websockets.asyncio.client import connect

    attempt, failures = 0, 0
    while True:
        attempt += 1
        parser = StreamParser(venue, pair)
        writer.write(venue, {"kind": "session_start", "attempt": attempt, "pair": pair, "url": URLS[venue]})
        try:
            async with connect(URLS[venue], open_timeout=10, close_timeout=2,
                               max_size=2**20, max_queue=16, ping_interval=20,
                               ping_timeout=20, user_agent_header="cryptoIR/" + __version__) as ws:
                subscribed = venue == "tabdeal"
                if subscribed:
                    for command in parser.subscriptions():
                        await ws.send(dumps(command))
                else:
                    await ws.send(dumps({"id": 1, "connect": {}}))
                last_wall, last_mono = time.time_ns(), time.monotonic_ns()
                while True:
                    # Python 3.11 wait_for may swallow external cancellation when
                    # recv finishes concurrently. Keep the timeout in this task.
                    async with asyncio.timeout(40 if subscribed else 10):
                        raw = await ws.recv()
                    received_ns, received_mono = time.time_ns(), time.monotonic_ns()
                    clock_error = (received_ns - last_wall) - (received_mono - last_mono)
                    last_wall, last_mono = received_ns, received_mono
                    writer.write(venue, {"kind": "frame", "pair": pair, "attempt": attempt,
                                         "received_ns": received_ns, "raw": raw,
                                         "sha256": hashlib.sha256(raw.encode() if isinstance(raw, str) else raw).hexdigest()})
                    if abs(clock_error) > 1_000_000_000:
                        parser.reset()
                        writer.write(venue, {"kind": "gap", "reason": "local_clock_change", "clock_error_ns": clock_error})
                        continue
                    for message in messages(raw):
                        if message == {}:
                            await ws.send("{}")
                            writer.write(venue, {"kind": "heartbeat"})
                            continue
                        if "connect" in message and not subscribed:
                            for command in parser.subscriptions():
                                await ws.send(dumps(command))
                            subscribed = True
                        for event in parser.parse(message, received_ns):
                            writer.write(venue, {"pair": pair, "attempt": attempt, **event})
                            if event["kind"] == "book":
                                failures = 0
                        if "subscribe" in message or "result" in message:
                            writer.write(venue, {"kind": "subscription_ack", "id": message.get("id")})
        except asyncio.CancelledError:
            raise
        except (RecordingIOError, ConsumerFailure):
            raise
        except (RecordingLimit, OSError) as exc:
            # Disk errors must terminate recording. Network OSError gets bounded reconnects.
            if isinstance(exc, RecordingLimit) or getattr(exc, "errno", None) in (13, 28):
                raise
            failures += 1
            writer.write(venue, {"kind": "gap", "reason": "connection_error", "error_type": type(exc).__name__})
        except Exception as exc:
            failures += 1
            writer.write(venue, {"kind": "gap", "reason": "stream_invalidated", "error_type": type(exc).__name__})
        finally:
            parser.reset()
        if failures >= 5:
            writer.write(venue, {"kind": "venue_stopped", "reason": "five_consecutive_failures"})
            return
        await asyncio.sleep(min(30, 2 ** failures))


async def record_streams(path: Path, pair: str, seconds: int, venues: list[str], max_mb: int = 64):
    if type(seconds) is not int or not 5 <= seconds <= 86400 or not 1 <= max_mb <= 512:
        raise DataError("duration 5..86400 seconds and cap 1..512 MiB required")
    if not venues or len(venues) != len(set(venues)):
        raise DataError("select unique supported venues")
    for venue in venues:
        StreamParser(venue, pair)
    writer = EventWriter(path, max_mb * 1024 * 1024)
    tasks, reason = [], "interrupted"
    try:
        writer.write("all", {"kind": "recording_start", "pair": pair, "duration_seconds": seconds,
                             "venues": venues, "version": __version__, "max_mb": max_mb})
        tasks = [asyncio.create_task(capture_venue(venue, pair, writer)) for venue in venues]
        try:
            async with asyncio.timeout(seconds):
                await asyncio.gather(*tasks)
            reason = "all_venues_stopped"
        except TimeoutError:
            reason = "duration_completed"
        except RecordingLimit:
            reason = "storage_limit"
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            writer.write("all", {"kind": "recording_end", "reason": reason})
        except (RecordingLimit, RecordingIOError):
            pass
        writer.close()
    return {"kind": "stream_recording_summary", "reason": reason, "counts": dict(writer.counts),
            "venues_without_books": [venue for venue in venues if not writer.counts[venue + ":book"]],
            "bytes": writer.size, "complete_market_history": False, "live_execution": False}
