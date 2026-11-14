"""Read-only, bounded local paper status. No account credentials or exchange calls."""

import json
import time
from pathlib import Path

from .domain import DataError, number
from .runtime import OwnerLock
from .stream_audit import book_from_dict
from .valuation import verify_receipt

NS = 1_000_000_000


def read_json(path: Path, maximum=8*1024*1024):
    with path.open("rb") as stream:
        raw = stream.read(maximum+1)
    if len(raw) > maximum:
        raise DataError("local status exceeds the read limit")
    return json.loads(raw)


def read_status(directory: Path, *, now_ns=None) -> dict:
    now = time.time_ns() if now_ns is None else now_ns
    report = read_json(directory/"status.json")
    if (report.get("schema") != 1 or report.get("kind") != "paper_accounting"
            or report.get("live_execution") is not False):
        raise DataError("unsupported paper status")
    if type(report.get("updated_ns")) is not int:
        raise DataError("invalid status timestamp")
    if not (directory/"owner.lock").is_file():
        active = None
    else:
        try:
            with OwnerLock(directory/"owner.lock"):
                active = False
        except DataError:
            active = True
    age = (now-report["updated_ns"])/NS
    if age < 0:
        process = "clock_unknown"
    elif active and report.get("session_status") == "running":
        process = "running" if age <= 15 else "unresponsive"
    elif active is False:
        process = "stopped" if report.get("session_status") == "stopped" else "interrupted"
    else:
        process = "unknown"
    report.update(owner_lock_held=active, heartbeat_age_seconds=age, process_state=process,
                  status_read_ns=now, stop_requested=(directory/"STOP").exists())
    valuation = report.get("matched_valuation", {})
    value = valuation.get("last_valuation")
    valuation_age = None
    if value is not None:
        valuation_age = (now-value["observed_ns"])/NS
    marked = valuation.get("marked_balances")
    totals = {asset:sum(number(row["free"])+number(row["reserved"]) for row in report.get("balances", [])
                        if row["venue"] == "nobitex" and row["asset"] == asset) for asset in ("USDT", "BTC")}
    same_balances = marked is not None and all(number(marked[k.lower()]) == v for k,v in totals.items())
    current = bool(valuation.get("available") and process == "running" and same_balances
                   and valuation_age is not None and 0 <= valuation_age <= 5)
    valuation.update(available=current, age_seconds=valuation_age)
    if not current and value is not None:
        valuation["reason"] = "historical_mark_not_current"
    report.update(matched_valuation=valuation, irt_performance_available=current)
    book = report.get("last_book")
    report["book_current"] = False
    report["book_age_seconds"] = None
    if book is not None:
        parsed = book_from_dict(book)
        report["book_age_seconds"] = (now-parsed.exchange_ns)/NS if parsed.exchange_ns else None
        try:
            parsed.validate_age(now, report.get("risk_limits", {}).get("max_feed_age_ns", 5*NS))
            report["book_current"] = process == "running" and parsed.exchange_ns is not None
        except DataError:
            pass
    report["risk_current"] = bool(report.get("risk", {}).get("data_ok") and report["book_current"])
    return report


def dashboard_snapshot(directory: Path, *, now_ns=None) -> dict:
    report = read_status(directory, now_ns=now_ns)
    # Explicit projection: never serve local files, PID, arbitrary new fields or paths.
    fields = ("process_state", "session_status", "reason", "heartbeat_age_seconds", "stop_requested",
              "balances", "risk", "risk_limits", "risk_current", "forward", "strategy", "capital_basis",
              "warmup_bars", "last_candle", "last_book", "book_current", "book_age_seconds", "matched_valuation",
              "counts", "journal_events", "pair", "venue")
    view = {key:report.get(key) for key in fields}
    view.update(schema=1, mode="paper", live_execution=False, profitability_established=False,
                order_count=len(report.get("orders", [])), orders=report.get("orders", [])[-50:][::-1],
                updated_ms=report["updated_ns"]//1_000_000, read_ms=report["status_read_ns"]//1_000_000)
    receipt_path = directory/"capital-receipt.json"
    view["initial_irt"] = None
    if report.get("capital_basis") == "paper_irt_conversion":
        receipt = read_json(receipt_path, 1024*1024)
        verify_receipt(receipt)
        value = view["matched_valuation"].get("last_valuation")
        if value and value["capital_receipt_sha256"] != receipt["receipt_sha256"]:
            raise DataError("status and capital receipt do not match")
        view["initial_irt"] = receipt["initial_irt"]
    # Percentage conversion is exact on the server; the browser only formats text.
    for name in ("risk", "risk_limits"):
        for key,value in list((view[name] or {}).items()):
            if key.endswith("_fraction"):
                view[name][key+"_percent"] = str(number(value)*100)
    value = view["matched_valuation"].get("last_valuation")
    if value:
        value["observed_ms"] = value["observed_ns"]//1_000_000
        for portfolio in [value["portfolio"], *value["benchmarks"].values()]:
            for key in ("net_return_irt", "net_return_usdt_numeraire"):
                portfolio[key+"_percent"] = str(number(portfolio[key])*100)
    return view
