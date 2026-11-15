"""Frozen small hypothesis sets, sequential gates, and append-only trial records."""

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from .backtest import Assumptions, STRATEGIES, screen
from .domain import DataError, number
from .history import DAY, audit_history, volume_quality
from .streams import dumps


def epoch(date: str) -> int:
    return int(datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def session_boundary(date: str, phase: int) -> int:
    """Snap backwards to a venue day open. Adjacent splits stay contiguous."""
    value = epoch(date)
    return (value-phase)//DAY*DAY+phase


def validate_plan(plan: dict):
    if plan.get("schema") != 1 or plan.get("benchmarks") != ["cash", "hold"]:
        raise DataError("unsupported research plan")
    candidates = plan["candidates"]
    if (not candidates or len(candidates) != len(set(candidates))
            or any(s not in STRATEGIES or s in ("cash", "hold") for s in candidates)):
        raise DataError("invalid bounded candidate list")
    splits = plan["splits"]
    if [s["name"] for s in splits] != ["discovery", "validation", "held_out"]:
        raise DataError("require discovery, validation and held-out splits")
    previous = epoch(plan["data_start"])
    for split in splits:
        start, end = epoch(split["start"]), epoch(split["end"])
        if start < previous or start >= end or end > epoch(plan["data_end_exclusive"]):
            raise DataError("invalid or overlapping research splits")
        previous = end
    scenarios = plan["cost_scenarios"]
    if [s["name"] for s in scenarios] != ["base_assumed", "stress_assumed"]:
        raise DataError("require explicit base and stress costs")
    if any(number(scenarios[1][key]) < number(scenarios[0][key]) for key in ("fee", "adverse_bps")):
        raise DataError("stress costs must not improve on base costs")
    for scenario in scenarios:
        Assumptions(**plan["assumptions"], fee=scenario["fee"], adverse_bps=scenario["adverse_bps"])
    gate = plan["gate"]
    if (type(gate["minimum_completed_sells"]) is not int or gate["minimum_completed_sells"] < 1
            or not 0 < number(gate["maximum_drawdown"]) < 1
            or gate.get("positive_return_in_every_cost_scenario") is not True
            or gate.get("beat_equal_allocation_hold_in_base") is not True):
        raise DataError("invalid research advancement gate")


def passes_gate(candidate: str, results: dict, plan: dict) -> bool:
    gate = plan["gate"]
    for scenario in plan["cost_scenarios"]:
        report = results[(candidate, scenario["name"])]
        if (report["net_return"] <= 0 or report["max_drawdown"] > number(gate["maximum_drawdown"])
                or report["completed_sells"] < gate["minimum_completed_sells"]):
            return False
    return results[(candidate, "base_assumed")]["net_return"] > results[("hold", "base_assumed")]["net_return"]


def run_experiment(plan_path: Path, dataset: Path, output: Path) -> dict:
    plan_bytes = plan_path.read_bytes()
    plan = json.loads(plan_bytes)
    validate_plan(plan)
    output.mkdir(parents=True, exist_ok=False)
    report = {"schema": 1, "experiment": plan["experiment"], "status": "incomplete",
              "created_ns": time.time_ns(), "plan_sha256": hashlib.sha256(plan_bytes).hexdigest(),
              "source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in sorted(Path(__file__).parent.glob("*.py"))},
              "stages": [], "selected": None, "held_out_inspected": False,
              "execution_ready": False, "irt_performance_available": False,
              "personal_capital_conversion_verified": False}
    (output / "plan.json").write_bytes(plan_bytes)
    journal_path = output / "trials.jsonl"

    def journal(event: dict):
        with journal_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(dumps({"recorded_ns": time.time_ns(), **event}) + "\n")
            stream.flush()

    journal({"kind": "begin", "plan_sha256": report["plan_sha256"]})
    try:
        audit, bars = audit_history(dataset)
        report["dataset"] = {key: audit[key] for key in ("venue", "pair", "manifest_sha256", "normalized_sha256", "reconciled")}
        report["dataset"]["volume_quality"] = volume_quality(bars)
        if not audit["reconciled"]:
            raise DataError("research blocked: daily reconciliation failed")
        if not report["dataset"]["volume_quality"]["usable_for_volume_screen"]:
            raise DataError("research blocked: reported volume is insufficient or inconsistent")
        if audit["venue"] not in plan["venues"] or audit["pair"] not in plan["pairs"]:
            raise DataError("dataset is outside preregistered universe")
        source_manifest = json.loads((dataset / "manifest.json").read_bytes())
        if (source_manifest["start"] != epoch(plan["data_start"])
                or source_manifest["end_exclusive"] != epoch(plan["data_end_exclusive"])):
            raise DataError("dataset request window differs from preregistered window")
        selected = None
        for stage in plan["splits"]:
            start = session_boundary(stage["start"], audit["day_phase_utc_seconds"])
            end = session_boundary(stage["end"], audit["day_phase_utc_seconds"])
            names = plan["candidates"] if stage["name"] == "discovery" else [selected]
            if stage["name"] == "held_out":
                report["held_out_inspected"] = True
                journal({"kind": "held_out_opened", "candidate": selected})
            results, stage_summary = {}, {"stage": stage["name"], "start": start, "end_exclusive": end, "trials": []}
            for name in plan["benchmarks"] + names:
                for scenario in plan["cost_scenarios"]:
                    cfg = Assumptions(**plan["assumptions"], fee=scenario["fee"], adverse_bps=scenario["adverse_bps"])
                    identity = {"stage": stage["name"], "strategy": name, "scenario": scenario["name"]}
                    journal({"kind": "trial_started", **identity})
                    result = screen(bars, name, cfg, start, end)
                    filename = f"{stage['name']}-{name}-{scenario['name']}.json"
                    content = dumps(result).encode()
                    (output / filename).write_bytes(content)
                    summary = {key: value for key, value in result.items() if key not in ("trades", "equity")}
                    summary.update(scenario=scenario["name"], artifact=filename, sha256=hashlib.sha256(content).hexdigest())
                    results[(name, scenario["name"])]=summary
                    stage_summary["trials"].append(summary)
                    journal({"kind": "trial_finished", **identity, "sha256": summary["sha256"],
                             "net_return": summary["net_return"], "max_drawdown": summary["max_drawdown"]})
            report["stages"].append(stage_summary)
            if stage["name"] == "discovery":
                eligible = [name for name in names if passes_gate(name, results, plan)]
                eligible.sort(key=lambda name: (-min(results[(name, s["name"])]["net_return"] for s in plan["cost_scenarios"]), name))
                if not eligible:
                    report["status"] = "rejected_at_discovery"
                    break
                selected = eligible[0]
                report["selected"] = selected
                journal({"kind": "selected_on_discovery", "candidate": selected})
            elif not passes_gate(selected, results, plan):
                report["status"] = "rejected_at_"+stage["name"]
                break
            else:
                report["status"] = "passed_"+stage["name"]+"_screen_only"
        journal({"kind": "end", "status": report["status"]})
    except Exception as exc:
        report["error_type"] = type(exc).__name__
        if isinstance(exc, DataError):
            report["reason"] = str(exc)
        journal({"kind": "failed", "error_type": type(exc).__name__})
        raise
    finally:
        (output / "report.json").write_text(dumps(report), encoding="utf-8")
    return report
