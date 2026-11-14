import copy
import json
import tempfile
import unittest
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch

from cryptoir.domain import DataError
from cryptoir.history import Candle, DAY, HOUR
from cryptoir.research import epoch, passes_gate, run_experiment, session_boundary, validate_plan

PLAN = Path(__file__).resolve().parents[1] / "research" / "baseline-v1.json"


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.plan = json.loads(PLAN.read_bytes())

    def test_preregistered_plan_is_valid(self):
        validate_plan(self.plan)

    def test_overlapping_splits_are_rejected(self):
        self.plan["splits"][1]["start"] = "2025-10-01"
        with self.assertRaises(DataError):
            validate_plan(self.plan)

    def test_stress_cannot_silently_reduce_costs(self):
        self.plan["cost_scenarios"][1]["fee"] = "0"
        with self.assertRaises(DataError):
            validate_plan(self.plan)

    def test_gate_requires_profit_stress_drawdown_and_round_trips(self):
        results = {("hold", "base_assumed"): {"net_return": D("0.01")}}
        for scenario in self.plan["cost_scenarios"]:
            results[("candidate", scenario["name"])] = {"net_return": D("0.02"), "max_drawdown": D("0.05"), "completed_sells": 10}
        self.assertTrue(passes_gate("candidate", results, self.plan))
        for field, value in (("net_return", D("-0.001")), ("max_drawdown", D("0.11")), ("completed_sells", 9)):
            changed = copy.deepcopy(results)
            changed[("candidate", "stress_assumed")][field] = value
            self.assertFalse(passes_gate("candidate", changed, self.plan))

    def test_tehran_boundaries_do_not_overlap_adjacent_splits(self):
        boundary = session_boundary("2026-01-01", 73800)
        self.assertEqual(boundary % DAY, 73800)
        self.assertLessEqual(boundary, epoch("2026-01-01"))
        self.assertGreater(boundary+DAY, epoch("2026-01-01"))

    def test_discovery_failure_never_reads_validation_or_holdout_in_screen(self):
        plan = copy.deepcopy(self.plan)
        plan["data_start"], plan["data_end_exclusive"] = "2025-01-01", "2025-02-01"
        plan["splits"] = [dict(name=name, start=start, end=end) for name,start,end in (
            ("discovery","2025-01-02","2025-01-10"), ("validation","2025-01-10","2025-01-20"), ("held_out","2025-01-20","2025-02-01"))]
        start, end = epoch(plan["data_start"]), epoch(plan["data_end_exclusive"])
        bars = [Candle(t, 100, 100, 100, 100, 100) for t in range(start, end, HOUR)]
        audit = {"venue":"nobitex", "pair":"BTC/USDT", "manifest_sha256":"test", "normalized_sha256":"test",
                 "reconciled":True, "day_phase_utc_seconds":0}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
            (root / "manifest.json").write_text(json.dumps({"start":start,"end_exclusive":end}), encoding="utf-8")
            from cryptoir.backtest import screen
            with patch("cryptoir.research.audit_history", return_value=(audit,bars)), patch("cryptoir.research.screen", wraps=screen) as runner:
                result = run_experiment(root/"plan.json", root, root/"output")
            self.assertEqual(result["status"], "rejected_at_discovery")
            self.assertFalse(result["held_out_inspected"])
            self.assertEqual(len(result["stages"]), 1)
            self.assertEqual(runner.call_count, 10)
            for call in runner.call_args_list:
                self.assertEqual(call.args[-1], epoch("2025-01-10"))


if __name__ == "__main__":
    unittest.main()
