"""Bounded, read-only recording and deterministic quality audit."""

import argparse
import hashlib
import json
import time
from collections import Counter
from decimal import Decimal
from pathlib import Path

from .domain import DataError, estimate_spread, number
from .feeds import VENUES, collect, endpoint, normalize


def encode(value):
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"), allow_nan=False)


def audit(path: Path) -> dict:
    counts, last, fingerprints = Counter(), {}, set()
    previous_ns = 0
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            counts["records"] += 1
            try:
                record = json.loads(line, parse_float=Decimal)
                if not isinstance(record, dict) or record.get("schema") != 1:
                    raise DataError("invalid event schema")
                key = (record["venue"], record["pair"])
                endpoint(*key)
                received_ns = record["received_ns"]
                if type(received_ns) is not int or received_ns <= 0:
                    raise DataError("invalid receive time")
                if received_ns < previous_ns or received_ns <= last.get(key, 0):
                    raise DataError("out-of-order or duplicate event")
                previous_ns, last[key] = received_ns, received_ns
                if record.get("kind") == "error":
                    counts["source_errors"] += 1
                    continue
                book = normalize(record)
                digest = hashlib.sha256(record["raw_body"].encode("utf-8")).hexdigest()
                if digest != record["body_sha256"]:
                    raise ValueError("raw hash mismatch")
                if json.loads(record["raw_body"], parse_float=Decimal) != record["payload"]:
                    # JSON Decimal is stored as a string; compare canonical forms.
                    if encode(json.loads(record["raw_body"], parse_float=Decimal)) != encode(record["payload"]):
                        raise ValueError("payload mismatch")
                fingerprint = (key, digest)
                if fingerprint in fingerprints:
                    counts["repeated_payloads"] += 1
                fingerprints.add(fingerprint)
                if book.exchange_ns is None:
                    counts["unknown_source_time"] += 1
                try:
                    book.validate_age(book.received_ns, 5_000_000_000)
                except ValueError:
                    counts["source_time_or_latency_failures"] += 1
                counts["valid_snapshots"] += 1
                counts[book.venue] += 1
            except (ValueError, KeyError, TypeError, IndexError):
                counts["invalid_records"] += 1
    return {"kind": "data_quality_audit", "counts": dict(counts),
            "historically_reconciled": False, "execution_ready": False,
            "limitations": ["REST sampling omits events between snapshots",
                            "unknown source times cannot establish freshness",
                            "no account, fee, inventory or fill verification"]}


def estimates(path: Path, *, quantity: Decimal, fee: Decimal, reserve_bps: Decimal,
              max_age_ns: int):
    """Replay in recorded receive order, with no access to later observations."""
    report = audit(path)
    if report["counts"].get("invalid_records"):
        raise DataError("audit failed; do not replay corrupt data")
    latest, previous_ns = {}, 0
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            record = json.loads(line, parse_float=Decimal)
            event_ns = record["received_ns"]
            if event_ns < previous_ns:
                raise DataError("global observation order regressed")
            previous_ns = event_ns
            key = (record["venue"], record["pair"])
            if record["kind"] == "error":
                latest.pop(key, None)
                continue
            current = normalize(record)
            latest[key] = current
            for other in latest.values():
                if other.venue == current.venue or other.pair != current.pair:
                    continue
                for buy, sell in ((current, other), (other, current)):
                    try:
                        yield estimate_spread(buy, sell, quantity, buy_fee=fee,
                                              sell_fee=fee, reserve_bps=reserve_bps,
                                              now_ns=event_ns, max_age_ns=max_age_ns)
                    except DataError as exc:
                        yield {"kind": "estimate_rejected", "reason": str(exc),
                               "buy_venue": buy.venue, "sell_venue": sell.venue}


def main() -> int:
    parser = argparse.ArgumentParser(description="cryptoIR: public data only; no real orders")
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("record", help="bounded public REST sampling")
    capture.add_argument("--pair", default="BTC/USDT")
    capture.add_argument("--samples", type=int, default=1)
    capture.add_argument("--interval", type=float, default=30)
    capture.add_argument("--output", type=Path, required=True)
    check = commands.add_parser("audit", help="offline record integrity and coverage")
    check.add_argument("path", type=Path)
    estimate = commands.add_parser("estimate", help="offline conditional spread estimates; no simulated fills")
    estimate.add_argument("path", type=Path)
    estimate.add_argument("--quantity", required=True, type=number)
    estimate.add_argument("--fee", required=True, type=number, help="assumed fee per leg, e.g. 0.004; NOT account-verified")
    estimate.add_argument("--reserve-bps", required=True, type=number)
    estimate.add_argument("--max-age-ms", type=int, default=5000)
    args = parser.parse_args()
    if args.command == "estimate":
        if args.quantity <= 0 or not 0 <= args.fee < 1 or not 0 <= args.reserve_bps < 10000 or args.max_age_ms <= 0:
            parser.error("invalid quantity, costs or age")
        for result in estimates(args.path, quantity=args.quantity, fee=args.fee,
                                reserve_bps=args.reserve_bps, max_age_ns=args.max_age_ms * 1_000_000):
            print(encode(result))
        return 0
    if args.command == "audit":
        report = audit(args.path)
        print(encode(report))
        counts = report["counts"]
        return int(bool(counts.get("invalid_records") or counts.get("source_errors") or not counts.get("valid_snapshots")))
    if not 1 <= args.samples <= 120 or not 30 <= args.interval <= 3600:
        parser.error("use 1..120 samples and 30..3600 seconds between batches")
    # Avoid append collisions, accidental overwrite and mixing independent runs.
    args.output.parent.mkdir(parents=True, exist_ok=True)
    errors = 0
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        for batch in range(args.samples):
            records = collect(args.pair)
            for record in records:
                stream.write(encode(record) + "\n")
                errors += record["kind"] == "error"
                print(encode({k: record[k] for k in ("venue", "pair", "kind", "received_ns")}))
            stream.flush()
            # A failing venue stops this bounded diagnostic run; no retry storm.
            if errors or batch + 1 == args.samples:
                break
            time.sleep(args.interval)
    print(encode({"output": str(args.output), "source_errors": errors, "venues": list(VENUES)}))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
