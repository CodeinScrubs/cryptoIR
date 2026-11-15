"""Fee-aware, inventory-bounded snapshot diagnostics; never portfolio returns."""

import hashlib
import json
import re
from collections import Counter
from decimal import Decimal
from pathlib import Path

from .backtest import floor_quantity
from .catalog import ENDPOINTS, parse_catalog
from .domain import Book, DataError, number, walk
from .stream_audit import audit_stream, events, replay_books
from .streams import dumps


def validate_plan(plan: dict) -> None:
    if plan.get("schema") != 1 or plan.get("pair") != "BTC/USDT":
        raise DataError("spread diagnostic currently requires explicit BTC/USDT units")
    venues = plan["venues"]
    if (len(venues) < 2 or len(venues) != len(set(venues))
            or any(v not in ENDPOINTS for v in venues)
            or set(plan["market_rules"]) != set(venues)):
        raise DataError("invalid spread venue universe")
    for key, low, high in (("capture_seconds", 5, 86400), ("capture_max_mb", 1, 512),
                           ("max_age_ms", 1, 60000)):
        if type(plan[key]) is not int or not low <= plan[key] <= high:
            raise DataError("invalid spread observation bounds")
    for key in ("gross_buy_quantity", "buy_quote_budget", "sell_base_inventory"):
        if number(plan[key]) <= 0:
            raise DataError("positive explicit size and capacity ceilings required")
    if not re.fullmatch(r"[a-f0-9]{64}", plan["catalog_sha256"]):
        raise DataError("catalog hash required")
    for rule in plan["market_rules"].values():
        if number(rule["quantity_step"]) <= 0:
            raise DataError("positive quantity increment required")
        for key in ("minimum_quantity", "minimum_notional"):
            if rule[key] is not None and number(rule[key]) <= 0:
                raise DataError("invalid known order minimum")
    scenarios = plan["scenarios"]
    if [s["name"] for s in scenarios] != ["zero_cost_diagnostic", "assumed_base", "assumed_stress"]:
        raise DataError("require zero-cost diagnostic, assumed base and stress scenarios")
    for scenario in scenarios:
        if set(scenario["fees"]) != set(venues) or not 0 <= number(scenario["reserve_bps"]) < 10000:
            raise DataError("explicit costs required for every venue")
        for fee in scenario["fees"].values():
            if not 0 <= number(fee["rate"]) < 1 or fee["buy_asset"] not in ("quote", "received_base"):
                raise DataError("invalid fee rate or buy fee asset")
    if (number(scenarios[0]["reserve_bps"]) != 0
            or any(number(f["rate"]) != 0 for f in scenarios[0]["fees"].values())):
        raise DataError("zero-cost diagnostic must have zero costs")
    if number(scenarios[2]["reserve_bps"]) < number(scenarios[1]["reserve_bps"]):
        raise DataError("stress reserve cannot improve base costs")
    for venue in venues:
        base, stress = (s["fees"][venue] for s in scenarios[1:])
        if number(stress["rate"]) < number(base["rate"]) or stress["buy_asset"] != base["buy_asset"]:
            raise DataError("stress cannot improve fee rate or change fee denomination")


def verify_catalog(path: Path, plan: dict) -> None:
    """Bind registered rule observations to original public response bodies."""
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != plan["catalog_sha256"]:
        raise DataError("catalog differs from registered source hash")
    found = set()
    for line in content.splitlines():
        row = json.loads(line, parse_float=Decimal)
        venue = row["venue"]
        if (venue in found or venue not in plan["venues"] or row.get("kind") != "snapshot"
                or row.get("dataset") != "market_catalog" or row.get("url") != ENDPOINTS[venue]
                or row.get("status_code") != 200):
            raise DataError("catalog source or venue mismatch")
        found.add(venue)
        if hashlib.sha256(row["raw_body"].encode()).hexdigest() != row["body_sha256"]:
            raise DataError("catalog raw body hash mismatch")
        original = json.loads(row["raw_body"], parse_float=Decimal)
        markets = parse_catalog(venue, original)
        if dumps(original) != dumps(row["payload"]) or dumps(markets) != dumps(row["markets"]):
            raise DataError("catalog normalization differs from source")
        matches = [m for m in markets if m["base"] == "BTC" and m["quote"] == "USDT"]
        if len(matches) != 1:
            raise DataError("ambiguous or absent native BTC/USDT market")
        for key, value in plan["market_rules"][venue].items():
            observed = matches[0].get(key)
            if (observed is None) != (value is None) or (value is not None and number(observed) != number(value)):
                raise DataError("registered order rule differs from catalog")
    if found != set(plan["venues"]):
        raise DataError("incomplete registered catalog coverage")


def quote_pair(buy: Book, sell: Book, plan: dict, scenario: dict, now_ns: int) -> dict:
    """Independent capacity check with no transfer, fill, or balance mutation.

    The plan is validated by the caller. Sell size never exceeds net bought base;
    existing inventory at the selling venue must separately cover that sale.
    """
    if buy.venue == sell.venue or buy.pair != plan["pair"] or sell.pair != plan["pair"]:
        raise DataError("require two venues and the registered pair")
    if buy.exchange_ns is None or sell.exchange_ns is None:
        raise DataError("unknown_source_time")
    for book in (buy, sell):
        book.validate_age(now_ns, plan["max_age_ms"] * 1_000_000)
    br, sr = (plan["market_rules"][b.venue] for b in (buy, sell))
    if br["minimum_notional"] is None or sr["minimum_notional"] is None:
        raise DataError("unknown_minimum_notional")
    quantity = number(plan["gross_buy_quantity"])
    if quantity % number(br["quantity_step"]):
        raise DataError("buy_quantity_step")
    bf, sf = (scenario["fees"][b.venue] for b in (buy, sell))
    buy_rate, sell_rate = number(bf["rate"]), number(sf["rate"])
    base_fee = quantity * buy_rate if bf["buy_asset"] == "received_base" else Decimal(0)
    received_base = quantity - base_fee
    sell_quantity = floor_quantity(received_base, number(sr["quantity_step"]))
    if sell_quantity <= 0:
        raise DataError("no_sellable_base_after_fee")
    for qty, rule in ((quantity, br), (sell_quantity, sr)):
        if rule["minimum_quantity"] is not None and qty < number(rule["minimum_quantity"]):
            raise DataError("minimum_quantity")
    bought, sold = walk(buy.asks, quantity), walk(sell.bids, sell_quantity)
    if bought.filled != quantity or sold.filled != sell_quantity:
        raise DataError("insufficient_displayed_depth")
    if bought.notional < number(br["minimum_notional"]) or sold.notional < number(sr["minimum_notional"]):
        raise DataError("minimum_notional")
    buy_quote_fee = bought.notional * buy_rate if bf["buy_asset"] == "quote" else Decimal(0)
    sell_quote_fee = sold.notional * sell_rate
    cost, proceeds = bought.notional + buy_quote_fee, sold.notional - sell_quote_fee
    reserve_rate = number(scenario["reserve_bps"]) / 10000
    reserve = (bought.notional + sold.notional) * reserve_rate
    if cost + bought.notional * reserve_rate > number(plan["buy_quote_budget"]):
        raise DataError("insufficient_buy_quote_capacity")
    if sell_quantity > number(plan["sell_base_inventory"]):
        raise DataError("insufficient_prefunded_sell_inventory")
    return {"kind": "conditional_spread_capacity", "executable": False,
            "observed_ns": now_ns, "pair": buy.pair,
            "buy_venue": buy.venue, "sell_venue": sell.venue,
            "gross_buy_quantity": quantity, "sell_quantity": sell_quantity,
            "buy_base_fee": base_fee, "buy_quote_fee": buy_quote_fee,
            "sell_quote_fee": sell_quote_fee, "residual_base": received_base-sell_quantity,
            "residual_base_valuation": "zero; retained, not realized",
            "buy_cost_quote": cost, "sell_proceeds_quote": proceeds,
            "reserve_quote": reserve, "estimated_net_quote": proceeds-cost-reserve,
            "buy_receive_ns": buy.received_ns, "sell_receive_ns": sell.received_ns,
            "buy_source_ns": buy.exchange_ns, "sell_source_ns": sell.exchange_ns,
            "unknown_minimum_quantity_venues": [b.venue for b, r in ((buy, br), (sell, sr))
                                                   if r["minimum_quantity"] is None]}


def screen_events(source, plan: dict) -> dict:
    """Consume causal books/invalidation events; do not sum repeated estimates."""
    validate_plan(plan)
    latest, counts, summaries, last_time = {}, Counter(), {}, 0
    for scenario in plan["scenarios"]:
        for buy in plan["venues"]:
            for sell in plan["venues"]:
                if buy != sell:
                    summaries[f"{scenario['name']}:{buy}->{sell}"] = {
                        "scenario": scenario["name"], "buy_venue": buy, "sell_venue": sell,
                        "comparisons": 0, "positive_estimates": 0, "rejections": {}, "best": None}
    for event in source:
        venue = event["venue"]
        if venue not in plan["venues"]:
            raise DataError("unregistered stream venue")
        if event["kind"] != "book":
            latest.pop(venue, None)
            counts["invalidations"] += 1
            continue
        current = event["book"]
        if current.venue != venue or current.pair != plan["pair"]:
            raise DataError("book identity differs from recording")
        if current.received_ns < last_time:
            raise DataError("book receive time regressed")
        last_time = current.received_ns
        counts[venue+":books"] += 1
        latest[venue] = current
        for other in latest.values():
            if other.venue == venue:
                continue
            for buy, sell in ((current, other), (other, current)):
                for scenario in plan["scenarios"]:
                    key = f"{scenario['name']}:{buy.venue}->{sell.venue}"
                    row = summaries[key]
                    try:
                        result = quote_pair(buy, sell, plan, scenario, current.received_ns)
                    except DataError as exc:
                        reason = str(exc)
                        row["rejections"][reason] = row["rejections"].get(reason, 0)+1
                        continue
                    row["comparisons"] += 1
                    row["positive_estimates"] += result["estimated_net_quote"] > 0
                    if row["best"] is None or result["estimated_net_quote"] > row["best"]["estimated_net_quote"]:
                        row["best"] = result
    return {"counts": dict(counts), "scenarios_and_routes": list(summaries.values()),
            "portfolio_return_available": False, "estimates_are_independent_trials": False,
            "capacity_is_reused_per_comparison": True, "execution_ready": False}


def run_spread_screen(plan_path: Path, catalog: Path, recording: Path, output: Path) -> dict:
    plan_bytes = plan_path.read_bytes()
    plan = json.loads(plan_bytes)
    validate_plan(plan)
    verify_catalog(catalog, plan)
    audit = audit_stream(recording)
    rows = events(recording)
    try:
        header = next(rows)
    except StopIteration as exc:
        raise DataError("empty stream recording") from exc
    finally:
        rows.close()
    for key, expected in (("pair", plan["pair"]), ("venues", plan["venues"]),
                           ("duration_seconds", plan["capture_seconds"]), ("max_mb", plan["capture_max_mb"])):
        if header.get(key) != expected:
            raise DataError("recording header differs from registered observation")
    output.mkdir(parents=True, exist_ok=False)
    (output/"plan.json").write_bytes(plan_bytes)
    report = {"schema": 1, "experiment": plan["experiment"], "status": "incomplete",
              "plan_sha256": hashlib.sha256(plan_bytes).hexdigest(),
              "source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in sorted(Path(__file__).parent.glob("*.py"))},
              "catalog_sha256": plan["catalog_sha256"], "recording_audit": audit,
              "recording_start_ns": header["observed_ns"], "recording_run_id": header["run_id"],
              "capture_version": header.get("version"),
              "account_fees_verified": False, "inventory_verified": False,
              "execution_ready": False}
    try:
        report.update(screen_events(replay_books(recording), plan))
        report["status"] = ("completed_conditional_diagnostic" if audit["ended_cleanly"]
                            and not audit["venues_without_books"] else "incomplete_observation")
    except Exception as exc:
        report["error_type"] = type(exc).__name__
        raise
    finally:
        (output/"report.json").write_text(dumps(report), encoding="utf-8")
    return report
