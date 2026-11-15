import copy
import gzip
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from decimal import Decimal as D
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from cryptoir import triangular as t
from cryptoir.catalog import ENDPOINTS, parse_catalog
from cryptoir.domain import Book, DataError, Level
from cryptoir.feeds import native_symbol
from cryptoir.streams import dumps
from cryptoir.valuation import ALL_BOOKS

NS = 1_000_000_000


def fixture():
    plan = json.loads((Path(__file__).resolve().parents[1]/"research/triangular-v1.json").read_bytes())
    plan.update(assets=["BTC"], samples=2, depth_participation="1", start_budget="100")
    plan["market_rules"] = {p:{"quantity_step":"0.01", "price_tick":"0.01", "minimum_notional":"0.01"}
                            for p in t.required_pairs(plan)}
    payload = {"status":"ok", "coins":[{"coin":coin, "scale":"1"} for coin in ("BTC", "USDT")],
        "nobitex":{"activeCurrencies":["btc", "usdt"], "minOrders":{"rls":"0.01", "usdt":"0.01"},
        "amountPrecisions":{native_symbol("nobitex",p):"0.01" for p in t.required_pairs(plan)},
        "pricePrecisions":{native_symbol("nobitex",p):"0.01" for p in t.required_pairs(plan)}}}
    catalog = response(payload, ENDPOINTS["nobitex"])
    catalog["markets"] = parse_catalog("nobitex", payload)
    encoded = dumps(catalog).encode()
    plan["catalog_sha256"] = t.digest(encoded)
    return plan, encoded


def response(payload, url=ALL_BOOKS, now=100*NS):
    raw = dumps(payload)
    return {"schema":1, "kind":"snapshot", "url":url, "status_code":200,
            "started_ns":now-NS, "received_ns":now, "duration_ns":NS,
            "payload":payload, "raw_body":raw, "body_sha256":t.digest(raw.encode())}


def books(now=100*NS):
    return {p:Book("nobitex", p, (Level(bid,10000),), (Level(ask,10000),), now, NS, now-NS)
            for p, bid, ask in (("BTC/USDT",9,10), ("BTC/IRR",1100,1110), ("USDT/IRR",99,100))}


def snapshot(now=100*NS):
    payload = {"status":"ok"}
    for pair, book in books(now).items():
        payload[native_symbol("nobitex", pair)] = {"lastUpdate":book.exchange_ns//1_000_000,
            "bids":[[str(l.price), str(l.quantity)] for l in book.bids],
            "asks":[[str(l.price), str(l.quantity)] for l in book.asks]}
    return response(payload, now=now)


class TriangleTests(unittest.TestCase):
    def setUp(self):
        self.plan, self.catalog = fixture()

    def quote(self, bookset=None, route=t.ROUTES[0], scenario=0):
        return t.quote_cycle(bookset or books(), "BTC", route, self.plan, self.plan["scenarios"][scenario], 100*NS)

    def paths(self, root):
        plan, catalog, recording = root/"plan.json", root/"catalog.json", root/"recording.gz"
        plan.write_text(dumps(self.plan), encoding="utf-8")
        catalog.write_bytes(self.catalog)
        return plan, catalog, recording

    def recording(self, path, plan_path, *, footer=True, records=None):
        rows = [{"schema":1, "kind":"triangular_capture_start", "plan_sha256":t.digest(plan_path.read_bytes()),
                 "observed_ns":99*NS, "version":"synthetic", "source_sha256":{}}]
        rows += [{"kind":"sample", "index":i, "record":r} for i,r in enumerate(
            records if records is not None else (snapshot(), snapshot(131*NS)))]
        if footer:
            rows.append({"kind":"triangular_capture_end", "samples":len(rows)-1, "stop_reason":"sample_limit"})
        with gzip.open(path, "xt", encoding="utf-8") as stream:
            stream.write("\n".join(dumps(row) for row in rows)+"\n")

    def test_registered_plan_and_rule_provenance(self):
        t.validate_plan(self.plan)
        with tempfile.TemporaryDirectory() as tmp:
            _, catalog, _ = self.paths(Path(tmp))
            t.verify_catalog(catalog, self.plan)
            self.plan["market_rules"]["BTC/IRR"]["minimum_notional"] = "0.02"
            with self.assertRaisesRegex(DataError, "observed catalog"):
                t.verify_catalog(catalog, self.plan)

    def test_plan_rejects_bad_bounds_units_cost_order_and_missing_rules(self):
        for key,value in (("samples",True), ("samples",121), ("interval_seconds",1),
                          ("start_asset","IRT"), ("start_budget","0"), ("depth_participation","1.1"),
                          ("assets",["USDT"]), ("market_rules",{})):
            with self.subTest(key=key),self.assertRaises(DataError):t.validate_plan({**self.plan,key:value})
        self.plan["scenarios"][2]["usdt_fee"] = "0"
        with self.assertRaises(DataError):t.validate_plan(self.plan)

    def test_three_leg_units_and_both_directions(self):
        forward = self.quote()
        self.assertEqual(forward["final_usdt"],110)
        self.assertEqual(forward["estimated_net_usdt"],10)
        self.assertEqual([leg["side"] for leg in forward["legs"]],["buy","sell","buy"])
        self.assertLess(self.quote(route=t.ROUTES[1])["estimated_net_usdt"],0)
        self.assertFalse(forward["execution_ready"])

    def test_received_fees_and_reserves_propagate_into_next_leg(self):
        raw, base, stress = (self.quote(scenario=i) for i in range(3))
        self.assertGreater(raw["final_usdt"],base["final_usdt"])
        self.assertGreater(base["final_usdt"],stress["final_usdt"])
        for leg in base["legs"]:
            self.assertEqual(leg["gross_received"]-leg["fee_received_asset"]-leg["reserve_received_asset"],leg["net_received"])
            self.assertEqual(leg["spent_input"]+leg["unspent_input"],leg["available_input"])
        self.assertEqual(base["legs"][0]["net_received"],base["legs"][1]["available_input"])

    def test_starting_cash_residual_preserved_intermediate_dust_not_valued(self):
        self.plan["start_budget"] = "100.05"
        result = self.quote(scenario=1)
        self.assertEqual(result["final_usdt"],result["legs"][0]["unspent_input"]+result["legs"][2]["net_received"])
        self.assertGreater(result["residuals_zero_usdt_valuation"]["BTC"],0)
        self.assertEqual(result["estimated_net_usdt"],result["final_usdt"]-D("100.05"))

    def test_sell_depth_and_minimums_cannot_be_invented(self):
        shallow = books()
        shallow["BTC/IRR"] = replace(shallow["BTC/IRR"], bids=(Level(1100,"0.001"),))
        with self.assertRaises(DataError):self.quote(shallow)
        self.plan["market_rules"]["BTC/IRR"]["minimum_notional"] = "999999"
        with self.assertRaises(DataError):self.quote()

    def test_buy_uses_only_registered_depth_and_retains_unspent_input(self):
        shallow = books()
        shallow["BTC/USDT"] = replace(shallow["BTC/USDT"], asks=(Level(10,1),))
        self.plan["depth_participation"] = "0.1"
        result = self.quote(shallow)
        self.assertEqual(result["legs"][0]["gross_received"],D("0.1"))
        self.assertEqual(result["legs"][0]["unspent_input"],99)
        self.assertEqual(result["final_usdt"],D("100.10"))

    def test_multiple_levels_change_cycle_proceeds(self):
        deep = books()
        deep["BTC/USDT"] = replace(deep["BTC/USDT"],asks=(Level(10,2),Level(20,100)))
        result = self.quote(deep)
        self.assertEqual(result["legs"][0]["gross_received"],6)
        self.assertEqual(result["final_usdt"],66)
        self.assertEqual(result["estimated_net_usdt"],-34)

    def test_stale_future_unknown_wrong_units_and_tick_are_rejected(self):
        b = books()["BTC/IRR"]
        variants = (replace(b,exchange_ns=None), replace(b,exchange_ns=101*NS),
                    replace(b,exchange_ns=90*NS), replace(b,received_ns=90*NS),
                    replace(b,pair="BTC/IRT"), replace(b,venue="other"),
                    replace(b,bids=(Level("1100.001",10000),)))
        for changed in variants:
            with self.subTest(pair=changed.pair),self.assertRaises(DataError):
                self.quote({**books(),"BTC/IRR":changed})

    def test_rial_aliases_are_scoped_and_preserve_prices(self):
        for base in ("BTC","ETH","SOL","USDT"):
            self.assertEqual(native_symbol("nobitex",base+"/IRR"),base+"IRT")
        for venue,pair in (("wallex","BTC/IRR"),("nobitex","BTC/IRT"),("nobitex","DOGE/IRR")):
            with self.assertRaises(DataError):native_symbol(venue,pair)

    def test_replay_covers_all_routes_and_does_not_add_cycle_profits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);plan,catalog,recording=self.paths(root)
            self.recording(recording,plan)
            report=t.run_screen(plan,catalog,recording,root/"output")
            self.assertEqual(report["status"],"completed_conditional_diagnostic")
            self.assertEqual(len(report["scenarios_and_routes"]),6)
            self.assertTrue(all(s["comparisons"]==2 for s in report["scenarios_and_routes"]))
            self.assertFalse(report["portfolio_return_available"])
            self.assertNotIn("profit_sum",report)
            with self.assertRaises(FileExistsError):t.run_screen(plan,catalog,recording,root/"output")

    def test_corruption_and_repaired_payload_without_body_stop_replay(self):
        for change in ("hash","payload"):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);plan,catalog,recording=self.paths(root)
                bad=snapshot()
                if change=="hash":bad["body_sha256"]="a"*64
                else:bad["payload"]["BTCIRT"]["bids"][0][0]="999"
                self.recording(recording,plan,records=[bad,snapshot(131*NS)])
                with self.assertRaises(DataError):t.run_screen(plan,catalog,recording,root/"output")
                report=json.loads((root/"output/report.json").read_bytes())
                self.assertEqual(report["status"],"incomplete_observation")

    def test_missing_or_stale_market_does_not_reuse_prior_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);plan,catalog,recording=self.paths(root)
            bad=snapshot(131*NS)
            bad["payload"].pop("BTCIRT")
            bad=response(bad["payload"],now=131*NS)
            self.recording(recording,plan,records=[snapshot(),bad])
            report=t.run_screen(plan,catalog,recording,root/"output")
            self.assertTrue(all(s["comparisons"]==1 and s["rejections"]["missing_market"]==1 for s in report["scenarios_and_routes"]))

    def test_truncation_or_absent_route_is_not_a_complete_observation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);plan,catalog,recording=self.paths(root)
            self.recording(recording,plan,footer=False)
            self.assertEqual(t.run_screen(plan,catalog,recording,root/"output")["status"],"incomplete_observation")
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);plan,catalog,recording=self.paths(root)
            self.recording(recording,plan,records=[response({"status":"ok"}),response({"status":"ok"},now=131*NS)])
            report=t.run_screen(plan,catalog,recording,root/"output")
            self.assertEqual(report["status"],"incomplete_observation")
            self.assertTrue(all(s["comparisons"]==0 for s in report["scenarios_and_routes"]))

    def test_sampling_spacing_and_header_must_match_registration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);plan,catalog,recording=self.paths(root)
            self.recording(recording,plan,records=[snapshot(),snapshot(101*NS)])
            with self.assertRaisesRegex(DataError,"spacing"):
                t.run_screen(plan,catalog,recording,root/"output")
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);plan,catalog,recording=self.paths(root)
            self.recording(recording,plan)
            changed={**self.plan,"start_budget":"200"}
            plan.write_text(dumps(changed),encoding="utf-8")
            with self.assertRaisesRegex(DataError,"header differs"):
                t.run_screen(plan,catalog,recording,root/"output")

    def test_capture_stops_on_source_error_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);plan,catalog,recording=self.paths(root)
            with patch.object(t,"public_response",return_value={"kind":"error"}) as fetch,patch.object(t.time,"sleep") as sleep,redirect_stdout(StringIO()):
                result=t.capture(plan,catalog,recording)
                self.assertEqual(result["stop_reason"],"source_error")
                self.assertEqual(result["samples"],1)
                fetch.assert_called_once_with(ALL_BOOKS)
                sleep.assert_not_called()
                with self.assertRaises(FileExistsError):t.capture(plan,catalog,recording)

    def test_duplicate_json_and_nonfinite_values_are_rejected(self):
        for raw in ('{"x":1,"x":2}','{"x":NaN}'):
            with self.assertRaises(DataError):t.strict_json(raw)

    def test_catalog_membership_excludes_scaled_unknown_inactive_and_missing_pairs(self):
        _,encoded=fixture();base=json.loads(encoded)["markets"][0]
        rows=[]
        for asset,scale,active in (("A","1",True),("BTC","1",True),("SCALED","1000",True),
                                   ("UNKNOWN",None,True),("INACTIVE","1",False),("USDT","1",True)):
            for quote in ("USDT","IRT"):
                rows.append({**base,"native_symbol":asset+quote,"base":asset,"quote":quote,
                             "native_scale":scale,"base_currency_active":active})
        rows.append({**base,"native_symbol":"MISSINGUSDT","base":"MISSING","quote":"USDT"})
        result=t.catalog_universe(rows)
        self.assertEqual(result["assets"],["A","BTC"])
        self.assertEqual(result["exclusions"],{"scaled_asset":1,"unknown_scale":1,"inactive_currency":1,
                                               "unsupported_identity":1,"missing_dual_quote":1})

    def test_broad_universe_must_match_whole_catalog_without_price_selection(self):
        self.plan["universe"]=t.CATALOG_UNIVERSE
        t.validate_plan(self.plan)
        with tempfile.TemporaryDirectory() as tmp:
            _,catalog,_=self.paths(Path(tmp));t.verify_catalog(catalog,self.plan)
            self.plan["assets"]=["ETH"]
            self.plan["market_rules"]={p.replace("BTC/","ETH/"):v for p,v in self.plan["market_rules"].items()}
            t.validate_plan(self.plan)
            with self.assertRaisesRegex(DataError,"deterministic catalog"):
                t.verify_catalog(catalog,self.plan)

    def test_catalog_pair_parser_handles_single_and_digit_names_without_global_aliases(self):
        for asset in ("A","1INCH","WRAPPED_X"):
            record=snapshot()
            record["payload"][asset+"IRT"]=record["payload"]["BTCIRT"]
            book=t.snapshot_book(record,asset+"/IRR")
            self.assertEqual(book.pair,asset+"/IRR")
            self.assertEqual(book.bids[0].price,1100)
        for pair in ("BTC/IRT","IRR/USDT","USDT/USDT","../../BTC/IRR"):
            with self.assertRaises(DataError):t.pair_symbol(pair)

    def test_existing_recording_replay_requires_both_pins_and_discloses_exploration(self):
        for bad in (None,"sha256","capture_plan_sha256"):
            with self.subTest(bad=bad),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);plan,catalog,recording=self.paths(root)
                self.recording(recording,plan)
                pin={"evaluation_type":"exploratory_existing_recording","sha256":t.digest(recording.read_bytes()),
                     "capture_plan_sha256":t.digest(plan.read_bytes())}
                if bad:pin[bad]="a"*64
                changed={**self.plan,"replay_recording":pin}
                t.validate_plan(changed);plan.write_text(dumps(changed),encoding="utf-8")
                if bad:
                    with self.assertRaises(DataError):t.run_screen(plan,catalog,recording,root/"output")
                else:
                    report=t.run_screen(plan,catalog,recording,root/"output")
                    self.assertEqual(report["evaluation_type"],"exploratory_existing_recording")
                    self.assertTrue(report["capture_complete"])
                    self.assertTrue(report["universe_coverage_complete"])
                    self.assertNotEqual(report["plan_sha256"],report["capture"]["plan_sha256"])

    def test_replay_plan_cannot_launch_fresh_capture_or_mislabel_new_evidence(self):
        self.plan["replay_recording"]={"evaluation_type":"exploratory_existing_recording", "sha256":"a"*64,
                                       "capture_plan_sha256":"b"*64}
        with tempfile.TemporaryDirectory() as tmp:
            plan,catalog,recording=self.paths(Path(tmp))
            with patch.object(t,"public_response") as request:
                with self.assertRaisesRegex(DataError,"cannot capture"):
                    t.capture(plan,catalog,recording)
                request.assert_not_called()
                self.assertFalse(recording.exists())
        for value in ({},None,{**self.plan["replay_recording"],"evaluation_type":"fresh_independent"}):
            with self.assertRaises(DataError):t.validate_plan({**self.plan,"replay_recording":value})

    def test_corrupt_record_after_footer_cannot_claim_complete_capture_or_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);plan,catalog,recording=self.paths(root)
            self.recording(recording,plan)
            with gzip.open(recording,"at",encoding="utf-8") as stream:
                stream.write('{}\n')
            with self.assertRaisesRegex(DataError,"records after capture end"):
                t.run_screen(plan,catalog,recording,root/"output")
            report=json.loads((root/"output/report.json").read_bytes())
            self.assertFalse(report["capture_complete"])
            self.assertFalse(report["universe_coverage_complete"])


if __name__ == "__main__":
    unittest.main()
