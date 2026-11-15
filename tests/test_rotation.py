import copy
import json
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch

from cryptoir.backtest import Assumptions
from cryptoir.domain import DataError
from cryptoir.history import Candle, HOUR, DAY
from cryptoir.rotation import RelativeMomentum, rotation_screen, validate_plan, gate_failures, run_experiment
from cryptoir.research import epoch

PLAN = Path(__file__).resolve().parents[1]/"research/rotation-v1.json"


class RotationTests(unittest.TestCase):
    def setUp(self):
        self.assets = ("BTC", "ETH", "SOL")
        self.origin = 1000*HOUR
        self.series = {a: [Candle(self.origin+i*HOUR, 100, 100, 100, 100, 10000) for i in range(44)] for a in self.assets}
        self.cfg = {a: Assumptions(1000, "0.25", "0.0013", 15, "0.01", "0.001", 1,
                                   buy_fee_mode="received_base") for a in self.assets}
        self.plan = json.loads(PLAN.read_bytes())

    def run_screen(self, series=None, cfg=None, end_index=44, rebalance=4):
        return rotation_screen(series or self.series, cfg or self.cfg, self.origin+4*HOUR,
                               self.origin+end_index*HOUR, lookback_hours=4, rebalance_hours=rebalance)

    def trends(self):
        rows = copy.deepcopy(self.series)
        for a in self.assets:
            for i in range(44):
                price = 100+i if a == "BTC" and i < 16 else 100
                if a == "ETH" and 16 <= i < 32:
                    price = 100+(i-15)*3
                rows[a][i] = Candle(self.origin+i*HOUR, price, price, price, price, 10000)
        return rows

    def test_fixed_plan_and_invalid_units_costs_pins_or_stages(self):
        validate_plan(self.plan)
        changes = []
        for key,value in (("assets",["BTC"]),("dataset_pins",self.plan["dataset_pins"][:-1]),("benchmarks",["cash"])):
            changes.append({**self.plan,key:value})
        altered = copy.deepcopy(self.plan);altered["cost_scenarios"][1]["fee"]="0";changes.append(altered)
        altered = copy.deepcopy(self.plan);altered["splits"][1]["start"]="2025-09-01";changes.append(altered)
        altered = copy.deepcopy(self.plan);altered["strategy"]["lookback_hours"]=True;changes.append(altered)
        altered = copy.deepcopy(self.plan);altered["dataset_pins"][0]["manifest_sha256"]="bad";changes.append(altered)
        altered = copy.deepcopy(self.plan);altered["assumptions"]["buy_fee_mode"]="quote";changes.append(altered)
        for plan in changes:
            with self.subTest(plan=plan["experiment"]),self.assertRaises(DataError):validate_plan(plan)

    def test_selector_uses_exact_past_window_ties_and_cash(self):
        s=RelativeMomentum(self.assets,2)
        for i in range(3):
            price=100+i
            s.observe({a:Candle(HOUR*(i+1),price,price,price,price,100) for a in self.assets})
        target,returns=s.choose()
        self.assertEqual(target,"BTC")
        self.assertEqual(returns["BTC"],D("0.02"))
        s.observe({a:Candle(4*HOUR,90,90,90,90,100) for a in self.assets})
        self.assertIsNone(s.choose()[0])

    def test_selector_rejects_future_gaps_unaligned_and_missing_assets(self):
        for variant in ("gap","unaligned","missing"):
            s=RelativeMomentum(self.assets,2)
            first={a:self.series[a][0] for a in self.assets};s.observe(first)
            bad={a:self.series[a][2 if variant=="gap" else 1] for a in self.assets}
            if variant=="unaligned":bad["ETH"]=self.series["ETH"][2]
            if variant=="missing":bad.pop("ETH")
            with self.assertRaises(DataError):s.observe(bad)
        with self.assertRaisesRegex(DataError,"warmup"):RelativeMomentum(self.assets,4).choose()

    def test_flat_prices_preserve_one_cash_account_without_trades(self):
        r=self.run_screen()
        self.assertEqual(r["final_liquidation_mark"],1000)
        self.assertEqual(r["trade_count"],0)
        self.assertEqual(r["cash"],1000)
        self.assertFalse(r["execution_ready"])

    def test_switch_exits_first_and_separately_delays_entry(self):
        r=self.run_screen(self.trends())
        trades=r["trades"]
        self.assertGreaterEqual(len(trades),4)
        for t in trades:
            self.assertGreaterEqual(t["execution_time"]-t["decision_time"],HOUR)
            self.assertGreaterEqual(t["cash_after"],0)
            self.assertLessEqual(sum(q>=D("0.01") for q in t["quantities_after"].values()),1)
        eth_buy=next(t for t in trades if t["asset"]=="ETH" and t["side"]=="buy")
        btc_sell=next(t for t in trades if t["asset"]=="BTC" and t["side"]=="sell")
        self.assertGreater(eth_buy["execution_time"],btc_sell["execution_time"])
        self.assertGreaterEqual(eth_buy["decision_time"],btc_sell["execution_time"]+HOUR)

    def test_received_fee_residuals_and_pnl_are_conserved(self):
        r=self.run_screen(self.trends())
        for t in r["trades"]:
            self.assertEqual(t["fee_quote_equivalent"],t["quantity"]*t["price"]*D("0.0013"))
            if t["side"]=="buy":
                self.assertEqual(t["fee_base"],t["quantity"]*D("0.0013"))
                self.assertEqual(t["fee_quote"],0)
        self.assertAlmostEqual(r["final_liquidation_mark"]-1000,r["realized_pnl"]+r["unrealized_pnl_with_exit_costs"],places=20)
        self.assertGreater(sum(r["residual_dust_quantities"].values()),0)
        self.assertGreater(sum(r["cost_basis"].values()),0)

    def test_partial_exit_does_not_finance_a_premature_other_entry(self):
        rows=self.trends()
        for i in range(15,26):rows["BTC"][i]=replace(rows["BTC"][i],volume=D("20"))
        r=self.run_screen(rows)
        sells=[t for t in r["trades"] if t["asset"]=="BTC" and t["side"]=="sell"]
        self.assertGreater(len(sells),1)
        buys=[t for t in r["trades"] if t["asset"]=="ETH" and t["side"]=="buy"]
        self.assertTrue(buys)
        self.assertGreater(buys[0]["execution_time"],sells[-1]["execution_time"])

    def test_capacity_uses_prior_volume_and_never_current_volume(self):
        rows=self.trends();original=self.run_screen(rows)
        first=original["trades"][0];i=(first["execution_time"]-self.origin)//HOUR
        rows["BTC"][i]=replace(rows["BTC"][i],volume=0)
        self.assertEqual(self.run_screen(rows)["trades"][0],first)
        rows["BTC"][i-1]=replace(rows["BTC"][i-1],volume=0)
        r=self.run_screen(rows)
        self.assertGreater(r["trades"][0]["execution_time"],first["execution_time"])
        self.assertGreater(r["rejected_orders"],0)

    def test_future_changes_leave_earlier_decisions_trades_and_equity_identical(self):
        rows=self.trends();original=self.run_screen(rows)
        for a in self.assets:
            for i in range(30,44):rows[a][i]=Candle(rows[a][i].time,1000,1000,1000,1000,0)
        changed=self.run_screen(rows);boundary=self.origin+30*HOUR
        for key,timekey in (("trades","execution_time"),("decisions","time"),("equity","time")):
            select=lambda r:[v for v in r[key] if v[timekey] < boundary]
            self.assertEqual(select(original),select(changed))

    def test_future_outside_stage_cannot_change_any_report_value(self):
        rows=self.trends();short={a:s[:30] for a,s in rows.items()}
        self.assertEqual(self.run_screen(rows,end_index=30),self.run_screen(short,end_index=30))

    def test_mismatched_timelines_or_insufficient_warmup_fail(self):
        bad=copy.deepcopy(self.series);bad["ETH"].pop(5)
        with self.assertRaisesRegex(DataError,"timelines"):self.run_screen(bad)
        with self.assertRaisesRegex(DataError,"warmup"):
            rotation_screen(self.series,self.cfg,self.origin,self.origin+10*HOUR,lookback_hours=4,rebalance_hours=4)
        cfg={**self.cfg,"ETH":replace(self.cfg["ETH"],capital=2000)}
        with self.assertRaisesRegex(DataError,"shared capital"):self.run_screen(cfg=cfg)

    def test_minimum_increment_full_allocation_and_price_jump(self):
        rows=self.trends()
        cfg={a:replace(c,allocation=1,quantity_step=D("0.3")) for a,c in self.cfg.items()}
        rows["BTC"][6]=Candle(rows["BTC"][6].time,10000,10000,10000,10000,10000)
        r=self.run_screen(rows,cfg=cfg)
        self.assertGreater(r["rejected_orders"],0)
        for t in r["trades"]:
            self.assertEqual(t["quantity"]%D("0.3"),0)
            self.assertGreaterEqual(t["cash_after"],0)
        impossible={a:replace(c,minimum_notional=100000) for a,c in self.cfg.items()}
        self.assertEqual(self.run_screen(rows,cfg=impossible)["trade_count"],0)

    def test_decisions_only_on_fixed_schedule(self):
        r=self.run_screen(self.trends())
        self.assertEqual([d["time"] for d in r["decisions"]],list(range(self.origin+5*HOUR,self.origin+45*HOUR,4*HOUR)))

    def test_changed_target_cancels_obsolete_delayed_intent(self):
        cfg={a:replace(c,delay_hours=24) for a,c in self.cfg.items()}
        r=self.run_screen(self.trends(),cfg=cfg)
        self.assertGreater(r["cancelled_intents"],0)
        self.assertEqual(r["trade_count"],0)
        self.assertEqual(r["cash"],1000)

    def test_gate_checks_both_costs_and_both_btc_benchmarks(self):
        results={}
        for scenario in self.plan["cost_scenarios"]:
            results[("relative_momentum",scenario["name"])]=dict(net_return=D("0.03"),max_drawdown=D("0.05"),completed_sells=10)
        for name in ("btc_hold_full","btc_hold_matched"):results[(name,"base_assumed")]=dict(net_return=D("0.02"))
        self.assertEqual(gate_failures(results,self.plan),[])
        for key,field,value in ((("relative_momentum","stress_assumed"),"net_return",0),
                 (("relative_momentum","base_assumed"),"max_drawdown",D("0.11")),
                 (("relative_momentum","stress_assumed"),"completed_sells",9),
                 (("btc_hold_full","base_assumed"),"net_return",D("0.04"))):
            changed=copy.deepcopy(results);changed[key][field]=value
            self.assertTrue(gate_failures(changed,self.plan))

    def experiment_fixture(self,root):
        p=copy.deepcopy(self.plan)
        p.update(data_start="2025-01-01",data_end_exclusive="2025-02-01")
        p["splits"]=[dict(name=n,start=s,end=e) for n,s,e in (("discovery","2025-01-03","2025-01-10"),
            ("validation","2025-01-10","2025-01-20"),("held_out","2025-01-20","2025-02-01"))]
        p["strategy"].update(lookback_hours=24,rebalance_hours=24)
        start,end=epoch(p["data_start"]),epoch(p["data_end_exclusive"])
        bars=[Candle(t,100,100,100,100,100) for t in range(start,end,HOUR)]
        paths=[];audits={}
        for pin in p["dataset_pins"]:
            path=root/pin["pair"].split("/")[0];path.mkdir();paths.append(path)
            (path/"manifest.json").write_text(json.dumps(dict(start=start,end_exclusive=end)),encoding="utf-8")
            audits[path]=({**pin,"reconciled":True,"volume_quality":{"usable_for_volume_screen":True},"day_phase_utc_seconds":0},bars)
        plan=root/"plan.json";plan.write_text(json.dumps(p),encoding="utf-8")
        return plan,paths,audits

    def test_failed_discovery_never_opens_later_performance_or_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);plan,paths,audits=self.experiment_fixture(root)
            with patch("cryptoir.rotation.audit_history",side_effect=lambda p:audits[p]),\
                    patch("cryptoir.rotation.rotation_screen",wraps=rotation_screen) as runner:
                r=run_experiment(plan,paths,root/"output")
            self.assertEqual(r["status"],"rejected_at_discovery")
            self.assertFalse(r["held_out_inspected"])
            self.assertEqual(len(r["stages"]),1)
            self.assertEqual(len(r["stages"][0]["trials"]),8)
            self.assertEqual(runner.call_count,2)
            for call in runner.call_args_list:
                self.assertEqual(call.args[-1],epoch("2025-01-10"))
                self.assertTrue(all(b.time<epoch("2025-01-10") for s in call.args[0].values() for b in s))

    def test_changed_pin_duplicate_or_bad_data_fail_before_first_trial(self):
        for variant in ("hash","duplicate","reconciliation","volume","alignment"):
            with self.subTest(variant=variant),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);plan,paths,audits=self.experiment_fixture(root)
                if variant=="duplicate":paths[1]=paths[0]
                elif variant=="hash":audits[paths[1]][0]["normalized_sha256"]="a"*64
                elif variant=="reconciliation":audits[paths[1]][0]["reconciled"]=False
                elif variant=="volume":audits[paths[1]][0]["volume_quality"]["usable_for_volume_screen"]=False
                else:audits[paths[1]]=(audits[paths[1]][0],audits[paths[1]][1][1:])
                with patch("cryptoir.rotation.audit_history",side_effect=lambda p:audits[p]),patch("cryptoir.rotation.screen") as runner:
                    with self.assertRaises(DataError):run_experiment(plan,paths,root/"output")
                runner.assert_not_called()
                report=json.loads((root/"output/report.json").read_bytes())
                self.assertFalse(report["dataset_pins_verified"])
                self.assertEqual(report["stages"],[])

    def test_validation_failure_stops_holdout_and_success_opens_each_stage_once(self):
        for fail_validation in (True,False):
            with self.subTest(fail_validation=fail_validation),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);plan,paths,audits=self.experiment_fixture(root)
                def model(series,cfg,start,end,**kwargs):
                    negative=fail_validation and start==epoch("2025-01-10")
                    return dict(net_return=D("-0.01") if negative else D("0.05"),max_drawdown=D("0.01"),completed_sells=10)
                with patch("cryptoir.rotation.audit_history",side_effect=lambda p:audits[p]),\
                        patch("cryptoir.rotation.rotation_screen",side_effect=model) as runner:
                    r=run_experiment(plan,paths,root/"output")
                self.assertEqual(r["held_out_inspected"],not fail_validation)
                self.assertEqual(r["status"],"rejected_at_validation" if fail_validation else "passed_held_out_screen_only")
                self.assertEqual(runner.call_count,4 if fail_validation else 6)
                events=[json.loads(line) for line in (root/"output/trials.jsonl").read_text().splitlines()]
                self.assertEqual(sum(e["kind"]=="held_out_opened" for e in events),int(not fail_validation))
                self.assertFalse(r["execution_ready"])


if __name__ == "__main__":
    unittest.main()
