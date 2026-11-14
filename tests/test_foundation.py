import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch

from cryptoir.__main__ import audit, encode, estimates
from cryptoir.domain import Book, DataError, Level, estimate_spread, number, toman, walk
from cryptoir.feeds import endpoint, fetch, normalize


def book(venue="nobitex", now=10_000_000_000):
    return Book(venue, "BTC/USDT", (Level(D("100"), D("2")),),
                (Level(D("101"), D("1")), Level(D("103"), D("2"))), now, 100)


def record(venue="nobitex", now=10_000_000_000):
    if venue == "nobitex":
        payload = {"status": "ok", "lastUpdate": now // 1_000_000,
                   "bids": [["100", "2"]], "asks": [["101", "2"]]}
    elif venue == "wallex":
        payload = {"success": True, "result": {"bid": [{"price": "102", "quantity": "2"}],
                                               "ask": [{"price": "103", "quantity": "2"}]}}
    else:
        payload = {"bids": [["100", "2"]], "asks": [["101", "2"]]}
    raw = json.dumps(payload)
    return {"schema": 1, "kind": "snapshot", "venue": venue, "pair": "BTC/USDT",
            "url": endpoint(venue, "BTC/USDT"), "payload": payload,
            "received_ns": now, "duration_ns": 100, "raw_body": raw,
            "body_sha256": hashlib.sha256(raw.encode()).hexdigest()}


class MoneyTests(unittest.TestCase):
    def test_decimal_and_explicit_units(self):
        self.assertEqual(number("0.1") + number("0.2"), D("0.3"))
        self.assertEqual(toman("100", "IRR"), D("10"))
        self.assertEqual(toman("100", "IRT"), D("100"))
        with self.assertRaises(DataError):
            toman("100", "USDT")

    def test_bad_numbers_rejected(self):
        for value in (0.1, True, None, "NaN", "Infinity", "-Infinity", "x"):
            with self.subTest(value=value), self.assertRaises(DataError):
                number(value)

    def test_invalid_levels(self):
        for price, qty in ((0, 1), (1, 0), (-1, 1), (1, -1)):
            with self.assertRaises(DataError):
                Level(price, qty)


class BookTests(unittest.TestCase):
    def test_duplicate_or_unordered_levels_rejected(self):
        base = book()
        for asks in ((base.asks[0], base.asks[0]), tuple(reversed(base.asks))):
            with self.assertRaises(DataError):
                replace(base, asks=asks)

    def test_empty_locked_crossed(self):
        for bids in ((), (Level(101, 1),), (Level(102, 1),)):
            with self.assertRaises(DataError):
                replace(book(), bids=bids)

    def test_clock_and_staleness(self):
        for modified, now in ((book(), 9_000_000_000), (book(), 16_000_000_000),
                              (replace(book(), request_duration_ns=6_000_000_000), 10_000_000_000),
                              (replace(book(), exchange_ns=1), 10_000_000_000),
                              (replace(book(), exchange_ns=11_000_000_000), 10_000_000_000)):
            with self.assertRaises(DataError):
                modified.validate_age(now, 5_000_000_000)

    def test_depth_weighting_and_partial_quote(self):
        quote = walk(book().asks, D("2"))
        self.assertEqual(quote.notional, D("204"))
        partial = walk(book().asks, D("4"))
        self.assertEqual(partial.filled, D("3"))
        for quantity in (D(0), D(-1)):
            with self.assertRaises(DataError):
                walk(book().asks, quantity)

    def test_costs_can_erase_positive_gross_spread(self):
        sell = replace(book("wallex"), bids=(Level(102, 5),), asks=(Level(103, 5),))
        result = estimate_spread(book(), sell, D(1), buy_fee=D("0.01"),
                                 sell_fee=D("0.01"), reserve_bps=D(10),
                                 now_ns=10_000_000_000, max_age_ns=5_000_000_000)
        self.assertEqual(D(result["estimated_net"]), D("-1.233"))
        self.assertFalse(result["executable"])

    def test_insufficient_liquidity_never_assumed_filled(self):
        with self.assertRaises(DataError):
            estimate_spread(book(), book("wallex"), D(4), buy_fee=D(0), sell_fee=D(0),
                            reserve_bps=D(0), now_ns=10_000_000_000, max_age_ns=5_000_000_000)

    def test_wrong_market_or_same_venue(self):
        for sell in (book(), replace(book("wallex"), pair="ETH/USDT")):
            with self.assertRaises(DataError):
                estimate_spread(book(), sell, D(1), buy_fee=D(0), sell_fee=D(0),
                                reserve_bps=D(0), now_ns=10_000_000_000, max_age_ns=5_000_000_000)


class FeedTests(unittest.TestCase):
    def test_all_three_schemas(self):
        for venue in ("nobitex", "wallex", "tabdeal"):
            self.assertEqual(normalize(record(venue)).venue, venue)

    def test_unknown_time_is_not_fabricated(self):
        self.assertIsNone(normalize(record("tabdeal")).exchange_ns)

    def test_symbol_cannot_inject_request_parameters(self):
        for pair in ("BTC/USDT?token=x", "BTC/IRT", "btc/USDT", "USDT/USDT"):
            with self.assertRaises(DataError):
                endpoint("wallex", pair)

    def test_unknown_venue_or_endpoint_rejected(self):
        with self.assertRaises(DataError):
            endpoint("versland", "BTC/USDT")
        bad = record()
        bad["url"] = "https://example.org"
        with self.assertRaises(DataError):
            normalize(bad)

    def test_rejected_exchange_response(self):
        bad = record("wallex")
        bad["payload"]["success"] = False
        with self.assertRaises(DataError):
            normalize(bad)

    @patch("cryptoir.feeds.build_opener")
    def test_network_failure_is_an_error_event(self, opener):
        opener.return_value.open.side_effect = TimeoutError("sensitive diagnostic")
        event = fetch("wallex", "BTC/USDT")
        self.assertEqual(event["kind"], "error")
        self.assertNotIn("sensitive diagnostic", encode(event))


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "sample.jsonl"

    def tearDown(self):
        self.temp.cleanup()

    def write(self, rows):
        self.path.write_text("".join(encode(row) + "\n" for row in rows), encoding="utf-8")

    def test_valid_data_is_not_execution_evidence(self):
        self.write([record(), record("wallex")])
        result = audit(self.path)
        self.assertEqual(result["counts"]["valid_snapshots"], 2)
        self.assertFalse(result["execution_ready"])
        self.assertFalse(result["historically_reconciled"])

    def test_corruption_and_duplicate_observation(self):
        bad = record()
        bad["raw_body"] += " "
        self.write([record(), record(), bad])
        self.assertEqual(audit(self.path)["counts"]["invalid_records"], 2)

    def test_truncated_last_line(self):
        self.path.write_text('{"schema":', encoding="utf-8")
        self.assertEqual(audit(self.path)["counts"]["invalid_records"], 1)

    def test_invalid_event_shapes(self):
        self.write([None, [], {"kind": "error"}])
        self.assertEqual(audit(self.path)["counts"]["invalid_records"], 3)

    def test_no_future_snapshot_used(self):
        self.write([record()])
        kwargs = dict(quantity=D(1), fee=D("0.004"), reserve_bps=D(10), max_age_ns=5_000_000_000)
        self.assertEqual(list(estimates(self.path, **kwargs)), [])
        self.write([record(), record("wallex", 11_000_000_000)])
        self.assertEqual(len(list(estimates(self.path, **kwargs))), 2)

    def test_feed_error_invalidates_old_book(self):
        failure = {"schema": 1, "kind": "error", "venue": "nobitex", "pair": "BTC/USDT", "received_ns": 11_000_000_000}
        self.write([record(), failure, record("wallex", 12_000_000_000)])
        self.assertEqual(list(estimates(self.path, quantity=D(1), fee=D(0), reserve_bps=D(0), max_age_ns=5_000_000_000)), [])

    def test_out_of_order_across_venues_rejected(self):
        self.write([record("wallex", 11_000_000_000), record()])
        with self.assertRaises(DataError):
            list(estimates(self.path, quantity=D(1), fee=D(0), reserve_bps=D(0), max_age_ns=5_000_000_000))


if __name__ == "__main__":
    unittest.main()
