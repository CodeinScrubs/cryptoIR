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
    parser = argparse.ArgumentParser(description="cryptoIR: research and read-only account checks; no real orders")
    commands = parser.add_subparsers(dest="command", required=True)
    products=commands.add_parser("products",help="record public leveraged-product catalogs; optional separate FAPI book")
    products.add_argument("--output",type=Path,required=True)
    products.add_argument("--tabdeal-book",help="explicit BASE/QUOTE resolved from the current FAPI catalog")
    products_audit=commands.add_parser("products-audit",help="reconstruct product observations from their public raw responses")
    products_audit.add_argument("path",type=Path)
    quote=commands.add_parser("margin-quote",help="Wallex public calculation only; creates no position")
    quote.add_argument("--market",required=True)
    quote.add_argument("--side",choices=("long","short"),required=True)
    quote.add_argument("--collateral",type=number,required=True)
    quote.add_argument("--credit-ratio",type=number,required=True)
    quote.add_argument("--open-price",type=number,required=True)
    quote.add_argument("--output",type=Path,required=True)
    quote_audit=commands.add_parser("margin-quote-audit",help="reconstruct an accepted public calculation; no network")
    quote_audit.add_argument("path",type=Path)
    account=commands.add_parser("account-check",help="read-only account assessment; optional local Windows credential storage")
    account.add_argument("--venue",choices=("nobitex","wallex","tabdeal"),required=True)
    account.add_argument("--legacy-token",action="store_true",help="Nobitex legacy token instead of the default Ed25519 API key")
    account.add_argument("--save-credentials",action="store_true",help="prompt once and save/replace in local Windows Credential Manager")
    credential_status=commands.add_parser("credentials-status",help="check this checkout's local vault entry without showing secrets")
    credential_status.add_argument("--venue",choices=VENUES,required=True)
    credential_forget=commands.add_parser("credentials-forget",help="remove this checkout's saved key; does not revoke it at the exchange")
    credential_forget.add_argument("--venue",choices=VENUES,required=True)
    for name, help_text in (("triangle-record", "bounded public all-market sampling; no orders"),
                            ("triangle-screen", "replay registered three-leg snapshot estimates")):
        triangle = commands.add_parser(name, help=help_text)
        triangle.add_argument("--plan", type=Path, required=True)
        triangle.add_argument("--catalog", type=Path, required=True)
        triangle.add_argument("--output", type=Path, required=True)
        if name == "triangle-screen":
            triangle.add_argument("--recording", type=Path, required=True)
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
    spread = commands.add_parser("spread-screen", help="registered fee-aware snapshot capacity diagnostic; no fills")
    spread.add_argument("--plan", type=Path, required=True)
    spread.add_argument("--catalog", type=Path, required=True)
    spread.add_argument("--recording", type=Path, required=True)
    spread.add_argument("--output", type=Path, required=True)
    paper = commands.add_parser("paper-demo", help="synthetic durable accounting exercise, not a trading strategy")
    paper.add_argument("--output", required=True, type=Path)
    risk_demo = commands.add_parser("risk-demo", help="synthetic loss, risk latch and restart exercise")
    risk_demo.add_argument("--output", required=True, type=Path)
    forward = commands.add_parser("paper-run", help="bounded experimental forward paper session; public data only")
    forward.add_argument("--config", required=True, type=Path)
    forward.add_argument("--directory", required=True, type=Path)
    forward.add_argument("--seconds", type=int, default=60)
    forward.add_argument("--max-mb", type=int, default=64)
    paper_status = commands.add_parser("paper-status", help="read local paper status and inspect its OS owner lock")
    paper_status.add_argument("directory", type=Path)
    dashboard = commands.add_parser("dashboard", help="serve a read-only paper dashboard on local loopback")
    dashboard.add_argument("directory", type=Path)
    dashboard.add_argument("--port", type=int, default=8765)
    dashboard.add_argument("--seconds", type=int, default=3600)
    paper_stop = commands.add_parser("paper-stop", help="create a persistent paper STOP marker; holdings remain owned")
    paper_stop.add_argument("directory", type=Path)
    funding=commands.add_parser("paper-fund",help="prepare an IRT-funded paper account and matched BTC/USDT holding benchmarks")
    funding.add_argument("--template",type=Path,required=True)
    funding.add_argument("--initial-irt",type=number,required=True)
    funding.add_argument("--fx-fee",type=number,required=True,help="explicit assumed fiat-market fee; not account-verified")
    funding.add_argument("--directory",type=Path,required=True)
    upgrade=commands.add_parser("paper-upgrade-empty",help="explicit source upgrade with backup; only accounts that have never placed an order")
    upgrade.add_argument("--config",type=Path,required=True)
    upgrade.add_argument("--directory",type=Path,required=True)
    catalog = commands.add_parser("catalog", help="record current public market metadata; no account requests")
    catalog.add_argument("--output", required=True, type=Path)
    stream = commands.add_parser("stream", help="bounded public WebSocket recording with gap markers")
    stream.add_argument("--output", required=True, type=Path)
    stream.add_argument("--pair", default="BTC/USDT")
    stream.add_argument("--seconds", type=int, default=60)
    stream.add_argument("--venues", nargs="+", choices=VENUES, default=list(VENUES))
    stream.add_argument("--max-mb", type=int, default=64)
    stream_check = commands.add_parser("stream-audit", help="validate stream log and replay original frames")
    stream_check.add_argument("path", type=Path)
    history = commands.add_parser("history", help="download hourly data and reconcile against exchange daily bars")
    history.add_argument("--venue", choices=["nobitex", "wallex"], required=True)
    history.add_argument("--pair", default="BTC/USDT")
    history.add_argument("--start", required=True, help="UTC date YYYY-MM-DD inclusive")
    history.add_argument("--end", required=True, help="UTC date YYYY-MM-DD exclusive")
    history.add_argument("--output", type=Path, required=True)
    history_check = commands.add_parser("history-audit", help="rebuild historical reconciliation from original responses")
    history_check.add_argument("directory", type=Path)
    research = commands.add_parser("research", help="run preregistered cost screens with sequential holdout gates")
    research.add_argument("--plan", type=Path, required=True)
    research.add_argument("--dataset", type=Path, required=True)
    research.add_argument("--output", type=Path, required=True)
    rotation = commands.add_parser("rotation-research", help="registered shared-cash relative-momentum screen; no real orders")
    rotation.add_argument("--plan", type=Path, required=True)
    rotation.add_argument("--dataset", type=Path, action="append", required=True)
    rotation.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "rotation-research":
        from .rotation import run_experiment
        report = run_experiment(args.plan, args.dataset, args.output)
        print(encode({k: report[k] for k in ("experiment", "status", "dataset_pins_verified", "held_out_inspected", "execution_ready")}))
        return int(report["status"] == "incomplete")
    if args.command=="products":
        from .products import collect_products
        report=collect_products(args.output,args.tabdeal_book)
        print(encode(report))
        return int(not report["all_requests_observed"])
    if args.command=="products-audit":
        from .products import audit_products
        report=audit_products(args.path)
        print(encode(report))
        return int(not report["all_requests_observed"])
    if args.command=="margin-quote":
        from .products import margin_quote
        report=margin_quote(args.output,args.market,args.side,args.collateral,args.credit_ratio,args.open_price)
        print(encode(report))
        return int(report["status"]!="observed")
    if args.command=="margin-quote-audit":
        from .products import audit_quote
        print(encode(audit_quote(args.path)))
        return 0
    if args.command=="account-check":
        from .accounts import account_check
        return account_check(args.venue,legacy_token=args.legacy_token,save=args.save_credentials)
    if args.command == "triangle-record":
        from .triangular import capture
        report = capture(args.plan, args.catalog, args.output)
        print(encode(report))
        return int(report["stop_reason"] != "sample_limit")
    if args.command == "triangle-screen":
        from .triangular import run_screen
        report = run_screen(args.plan, args.catalog, args.recording, args.output)
        print(encode({k:report[k] for k in ("experiment", "status", "counts", "execution_ready")}))
        return int(report["status"] != "completed_conditional_diagnostic")
    if args.command=="credentials-status":
        from .credentials import credential_status
        print(encode(credential_status(args.venue)))
        return 0
    if args.command=="credentials-forget":
        from .credentials import forget_credentials
        print(encode(forget_credentials(args.venue)))
        return 0
    if args.command=="paper-upgrade-empty":
        from .forward import ForwardConfig
        from .upgrade import upgrade_empty_account
        config=ForwardConfig.from_dict(json.loads(args.config.read_text(encoding="utf-8-sig")))
        print(encode(upgrade_empty_account(args.directory,config)))
        return 0
    if args.command=="paper-fund":
        from .forward import ForwardConfig
        from .funding import fund_paper
        config=ForwardConfig.from_dict(json.loads(args.template.read_text(encoding="utf-8-sig")))
        print(encode(fund_paper(args.directory,args.initial_irt,config,args.fx_fee)))
        return 0
    if args.command == "paper-run":
        import asyncio
        from .forward import ForwardConfig
        from .runner import run_paper
        config = ForwardConfig.from_dict(json.loads(args.config.read_text(encoding="utf-8-sig")))
        report = asyncio.run(run_paper(config,args.directory,args.seconds,max_mb=args.max_mb))
        print(encode({"kind":"forward_paper_summary","reason":report["reason"],"counts":report.get("counts",{}),
                      "orders":len(report["orders"]),"warmup_bars":report["warmup_bars"],
                      "risk_halt":report["risk"]["halt"],"capital_basis":report["capital_basis"],
                      "live_execution":False,"profitability_established":False}))
        return int(report["reason"]!="duration_completed" or not report.get("counts",{}).get(config.venue+":book")
                   or not report.get("counts",{}).get(config.venue+":candle_accepted"))
    if args.command == "dashboard":
        from .dashboard import serve_dashboard
        serve_dashboard(args.directory, port=args.port, seconds=args.seconds)
        return 0
    if args.command == "paper-status":
        from .monitor import read_status
        print(encode(read_status(args.directory)))
        return 0
    if args.command == "paper-stop":
        if not (args.directory/"paper.sqlite").is_file():
            raise DataError("not an existing paper experiment")
        (args.directory/"STOP").touch(exist_ok=True)
        print(encode({"kind":"paper_stop_requested","holdings":"retained_not_liquidated"}))
        return 0
    if args.command == "risk-demo":
        from .risk import demo
        print(encode(demo(args.output)))
        return 0
    if args.command == "research":
        from .research import run_experiment
        report = run_experiment(args.plan, args.dataset, args.output)
        print(encode({key: report[key] for key in ("experiment", "status", "selected", "held_out_inspected", "execution_ready")}))
        return 0
    if args.command == "history-audit":
        from datetime import datetime, timezone, timedelta
        from .history import audit_history
        result, _ = audit_history(args.directory)
        table = result.pop("table")
        print(encode(result))
        print("Day_UTC | Day_Tehran | Bars | Open_delta | High_delta | Low_delta | Close_delta | Volume_delta | Match")
        for row in table:
            utc = datetime.fromtimestamp(row["day_open_epoch"], timezone.utc)
            deltas = row["deltas"] or {key: "unknown" for key in ("open", "high", "low", "close", "volume")}
            print(" | ".join(map(str, (utc.isoformat(), utc.astimezone(timezone(timedelta(hours=3, minutes=30))).isoformat(),
                                       row["hour_count"], *(deltas[k] for k in ("open", "high", "low", "close", "volume")), row["matched"]))))
        return int(not result["reconciled"])
    if args.command == "history":
        from datetime import datetime, timezone
        from .history import download_history
        start = int(datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
        end = int(datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
        result = download_history(args.output, args.venue, args.pair, start, end)
        print(encode(result))
        return int(not result["reconciled"])
    if args.command == "stream-audit":
        from .stream_audit import audit_stream
        result = audit_stream(args.path)
        print(encode(result))
        return int(bool(result["venues_without_books"] or not result["ended_cleanly"]))
    if args.command == "stream":
        import asyncio
        from .streams import record_streams
        result = asyncio.run(record_streams(args.output, args.pair, args.seconds, args.venues, args.max_mb))
        print(encode(result))
        return int(bool(result["venues_without_books"] or result["reason"] != "duration_completed"))
    if args.command == "catalog":
        from .catalog import collect_catalogs
        args.output.parent.mkdir(parents=True, exist_ok=True)
        errors = 0
        with args.output.open("x", encoding="utf-8", newline="\n") as stream:
            for result in collect_catalogs():
                stream.write(encode(result) + "\n")
                errors += result["kind"] == "error"
                print(encode({"venue": result["venue"], "kind": result["kind"],
                              "markets": len(result.get("markets", [])), "execution_ready": False}))
        return int(bool(errors))
    if args.command == "paper-demo":
        from .paper import demo
        print(encode(demo(args.output)))
        return 0
    if args.command == "estimate":
        if args.quantity <= 0 or not 0 <= args.fee < 1 or not 0 <= args.reserve_bps < 10000 or args.max_age_ms <= 0:
            parser.error("invalid quantity, costs or age")
        for result in estimates(args.path, quantity=args.quantity, fee=args.fee,
                                reserve_bps=args.reserve_bps, max_age_ns=args.max_age_ms * 1_000_000):
            print(encode(result))
        return 0
    if args.command == "spread-screen":
        from .arbitrage import run_spread_screen
        report = run_spread_screen(args.plan, args.catalog, args.recording, args.output)
        print(encode({k: report[k] for k in ("experiment", "status", "execution_ready", "scenarios_and_routes")}))
        return int(report["status"] != "completed_conditional_diagnostic")
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
