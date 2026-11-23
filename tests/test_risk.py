import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch

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

    def test_equivalent_book_does_not_refresh_mark_or_rearm_feed(self):
        guard = self.guard()
        initial = book()
        self.assertTrue(guard.process_book(initial)[0])
        repeated = replace(book(now=1_100_000_000, bid="99.00", ask="100.00", volume="100.0"),
                           exchange_ns=initial.exchange_ns)
        self.assertEqual(guard.process_book(repeated), (False, []))
        self.assertEqual(guard.state()["mark_ns"], initial.received_ns)
        guard.invalidate(now_ns=1_110_000_000)
        self.assertEqual(guard.process_book(repeated, now_ns=1_120_000_000), (False, []))
        self.assertFalse(guard.state()["data_ok"])
        self.assertTrue(guard.process_book(book(now=1_130_000_000))[0])
        self.assertTrue(guard.state()["data_ok"])

    def test_regressed_or_conflicting_source_cancels_buy_and_blocks_data(self):
        for source_delta, reason in ((-1, "book_source_time_regressed"), (0, "book_source_time_conflict")):
            guard = self.guard()
            now = 1_000_000_000 if source_delta == -1 else 2_000_000_000
            original = book(now=now)
            identity = str(now)
            self.submit(guard, identity, snapshot=original)
            old = replace(book(now=now+200_000_000, bid=98), exchange_ns=original.exchange_ns+source_delta)
            self.assertEqual(guard.process_book(old), (False, []))
            self.assertEqual(self.ledger.order(identity)["status"], "CANCELED")
            self.assertEqual(guard.state()["data_reason"], reason)
            self.assertEqual(self.ledger.balance("test", "USDT"), (D(1000), D(0)))

    def test_feed_timer_uses_exchange_time_instead_of_recent_processing_time(self):
        guard = self.guard()
        aged = replace(book(), exchange_ns=100_000_000)
        self.submit(guard, snapshot=aged)
        guard.advance(1_110_000_000)
        self.assertFalse(guard.state()["data_ok"])
        self.assertEqual(self.ledger.order("one")["status"], "CANCELED")

    def test_postfill_fault_rolls_back_fill_mark_source_and_journal_together(self):
        guard = self.guard()
        self.submit(guard)
        account_before = self.ledger.report()
        risk_before = guard.state()
        pending_before = self.ledger.order("one")
        next_book = book(now=1_200_000_000)
        mark = guard._mark
        def fail_after_fill(snapshot, now):
            if self.ledger.order("one")["status"] == "FILLED":
                raise RuntimeError("post-fill mark fault")
            return mark(snapshot, now)
        with patch.object(guard, "_mark", side_effect=fail_after_fill):
            with self.assertRaisesRegex(RuntimeError, "post-fill mark fault"):
                guard.on_book(next_book)
        self.assertEqual(self.ledger.report(), account_before)
        self.assertEqual(guard.state(), risk_before)
        self.assertEqual(self.ledger.order("one"), pending_before)
        self.assertEqual(guard.on_book(next_book)[0]["status"], "FILLED")
        self.assertEqual(guard.on_book(next_book), [])

    def test_process_death_before_commit_preserves_pending_order_on_reopen(self):
        guard = self.guard()
        self.submit(guard)
        before = self.ledger.report(), guard.state(), self.ledger.order("one")
        child = """
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
from test_risk import PaperLedger, SpotRiskGuard, RULES, LIMITS, book

ledger = PaperLedger(Path(sys.argv[1]))
guard = SpotRiskGuard(ledger, 'test', 'BTC/USDT', RULES, LIMITS)
mark = guard._mark
def terminate_after_fill(snapshot, now):
    if ledger.order('one')['status'] == 'FILLED':
        os._exit(73)
    return mark(snapshot, now)
guard._mark = terminate_after_fill
guard.on_book(book(now=1_200_000_000))
"""
        result = subprocess.run([sys.executable, "-c", child, str(self.path),
                                 str(Path(__file__).resolve().parent)], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 73, result.stderr.decode(errors="replace"))
        self.ledger.close()
        self.ledger = PaperLedger(self.path)
        guard = self.guard()
        self.assertEqual((self.ledger.report(), guard.state(), self.ledger.order("one")), before)
        self.assertEqual(self.fill(guard)[0]["status"], "FILLED")
        self.assertEqual(self.ledger.db.execute("SELECT count(*) FROM journal WHERE event='ioc_result'").fetchone()[0], 1)

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
