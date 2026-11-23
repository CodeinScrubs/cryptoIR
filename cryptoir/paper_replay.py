"""Reproduce paper execution from recorded inputs and verify ledger parity."""

from collections import deque
from dataclasses import asdict
from hashlib import file_digest, sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlencode

from .domain import DataError
from .forward import ForwardEngine, NS, source_fingerprint
from .history import HISTORY, HOUR, parse_udf
from .paper import PaperLedger, timestamp
from .stream_audit import book_from_dict, events
from .streams import StreamParser, dumps, messages
from .triangular import strict_json


INPUT_VERSION = 1
FEED_ACTIONS = {"book", "gap", "session_start", "book_unavailable", "candle_response"}
REPLAYABLE_STOPS = {"duration_completed", "manual_stop", "feed_worker_stopped"}
MAX_RECORDING_BYTES = 512*1024*1024


def config_digest(config):
    return sha256(dumps(asdict(config)).encode()).hexdigest()


def ledger_digest(ledger):
    """Compare durable rows, independent of SQLite page layout and WAL state."""
    digest = sha256()
    for table, order in (("balances", "venue, asset"), ("orders", "seq"), ("journal", "seq"),
                         ("metadata", "key"), ("snapshots", "id")):
        digest.update((table+"\n").encode())
        for row in ledger.db.execute(f"SELECT * FROM {table} ORDER BY {order}"):
            digest.update((dumps(list(row))+"\n").encode())
    return digest.hexdigest()


def candle_url(config, start, end):
    return HISTORY[config.venue]+"?"+urlencode({"symbol": config.pair.replace("/", ""),
        "resolution": "60", "from": start, "to": end-1})


def verified_candle_payload(response):
    body = response.get("raw_body")
    if not isinstance(body, str) or sha256(body.encode()).hexdigest() != response.get("body_sha256"):
        raise DataError("candle response body hash mismatch")
    try:
        payload = strict_json(body)
    except ValueError as exc:
        raise DataError("invalid raw candle response") from exc
    if dumps(payload) != dumps(response.get("payload")):
        raise DataError("candle response payload differs from raw body")
    return payload


def closed_candles(record, config=None):
    response = record["response"]
    if response["kind"] != "snapshot":
        raise DataError("public candle request failed")
    payload = verified_candle_payload(response)
    start, end = record["requested_start"], record["requested_end"]
    if type(start) is not int or type(end) is not int or not 0 < start < end:
        raise DataError("invalid candle request interval")
    if config is not None and response.get("url") != candle_url(config, start, end):
        raise DataError("candle response URL differs from the requested market and interval")
    rows = parse_udf(payload)
    if any(not start <= bar.time < end for bar in rows):
        raise DataError("candle endpoint returned an unexpected timestamp")
    cutoff = min(end, timestamp(response["received_ns"])//NS-120)
    return [bar for bar in rows if bar.time+HOUR <= cutoff]


def apply_input(engine, action, now_ns, source=None, reason=None):
    """Run a recorded consumer action. Return a candle source failure if any."""
    timestamp(now_ns)
    if action == "start":
        engine.start(now_ns)
    elif action == "advance":
        engine.advance(now_ns)
    elif action == "stop":
        engine.stop(now_ns, permanent=reason == "manual_stop", reason=reason)
    elif action == "book":
        engine.on_book(book_from_dict(source["book"]), now_ns=now_ns)
    elif action in ("gap", "session_start", "book_unavailable"):
        engine.invalidate(now_ns, source.get("reason", action))
    elif action == "candle_response":
        try:
            rows = closed_candles(source, engine.config)
        except (DataError, KeyError, AttributeError, TypeError) as exc:
            reason = str(exc) if isinstance(exc, DataError) else "invalid candle response schema"
            engine.candle_failure(now_ns, reason)
            return reason
        engine.apply_candles(rows, received_ns=source["response"]["received_ns"], now_ns=now_ns)
    elif action == "candle_failure":
        engine.candle_failure(now_ns, reason)
    else:
        raise DataError("unknown paper input action")
    return None


class BookEvidence:
    """Check the recorded derived events against the exact stream parser."""

    def __init__(self, config):
        self.config = config
        self.parser = None
        self.attempt = 0
        self.pending = deque()
        self.invalid_frame = False

    def on_row(self, row):
        kind = row["kind"]
        if self.pending:
            expected = self.pending.popleft()
            if (row.get("attempt") != self.attempt or row.get("pair") != self.config.pair
                    or any(dumps(row.get(key)) != dumps(value) for key, value in expected.items())):
                raise DataError("paper book or gap differs from original frame")
            return
        if self.invalid_frame and not (kind == "gap" and row.get("reason") == "stream_invalidated"):
            if kind not in ("heartbeat", "subscription_ack"):
                raise DataError("invalid frame lacks recorded gap")
        if kind == "session_start":
            self.attempt += 1
            if row.get("attempt") != self.attempt or row.get("pair") != self.config.pair:
                raise DataError("paper stream attempt or pair differs")
            self.parser = StreamParser(self.config.venue, self.config.pair)
        elif kind == "frame":
            if self.parser is None or row.get("attempt") != self.attempt or row.get("pair") != self.config.pair:
                raise DataError("paper frame outside recorded connection")
            if timestamp(row["received_ns"]) > row["observed_ns"]:
                raise DataError("paper frame receipt after persistence")
            try:
                for message in messages(row["raw"]):
                    self.pending.extend(self.parser.parse(message, row["received_ns"]))
            except (ValueError, KeyError, TypeError, IndexError, AttributeError, ArithmeticError):
                self.invalid_frame = True
        elif kind in ("book", "book_unavailable") or (kind == "gap" and row.get("reason") == "offset_jump"):
            raise DataError("derived paper event has no original frame")
        elif kind == "gap":
            if row.get("reason") not in ("connection_error", "stream_invalidated"):
                raise DataError("unsupported paper stream gap")
            self.parser = None
            self.invalid_frame = False


def _replay_session(engine, path, code_sha256, config_sha256):
    config = engine.config
    initial_stat = path.stat()
    if initial_stat.st_size > MAX_RECORDING_BYTES + 1024*1024:
        raise DataError("paper recording file exceeds replay bounds")
    rows = events(path, max_bytes=MAX_RECORDING_BYTES, strict=True)
    first = next(rows, None)
    if not first or first.get("paper_input_version") != INPUT_VERSION:
        raise DataError("recording lacks exact paper inputs from version 0.26 or later")
    if (first["venue"] != "all" or first.get("purpose") != "experimental_forward_paper"
            or first.get("pair") != config.pair or first.get("venues") != [config.venue]
            or first.get("live_execution") is not False
            or first.get("config_sha256") != config_sha256 or first.get("source_sha256") != code_sha256):
        raise DataError("paper recording scope, configuration or source differs")
    if first.get("opening_ledger_sha256") != ledger_digest(engine.ledger):
        raise DataError("paper replay needs the full account session chain from its initial allocation")
    evidence = BookEvidence(config)
    pending, ended, stop, started = None, False, None, False
    latest_time, previous_wall = 0, timestamp(first["observed_ns"])
    input_count = 0
    origin_wall = previous_wall
    origin_mono = first["monotonic_ns"]
    for row in rows:
        observed = timestamp(row["observed_ns"])
        if (ended or observed < previous_wall or type(row["clock_error_ns"]) is not int or abs(row["clock_error_ns"]) > NS
                or abs(observed-origin_wall-(row["monotonic_ns"]-origin_mono)) > NS):
            raise DataError("paper replay recording order or clock invalid")
        kind = row["kind"]
        if pending is not None and kind != "paper_input":
            raise DataError("missing paper input for preceding source event")
        if kind == "paper_input":
            action, now = row["action"], timestamp(row["now_ns"])
            if row["venue"] != "all" or now < max(latest_time, previous_wall) or now > observed or stop is not None:
                raise DataError("paper input clock or lifecycle invalid")
            if not started:
                if action != "start" or now < origin_wall:
                    raise DataError("missing paper session start input")
                started = True
            elif action == "start":
                raise DataError("duplicate paper session start input")
            reason = row.get("reason")
            if action in FEED_ACTIONS:
                if (pending is None or pending["kind"] != action or row.get("source_seq") != pending["seq"]
                        or now < pending["observed_ns"] or type(row.get("source_seq")) is not int):
                    raise DataError("paper input lacks a causally available source")
                if action == "candle_response":
                    response = pending["response"]
                    if timestamp(response["received_ns"]) > pending["observed_ns"]:
                        raise DataError("candle response receipt after persistence")
                    if response["kind"] == "snapshot":
                        verified_candle_payload(response)
            elif pending is not None or row.get("source_seq") is not None:
                raise DataError("unexpected source for paper input")
            if action == "stop":
                if reason not in REPLAYABLE_STOPS or evidence.pending or evidence.invalid_frame:
                    raise DataError("paper session ended without a complete replayable input sequence")
                stop = reason
            elif action == "candle_failure":
                if reason != "downtime exceeds bounded candle recovery window":
                    raise DataError("unsupported standalone paper candle failure")
            apply_input(engine, action, now, source=pending, reason=reason)
            input_count += 1
            pending, latest_time = None, now
        elif kind == "recording_end":
            if (row["venue"] != "all" or stop is None or row.get("reason") != stop
                    or row.get("closing_ledger_sha256") != ledger_digest(engine.ledger)):
                raise DataError("paper closing ledger does not match replay")
            ended = True
        elif not started or stop is not None or row["venue"] != config.venue:
            raise DataError("paper source event outside active session")
        elif kind in FEED_ACTIONS or kind in ("frame", "heartbeat", "subscription_ack", "venue_stopped"):
            # Heartbeat and acknowledgement records may be interleaved with
            # parser outputs from one multi-message frame.
            if kind not in ("heartbeat", "subscription_ack", "venue_stopped", "candle_response"):
                evidence.on_row(row)
            if kind in FEED_ACTIONS:
                if kind == "candle_response" and row.get("pair") != config.pair:
                    raise DataError("candle response pair differs from paper session")
                pending = row
        elif kind not in ("candle_accepted", "valuation_response", "portfolio_valuation", "valuation_unavailable"):
            raise DataError("unexpected event in paper recording")
        previous_wall = observed
    if not ended:
        raise DataError("paper recording lacks a verified closing checkpoint")
    with path.open("rb") as recorded:
        recording_sha256 = file_digest(recorded, "sha256").hexdigest()
    final_stat = path.stat()
    if (initial_stat.st_size, initial_stat.st_mtime_ns) != (final_stat.st_size, final_stat.st_mtime_ns):
        raise DataError("paper recording changed during replay")
    return {"run_id": first["run_id"], "reason": stop, "inputs": input_count,
            "recording_sha256": recording_sha256,
            "closing_ledger_sha256": ledger_digest(engine.ledger)}


def replay(config, recordings, output: Path):
    """Use a disposable ledger and publish a report only after exact parity."""
    if not recordings:
        raise DataError("at least one paper recording is required")
    source_sha, config_sha = source_fingerprint(), config_digest(config)
    with TemporaryDirectory(prefix="cryptoir-paper-replay-") as scratch:
        ledger = PaperLedger(Path(scratch)/"paper.sqlite")
        try:
            ledger.seed({(config.venue, "USDT"): config.risk.initial_quote})
            sessions = []
            for path in recordings:
                engine = ForwardEngine(ledger, config, source_id=source_sha)
                try:
                    sessions.append(_replay_session(engine, path, source_sha, config_sha))
                except (OSError, EOFError, KeyError, TypeError, AttributeError, ValueError) as exc:
                    if isinstance(exc, DataError):
                        raise
                    raise DataError("invalid or truncated paper recording") from exc
            report = {"kind": "paper_replay", "ledger_parity": True, "source_sha256": source_sha,
                      "config_sha256": config_sha, "sessions": sessions, "paper": engine.report(),
                      "live_execution": False, "profitability_established": False}
        finally:
            ledger.close()
    with output.open("x", encoding="utf-8", newline="\n") as target:
        target.write(dumps(report)+"\n")
    return report
