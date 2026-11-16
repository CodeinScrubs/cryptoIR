"""Shared-cash spot rotation research; conditional hourly costs, no real orders."""

import hashlib
import json
import re
import time
from collections import deque
from dataclasses import asdict, replace
from decimal import Decimal
from pathlib import Path

from .backtest import Assumptions, floor_quantity, screen
from .domain import DataError, number
from .history import HOUR, audit_history
from .research import epoch, session_boundary
from .streams import dumps

ZERO = Decimal(0)
BENCHMARKS = ("cash", "btc_hold_matched", "btc_hold_full")


class RelativeMomentum:
    """Only completed aligned bars enter this selector; no dataset access."""

    def __init__(self, assets, lookback_hours):
        if type(lookback_hours) is not int or not 1 <= lookback_hours <= 24*365:
            raise DataError("invalid momentum lookback")
        self.assets = tuple(sorted(assets))
        if not self.assets or len(set(self.assets)) != len(self.assets):
            raise DataError("unique nonempty assets required")
        self.history = {a: deque(maxlen=lookback_hours+1) for a in self.assets}
        self.lookback, self.previous = lookback_hours, None

    def observe(self, bars):
        if set(bars) != set(self.assets) or len({b.time for b in bars.values()}) != 1:
            raise DataError("all asset bars must be aligned")
        now = next(iter(bars.values())).time
        if self.previous is not None and now != self.previous+HOUR:
            raise DataError("rotation requires contiguous completed hours")
        for asset in self.assets:
            self.history[asset].append(bars[asset].close)
        self.previous = now

    def past_returns(self):
        if any(len(h) != self.lookback+1 for h in self.history.values()):
            raise DataError("incomplete causal momentum warmup")
        return {a: h[-1]/h[0]-1 for a, h in self.history.items()}

    def choose(self):
        returns = self.past_returns()
        best = min(self.assets, key=lambda a: (-returns[a], a))
        return (best if returns[best] > 0 else None), returns


class RelativeReversal(RelativeMomentum):
    """Long-only relative loser selection; no shorting of the winners."""

    def choose(self):
        returns = self.past_returns()
        worst = min(self.assets, key=lambda a: (returns[a], a))
        return (worst if returns[worst] < 0 else None), returns


SELECTORS = {"relative_momentum": RelativeMomentum, "relative_reversal": RelativeReversal}


def rotation_screen(series, assumptions, start, end, *, lookback_hours, rebalance_hours,
                    strategy_name="relative_momentum"):
    if strategy_name not in SELECTORS:
        raise DataError("unsupported rotation selector")
    if type(start) is not int or type(end) is not int or not 0 < start < end:
        raise DataError("invalid screening interval")
    if type(rebalance_hours) is not int or not 1 <= rebalance_hours <= 24*365:
        raise DataError("invalid rebalance schedule")
    assets = tuple(sorted(series))
    if not assets or set(assumptions) != set(assets):
        raise DataError("exact asset assumptions required")
    cfg = assumptions[assets[0]]
    common = ("capital", "allocation", "fee", "adverse_bps", "prior_volume_fraction", "delay_hours", "buy_fee_mode")
    if any(getattr(c, k) != getattr(cfg, k) for c in assumptions.values() for k in common):
        raise DataError("one shared capital and cost policy required")
    if cfg.buy_fee_mode != "received_base":
        raise DataError("rotation requires explicit received-base buy fees")
    times = [b.time for b in series[assets[0]]]
    if not times or any(b-a != HOUR for a, b in zip(times, times[1:])):
        raise DataError("rotation requires contiguous hours")
    if any([b.time for b in series[a]] != times for a in assets):
        raise DataError("asset timelines differ; no intersection or forward fill")
    if start not in times or end-HOUR not in times:
        raise DataError("evaluation boundaries exceed aligned data")
    first, last = times.index(start), times.index(end-HOUR)
    selector = SELECTORS[strategy_name](assets, lookback_hours)
    if first < lookback_hours or last-first < 1:
        raise DataError("insufficient warmup or evaluation hours")
    for i in range(first-lookback_hours, first):
        selector.observe({a: series[a][i] for a in assets})
    cash, realized, fees, turnover = cfg.capital, ZERO, ZERO, ZERO
    quantities = {a: ZERO for a in assets}
    basis, base_fees = quantities.copy(), quantities.copy()
    peak, drawdown, impact = cfg.capital, ZERO, ZERO
    buy_factor, sell_factor = 1+cfg.adverse_bps/10000, 1-cfg.adverse_bps/10000
    pending, target = None, None
    trades, equity, decisions = [], [], []
    rejected, cancelled, exits = 0, 0, 0

    def saleable(a, price):
        rounded = floor_quantity(quantities[a], assumptions[a].quantity_step)
        return rounded if rounded*price*sell_factor >= assumptions[a].minimum_notional else ZERO

    for i in range(first, last+1):
        bars = {a: series[a][i] for a in assets}
        if pending and i >= pending["due_index"]:
            intent, pending = pending, None
            asset, buy = intent["asset"], intent["side"] == "buy"
            bar, rule = bars[asset], assumptions[asset]
            price = bar.open*(buy_factor if buy else sell_factor)
            wanted = intent["quantity"]
            if buy:
                gross_holdings = sum(quantities[a]*bars[a].open for a in assets)
                exposure_room = max(ZERO, (cash+gross_holdings)*cfg.allocation-gross_holdings)
                wanted = min(wanted, cash/price, exposure_room/price)
                if any(saleable(a, bars[a].open) for a in assets if a != asset):
                    wanted = ZERO
            else:
                wanted = min(wanted, quantities[asset])
            capacity = series[asset][i-1].volume*cfg.prior_volume_fraction
            fill = floor_quantity(min(wanted, capacity), rule.quantity_step)
            notional = fill*price
            if fill <= 0 or notional < rule.minimum_notional:
                rejected += 1
            else:
                fee_quote_equivalent = notional*cfg.fee
                fee_base = fill*cfg.fee if buy else ZERO
                if buy:
                    cash -= notional
                    quantities[asset] += fill-fee_base
                    basis[asset] += notional
                else:
                    allocated_basis = basis[asset]*fill/quantities[asset]
                    cash += notional-fee_quote_equivalent
                    quantities[asset] -= fill
                    basis[asset] -= allocated_basis
                    realized += notional-fee_quote_equivalent-allocated_basis
                    exits += not bool(saleable(asset, bar.open))
                fees += fee_quote_equivalent
                base_fees[asset] += fee_base
                turnover += notional
                impact += fill*abs(price-bar.open)
                trades.append({"asset": asset, "side": intent["side"], "decision_time": intent["decision_time"],
                    "execution_time": bar.time, "requested": intent["quantity"], "quantity": fill,
                    "price": price, "fee_base": fee_base, "fee_quote": ZERO if buy else fee_quote_equivalent,
                    "fee_quote_equivalent": fee_quote_equivalent, "unused_quantity": intent["quantity"]-fill,
                    "cash_after": cash, "quantities_after": quantities.copy()})
            if cash < 0 or min(quantities.values()) < 0 or min(basis.values()) < 0:
                raise AssertionError("shared-cash spot accounting invariant")
        liquidation = cash+sum(saleable(a, bars[a].close)*bars[a].close*sell_factor*(1-cfg.fee) for a in assets)
        peak = max(peak, liquidation)
        drawdown = max(drawdown, (peak-liquidation)/peak)
        equity.append({"time": times[i]+HOUR, "equity": liquidation, "cash": cash, "quantities": quantities.copy()})
        selector.observe(bars)
        if (i-first) % rebalance_hours == 0:
            target, returns = selector.choose()
            decisions.append({"time": times[i]+HOUR, "target": target, "past_returns": returns})
            if pending and ((pending["side"] == "buy" and pending["asset"] != target)
                            or (pending["side"] == "sell" and pending["asset"] == target)):
                pending = None
                cancelled += 1
        if pending is None:
            to_exit = [a for a in assets if a != target and saleable(a, bars[a].close)]
            side, asset, wanted = None, None, ZERO
            if to_exit:
                side, asset = "sell", to_exit[0]
                wanted = floor_quantity(quantities[asset], assumptions[asset].quantity_step)
            elif target and not saleable(target, bars[target].close):
                side, asset = "buy", target
                gross_holdings = sum(quantities[a]*bars[a].close for a in assets)
                room = max(ZERO, liquidation*cfg.allocation-gross_holdings)
                wanted = floor_quantity(min(cash, room)/(bars[asset].close*buy_factor), assumptions[asset].quantity_step)
            if wanted > 0:
                pending = {"asset": asset, "side": side, "quantity": wanted,
                           "decision_time": times[i]+HOUR, "due_index": i+1+cfg.delay_hours}
    unrealized = liquidation-cash-sum(basis.values())
    if abs(liquidation-cfg.capital-realized-unrealized) > Decimal("1e-20")*cfg.capital:
        raise AssertionError("shared-cash P&L identity")
    return {"kind": "hourly_rotation_cost_screen", "strategy": strategy_name, "execution_ready": False,
        "start": start, "end_exclusive": end, "bars": last-first+1, "assets": list(assets),
        "assumptions_by_asset": {a: asdict(c) for a, c in assumptions.items()},
        "lookback_hours": lookback_hours, "rebalance_hours": rebalance_hours,
        "final_liquidation_mark": liquidation, "net_return": liquidation/cfg.capital-1, "max_drawdown": drawdown,
        "cash": cash, "base_quantities": quantities, "cost_basis": basis, "realized_pnl": realized,
        "unrealized_pnl_with_exit_costs": unrealized, "paid_fees_quote_equivalent": fees,
        "paid_fees_base": base_fees, "modeled_adverse_price_cost": impact, "turnover_multiple": turnover/cfg.capital,
        "rejected_orders": rejected, "cancelled_intents": cancelled, "pending_at_end": pending is not None,
        "trade_count": len(trades), "completed_sells": exits,
        "residual_dust_quantities": {a: quantities[a]-saleable(a, bars[a].close) for a in assets},
        "decisions": decisions, "trades": trades, "equity": equity}


def validate_plan(plan):
    if (plan.get("schema") != 1 or plan.get("venue") != "nobitex"
            or plan.get("assets") != ["BTC", "ETH", "SOL"] or plan.get("benchmarks") != list(BENCHMARKS)):
        raise DataError("unsupported rotation universe or benchmarks")
    strategy = plan["strategy"]
    name = strategy["name"]
    if name not in SELECTORS or strategy["tie_break"] != "ascending_asset":
        raise DataError("unsupported rotation signal")
    required = "positive_absolute_return_required" if name == "relative_momentum" else "negative_absolute_return_required"
    opposite = "negative_absolute_return_required" if name == "relative_momentum" else "positive_absolute_return_required"
    if strategy.get(required) is not True or opposite in strategy:
        raise DataError("rotation signal must declare one matching absolute-return condition")
    for key in ("lookback_hours", "rebalance_hours"):
        if type(strategy[key]) is not int or not 1 <= strategy[key] <= 24*365:
            raise DataError("invalid signal horizon")
    if name == "relative_reversal" and (strategy["lookback_hours"], strategy["rebalance_hours"]) != (24, 24):
        raise DataError("registered reversal requires fixed daily lookback and schedule")
    if set(plan["market_rules"]) != set(plan["assets"]):
        raise DataError("per-asset rules required")
    scenarios = plan["cost_scenarios"]
    if [s["name"] for s in scenarios] != ["base_assumed", "stress_assumed"]:
        raise DataError("base and stress required")
    for s in scenarios:
        for rule in plan["market_rules"].values():
            c = Assumptions(**plan["assumptions"], **rule, fee=s["fee"], adverse_bps=s["adverse_bps"])
            if c.buy_fee_mode != "received_base":
                raise DataError("received-base buy fee required")
    if any(number(scenarios[1][k]) < number(scenarios[0][k]) for k in ("fee", "adverse_bps")):
        raise DataError("stress must not reduce costs")
    splits = plan["splits"]
    if [s["name"] for s in splits] != ["discovery", "validation", "held_out"]:
        raise DataError("sequential stages required")
    previous = epoch(plan["data_start"])
    for s in splits:
        start, end = epoch(s["start"]), epoch(s["end"])
        if not previous <= start < end <= epoch(plan["data_end_exclusive"]):
            raise DataError("invalid or overlapping stages")
        previous = end
    pins = plan["dataset_pins"]
    if not isinstance(pins, list) or len(pins) != 3:
        raise DataError("three dataset pins required")
    if {(p["venue"], p["pair"]) for p in pins} != {("nobitex", a+"/USDT") for a in plan["assets"]}:
        raise DataError("dataset pins differ from universe")
    if any(not isinstance(p[k], str) or not re.fullmatch(r"[a-f0-9]{64}", p[k])
           for p in pins for k in ("manifest_sha256", "normalized_sha256")):
        raise DataError("invalid dataset hash")
    gate = plan["gate"]
    if (type(gate["minimum_completed_sells"]) is not int or gate["minimum_completed_sells"] < 1
            or not 0 < number(gate["maximum_drawdown"]) < 1
            or gate["positive_return_in_every_cost_scenario"] is not True
            or gate["beat_both_btc_benchmarks_in_base"] is not True):
        raise DataError("invalid rotation gate")


def gate_failures(results, plan):
    failures = []
    name = plan["strategy"]["name"]
    for scenario in plan["cost_scenarios"]:
        s = scenario["name"]
        r = results[(name, s)]
        for failed, label in ((r["net_return"] <= 0, "nonpositive_return"),
                (r["max_drawdown"] > number(plan["gate"]["maximum_drawdown"]), "drawdown"),
                (r["completed_sells"] < plan["gate"]["minimum_completed_sells"], "insufficient_exits")):
            if failed:
                failures.append(s+":"+label)
    candidate = results[(name, "base_assumed")]["net_return"]
    for name in BENCHMARKS[1:]:
        if candidate <= results[(name, "base_assumed")]["net_return"]:
            failures.append("base_assumed:did_not_beat_"+name)
    return failures


def run_experiment(plan_path, datasets, output):
    plan_bytes = plan_path.read_bytes()
    plan = json.loads(plan_bytes)
    validate_plan(plan)
    output.mkdir(parents=True, exist_ok=False)
    (output/"plan.json").write_bytes(plan_bytes)
    report = {"schema": 1, "experiment": plan["experiment"], "status": "incomplete", "stages": [],
        "plan_sha256": hashlib.sha256(plan_bytes).hexdigest(), "datasets": [], "dataset_pins_verified": False,
        "source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(__file__).parent.glob("*.py"))},
        "held_out_inspected": False, "execution_ready": False, "irt_performance_available": False}
    def journal(event):
        with (output/"trials.jsonl").open("a", encoding="utf-8", newline="\n") as f:
            f.write(dumps({"recorded_ns": time.time_ns(), **event})+"\n")
    journal({"kind": "begin", "plan_sha256": report["plan_sha256"]})
    try:
        series, phases = {}, set()
        if len(datasets) != len(plan["assets"]):
            raise DataError("supply each registered dataset once")
        for dataset in datasets:
            audit, bars = audit_history(dataset)
            pair = audit["pair"]
            asset = pair.removesuffix("/USDT")
            if (audit["venue"] != plan["venue"] or pair != asset+"/USDT"
                    or asset not in plan["assets"] or asset in series):
                raise DataError("duplicate or unregistered dataset")
            pin = next(p for p in plan["dataset_pins"] if p["pair"] == pair)
            if any(audit[k] != pin[k] for k in ("manifest_sha256", "normalized_sha256")):
                raise DataError("dataset differs from registered hashes")
            if not audit["reconciled"] or not audit["volume_quality"]["usable_for_volume_screen"]:
                raise DataError("daily or volume acceptance failed")
            manifest = json.loads((dataset/"manifest.json").read_bytes())
            if (manifest["start"] != epoch(plan["data_start"])
                    or manifest["end_exclusive"] != epoch(plan["data_end_exclusive"])):
                raise DataError("dataset request window differs from plan")
            (output/(asset.lower()+"-data-audit.json")).write_text(dumps(audit), encoding="utf-8")
            report["datasets"].append({k: audit[k] for k in ("venue", "pair", "manifest_sha256", "normalized_sha256", "reconciled", "volume_quality")})
            phases.add(audit["day_phase_utc_seconds"])
            series[asset] = bars
        if len(phases) != 1 or any([b.time for b in s] != [b.time for b in series["BTC"]] for s in series.values()):
            raise DataError("dataset sessions or timelines differ")
        report["dataset_pins_verified"] = True
        phase = phases.pop()
        for stage in plan["splits"]:
            start, end = (session_boundary(stage[k], phase) for k in ("start", "end"))
            # Restrict every model input to the opened stage; future prices cannot leak.
            scoped = {a: [b for b in s if b.time < end] for a, s in series.items()}
            if stage["name"] == "held_out":
                report["held_out_inspected"] = True
                journal({"kind": "held_out_opened"})
            stage_report = {"stage": stage["name"], "start": start, "end_exclusive": end, "trials": []}
            results = {}
            for scenario in plan["cost_scenarios"]:
                cfgs = {a: Assumptions(**plan["assumptions"], **plan["market_rules"][a], fee=scenario["fee"],
                                      adverse_bps=scenario["adverse_bps"]) for a in plan["assets"]}
                for name in (*BENCHMARKS, plan["strategy"]["name"]):
                    identity = {"stage": stage["name"], "strategy": name, "scenario": scenario["name"]}
                    journal({"kind": "trial_started", **identity})
                    if name == plan["strategy"]["name"]:
                        result = rotation_screen(scoped, cfgs, start, end, strategy_name=name,
                            **{k: plan["strategy"][k] for k in ("lookback_hours", "rebalance_hours")})
                    else:
                        cfg = replace(cfgs["BTC"], allocation=1) if name == "btc_hold_full" else cfgs["BTC"]
                        result = screen(scoped["BTC"], "cash" if name == "cash" else "hold", cfg, start, end)
                    content = dumps(result).encode()
                    filename = f"{stage['name']}-{name}-{scenario['name']}.json"
                    (output/filename).write_bytes(content)
                    summary = {k: v for k, v in result.items() if k not in ("equity", "trades", "decisions")}
                    summary.update(strategy=name, scenario=scenario["name"], artifact=filename,
                                   sha256=hashlib.sha256(content).hexdigest())
                    results[(name, scenario["name"])] = summary
                    stage_report["trials"].append(summary)
                    journal({"kind": "trial_finished", **identity, "sha256": summary["sha256"]})
            failures = gate_failures(results, plan)
            stage_report["gate_failures"] = failures
            report["stages"].append(stage_report)
            if failures:
                report["status"] = "rejected_at_"+stage["name"]
                break
            report["status"] = "passed_"+stage["name"]+"_screen_only"
        journal({"kind": "end", "status": report["status"]})
    except Exception as exc:
        report["error_type"] = type(exc).__name__
        if isinstance(exc, DataError):
            report["reason"] = str(exc)
        journal({"kind": "failed", "error_type": type(exc).__name__})
        raise
    finally:
        (output/"report.json").write_text(dumps(report), encoding="utf-8")
    return report
