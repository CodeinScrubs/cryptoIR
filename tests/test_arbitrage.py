import copy
import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path

from cryptoir.arbitrage import quote_pair, run_spread_screen, screen_events, validate_plan, verify_catalog
from cryptoir.catalog import ENDPOINTS, parse_catalog
from cryptoir.domain import Book, DataError, Level
from cryptoir.streams import dumps

PLAN = Path(__file__).resolve().parents[1]/"research"/"arbitrage-v1.json"


def book(venue, bid, ask, received=1_100_000_000):
    return Book(venue, "BTC/USDT", (Level(bid, 10),), (Level(ask, 10),), received, 0, 1_000_000_000)


class SpreadTests(unittest.TestCase):
    def setUp(self):
        self.plan = json.loads(PLAN.read_bytes())
        self.plan.update(gross_buy_quantity="1", buy_quote_budget="1000", sell_base_inventory="1")
        for rule in self.plan["market_rules"].values():
            rule.update(quantity_step="0.01", minimum_notional="1", minimum_quantity=None)
        self.scenario = self.plan["scenarios"][1]
        self.scenario["reserve_bps"] = "0"
        self.scenario["fees"]["nobitex"]["rate"] = "0.003"
        self.scenario["fees"]["tabdeal"]["rate"] = "0.02"
        for fee in self.plan["scenarios"][2]["fees"].values():
            fee["rate"] = "0.04"
        self.buy, self.sell = book("nobitex", 99, 100), book("tabdeal", 110, 111)

    def quote(self):
        return quote_pair(self.buy, self.sell, self.plan, self.scenario, 1_100_000_000)

    def test_received_base_fee_rounding_and_residual_are_not_quote_profit(self):
        result = self.quote()
        self.assertEqual(result["buy_base_fee"], D("0.003"))
        self.assertEqual(result["buy_quote_fee"], 0)
        self.assertEqual(result["sell_quantity"], D("0.99"))
        self.assertEqual(result["residual_base"], D("0.007"))
        self.assertEqual(result["estimated_net_quote"], D("6.722"))
        self.assertFalse(result["executable"])

    def test_quote_buy_fee_does_not_also_reduce_base(self):
        self.scenario["fees"]["nobitex"]["buy_asset"] = "quote"
        result = self.quote()
        self.assertEqual(result["buy_base_fee"], 0)
        self.assertEqual(result["buy_quote_fee"], D("0.3"))
        self.assertEqual(result["sell_quantity"], 1)
        self.assertEqual(result["estimated_net_quote"], D("7.5"))

    def test_depth_weighting_and_both_leg_reserve(self):
        self.buy = replace(self.buy, asks=(Level(100, "0.5"), Level(102, "0.5")))
        self.scenario["reserve_bps"] = "10"
        result = self.quote()
        self.assertEqual(result["buy_cost_quote"], 101)
        self.assertEqual(result["reserve_quote"], D("0.2099"))
        self.assertEqual(result["estimated_net_quote"], D("5.5121"))

    def test_insufficient_depth_does_not_become_two_full_fills(self):
        for side in ("buy", "sell"):
            with self.subTest(side=side):
                original = getattr(self, side)
                setattr(self, side, replace(original, **({"asks":(Level(100, "0.5"),)} if side=="buy"
                                                        else {"bids":(Level(110, "0.5"),)})))
                with self.assertRaisesRegex(DataError, "displayed_depth"):
                    self.quote()
                setattr(self, side, original)

    def test_inventory_and_quote_ceiling_are_separate(self):
        self.plan["buy_quote_budget"] = "99"
        with self.assertRaisesRegex(DataError, "buy_quote_capacity"):
            self.quote()
        self.plan["buy_quote_budget"] = "1000"
        self.plan["sell_base_inventory"] = "0.98"
        with self.assertRaisesRegex(DataError, "prefunded_sell_inventory"):
            self.quote()

    def test_buy_reserve_is_included_in_capacity_requirement(self):
        self.plan["buy_quote_budget"] = "100"
        self.scenario["reserve_bps"] = "1"
        with self.assertRaisesRegex(DataError, "buy_quote_capacity"):
            self.quote()

    def test_step_minimum_quantity_and_minimum_notional(self):
        self.plan["gross_buy_quantity"] = "1.001"
        with self.assertRaisesRegex(DataError, "buy_quantity_step"):
            self.quote()
        self.plan["gross_buy_quantity"] = "1"
        rule = self.plan["market_rules"]["tabdeal"]
        rule["minimum_quantity"] = "1"
        with self.assertRaisesRegex(DataError, "minimum_quantity"):
            self.quote()
        rule["minimum_quantity"], rule["minimum_notional"] = None, "109"
        with self.assertRaisesRegex(DataError, "minimum_notional"):
            self.quote()

    def test_unknown_timestamp_or_minimum_is_not_silently_assumed(self):
        self.buy = replace(self.buy, exchange_ns=None)
        with self.assertRaisesRegex(DataError, "unknown_source_time"):
            self.quote()
        self.buy = replace(self.buy, exchange_ns=1_000_000_000)
        self.plan["market_rules"]["nobitex"]["minimum_notional"] = None
        with self.assertRaisesRegex(DataError, "unknown_minimum_notional"):
            self.quote()

    def test_stale_and_future_books_cannot_create_estimates(self):
        for fields in ({"exchange_ns":1}, {"received_ns":1}, {"exchange_ns":2_000_000_000},
                       {"received_ns":2_000_000_000}, {"request_duration_ns":6_000_000_000}):
            with self.subTest(fields=fields):
                original = self.buy
                self.buy = replace(original, **fields)
                self.plan["max_age_ms"] = 500
                with self.assertRaises(DataError):
                    self.quote()
                self.buy = original

    def test_no_sellable_base_after_rounding(self):
        self.plan["market_rules"]["tabdeal"]["quantity_step"] = "2"
        with self.assertRaisesRegex(DataError, "no_sellable_base"):
            self.quote()

    def test_plan_rejects_missing_costs_fee_currency_and_improved_stress(self):
        validate_plan(self.plan)
        for modify in (lambda p:p["scenarios"][1]["fees"].pop("wallex"),
                       lambda p:p["scenarios"][1]["fees"]["nobitex"].update(buy_asset="unknown"),
                       lambda p:p["scenarios"][2]["fees"]["nobitex"].update(rate="0")):
            changed = copy.deepcopy(self.plan)
            modify(changed)
            with self.assertRaises(DataError):
                validate_plan(changed)

    def test_gap_clears_inventory_of_known_books_and_counts_are_not_profit(self):
        def event(b):
            return {"kind":"book", "venue":b.venue, "book":b}
        rows = [event(self.buy), event(self.sell), {"kind":"gap", "venue":"nobitex"},
                event(replace(self.sell, received_ns=1_200_000_000))]
        report = screen_events(rows, self.plan)
        self.assertEqual(sum(r["comparisons"] for r in report["scenarios_and_routes"]), 6)
        self.assertFalse(report["portfolio_return_available"])
        self.assertTrue(report["capacity_is_reused_per_comparison"])
        self.assertEqual(self.plan["sell_base_inventory"], "1")

    def test_absent_venue_never_silently_omits_registered_routes(self):
        report = screen_events([], self.plan)
        self.assertEqual(len(report["scenarios_and_routes"]), 18)
        self.assertTrue(all(r["comparisons"] == 0 and r["best"] is None for r in report["scenarios_and_routes"]))

    def test_newer_book_is_never_used_before_its_receive_event(self):
        rows = [{"kind":"book", "venue":b.venue, "book":b} for b in (
            self.buy, self.sell, replace(self.buy, received_ns=1_200_000_000, asks=(Level(90,10),), bids=(Level(89,10),)))]
        prefix = screen_events(rows[:2], self.plan)
        full = screen_events(rows, self.plan)
        def chosen(report):
            return next(r["best"] for r in report["scenarios_and_routes"]
                        if r["scenario"]=="assumed_base" and r["buy_venue"]=="nobitex" and r["sell_venue"]=="tabdeal")
        old, new = chosen(prefix), chosen(full)
        self.assertEqual(old["buy_cost_quote"], 100)
        self.assertEqual(old["observed_ns"], 1_100_000_000)
        self.assertEqual(new["buy_cost_quote"], 90)
        self.assertEqual(new["observed_ns"], 1_200_000_000)
        with self.assertRaisesRegex(DataError, "regressed"):
            screen_events(rows+[rows[0]], self.plan)


class SpreadArtifactTests(unittest.TestCase):
    def fixture(self, root):
        plan = json.loads(PLAN.read_bytes())
        plan.update(capture_seconds=5, capture_max_mb=1)
        nob = {"status":"ok", "coins":[{"coin":"btc", "scale":"1"}], "nobitex":{
            "activeCurrencies":["btc"], "amountPrecisions":{"BTCUSDT":"0.000001"},
            "pricePrecisions":{"BTCUSDT":"0.01"}, "minOrders":{"usdt":"0.5"}}}
        wal = {"success":True, "result":{"markets":[{"symbol":"BTCUSDT", "base_asset":"BTC", "quote_asset":"USDT",
            "amount_precision":8, "price_precision":2, "is_spot":True, "is_margin":True}]}}
        tab = [{"symbol":"BTCUSDT", "baseAsset":"BTC", "quoteAsset":"USDT", "status":"TRADING",
            "isSpotTradingAllowed":True, "isMarginTradingAllowed":False, "filters":[
                {"filterType":"LOT_SIZE", "stepSize":"0.000001", "minQty":"0.000001"},
                {"filterType":"PRICE_FILTER", "tickSize":"0.01"},
                {"filterType":"MIN_NOTIONAL", "minNotional":"1"}]}]
        records=[]
        for venue, payload in zip(plan["venues"], (nob,wal,tab)):
            body=dumps(payload)
            records.append({"venue":venue, "dataset":"market_catalog", "kind":"snapshot", "status_code":200,
                "url":ENDPOINTS[venue], "raw_body":body, "body_sha256":hashlib.sha256(body.encode()).hexdigest(),
                "payload":payload, "markets":parse_catalog(venue,payload)})
        catalog=root/"catalog.jsonl"
        catalog.write_text("".join(dumps(r)+"\n" for r in records),encoding="utf-8")
        plan["catalog_sha256"]=hashlib.sha256(catalog.read_bytes()).hexdigest()
        plan_path=root/"plan.json"
        plan_path.write_text(dumps(plan),encoding="utf-8")
        rows=[]
        def add(venue,kind,**fields):
            rows.append({"schema":2,"seq":len(rows)+1,"monotonic_ns":len(rows)+1,"run_id":"fixture",
                         "observed_ns":1_100_000_000,"venue":venue,"kind":kind,**fields})
        add("all","recording_start",pair="BTC/USDT",venues=plan["venues"],duration_seconds=5,max_mb=1)
        frames={
            "nobitex":[{"push":{"channel":"public:orderbook-BTCUSDT","pub":{"offset":10,
                "data":{"bids":[["59999","2"]],"asks":[["60000","2"]],"lastUpdate":1000}}}}],
            "wallex":[{"push":{"channel":"orderbook:BTCUSDT@"+side+"Depth","pub":{"data":{"message":[
                {"price":price,"quantity":"2"}]}}}} for side,price in (("buy","59999"),("sell","60000"))],
            "tabdeal":[{"stream":"btcusdt@depth@2000ms","data":{"s":"BTCUSDT","e":"depthUpdate","E":1000,
                "b":[["61000","2"]],"a":[["61001","2"]]}}]}
        for venue in plan["venues"]:
            add(venue,"session_start",pair="BTC/USDT")
            for frame in frames[venue]:
                raw=dumps(frame)
                add(venue,"frame",received_ns=1_100_000_000,clock_error_ns=0,raw=raw,sha256=hashlib.sha256(raw.encode()).hexdigest())
        add("all","recording_end",reason="duration_completed")
        recording=root/"stream.jsonl"
        recording.write_text("".join(dumps(r)+"\n" for r in rows),encoding="utf-8")
        return plan,plan_path,catalog,recording

    def test_raw_frame_replay_and_private_artifacts_are_exclusive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            plan,plan_path,catalog,recording=self.fixture(root)
            result=run_spread_screen(plan_path,catalog,recording,root/"output")
            self.assertEqual(result["status"],"completed_conditional_diagnostic")
            self.assertFalse(result["execution_ready"])
            self.assertTrue(result["recording_audit"]["ended_cleanly"])
            self.assertTrue(any(r["comparisons"] for r in result["scenarios_and_routes"]))
            with self.assertRaises(FileExistsError):
                run_spread_screen(plan_path,catalog,recording,root/"output")

    def test_catalog_hash_and_derived_rule_tampering_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            plan,_,catalog,_=self.fixture(root)
            verify_catalog(catalog,plan)
            content=catalog.read_text(encoding="utf-8")
            catalog.write_text(content+"\n",encoding="utf-8")
            with self.assertRaisesRegex(DataError,"registered source hash"):
                verify_catalog(catalog,plan)
            catalog.write_text(content,encoding="utf-8")
            plan["market_rules"]["nobitex"]["minimum_notional"]="0.1"
            with self.assertRaisesRegex(DataError,"rule differs"):
                verify_catalog(catalog,plan)

    def test_wrong_observation_header_and_corrupt_frame_block_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            _,plan_path,catalog,recording=self.fixture(root)
            content=recording.read_text(encoding="utf-8")
            recording.write_text(content.replace('"duration_seconds":5','"duration_seconds":6'),encoding="utf-8")
            with self.assertRaisesRegex(DataError,"header differs"):
                run_spread_screen(plan_path,catalog,recording,root/"output")
            rows=[json.loads(s) for s in content.splitlines()]
            next(r for r in rows if r["kind"]=="frame")["raw"]+=" "
            recording.write_text("".join(dumps(r)+"\n" for r in rows),encoding="utf-8")
            with self.assertRaisesRegex(DataError,"frame hash"):
                run_spread_screen(plan_path,catalog,recording,root/"output")

    def test_truncated_recording_is_not_complete_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            _,plan_path,catalog,recording=self.fixture(root)
            lines=recording.read_text(encoding="utf-8").splitlines()
            recording.write_text("\n".join(lines[:-1])+"\n",encoding="utf-8")
            result=run_spread_screen(plan_path,catalog,recording,root/"output")
            self.assertEqual(result["status"],"incomplete_observation")


if __name__ == "__main__":
    unittest.main()
