import unittest
import hashlib
import json
import tempfile
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from cryptoir.domain import DataError
from cryptoir.history import Candle, DAY, HOUR, audit_history, download_history, parse_udf, reconcile, volume_quality, verify_fiat_catalog, history_symbol
from cryptoir.streams import dumps


class HistoryTests(unittest.TestCase):
    def fixture(self, phase=0):
        start = DAY * 100 + phase
        hourly = [Candle(start+i*HOUR, 100, 102, 99, 101, 1) for i in range(48)]
        daily = [Candle(start+i*DAY, 100, 102, 99, 101, 24) for i in range(2)]
        return hourly, daily, start, start+2*DAY

    def test_reconciles_exactly(self):
        hourly, daily, start, end = self.fixture()
        result = reconcile(hourly, daily, start, end)
        self.assertTrue(result["reconciled"])
        self.assertEqual(len(result["bars"]), 48)

    def test_tehran_aligned_hours_do_not_shift_into_utc_hours(self):
        hourly, daily, start, end = self.fixture(73800)
        result = reconcile(hourly, daily, start, end)
        self.assertTrue(result["reconciled"])
        self.assertEqual(result["day_phase_utc_seconds"], 73800)
        self.assertEqual(result["bars"][0].time, start)

    def test_partial_edge_session_removed(self):
        hourly, daily, start, end = self.fixture()
        result = reconcile(hourly, daily, start+HOUR, end)
        self.assertTrue(result["reconciled"])
        self.assertEqual(len(result["bars"]), 24)
        self.assertEqual(result["excluded_edge_or_unaligned_bars"], 24)

    def test_missing_hour_is_not_forward_filled(self):
        hourly, daily, start, end = self.fixture()
        hourly.pop(10)
        result = reconcile(hourly, daily, start, end)
        self.assertFalse(result["reconciled"])
        self.assertEqual(result["gaps"][0]["missing_hours"], 1)

    def test_missing_daily_record_invalidates(self):
        hourly, daily, start, end = self.fixture()
        result = reconcile(hourly, daily[:1], start, end)
        self.assertFalse(result["reconciled"])
        self.assertEqual(result["missing_daily_epochs"], [start+DAY])

    def test_volume_difference_not_hidden_by_tolerance(self):
        hourly, daily, start, end = self.fixture()
        daily[0] = replace(daily[0], volume=D("24.0000000001"))
        result = reconcile(hourly, daily, start, end)
        self.assertFalse(result["reconciled"])
        self.assertEqual(D(result["table"][0]["deltas"]["volume"]), D("-0.0000000001"))

    def test_unaligned_interior_bar_is_not_silently_trimmed(self):
        hourly, daily, start, end = self.fixture()
        hourly.insert(1, replace(hourly[0], time=start+1))
        with self.assertRaisesRegex(DataError, "unaligned"):
            reconcile(hourly, daily, start, end)

    def test_invalid_ohlc_rejected(self):
        with self.assertRaises(DataError):
            Candle(100, 100, 99, 98, 101, 1)

    def test_udf_strict_order_and_equal_arrays(self):
        bad = {"s":"ok", "t":[2,1], "o":[1,1], "h":[1,1], "l":[1,1], "c":[1,1], "v":[0,0]}
        with self.assertRaises(DataError):
            parse_udf(bad)
        bad["t"] = [1]
        with self.assertRaises(DataError):
            parse_udf(bad)

    def test_no_data_does_not_become_zero_price(self):
        self.assertEqual(parse_udf({"s":"no_data"}), [])
        self.assertFalse(reconcile([], [], DAY, DAY*2)["reconciled"])

    def test_matching_zero_volumes_do_not_establish_volume_availability(self):
        hourly, daily, start, end = self.fixture()
        hourly = [replace(bar, volume=0) for bar in hourly]
        daily = [replace(bar, volume=0) for bar in daily]
        self.assertTrue(reconcile(hourly, daily, start, end)["reconciled"])
        quality = volume_quality(hourly)
        self.assertFalse(quality["usable_for_volume_screen"])
        self.assertEqual(quality["positive_volume_bars"], 0)
        self.assertEqual(quality["zero_volume_with_price_range_bars"], 48)
        self.assertEqual(quality["first_inconsistent_epoch"], start)
        self.assertEqual(quality["last_inconsistent_epoch"], end-HOUR)

    def test_flat_zero_volume_bar_does_not_invent_liquidity_or_poison_mixed_history(self):
        hourly, _, _, _ = self.fixture()
        hourly[0] = Candle(hourly[0].time, 100, 100, 100, 100, 0)
        quality = volume_quality(hourly)
        self.assertTrue(quality["usable_for_volume_screen"])
        self.assertEqual(quality["positive_volume_bars"], 47)
        self.assertEqual(quality["zero_volume_bars"], 1)
        self.assertFalse(quality["volume_units_verified"])

    def test_one_price_range_with_zero_volume_blocks_otherwise_positive_history(self):
        hourly, _, _, _ = self.fixture()
        hourly[5] = replace(hourly[5], volume=0)
        quality = volume_quality(hourly)
        self.assertFalse(quality["usable_for_volume_screen"])
        self.assertEqual(quality["blocking_reasons"], ["zero_volume_with_price_range"])

    def test_empty_and_flat_all_zero_history_have_no_positive_volume_evidence(self):
        self.assertEqual(volume_quality([])["blocking_reasons"], ["empty_history"])
        quality = volume_quality([Candle(HOUR, 100, 100, 100, 100, 0)])
        self.assertEqual(quality["blocking_reasons"], ["no_positive_reported_volume"])
        self.assertFalse(quality["usable_for_volume_screen"])

    def make_dataset(self, directory, venue="nobitex", pair="BTC/USDT", unit_catalog=None):
        hourly, daily, start, end = self.fixture()

        def response(url):
            rows = hourly if parse_qs(urlparse(url).query)["resolution"] == ["60"] else daily
            payload = {"s": "ok", **{key: [getattr(row, field) for row in rows]
                       for key, field in zip(("t", "o", "h", "l", "c", "v"), ("time", "open", "high", "low", "close", "volume"))}}
            body = dumps(payload)
            return {"kind": "snapshot", "url": url, "status_code": 200, "payload": payload,
                    "body_sha256": hashlib.sha256(body.encode()).hexdigest(), "raw_body": body}

        with patch("cryptoir.history.public_response", side_effect=response), patch("cryptoir.history.time.sleep"):
            download_history(directory, venue, pair, start, end, unit_catalog=unit_catalog)

    def unit_catalog(self, **changes):
        from cryptoir.catalog import ENDPOINTS
        market={"symbol":"USDTTMN","base_asset":"USDT","quote_asset":"TMN","en_quote_asset":"Toman",
                "fa_quote_asset":"تومان","is_spot":True,"is_tmn_based":True,**changes}
        payload={"success":True,"result":{"markets":[market]}};body=dumps(payload)
        return dumps({"schema":1,"kind":"snapshot","status_code":200,"url":ENDPOINTS["wallex"],
                      "payload":payload,"raw_body":body,"body_sha256":hashlib.sha256(body.encode()).hexdigest()}).encode()

    def test_fiat_mapping_is_scoped_to_source_declared_toman(self):
        self.assertEqual(history_symbol("wallex","USDT/IRT"),"USDTTMN")
        self.assertEqual(verify_fiat_catalog(self.unit_catalog())["raw_price_multiplier"],"1")
        for venue,pair in (("nobitex","USDT/IRT"),("nobitex","USDT/IRR"),("wallex","BTC/IRT"),("wallex","USDT/TMN")):
            with self.assertRaises(DataError):history_symbol(venue,pair)
        for change in ({"quote_asset":"IRR"},{"en_quote_asset":"Rial"},{"base_asset":"BTC"},{"is_spot":False},{"is_tmn_based":False}):
            with self.assertRaises(DataError):verify_fiat_catalog(self.unit_catalog(**change))

    def test_fiat_history_preserves_raw_prices_and_reconstructs_unit_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);catalog=root/"catalog.json";catalog.write_bytes(self.unit_catalog())
            directory=root/"history";self.make_dataset(directory,"wallex","USDT/IRT",catalog)
            report,bars=audit_history(directory)
            self.assertTrue(report["reconciled"])
            self.assertEqual(bars[0].open,100)
            self.assertEqual(report["unit_identity"]["quote_unit"],"IRT")
            raw=json.loads((directory/"raw-0000.json").read_bytes())
            self.assertEqual(parse_qs(urlparse(raw["url"]).query)["symbol"],["USDTTMN"])
            path=directory/"manifest.json";manifest=json.loads(path.read_bytes())
            manifest["unit_identity"]["raw_price_multiplier"]="10";path.write_text(dumps(manifest),encoding="utf-8")
            with self.assertRaisesRegex(DataError,"unit evidence mismatch"):audit_history(directory)

    def test_absent_unit_evidence_fails_before_network_or_output_creation(self):
        with tempfile.TemporaryDirectory() as tmp,patch("cryptoir.history.public_response") as fetch:
            output=Path(tmp)/"history"
            with self.assertRaisesRegex(DataError,"unit metadata"):
                download_history(output,"wallex","USDT/IRT",DAY,DAY*3)
            self.assertFalse(output.exists());fetch.assert_not_called()

    def test_unit_metadata_raw_tampering_and_duplicate_market_fail(self):
        record=json.loads(self.unit_catalog());record["payload"]["result"]["markets"][0]["quote_asset"]="IRR"
        with self.assertRaisesRegex(DataError,"raw response"):verify_fiat_catalog(dumps(record).encode())
        record=json.loads(self.unit_catalog());record["payload"]["result"]["markets"]*=2
        record["raw_body"]=dumps(record["payload"]);record["body_sha256"]=hashlib.sha256(record["raw_body"].encode()).hexdigest()
        with self.assertRaisesRegex(DataError,"unique"):verify_fiat_catalog(dumps(record).encode())

    def test_source_replay_audit_and_fabricated_normalized_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "history"
            self.make_dataset(directory)
            report, bars = audit_history(directory)
            self.assertTrue(report["reconciled"])
            self.assertTrue(report["volume_quality"]["usable_for_volume_screen"])
            self.assertEqual(len(bars), 48)
            # Even if someone repairs a normalized hash, it must match raw bodies.
            path = directory / "hourly.jsonl"
            changed = path.read_bytes().replace(b'"volume":"1"', b'"volume":"9"')
            path.write_bytes(changed)
            manifest = json.loads((directory / "manifest.json").read_bytes())
            manifest["normalized_sha256"] = hashlib.sha256(changed).hexdigest()
            (directory / "manifest.json").write_text(dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(DataError, "differs from original"):
                audit_history(directory)

    def test_raw_file_corruption_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "history"
            self.make_dataset(directory)
            path = directory / "raw-0000.json"
            path.write_bytes(path.read_bytes()+b" ")
            with self.assertRaisesRegex(DataError, "raw file hash"):
                audit_history(directory)

    def test_manifest_cannot_claim_unreconciled_data_is_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "history"
            self.make_dataset(directory)
            path = directory / "manifest.json"
            manifest = json.loads(path.read_bytes())
            manifest["bars"] = 1000
            path.write_text(dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(DataError, "disagrees"):
                audit_history(directory)


if __name__ == "__main__":
    unittest.main()
