"""Causal hourly spot *screening*, with explicit uncalibrated execution costs.

No candle is interpreted as a limit-order fill. This intentionally modest model
eliminates weak hypotheses before expensive order-book and forward validation.
"""

from collections import deque
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_DOWN

from .domain import DataError, number
from .history import Candle, HOUR

ZERO = Decimal(0)
STRATEGIES = ("cash", "hold", "trend_24_168", "breakout_168_72", "reversion_24_2pct")


@dataclass(frozen=True)
class Assumptions:
    capital: Decimal
    allocation: Decimal
    fee: Decimal
    adverse_bps: Decimal
    prior_volume_fraction: Decimal
    quantity_step: Decimal
    minimum_notional: Decimal
    delay_hours: int = 1
    buy_fee_mode: str = "quote"

    def __post_init__(self):
        for key in asdict(self):
            if key not in ("delay_hours", "buy_fee_mode"):
                object.__setattr__(self, key, number(getattr(self, key)))
        if self.capital <= 0 or not 0 < self.allocation <= 1 or not 0 <= self.fee < 1:
            raise DataError("invalid capital, allocation or fee")
        if not 0 <= self.adverse_bps < 10000 or not 0 < self.prior_volume_fraction <= 1:
            raise DataError("invalid price cost or prior volume fraction")
        if self.quantity_step <= 0 or self.minimum_notional <= 0:
            raise DataError("explicit positive quantity step and minimum required")
        if type(self.delay_hours) is not int or not 1 <= self.delay_hours <= 24:
            raise DataError("use 1..24 complete hours of decision-to-execution delay")
        if self.buy_fee_mode not in ("quote", "received_base"):
            raise DataError("buy fee must explicitly use quote or received_base")


class Signal:
    """Receives only already completed bars. No access to the dataset or clock."""

    def __init__(self, name: str):
        if name not in STRATEGIES:
            raise DataError("unknown preregistered strategy")
        self.name, self.long = name, False
        self.bars: deque[Candle] = deque(maxlen=168)

    def observe(self, bar: Candle) -> bool:
        if self.bars and bar.time != self.bars[-1].time + HOUR:
            raise DataError("signal requires contiguous completed hourly bars")
        prior = list(self.bars)
        if self.name == "cash":
            self.long = False
        elif self.name == "hold":
            self.long = True
        elif self.name == "breakout_168_72" and len(prior) == 168:
            if not self.long and bar.close > max(b.high for b in prior):
                self.long = True
            elif self.long and bar.close < min(b.low for b in prior[-72:]):
                self.long = False
        self.bars.append(bar)
        closes = [b.close for b in self.bars]
        if self.name == "trend_24_168" and len(closes) == 168:
            self.long = sum(closes[-24:]) / 24 > sum(closes) / 168
        elif self.name == "reversion_24_2pct" and len(closes) >= 24:
            mean = sum(closes[-24:]) / 24
            if not self.long and bar.close < mean * Decimal("0.98"):
                self.long = True
            elif self.long and bar.close >= mean:
                self.long = False
        return self.long


def floor_quantity(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def screen(bars: list[Candle], name: str, assumptions: Assumptions, start: int, end: int) -> dict:
    """Independent, cash-funded evaluation interval with a causal warmup.

    At each open, execute a previously decided order with adverse price costs.
    Volume capacity uses the previous completed hour, never current/future volume.
    Orders are IOC approximations: partial remainder is cancelled, exits may retry.
    Final holdings are marked with hypothetical exit costs, not falsely sold.
    """
    if type(start) is not int or type(end) is not int or not 0 < start < end:
        raise DataError("invalid screening interval")
    if not bars or any(b.time-a.time != HOUR for a, b in zip(bars, bars[1:])):
        raise DataError("screen requires contiguous ordered hourly data")
    if bars[0].time > start or bars[-1].time+HOUR < end:
        raise DataError("screen interval exceeds data coverage")
    indices = [i for i, b in enumerate(bars) if start <= b.time and b.time+HOUR <= end]
    if len(indices) < 2:
        raise DataError("screen interval contains too few complete bars")
    first, last = indices[0], indices[-1]
    if bars[first].time != start or bars[last].time+HOUR != end:
        raise DataError("evaluation boundaries must align with whole hourly bars")
    cfg, signal = assumptions, Signal(name)
    for bar in bars[max(0, first-168):first]:
        signal.observe(bar)
    cash, quantity, basis, realized = cfg.capital, ZERO, ZERO, ZERO
    fees, base_fees, quote_fees, impact, turnover, peak, drawdown = ZERO, ZERO, ZERO, ZERO, ZERO, cfg.capital, ZERO
    pending, trades, equity = None, [], []
    rejects, cancellations, exposed = 0, 0, 0
    buy_factor, sell_factor = 1+cfg.adverse_bps/10000, 1-cfg.adverse_bps/10000
    buy_cash_factor = 1+cfg.fee if cfg.buy_fee_mode == "quote" else Decimal(1)

    def tradeable_position(amount, reference):
        rounded = floor_quantity(amount, cfg.quantity_step)
        return rounded > 0 and rounded*reference*sell_factor >= cfg.minimum_notional

    for i in indices:
        bar = bars[i]
        if pending and i >= pending["due_index"]:
            intent = pending
            pending = None
            buy = intent["side"] == "buy"
            price = bar.open * (buy_factor if buy else sell_factor)
            # A past-volume cap is an explicit proxy, not executable depth proof.
            capacity = bars[i-1].volume * cfg.prior_volume_fraction if i else ZERO
            wanted = intent["quantity"]
            if buy:
                nav = cash + quantity * bar.open
                exposure_capacity = max(ZERO, nav * cfg.allocation - quantity * price)
                wanted = min(wanted, cash/(price*buy_cash_factor), exposure_capacity/(price*buy_cash_factor))
            else:
                wanted = min(wanted, quantity)
            fill = floor_quantity(min(wanted, capacity), cfg.quantity_step)
            notional = fill * price
            if fill <= 0 or notional < cfg.minimum_notional:
                rejects += 1
            else:
                fee = notional * cfg.fee
                base_fee = fill * cfg.fee if buy and cfg.buy_fee_mode == "received_base" else ZERO
                quote_fee = fee if not buy or cfg.buy_fee_mode == "quote" else ZERO
                if buy:
                    spent = notional + quote_fee
                    cash -= spent
                    basis += spent
                    quantity += fill - base_fee
                else:
                    allocated_basis = basis * fill / quantity
                    proceeds = notional - fee
                    cash += proceeds
                    realized += proceeds - allocated_basis
                    basis -= allocated_basis
                    quantity -= fill
                fees += fee
                base_fees += base_fee
                quote_fees += quote_fee
                impact += fill * abs(price-bar.open)
                turnover += notional
                trades.append({"decision_time": intent["decision_time"], "execution_time": bar.time,
                               "side": intent["side"], "requested": intent["quantity"], "quantity": fill,
                               "price": price, "fee": fee, "fee_quote_equivalent": fee,
                               "fee_base": base_fee, "fee_quote": quote_fee,
                               "net_base_change": fill-base_fee if buy else -fill,
                               "unused_quantity": intent["quantity"]-fill,
                               "position_after": quantity,
                               "remaining_position_tradeable": tradeable_position(quantity, bar.open)})
            if cash < 0 or quantity < 0 or basis < 0:
                raise AssertionError("spot accounting invariant violated")
        saleable = floor_quantity(quantity, cfg.quantity_step) if tradeable_position(quantity, bar.close) else ZERO
        # Untradeable dust remains owned but has zero assumed liquidation value.
        liquidation_mark = cash + saleable * bar.close * sell_factor * (1-cfg.fee)
        peak = max(peak, liquidation_mark)
        drawdown = max(drawdown, (peak-liquidation_mark)/peak)
        equity.append({"time": bar.time+HOUR, "equity": liquidation_mark, "cash": cash, "quantity": quantity})
        exposed += quantity > 0
        desired = signal.observe(bar)
        if pending and (pending["side"] == "buy") != desired:
            pending = None
            cancellations += 1
        if pending is None and desired != tradeable_position(quantity, bar.close):
            if desired:
                order_quantity = floor_quantity(liquidation_mark * cfg.allocation / (bar.close*buy_factor*buy_cash_factor), cfg.quantity_step)
            else:
                order_quantity = floor_quantity(quantity, cfg.quantity_step)
            if order_quantity > 0:
                pending = {"side": "buy" if desired else "sell", "quantity": order_quantity,
                           "decision_time": bar.time+HOUR, "due_index": i+1+cfg.delay_hours}
    final = equity[-1]["equity"]
    unrealized = final-cash-basis
    if abs((final-cfg.capital)-(realized+unrealized)) > Decimal("1e-20") * cfg.capital:
        raise AssertionError("P&L accounting identity violated")
    return {"kind": "hourly_cost_screen", "strategy": name, "execution_ready": False,
            "start": start, "end_exclusive": end, "assumptions": asdict(cfg), "bars": len(indices),
            "final_liquidation_mark": final, "net_return": final/cfg.capital-1,
            "max_drawdown": drawdown, "cash": cash, "base_quantity": quantity,
            "realized_pnl": realized, "unrealized_pnl_with_exit_costs": unrealized,
            "paid_fees": fees, "paid_fees_quote_equivalent": fees,
            "paid_fees_base": base_fees, "paid_fees_quote": quote_fees,
            "modeled_adverse_price_cost": impact,
            "turnover_multiple": turnover/cfg.capital, "exposed_bars": exposed,
            "rejected_orders": rejects, "cancelled_intents": cancellations,
            "pending_at_end": pending is not None, "trade_count": len(trades),
            "completed_sells": sum(t["side"] == "sell" and not t["remaining_position_tradeable"] for t in trades),
            "residual_dust_quantity": quantity-saleable,
            "dust_reference_value_before_costs": (quantity-saleable)*bars[last].close,
            "trades": trades, "equity": equity}
