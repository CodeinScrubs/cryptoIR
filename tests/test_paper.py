import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch

from cryptoir.domain import Book, DataError, Level
from cryptoir.paper import PaperLedger, Rules, demo


RULES = Rules(D("0.01"), D("0.01"), D(1), D(500), D("0.01"))


def snapshot(now=2_000_000_000, source=1_900_000_000, volume=D(1), venue="a"):
    return Book(venue, "BTC/USDT", (Level(99, volume),), (Level(100, volume),), now, 100, source)


class PaperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "paper.sqlite"
        self.ledger = PaperLedger(self.path)
        self.ledger.seed({("a", "USDT"): D(1000), ("a", "BTC"): D(2), ("b", "USDT"): D(1000)})

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def submit(self, order_id="one", side="buy", quantity=D(1), price=D(101), **kwargs):
        return self.ledger.submit(order_id, "a", "BTC/USDT", side, quantity, price, RULES,
                                  now_ns=kwargs.get("now_ns", 1_000_000_000),
                                  latency_ns=100_000_000, ttl_ns=3_000_000_000)

    def fill(self, book=None, participation=D(1)):
        return self.ledger.on_book(book or snapshot(), max_age_ns=1_000_000_000, participation=participation)

    def test_reserve_prevents_double_spending(self):
        self.submit(quantity=D(4), price=D(100))
        self.submit("two", quantity=D(4), price=D(100))
        self.assertEqual(self.ledger.balance("a", "USDT"), (D(192), D(808)))
        with self.assertRaises(DataError):
            self.submit("three", quantity=D(4), price=D(100))
        self.assertEqual(self.ledger.balance("a", "USDT"), (D(192), D(808)))

    def test_finite_order_limit_and_market_precision(self):
        for qty, price in ((D(6), D(100)), (D("0.001"), D(100)), (D(1), D("100.001")), (D("0.01"), D(1))):
            with self.assertRaises(DataError):
                self.submit(quantity=qty, price=price)

    def test_cannot_sell_unowned_asset(self):
        with self.assertRaises(DataError):
            self.submit(side="sell", quantity=D(3), price=D(99))

    def test_idempotent_intent_and_conflict(self):
        self.submit()
        before = self.ledger.report()
        self.submit()
        self.assertEqual(before, self.ledger.report())
        with self.assertRaises(DataError):
            self.submit(price=D(100))

    def test_same_decision_snapshot_cannot_fill(self):
        self.submit()
        self.assertEqual(self.fill(snapshot(now=1_000_000_000, source=1_000_000_000)), [])
        self.assertEqual(self.ledger.order("one")["status"], "PENDING")

    def test_receive_time_alone_does_not_bypass_latency(self):
        self.submit()
        self.assertEqual(self.fill(snapshot(source=1_050_000_000)), [])

    def test_unknown_source_time_rejected(self):
        self.submit()
        with self.assertRaises(DataError):
            self.fill(snapshot(source=None))
        self.assertEqual(self.ledger.order("one")["status"], "PENDING")

    def test_buy_fees_and_price_improvement(self):
        self.submit()
        self.fill()
        self.assertEqual(self.ledger.balance("a", "USDT"), (D(899), D(0)))
        self.assertEqual(self.ledger.balance("a", "BTC"), (D(3), D(0)))
        self.assertEqual(D(self.ledger.order("one")["fee"]), D(1))

    def test_sell_fee_and_asset_conservation(self):
        self.submit(side="sell", price=D(98))
        self.fill()
        self.assertEqual(self.ledger.balance("a", "USDT"), (D("1098.01"), D(0)))
        self.assertEqual(self.ledger.balance("a", "BTC"), (D(1), D(0)))

    def test_partial_fill_cancels_remainder_and_releases_reserve(self):
        self.submit()
        self.fill(participation=D("0.25"))
        self.assertEqual(self.ledger.order("one")["status"], "PARTIAL_CANCELED")
        self.assertEqual(self.ledger.balance("a", "USDT"), (D("974.75"), D(0)))
        self.assertEqual(self.ledger.balance("a", "BTC"), (D("2.25"), D(0)))

    def test_two_orders_share_depth_fifo(self):
        self.submit()
        self.submit("two")
        self.fill()
        self.assertEqual(self.ledger.order("one")["status"], "FILLED")
        self.assertEqual(self.ledger.order("two")["status"], "CANCELED")

    def test_nonmarketable_order_does_not_touch_balance(self):
        self.submit(price=D(90))
        self.fill()
        self.assertEqual(self.ledger.order("one")["status"], "CANCELED")
        self.assertEqual(self.ledger.balance("a", "USDT"), (D(1000), D(0)))

    def test_expiry_without_market_feed(self):
        self.submit()
        self.ledger.advance(4_000_000_000)
        self.assertEqual(self.ledger.order("one")["status"], "EXPIRED")
        self.assertEqual(self.ledger.balance("a", "USDT"), (D(1000), D(0)))

    def test_cancel_is_idempotent(self):
        self.submit()
        self.ledger.cancel("one")
        self.ledger.cancel("one")
        self.assertEqual(self.ledger.balance("a", "USDT"), (D(1000), D(0)))

    def test_seed_is_one_time_and_transactional(self):
        before = self.ledger.report()
        with self.assertRaises(DataError):
            self.ledger.seed({("a", "USDT"): D(1000)})
        self.assertEqual(before, self.ledger.report())

    def test_restart_preserves_reservation(self):
        self.submit()
        self.ledger.close()
        self.ledger = PaperLedger(self.path)
        self.assertEqual(self.ledger.balance("a", "USDT"), (D("897.99"), D("102.01")))
        self.fill()
        self.assertEqual(self.ledger.balance("a", "USDT"), (D(899), D(0)))

    def test_snapshot_cannot_be_reconsumed_after_restart(self):
        self.submit()
        self.fill()
        self.ledger.close()
        self.ledger = PaperLedger(self.path)
        self.submit("two", now_ns=2_000_000_000)
        self.assertEqual(self.fill(replace(snapshot(), received_ns=2_200_000_000)), [])
        self.assertEqual(self.ledger.order("two")["status"], "PENDING")

    def test_venue_isolation(self):
        self.submit()
        self.assertEqual(self.fill(snapshot(venue="b")), [])
        self.assertEqual(self.ledger.order("one")["status"], "PENDING")

    def test_no_clock_regression_after_submit(self):
        self.submit(now_ns=2_000_000_000)
        with self.assertRaises(DataError):
            self.ledger.advance(1_000_000_000)

    def test_atomic_fill_rollback_on_journal_failure(self):
        self.submit()
        before = self.ledger.report()
        with patch.object(self.ledger, "_event", side_effect=RuntimeError("disk fault")):
            with self.assertRaises(RuntimeError):
                self.fill()
        self.assertEqual(before, self.ledger.report())
        self.fill()
        self.assertEqual(self.ledger.order("one")["status"], "FILLED")

    def test_step_rounding_never_overfills(self):
        self.submit()
        self.fill(snapshot(volume=D("0.257")))
        self.assertEqual(D(self.ledger.order("one")["filled"]), D("0.25"))

    def test_stale_book_does_not_fill(self):
        self.submit()
        with self.assertRaises(DataError):
            self.fill(snapshot(now=3_000_000_000, source=1_000_000_000))

    def test_demo_is_synthetic_and_refuses_overwrite(self):
        target = Path(self.temp.name) / "demo.sqlite"
        result = demo(target)
        self.assertEqual(result["input"], "synthetic_fixture_not_market_data")
        self.assertFalse(result["profitability_established"])
        with self.assertRaises(FileExistsError):
            demo(target)


if __name__ == "__main__":
    unittest.main()
