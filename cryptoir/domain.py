"""Exact money arithmetic and explicit data-quality boundaries."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


class DataError(ValueError):
    pass


def number(value) -> Decimal:
    if isinstance(value, (bool, float)):
        raise DataError("money must use a decimal string, Decimal or integer")
    try:
        result = Decimal(value)
    except (TypeError, ValueError, InvalidOperation) as exc:
        raise DataError("invalid decimal") from exc
    if not result.is_finite():
        raise DataError("non-finite decimal")
    return result


def toman(value, currency: str) -> Decimal:
    """Only explicit IRR/IRT conversion; never infer units from price magnitude."""
    amount = number(value)
    if currency == "IRR":
        return amount / 10
    if currency == "IRT":
        return amount
    raise DataError("not a supported fiat unit")


@dataclass(frozen=True)
class Level:
    price: Decimal
    quantity: Decimal

    def __post_init__(self):
        object.__setattr__(self, "price", number(self.price))
        object.__setattr__(self, "quantity", number(self.quantity))
        if self.price <= 0 or self.quantity <= 0:
            raise DataError("snapshot price and quantity must be positive")


@dataclass(frozen=True)
class Book:
    venue: str
    pair: str
    bids: tuple[Level, ...]
    asks: tuple[Level, ...]
    received_ns: int
    request_duration_ns: int
    exchange_ns: int | None = None

    def __post_init__(self):
        if any(type(x) is not int for x in (self.received_ns, self.request_duration_ns)):
            raise DataError("timestamps must be integer nanoseconds")
        if self.exchange_ns is not None and type(self.exchange_ns) is not int:
            raise DataError("exchange timestamp must be integer nanoseconds")
        if not self.bids or not self.asks:
            raise DataError("empty side")
        for values, descending in ((self.bids, True), (self.asks, False)):
            prices = [v.price for v in values]
            if len(prices) != len(set(prices)):
                raise DataError("duplicate price level")
            if prices != sorted(prices, reverse=descending):
                raise DataError("unordered price levels")
        if self.bids[0].price >= self.asks[0].price:
            raise DataError("locked or crossed book")
        if self.received_ns <= 0 or self.request_duration_ns < 0:
            raise DataError("invalid observation timestamps")
        if self.exchange_ns is not None and self.exchange_ns <= 0:
            raise DataError("invalid exchange timestamp")

    def validate_age(self, now_ns: int, max_age_ns: int) -> None:
        if max_age_ns <= 0:
            raise DataError("max age must be positive")
        if not 0 <= now_ns - self.received_ns <= max_age_ns:
            raise DataError("stale or future receive timestamp")
        if self.request_duration_ns > max_age_ns:
            raise DataError("request duration exceeds freshness budget")
        if self.exchange_ns is not None:
            # A positive clock offset must be resolved, never silently ignored.
            if not 0 <= now_ns - self.exchange_ns <= max_age_ns:
                raise DataError("stale exchange data or clock skew")


@dataclass(frozen=True)
class Quote:
    requested: Decimal
    filled: Decimal
    notional: Decimal


def walk(levels: tuple[Level, ...], quantity: Decimal) -> Quote:
    quantity = number(quantity)
    if quantity <= 0:
        raise DataError("quantity must be positive")
    remaining, total = quantity, Decimal(0)
    for level in levels:
        take = min(remaining, level.quantity)
        total += take * level.price
        remaining -= take
        if not remaining:
            break
    return Quote(quantity, quantity - remaining, total)


def estimate_spread(buy: Book, sell: Book, quantity: Decimal, *,
                    buy_fee: Decimal, sell_fee: Decimal, reserve_bps: Decimal,
                    now_ns: int, max_age_ns: int) -> dict:
    """Conditional snapshot estimate, not a fill, profit, or trading signal.

    Assumes both fees are paid in quote currency and inventory already exists on
    both venues. Unknown fee currency, balances or precision invalidate execution.
    """
    if buy.venue == sell.venue or buy.pair != sell.pair:
        raise DataError("require different venues and identical pair units")
    buy.validate_age(now_ns, max_age_ns)
    sell.validate_age(now_ns, max_age_ns)
    buy_fee, sell_fee, reserve_bps = map(number, (buy_fee, sell_fee, reserve_bps))
    if not (0 <= buy_fee < 1 and 0 <= sell_fee < 1 and 0 <= reserve_bps < 10000):
        raise DataError("invalid explicit cost assumptions")
    bought, sold = walk(buy.asks, quantity), walk(sell.bids, quantity)
    if bought.filled != bought.requested or sold.filled != sold.requested:
        raise DataError("insufficient displayed liquidity for both legs")
    cost = bought.notional * (1 + buy_fee)
    proceeds = sold.notional * (1 - sell_fee)
    reserve = (bought.notional + sold.notional) * reserve_bps / 10000
    return {
        "kind": "conditional_snapshot_estimate", "executable": False,
        "pair": buy.pair, "buy_venue": buy.venue, "sell_venue": sell.venue,
        "quantity": str(quantity), "cost": str(cost), "proceeds": str(proceeds),
        "reserve": str(reserve), "estimated_net": str(proceeds - cost - reserve),
        "source_time_known": buy.exchange_ns is not None and sell.exchange_ns is not None,
        "assumptions": "quote-denominated fees; prefunded inventory; snapshot liquidity",
    }
