"""Audit recordings and replay original frames, ignoring derived book claims."""

import hashlib
import gzip
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

from .domain import Book, DataError, Level, number
from .streams import StreamParser, messages


def book_from_dict(value: dict) -> Book:
    def levels(side):
        return tuple(Level(number(level["price"]), number(level["quantity"])) for level in value[side])
    return Book(value["venue"], value["pair"], levels("bids"), levels("asks"),
                value["received_ns"], value["request_duration_ns"], value["exchange_ns"])


def events(path: Path, *, max_bytes=None, strict=False):
    if strict:
        from .triangular import strict_json
    previous_seq, previous_mono, run_id = 0, 0, None
    logical_bytes = 0
    opener = gzip.open(path, "rt", encoding="utf-8") if path.suffix == ".gz" else path.open(encoding="utf-8")
    with opener as stream:
        line_limit = 16*1024*1024 if max_bytes is not None else -1
        while line := stream.readline(line_limit+1 if line_limit > 0 else -1):
            if max_bytes is not None:
                logical_bytes += len(line.encode())
                if len(line) > line_limit or logical_bytes > max_bytes:
                    raise DataError("stream recording exceeds replay bounds")
            row = strict_json(line) if strict else json.loads(line, parse_float=Decimal)
            if not isinstance(row, dict) or row.get("schema") != 2:
                raise DataError("invalid stream record schema")
            if type(row["seq"]) is not int or row["seq"] != previous_seq + 1:
                raise DataError("record sequence missing or repeated")
            if type(row["monotonic_ns"]) is not int or row["monotonic_ns"] < previous_mono:
                raise DataError("monotonic receive clock regressed")
            if run_id is not None and row["run_id"] != run_id:
                raise DataError("mixed recording sessions")
            if previous_seq == 0 and row["kind"] != "recording_start":
                raise DataError("missing recording header")
            previous_seq, previous_mono, run_id = row["seq"], row["monotonic_ns"], row["run_id"]
            if row["kind"] == "frame":
                if not isinstance(row["raw"], str):
                    raise DataError("non-text stream frame")
                if hashlib.sha256(row["raw"].encode()).hexdigest() != row["sha256"]:
                    raise DataError("raw stream frame hash mismatch")
            yield row


def replay_books(path: Path):
    """Yield books and invalidation events reconstructed from original frames."""
    parsers = {}
    for row in events(path):
        venue, kind = row["venue"], row["kind"]
        if kind == "session_start":
            parsers[venue] = StreamParser(venue, row["pair"])
            yield {"kind": "gap", "venue": venue, "reason": "new_connection", "observed_ns": row["observed_ns"]}
        elif kind == "gap":
            # Offset jumps are within full-snapshot streams; the source frame
            # already reset side state. Connection/clock gaps invalidate all.
            if row["reason"] != "offset_jump" and venue in parsers:
                parsers[venue].reset()
                yield {"kind": "gap", "venue": venue, "reason": row["reason"], "observed_ns": row["observed_ns"]}
        elif kind == "frame":
            if venue not in parsers:
                raise DataError("frame before connection start")
            if abs(row["clock_error_ns"]) > 1_000_000_000:
                parsers[venue].reset()
                yield {"kind": "gap", "venue": venue, "reason": "clock_discontinuity", "observed_ns": row["observed_ns"]}
                continue
            try:
                for message in messages(row["raw"]):
                    for result in parsers[venue].parse(message, row["received_ns"]):
                        if result["kind"] == "book":
                            yield {"kind": "book", "venue": venue, "book": book_from_dict(result["book"])}
                        elif result["kind"] in ("gap", "book_unavailable"):
                            yield {"venue": venue, "observed_ns": row["observed_ns"], **result}
            except (ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
                parsers[venue].reset()
                yield {"kind": "gap", "venue": venue, "reason": "invalid_frame", "error_type": type(exc).__name__, "observed_ns": row["observed_ns"]}


def audit_stream(path: Path) -> dict:
    counts, venues, last_book, max_intervals = Counter(), set(), {}, {}
    source_ages = {}
    ended, reason, size = False, None, path.stat().st_size
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    for row in events(path):
        if ended:
            raise DataError("events after recording_end")
        counts[row["kind"]] += 1
        if row["kind"] == "recording_start":
            venues.update(row["venues"])
        elif row["kind"] == "recording_end":
            ended, reason = True, row["reason"]
    for result in replay_books(path):
        venue = result["venue"]
        if result["kind"] != "book":
            counts[venue + ":" + result["kind"]] += 1
            continue
        book = result["book"]
        counts[venue + ":replayed_book"] += 1
        if venue in last_book:
            interval = book.received_ns - last_book[venue]
            max_intervals[venue] = max(max_intervals.get(venue, 0), interval)
        last_book[venue] = book.received_ns
        if book.exchange_ns is None:
            counts[venue + ":unknown_source_time"] += 1
        else:
            age = book.received_ns - book.exchange_ns
            stats = source_ages.setdefault(venue, {"minimum": age, "maximum": age, "future_count": 0, "over_5s_count": 0})
            stats["minimum"], stats["maximum"] = min(stats["minimum"], age), max(stats["maximum"], age)
            stats["future_count"] += age < 0
            stats["over_5s_count"] += age > 5_000_000_000
        try:
            book.validate_age(book.received_ns, 5_000_000_000)
        except DataError:
            counts[venue + ":source_time_invalid"] += 1
    return {"kind": "stream_audit", "sha256": digest.hexdigest(), "bytes": size,
            "counts": dict(counts), "ended_cleanly": ended and reason == "duration_completed",
            "end_reason": reason, "max_book_interval_ns": max_intervals,
            "source_age_ns": source_ages,
            "venues_without_books": sorted(venues - set(last_book)),
            "market_history_complete": False, "execution_ready": False}
