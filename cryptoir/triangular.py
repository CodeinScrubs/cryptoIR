"""Bounded three-leg spot diagnostics. No orders, fills or portfolio-profit sums."""

import gzip
import hashlib
import json
import re
import time
from collections import Counter
from decimal import Decimal, localcontext
from pathlib import Path

from . import __version__
from .catalog import ENDPOINTS, parse_catalog
from .domain import Book, DataError, Level, number
from .feeds import public_response
from .streams import dumps, millis
from .valuation import ALL_BOOKS, ConversionRules, buy_budget, sell_value

ROUTES = ("USDT->asset->IRR->USDT", "USDT->IRR->asset->USDT")
SCENARIOS = ("zero_cost_diagnostic", "assumed_base", "assumed_stress")
MAX_LINE = 16 * 1024 * 1024
CATALOG_UNIVERSE = "catalog_active_unscaled_dual_quote"


def strict_json(value):
    def unique(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise DataError("duplicate JSON field")
            result[key] = item
        return result
    def nonfinite(_):
        raise DataError("nonfinite JSON value")
    return json.loads(value, parse_float=Decimal, object_pairs_hook=unique, parse_constant=nonfinite)


def digest(content):
    return hashlib.sha256(content).hexdigest()


def source_hashes():
    return {p.name: digest(p.read_bytes()) for p in sorted(Path(__file__).parent.glob("*.py"))}


def required_pairs(plan):
    return ["USDT/IRR"] + [asset+"/"+quote for asset in plan["assets"] for quote in ("USDT", "IRR")]


def pair_symbol(pair):
    """Nobitex-only diagnostic mapping, after catalog membership verification."""
    if not isinstance(pair, str) or not re.fullmatch(r"[A-Z0-9][A-Z0-9_]{0,19}/(?:USDT|IRR)", pair):
        raise DataError("unsupported native cycle pair")
    base, quote = pair.split("/")
    if base in ("IRR", "IRT") or base == quote:
        raise DataError("invalid cycle pair identity")
    return base+("IRT" if quote == "IRR" else quote)


def catalog_universe(markets):
    """Determine membership from metadata only, never price or performance."""
    by_symbol = {m["native_symbol"]:m for m in markets}
    eligible, exclusions = [], Counter()
    for market in markets:
        if market["quote"] != "USDT":
            continue
        base = market["base"]
        other = by_symbol.get(base+"IRT")
        if base in ("USDT", "IRT", "IRR") or not re.fullmatch(r"[A-Z0-9][A-Z0-9_]{0,19}", base):
            exclusions["unsupported_identity"] += 1
        elif other is None:
            exclusions["missing_dual_quote"] += 1
        elif any(m["native_scale"] is None for m in (market, other)):
            exclusions["unknown_scale"] += 1
        elif any(number(m["native_scale"]) != 1 for m in (market, other)):
            exclusions["scaled_asset"] += 1
        elif any(m["base_currency_active"] is not True for m in (market, other)):
            exclusions["inactive_currency"] += 1
        else:
            eligible.append(base)
    return {"assets":sorted(eligible), "exclusions":dict(exclusions)}


def snapshot_book(record, pair):
    # Caller verifies the full raw response once per sample, then uses catalog pairs.
    payload = record["payload"][pair_symbol(pair)]
    def levels(name):
        return tuple(Level(row[0], row[1]) for row in payload[name])
    return Book("nobitex", pair, levels("bids"), levels("asks"), record["received_ns"],
                record["duration_ns"], millis(payload["lastUpdate"]))


def validate_plan(plan):
    if (plan.get("schema") != 1 or plan.get("venue") != "nobitex"
            or plan.get("start_asset") != "USDT" or plan.get("routes") != list(ROUTES)):
        raise DataError("unsupported triangular plan or units")
    assets = plan["assets"]
    if (not isinstance(assets, list) or not 1 <= len(assets) <= 500
            or any(not isinstance(a, str) or not re.fullmatch(r"[A-Z0-9][A-Z0-9_]{0,19}", a)
                   or a in ("USDT", "IRR", "IRT") for a in assets)
            or len(assets) != len(set(assets))):
        raise DataError("explicit unscaled triangular asset universe required")
    universe = plan.get("universe", "explicit_legacy")
    if universe not in ("explicit_legacy", CATALOG_UNIVERSE):
        raise DataError("unknown universe selection rule")
    if universe == "explicit_legacy" and any(a not in ("BTC", "ETH", "SOL") for a in assets):
        raise DataError("broader universe must be reconstructed from catalog")
    if "replay_recording" in plan:
        pin = plan["replay_recording"]
        if (not isinstance(pin, dict) or set(pin) != {"sha256", "capture_plan_sha256", "evaluation_type"}
                or pin["evaluation_type"] != "exploratory_existing_recording"
                or any(not isinstance(pin[k], str) or not re.fullmatch(r"[a-f0-9]{64}", pin[k])
                       for k in ("sha256", "capture_plan_sha256"))):
            raise DataError("explicit exploratory recording pins required")
    for key, low, high in (("samples", 2, 120), ("interval_seconds", 30, 3600),
                           ("max_logical_mb", 1, 512), ("max_age_ms", 1, 60000)):
        if type(plan[key]) is not int or not low <= plan[key] <= high:
            raise DataError("invalid triangular capture bounds")
    if not re.fullmatch(r"[a-f0-9]{64}", plan["catalog_sha256"]):
        raise DataError("registered catalog hash required")
    if number(plan["start_budget"]) <= 0 or not 0 < number(plan["depth_participation"]) <= 1:
        raise DataError("positive finite budget and depth participation required")
    if set(plan["market_rules"]) != set(required_pairs(plan)):
        raise DataError("rules must cover exactly the registered pairs")
    for rule in plan["market_rules"].values():
        if set(rule) != {"quantity_step", "price_tick", "minimum_notional"} or any(number(v) <= 0 for v in rule.values()):
            raise DataError("explicit positive observed order rules required")
    scenarios = plan["scenarios"]
    if [s["name"] for s in scenarios] != list(SCENARIOS):
        raise DataError("require zero-cost, base and stress scenarios")
    for scenario in scenarios:
        reserve = number(scenario["reserve_bps"])/10000
        for key in ("usdt_fee", "irr_fee"):
            fee = number(scenario[key])
            if not 0 <= reserve < 1 or not 0 <= fee < 1 or fee+reserve >= 1:
                raise DataError("invalid received-asset costs")
    for key in ("usdt_fee", "irr_fee", "reserve_bps"):
        if number(scenarios[0][key]) != 0 or number(scenarios[2][key]) < number(scenarios[1][key]):
            raise DataError("zero costs or stress ordering invalid")


def verify_response(record, url):
    if (record.get("schema") != 1 or record.get("kind") != "snapshot"
            or record.get("url") != url or record.get("status_code") != 200):
        raise DataError("invalid public source provenance")
    start, received, duration = (record[k] for k in ("started_ns", "received_ns", "duration_ns"))
    if (any(type(v) is not int for v in (start, received, duration))
            or not 0 < start <= received or duration < 0 or abs(received-start-duration) > 1_000_000_000):
        raise DataError("invalid public source timing")
    body = record["raw_body"]
    if digest(body.encode()) != record["body_sha256"] or dumps(strict_json(body)) != dumps(record["payload"]):
        raise DataError("public source body or payload mismatch")
    if not isinstance(record["payload"], dict) or record["payload"].get("status") != "ok":
        raise DataError("public venue rejected response")


def verify_catalog(path, plan):
    content = path.read_bytes()
    if digest(content) != plan["catalog_sha256"]:
        raise DataError("catalog differs from registered hash")
    record = strict_json(content)
    verify_response(record, ENDPOINTS["nobitex"])
    rebuilt = parse_catalog("nobitex", record["payload"])
    if dumps(rebuilt) != dumps(record["markets"]):
        raise DataError("catalog normalization mismatch")
    markets = {m["native_symbol"]:m for m in rebuilt}
    if plan.get("universe") == CATALOG_UNIVERSE and plan["assets"] != catalog_universe(rebuilt)["assets"]:
        raise DataError("registered universe differs from deterministic catalog membership")
    for pair in required_pairs(plan):
        market = markets[pair_symbol(pair)]
        if number(market["native_scale"]) != 1 or market["base_currency_active"] is not True:
            raise DataError("scaled or inactive asset")
        quote = "rls" if pair.endswith("/IRR") else "usdt"
        expected = {"quantity_step":market["quantity_step"], "price_tick":market["price_tick"],
                    "minimum_notional":record["payload"]["nobitex"]["minOrders"][quote]}
        if any(number(plan["market_rules"][pair][k]) != number(v) for k, v in expected.items()):
            raise DataError("plan rules differ from observed catalog")


def capture(plan_path, catalog_path, output):
    plan_bytes = plan_path.read_bytes()
    plan = strict_json(plan_bytes)
    validate_plan(plan)
    if "replay_recording" in plan:
        raise DataError("exploratory replay plan cannot capture new evidence")
    verify_catalog(catalog_path, plan)
    output.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    count, size, reason = 0, 0, "sample_limit"
    # Exclusive compressed recording; no credentials or performance calculation.
    with gzip.open(output, "xt", encoding="utf-8", newline="\n") as stream:
        def write(value):
            nonlocal size
            line = dumps(value)+"\n"
            stream.write(line)
            stream.flush()
            size += len(line.encode())
        write({"schema":1, "kind":"triangular_capture_start", "plan_sha256":digest(plan_bytes),
               "observed_ns":time.time_ns(), "version":__version__, "source_sha256":source_hashes()})
        for index in range(plan["samples"]):
            if index:
                time.sleep(plan["interval_seconds"])
            record = public_response(ALL_BOOKS)
            row = {"kind":"sample", "index":index, "record":record}
            if size+len((dumps(row)+"\n").encode()) > plan["max_logical_mb"]*1024*1024:
                reason = "byte_limit"
                break
            write(row)
            count += 1
            print(dumps({"kind":"triangular_capture_progress", "samples":count, "source_status":record["kind"]}), flush=True)
            if record["kind"] != "snapshot":
                reason = "source_error"
                break
        footer = {"kind":"triangular_capture_end", "samples":count, "stop_reason":reason,
                  "elapsed_seconds":time.monotonic()-started, "observed_ns":time.time_ns()}
        write(footer)
    return footer


def quote_cycle(books, asset, route, plan, scenario, now_ns):
    if asset not in plan["assets"] or route not in ROUTES or scenario not in plan["scenarios"]:
        raise DataError("unregistered cycle")
    pairs = (asset+"/USDT", asset+"/IRR", "USDT/IRR")
    for pair in pairs:
        if pair not in books:
            raise DataError("missing_market")
        book = books[pair]
        if book.venue != "nobitex" or book.pair != pair or book.exchange_ns is None:
            raise DataError("invalid_book_identity_or_unknown_time")
        book.validate_age(now_ns, plan["max_age_ms"]*1_000_000)
        tick = number(plan["market_rules"][pair]["price_tick"])
        if any(level.price % tick for level in book.bids+book.asks):
            raise DataError("book_price_tick_mismatch")
    legs = ((pairs[0], "buy"), (pairs[1], "sell"), (pairs[2], "buy")) if route == ROUTES[0] else (
           (pairs[2], "sell"), (pairs[1], "buy"), (pairs[0], "sell"))
    with localcontext() as context:
        context.prec = 60
        budget = number(plan["start_budget"])
        holdings = {"USDT":budget, "IRR":Decimal(0), asset:Decimal(0)}
        details = []
        reserve_rate = number(scenario["reserve_bps"])/10000
        for pair, side in legs:
            base, quote = pair.split("/")
            incoming, outgoing = (quote, base) if side == "buy" else (base, quote)
            observed = plan["market_rules"][pair]
            rules = ConversionRules(observed["quantity_step"], observed["minimum_notional"],
                scenario["irr_fee" if quote == "IRR" else "usdt_fee"], plan["depth_participation"])
            available = holdings[incoming]
            if side == "buy":
                result = buy_budget(books[pair], available, rules)
                spent, gross, fee = result["spent_quote"], result["gross_base"], result["fee_base"]
            else:
                result = sell_value(books[pair], available, rules)
                spent, fee = result["sellable"], result["fee_quote"]
                gross = result["net_quote"]+fee
                if spent <= 0:
                    raise DataError("sell_below_minimum_after_rounding")
            reserve = gross*reserve_rate
            holdings[incoming] -= spent
            holdings[outgoing] += gross-fee-reserve
            if min(holdings.values()) < 0:
                raise AssertionError("cycle exceeded available holdings")
            details.append({"pair":pair, "side":side, "input_asset":incoming, "output_asset":outgoing,
                            "available_input":available, "spent_input":spent, "unspent_input":available-spent,
                            "gross_received":gross, "fee_received_asset":fee, "reserve_received_asset":reserve,
                            "net_received":gross-fee-reserve})
        return {"asset":asset, "route":route, "start_usdt":budget, "final_usdt":holdings["USDT"],
                "estimated_net_usdt":holdings["USDT"]-budget,
                "residuals_zero_usdt_valuation":{k:v for k,v in holdings.items() if k != "USDT"},
                "legs":details, "observed_ns":now_ns, "execution_ready":False}


def run_screen(plan_path, catalog_path, recording, output):
    plan_bytes = plan_path.read_bytes()
    plan = strict_json(plan_bytes)
    validate_plan(plan)
    verify_catalog(catalog_path, plan)
    output.mkdir(parents=True, exist_ok=False)
    (output/"plan.json").write_bytes(plan_bytes)
    report = {"schema":1, "experiment":plan["experiment"], "status":"incomplete_observation",
              "plan_sha256":digest(plan_bytes), "catalog_sha256":plan["catalog_sha256"],
              "source_sha256":source_hashes(), "execution_ready":False,
              "portfolio_return_available":False, "estimates_are_independent_trials":False,
              "account_fees_verified":False, "capacity_reused_per_comparison":True}
    report["evaluation_type"] = plan.get("replay_recording", {}).get("evaluation_type", "registered_capture")
    counts, summaries, footer = Counter(), {}, None
    for scenario in plan["scenarios"]:
        for asset in plan["assets"]:
            for route in ROUTES:
                summaries[(scenario["name"], asset, route)] = {"scenario":scenario["name"], "asset":asset,
                    "route":route, "comparisons":0, "positive_estimates":0, "rejections":{}, "best":None}
    try:
        if recording.stat().st_size > (plan["max_logical_mb"]+1)*1024*1024:
            raise DataError("compressed recording exceeds bound")
        report["recording_sha256"] = digest(recording.read_bytes())
        pin = plan.get("replay_recording")
        if pin and report["recording_sha256"] != pin["sha256"]:
            raise DataError("recording differs from exploratory source pin")
        capture_plan_hash = pin["capture_plan_sha256"] if pin else report["plan_sha256"]
        total, previous_received = 0, 0
        with gzip.open(recording, "rt", encoding="utf-8") as stream:
            header = strict_json(stream.readline(MAX_LINE+1))
            if (header.get("schema") != 1 or header.get("kind") != "triangular_capture_start"
                    or header.get("plan_sha256") != capture_plan_hash
                    or type(header.get("observed_ns")) is not int or header["observed_ns"] <= 0):
                raise DataError("recording header differs from plan")
            report["capture"] = header
            while line := stream.readline(MAX_LINE+1):
                total += len(line.encode())
                if len(line) > MAX_LINE or total > (plan["max_logical_mb"]+1)*1024*1024:
                    raise DataError("logical recording exceeds bound")
                row = strict_json(line)
                if footer is not None:
                    raise DataError("records after capture end")
                if row.get("kind") == "triangular_capture_end":
                    footer = row
                    if row["samples"] != counts["samples"]:
                        raise DataError("capture sample count mismatch")
                    continue
                if row.get("kind") != "sample" or row["index"] != counts["samples"] or counts["samples"] >= plan["samples"]:
                    raise DataError("invalid capture sample order")
                record = row["record"]
                counts["samples"] += 1
                if record["started_ns"] < header["observed_ns"]:
                    raise DataError("sample precedes capture registration header")
                if previous_received and record["started_ns"]-previous_received < plan["interval_seconds"]*1_000_000_000:
                    raise DataError("capture spacing differs from plan")
                previous_received = record["received_ns"]
                if record.get("kind") != "snapshot":
                    counts["source_errors"] += 1
                    continue
                verify_response(record, ALL_BOOKS)
                books = {}
                for pair in required_pairs(plan):
                    try:
                        books[pair] = snapshot_book(record, pair)
                    except (ValueError, TypeError, KeyError, IndexError):
                        counts["invalid_or_missing_books"] += 1
                for scenario in plan["scenarios"]:
                    for asset in plan["assets"]:
                        for route in ROUTES:
                            summary = summaries[(scenario["name"], asset, route)]
                            try:
                                result = quote_cycle(books, asset, route, plan, scenario, record["received_ns"])
                            except DataError as exc:
                                reason = str(exc)
                                summary["rejections"][reason] = summary["rejections"].get(reason, 0)+1
                                continue
                            summary["comparisons"] += 1
                            summary["positive_estimates"] += result["estimated_net_usdt"] > 0
                            if summary["best"] is None or result["estimated_net_usdt"] > summary["best"]["estimated_net_usdt"]:
                                summary["best"] = result
        if (footer and footer["stop_reason"] == "sample_limit" and counts["samples"] == plan["samples"]
                and not counts["source_errors"] and all(s["comparisons"] for s in summaries.values())):
            report["status"] = "completed_conditional_diagnostic"
    except Exception as exc:
        report["error_type"] = type(exc).__name__
        if isinstance(exc, DataError):
            report["reason"] = str(exc)
        raise
    finally:
        report["capture_complete"] = bool("error_type" not in report and footer and footer["stop_reason"] == "sample_limit"
            and counts["samples"] == plan["samples"] and not counts["source_errors"])
        report["universe_coverage_complete"] = "error_type" not in report and all(s["comparisons"] for s in summaries.values())
        report.update(counts=dict(counts), capture_end=footer, scenarios_and_routes=list(summaries.values()))
        (output/"report.json").write_text(dumps(report), encoding="utf-8")
    return report
