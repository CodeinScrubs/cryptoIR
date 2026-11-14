import unittest
from dataclasses import replace
from decimal import Decimal as D

from cryptoir.backtest import Assumptions, Signal, screen
from cryptoir.domain import DataError
from cryptoir.history import Candle, HOUR


class BacktestTests(unittest.TestCase):
    def setUp(self):
        self.start = HOUR * 1000
        self.bars = [Candle(self.start+i*HOUR, 100, 100, 100, 100, 100) for i in range(200)]
        self.cfg = Assumptions(1000, "0.25", "0.004", 15, "0.01", "0.000001", 1)

    def run_screen(self, bars=None, cfg=None, name="hold"):
        rows = bars if bars is not None else self.bars
        return screen(rows, name, cfg or self.cfg, rows[0].time, rows[-1].time+HOUR)

    def test_cash_does_not_invent_return(self):
        report = self.run_screen(name="cash")
        self.assertEqual(report["net_return"], 0)
        self.assertEqual(report["trade_count"], 0)

    def test_constant_price_loses_both_legs_costs_without_fabricated_exit(self):
        report = self.run_screen()
        self.assertLess(report["net_return"], 0)
        self.assertEqual(report["trade_count"], 1)
        self.assertGreater(report["base_quantity"], 0)
        self.assertEqual(report["realized_pnl"], 0)
        self.assertEqual(report["trades"][0]["side"], "buy")
        self.assertGreater(report["paid_fees"], 0)

    def test_execution_strictly_after_decision(self):
        trade = self.run_screen()["trades"][0]
        self.assertEqual(trade["execution_time"]-trade["decision_time"], HOUR)
        self.assertEqual(trade["execution_time"], self.start+2*HOUR)

    def test_late_price_jump_is_paid_not_filled_at_signal_price(self):
        bars = list(self.bars)
        bars[2] = Candle(bars[2].time, 200, 200, 200, 200, 100)
        trade = self.run_screen(bars)["trades"][0]
        self.assertEqual(trade["price"], D("200.3000"))
        self.assertLessEqual(trade["quantity"] * trade["price"] * (1+self.cfg.fee), 250)

    def test_current_bar_volume_does_not_determine_fill_capacity(self):
        bars = list(self.bars)
        bars[2] = replace(bars[2], volume=D("0"))
        self.assertEqual(self.run_screen(bars)["trades"][0], self.run_screen()["trades"][0])
        bars[1] = replace(bars[1], volume=D("0"))
        report = self.run_screen(bars)
        self.assertGreater(report["rejected_orders"], 0)
        self.assertGreater(report["trades"][0]["execution_time"], bars[2].time)

    def test_future_mutations_do_not_change_earlier_trades(self):
        original = self.run_screen()
        bars = [bar if i < 100 else Candle(bar.time, 1000, 1000, 1000, 1000, 0)
                for i, bar in enumerate(self.bars)]
        changed = self.run_screen(bars)
        self.assertEqual(original["trades"], changed["trades"])
        self.assertEqual(original["equity"][:100], changed["equity"][:100])

    def test_future_outside_interval_does_not_change_report(self):
        end = self.bars[100].time
        short = screen(self.bars[:100], "hold", self.cfg, self.start, end)
        full = screen(self.bars, "hold", self.cfg, self.start, end)
        self.assertEqual(short, full)

    def test_missing_hour_rejected_instead_of_forward_filled(self):
        with self.assertRaises(DataError):
            self.run_screen(self.bars[:50]+self.bars[51:])

    def test_minimum_order_and_quantity_rounding_are_enforced(self):
        report = self.run_screen(cfg=replace(self.cfg, minimum_notional=10000))
        self.assertEqual(report["trade_count"], 0)
        self.assertGreater(report["rejected_orders"], 0)
        trade = self.run_screen(cfg=replace(self.cfg, quantity_step=D("0.3")))["trades"][0]
        self.assertEqual(trade["quantity"] % D("0.3"), 0)

    def test_no_negative_cash_at_full_allocation(self):
        report = self.run_screen(cfg=replace(self.cfg, allocation=1, prior_volume_fraction=1))
        self.assertGreaterEqual(report["cash"], 0)
        self.assertLessEqual(report["base_quantity"] * report["trades"][0]["price"], 1000)

    def test_both_cost_scenarios_lose_on_flat_price(self):
        base = self.run_screen()["net_return"]
        stress = self.run_screen(cfg=replace(self.cfg, fee="0.008", adverse_bps=30))["net_return"]
        self.assertLess(stress, base)

    def test_unknown_strategy_and_zero_delay_rejected(self):
        with self.assertRaises(DataError):
            Signal("future_oracle")
        with self.assertRaises(DataError):
            replace(self.cfg, delay_hours=0)

    def test_trend_exit_reconciles_realized_and_unrealized_pnl(self):
        prices = [D(100)+i for i in range(180)] + [D(100)]*200
        bars = [Candle(self.start+i*HOUR, price, price, price, price, 100) for i, price in enumerate(prices)]
        report = self.run_screen(bars, name="trend_24_168")
        self.assertEqual(report["trade_count"], 2)
        self.assertEqual(report["base_quantity"], 0)
        self.assertLess(report["realized_pnl"], 0)
        self.assertAlmostEqual(report["final_liquidation_mark"]-1000, report["realized_pnl"])

    def test_zero_volume_cannot_create_a_fill(self):
        report = self.run_screen([replace(bar, volume=0) for bar in self.bars])
        self.assertEqual(report["trade_count"], 0)
        self.assertEqual(report["cash"], 1000)


if __name__ == "__main__":
    unittest.main()
