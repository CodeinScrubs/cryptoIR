import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path

from cryptoir.domain import Book, DataError, Level
from cryptoir.paper import PaperLedger, Rules
from cryptoir.risk import RiskLimits, SpotRiskGuard, demo

RULES = Rules(D("0.01"), D("0.01"), D(1), D(500), D("0.001"), "received_base")
LIMITS = RiskLimits(D(1000), D("0.25"), D("0.02"), D("0.05"), D(200), 2, 1_000_000_000, D(1))


def book(now=1_000_000_000, bid=99, ask=100, volume=100, source=True):
    return Book("test", "BTC/USDT", (Level(bid, volume),), (Level(ask, volume),), now, 0,
                now-10_000_000 if source else None)


class RiskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/"risk.sqlite"
        self.ledger = PaperLedger(self.path)
        self.ledger.seed({("test", "USDT"): D(1000)})

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def guard(self, limits=LIMITS):
        return SpotRiskGuard(self.ledger, "test", "BTC/USDT", RULES, limits)

    def submit(self, guard, identity="one", qty=1, price=100, side="buy", snapshot=None):
        snapshot = snapshot or book()
        return guard.submit(identity, side, D(qty), D(price), book=snapshot, now_ns=snapshot.received_ns,
                            latency_ns=100_000_000, ttl_ns=3_000_000_000)

    def fill(self, guard):
        return guard.on_book(book(now=1_200_000_000))

    def test_explicit_finite_limits_required(self):
        for field,value in (("daily_loss_fraction",0),("initial_quote","Infinity"),("maximum_open_orders",True),("depth_participation",0)):
            with self.assertRaises(DataError):
                replace(LIMITS, **{field:value})

    def test_capital_mismatch_and_configuration_change_refused(self):
        with self.assertRaises(DataError):
            self.guard(replace(LIMITS, initial_quote=2000))
        self.guard()
        with self.assertRaises(DataError):
            self.guard(replace(LIMITS, maximum_open_orders=3))

    def test_order_cap_denial_reserves_nothing(self):
        guard = self.guard()
        with self.assertRaisesRegex(DataError, "maximum_order_notional"):
            self.submit(guard, qty=3)
        self.assertEqual(self.ledger.balance("test","USDT"),(D(1000),D(0)))
        self.assertEqual(self.ledger.report()["orders"], [])

    def test_pending_orders_share_exposure_budget(self):
        guard = self.guard()
        self.submit(guard, qty=2)
        with self.assertRaisesRegex(DataError, "maximum_exposure"):
            self.submit(guard,"two",qty=1)
        self.assertEqual(self.ledger.balance("test","USDT"),(D(800),D(200)))

    def test_open_order_limit(self):
        guard = self.guard(replace(LIMITS,maximum_open_orders=1))
        self.submit(guard)
        with self.assertRaisesRegex(DataError, "maximum_open_orders"):
            self.submit(guard,"two")

    def test_gap_cancels_pending_buys_and_releases_funds(self):
        guard = self.guard()
        self.submit(guard)
        state = guard.invalidate(now_ns=1_050_000_000)
        self.assertFalse(state["data_ok"])
        self.assertEqual(self.ledger.order("one")["status"],"CANCELED")
        self.assertEqual(self.ledger.balance("test","USDT"),(D(1000),D(0)))
        self.assertEqual(self.fill(guard),[])

    def test_unknown_and_old_source_cannot_fill(self):
        guard = self.guard()
        self.submit(guard)
        self.assertEqual(guard.on_book(book(now=1_200_000_000, source=False)),[])
        self.assertEqual(self.ledger.order("one")["status"],"CANCELED")
        state=guard.observe(book(now=1_300_000_000),now_ns=3_000_000_000)
        self.assertFalse(state["data_ok"])

    def test_feed_timer_cancels_buy_before_order_ttl(self):
        guard = self.guard()
        self.submit(guard)
        guard.advance(2_100_000_000)
        self.assertEqual(self.ledger.order("one")["status"],"CANCELED")
        self.assertFalse(guard.state()["data_ok"])

    def test_daily_loss_latch_survives_restart_and_recovery(self):
        guard=self.guard()
        self.submit(guard)
        self.fill(guard)
        state=guard.observe(book(now=2_000_000_000,bid=70,ask=71),now_ns=2_000_000_000)
        self.assertEqual(state["halt"],"daily_loss")
        self.ledger.close()
        self.ledger=PaperLedger(self.path)
        guard=self.guard()
        recovered=book(now=3_000_000_000,bid=100,ask=101)
        with self.assertRaisesRegex(DataError,"latched_daily_loss"):
            self.submit(guard,"reentry",snapshot=recovered)
        self.assertEqual(guard.state()["halt"],"daily_loss")

    def test_drawdown_latch_with_looser_daily_limit(self):
        guard=self.guard(replace(LIMITS,daily_loss_fraction="0.9",drawdown_fraction="0.01"))
        self.submit(guard)
        self.fill(guard)
        state=guard.observe(book(now=2_000_000_000,bid=80,ask=81),now_ns=2_000_000_000)
        self.assertEqual(state["halt"],"drawdown")

    def test_loss_halt_does_not_block_owned_reduce_order(self):
        guard=self.guard()
        self.submit(guard)
        self.fill(guard)
        low=book(now=2_000_000_000,bid=70,ask=71)
        guard.observe(low,now_ns=low.received_ns)
        self.submit(guard,"exit",qty="0.99",price=70,side="sell",snapshot=low)
        result=guard.on_book(book(now=2_200_000_000,bid=70,ask=71))
        self.assertEqual(result[0]["side"],"sell")
        self.assertEqual(result[0]["status"],"FILLED")

    def test_risk_denial_commits_halt_atomically(self):
        guard=self.guard()
        self.submit(guard)
        self.fill(guard)
        with self.assertRaisesRegex(DataError,"latched_daily_loss"):
            self.submit(guard,"bad",snapshot=book(now=2_000_000_000,bid=70,ask=71))
        self.assertEqual(guard.state()["halt"],"daily_loss")
        self.assertEqual(len(self.ledger.report()["orders"]),1)

    def test_unknown_inventory_blocks_new_exposure(self):
        guard=self.guard()
        with self.ledger.db:
            self.ledger._adjust("another","ETH",free=D(1))
        with self.assertRaisesRegex(DataError,"unvalued inventory"):
            self.submit(guard)

    def test_insufficient_exit_depth_blocks_valuation(self):
        guard=self.guard()
        self.submit(guard)
        self.fill(guard)
        state=guard.observe(book(now=2_000_000_000,volume="0.01"),now_ns=2_000_000_000)
        self.assertFalse(state["data_ok"])
        self.assertIn("insufficient displayed depth",state["data_reason"])

    def test_manual_stop_does_not_invent_a_position_exit(self):
        guard=self.guard()
        self.submit(guard)
        self.fill(guard)
        before=self.ledger.balance("test","BTC")
        guard.halt(now_ns=2_000_000_000)
        self.assertEqual(self.ledger.balance("test","BTC"),before)
        self.assertEqual(guard.state()["halt"],"manual_stop")

    def test_day_rollover_does_not_hide_unobserved_overnight_loss(self):
        guard=self.guard()
        self.submit(guard)
        self.fill(guard)
        next_day=86_400_000_000_000
        state=guard.observe(book(now=next_day,bid=70,ask=71),now_ns=next_day)
        self.assertEqual(state["halt"],"daily_loss")

    def test_pending_buy_rechecked_before_fill(self):
        guard=self.guard()
        self.submit(guard,qty=2)
        guard.on_book(book(now=1_200_000_000,bid=199,ask=200))
        self.assertEqual(self.ledger.order("one")["status"],"CANCELED")
        self.assertEqual(self.ledger.balance("test","USDT"),(D(1000),D(0)))

    def test_synthetic_restart_demo_and_exclusive_output(self):
        path=Path(self.temp.name)/"demo.sqlite"
        result=demo(path)
        self.assertTrue(result["restart_verified"])
        self.assertFalse(result["profitability_established"])
        self.assertEqual(result["risk"]["halt"],"daily_loss")
        self.assertEqual(len(result["orders"]),1)
        with self.assertRaises(FileExistsError):
            demo(path)


if __name__ == "__main__":
    unittest.main()
