"""Persistent risk gates for one paper spot venue/pair, not a live controller."""

import hashlib
import json
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

from .domain import Book, DataError, number, walk
from .paper import PaperLedger, Rules, pair_assets, timestamp
from .streams import dumps

ZERO = Decimal(0)


@dataclass(frozen=True)
class RiskLimits:
    initial_quote: Decimal
    max_exposure_fraction: Decimal
    daily_loss_fraction: Decimal
    drawdown_fraction: Decimal
    maximum_order_notional: Decimal
    maximum_open_orders: int
    max_feed_age_ns: int
    depth_participation: Decimal

    def __post_init__(self):
        for key in ("initial_quote", "max_exposure_fraction", "daily_loss_fraction", "drawdown_fraction", "maximum_order_notional", "depth_participation"):
            object.__setattr__(self, key, number(getattr(self, key)))
        if min(self.initial_quote, self.maximum_order_notional) <= 0:
            raise DataError("finite positive paper capital and order cap required")
        if not 0 < self.max_exposure_fraction <= 1:
            raise DataError("exposure must be in (0,1]")
        if not 0 < self.depth_participation <= 1:
            raise DataError("explicit displayed-depth participation in (0,1] required")
        if not all(0 < x < 1 for x in (self.daily_loss_fraction, self.drawdown_fraction)):
            raise DataError("finite loss fractions in (0,1) required")
        if type(self.maximum_open_orders) is not int or not 1 <= self.maximum_open_orders <= 100:
            raise DataError("bounded open-order limit required")
        if type(self.max_feed_age_ns) is not int or not 0 < self.max_feed_age_ns <= 60_000_000_000:
            raise DataError("explicit feed freshness budget of at most 60 seconds required")


class SpotRiskGuard:
    """Single-venue USDT paper guard. Unknown inventory blocks valuation.

    All guarded reservations are checked atomically inside PaperLedger.submit.
    Use on_book, invalidate and advance instead of bypassing this wrapper.
    No order transport, withdrawals, borrowing, or automatic halt reset exists.
    """

    def __init__(self, ledger: PaperLedger, venue: str, pair: str, rules: Rules, limits: RiskLimits):
        base, quote = pair_assets(pair)
        if not venue or quote != "USDT":
            raise DataError("risk valuation currently requires one explicit USDT spot pair")
        if limits.maximum_order_notional < rules.minimum_notional:
            raise DataError("risk order cap is below market minimum")
        self.ledger, self.venue, self.pair = ledger, venue, pair
        self.base, self.quote, self.rules, self.limits = base, quote, rules, limits
        config = {"schema": 1, "venue": venue, "pair": pair, "rules": asdict(rules), "limits": asdict(limits)}
        config_hash = hashlib.sha256(dumps(config).encode()).hexdigest()
        with ledger.db:
            ledger.db.execute("BEGIN IMMEDIATE")
            existing = self._get("risk_config")
            if existing is not None and existing != config_hash:
                raise DataError("risk configuration differs from sealed experiment")
            if existing is None:
                if ledger.db.execute("SELECT 1 FROM orders").fetchone():
                    raise DataError("initialize risk guard before any paper order")
                if self._unknown_inventory() or ledger.balance(venue, base) != (ZERO, ZERO):
                    raise DataError("risk initialization requires quote-only initial capital")
                if ledger.balance(venue, quote) != (limits.initial_quote, ZERO):
                    raise DataError("risk capital differs from seeded quote balance")
                self._set("risk_config", config_hash)
                self._save({"schema": 1, "initial_quote": str(limits.initial_quote),
                            "equity": str(limits.initial_quote), "peak": str(limits.initial_quote),
                            "day_start": str(limits.initial_quote), "day": None,
                            "halt": None, "data_ok": False, "data_reason": "awaiting_book", "mark_ns": None})
                ledger._event("risk_initialized", configuration=config, config_sha256=config_hash)

    def _get(self, key):
        row = self.ledger.db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def _set(self, key, value):
        self.ledger.db.execute("INSERT INTO metadata VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def state(self) -> dict:
        value = self._get("risk_state")
        if value is None:
            raise DataError("missing durable risk state")
        state = json.loads(value)
        if state.get("schema") != 1:
            raise DataError("unsupported risk state")
        return state

    def _save(self, state):
        self._set("risk_state", dumps(state))

    def _unknown_inventory(self):
        for row in self.ledger.db.execute("SELECT * FROM balances"):
            if number(row["free"])+number(row["reserved"]) > 0:
                if row["venue"] != self.venue or row["asset"] not in (self.base, self.quote):
                    return True
        return False

    def _cancel_buys(self):
        orders = self.ledger.db.execute("SELECT * FROM orders WHERE status='PENDING' AND side='buy' ORDER BY seq").fetchall()
        for order in orders:
            self.ledger._release(order, "CANCELED")

    def _blocked_data(self, state, reason):
        if state["data_ok"] or state["data_reason"] != reason:
            self.ledger._event("risk_data_block", reason=reason)
        state.update(data_ok=False, data_reason=reason)
        self._cancel_buys()
        self._save(state)
        return state

    def _mark(self, book: Book, now_ns: int) -> dict:
        state = self.state()
        try:
            if book.venue != self.venue or book.pair != self.pair:
                raise DataError("wrong risk venue or pair")
            book.validate_age(now_ns, self.limits.max_feed_age_ns)
            if book.exchange_ns is None:
                raise DataError("unknown source time")
            if self._unknown_inventory():
                raise DataError("unvalued inventory outside configured venue/pair")
            quantity = sum(self.ledger.balance(self.venue, self.base))
            cash = sum(self.ledger.balance(self.venue, self.quote))
            saleable = (quantity/self.rules.quantity_step).to_integral_value(rounding=ROUND_DOWN)*self.rules.quantity_step
            if saleable * book.bids[0].price < self.rules.minimum_notional:
                saleable = ZERO
            value = ZERO
            if saleable:
                quote = walk(book.bids, saleable)
                if quote.filled != saleable:
                    raise DataError("insufficient displayed depth for portfolio valuation")
                value = quote.notional * (1-self.rules.fee_rate)
            equity = cash+value
        except DataError as exc:
            return self._blocked_data(state, str(exc))
        # Tehran day boundary; fixed UTC+03:30 for supported present-day runs.
        day = (now_ns//1_000_000_000 + 12600)//86400
        if state["day"] != day:
            # Preserve a possible overnight/gap loss relative to the last mark.
            state["day_start"] = state["equity"]
            state["day"] = day
        peak = max(number(state["peak"]), equity)
        daily_start = number(state["day_start"])
        daily_loss = max(ZERO, (daily_start-equity)/daily_start) if daily_start else Decimal(1)
        drawdown = (peak-equity)/peak
        reason = "daily_loss" if daily_loss >= self.limits.daily_loss_fraction else "drawdown" if drawdown >= self.limits.drawdown_fraction else None
        if reason and state["halt"] is None:
            state["halt"] = reason
            self.ledger._event("risk_halted", reason=reason, equity=equity, peak=peak, day_start=daily_start, now_ns=now_ns)
        if state["halt"]:
            self._cancel_buys()
        pending = self.ledger.db.execute("SELECT * FROM orders WHERE status='PENDING' AND side='buy'").fetchall()
        if pending and self._projected_exposure(book, equity, quantity, pending) is False:
            self._cancel_buys()
            self.ledger._event("risk_pending_buys_canceled", reason="projected_exposure")
        state.update(equity=str(equity), peak=str(peak), daily_loss_fraction=str(daily_loss),
                     drawdown_fraction=str(drawdown), data_ok=True, data_reason=None,
                     mark_ns=now_ns, base_quantity=str(quantity), dust_quantity=str(quantity-saleable),
                     gross_exposure_fraction=str(quantity*book.asks[0].price/equity) if equity else None)
        self._save(state)
        return state

    def _projected_exposure(self, book, equity, held_quantity, pending, new_quantity=ZERO, new_price=ZERO):
        """Conservative entry budget including an estimated round-trip cost."""
        intents = [(number(o["quantity"]), number(o["price"])) for o in pending]
        if new_quantity:
            intents.append((new_quantity, new_price))
        exposure = held_quantity * book.asks[0].price
        costs = ZERO
        for quantity, price in intents:
            reference = max(price, book.asks[0].price)
            exposure += quantity*reference
            costs += quantity*(reference-book.bids[0].price+2*reference*self.rules.fee_rate)
        projected_equity = max(ZERO, equity-costs)
        return exposure <= projected_equity*self.limits.max_exposure_fraction

    def observe(self, book: Book, *, now_ns: int):
        with self.ledger.db:
            self.ledger.db.execute("BEGIN IMMEDIATE")
            self.ledger._advance(timestamp(now_ns))
            return self._mark(book, now_ns)

    def invalidate(self, *, now_ns: int, reason="feed_gap"):
        with self.ledger.db:
            self.ledger.db.execute("BEGIN IMMEDIATE")
            self.ledger._advance(timestamp(now_ns))
            return self._blocked_data(self.state(), reason)

    def advance(self, now_ns: int):
        """A live runner must call this timer even when no book arrives."""
        with self.ledger.db:
            self.ledger.db.execute("BEGIN IMMEDIATE")
            self.ledger._advance(timestamp(now_ns))
            state = self.state()
            if state["mark_ns"] is None or now_ns-state["mark_ns"] > self.limits.max_feed_age_ns:
                self._blocked_data(state, "feed_timer_expired")

    def halt(self, *, now_ns: int, reason="manual_stop"):
        with self.ledger.db:
            self.ledger.db.execute("BEGIN IMMEDIATE")
            self.ledger._advance(timestamp(now_ns))
            state = self.state()
            if state["halt"] is None:
                state["halt"] = reason
                self.ledger._event("risk_halted", reason=reason, now_ns=now_ns)
            self._cancel_buys()
            self._save(state)

    def submit(self, order_id: str, side: str, quantity, limit_price, *,
               book: Book, now_ns: int, latency_ns: int, ttl_ns: int):
        quantity, price = number(quantity), number(limit_price)

        def preflight():
            state = self._mark(book, now_ns)
            if not state["data_ok"]:
                return state["data_reason"]
            if side == "buy" and state["halt"]:
                return "latched_"+state["halt"]
            if quantity*price > self.limits.maximum_order_notional:
                return "maximum_order_notional"
            pending = self.ledger.db.execute("SELECT * FROM orders WHERE status='PENDING'").fetchall()
            if len(pending) >= self.limits.maximum_open_orders:
                return "maximum_open_orders"
            if side == "buy":
                held_quantity = sum(self.ledger.balance(self.venue, self.base))
                buys = [o for o in pending if o["side"] == "buy"]
                if not self._projected_exposure(book, number(state["equity"]), held_quantity, buys, quantity, price):
                    return "maximum_exposure"
            return None

        return self.ledger.submit(order_id, self.venue, self.pair, side, quantity, price, self.rules,
                                  now_ns=now_ns, latency_ns=latency_ns, ttl_ns=ttl_ns, preflight=preflight)

    def on_book(self, book: Book, *, now_ns: int | None = None):
        now_ns = book.received_ns if now_ns is None else timestamp(now_ns)
        state = self.observe(book, now_ns=now_ns)
        if not state["data_ok"]:
            return []
        # Fill and post-fill mark use separate ledger transactions; the runner
        # is single-threaded and must not mutate this ledger from another owner.
        results = self.ledger.on_book(book, max_age_ns=self.limits.max_feed_age_ns,
                                      participation=self.limits.depth_participation, now_ns=now_ns)
        self.observe(book, now_ns=now_ns)
        return results


def demo(path: Path) -> dict:
    """Invented loss and restart exercise, never a market-performance claim."""
    from .domain import Level
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb"):
        pass
    ledger = PaperLedger(path)
    rules = Rules(Decimal("0.001"), Decimal("0.01"), Decimal(1), Decimal(500), Decimal("0.001"), "received_base")
    limits = RiskLimits(Decimal(1000), Decimal("0.25"), Decimal("0.02"), Decimal("0.05"), Decimal(200), 2,
                        1_000_000_000, Decimal("0.1"))
    try:
        ledger.seed({("synthetic", "USDT"): Decimal(1000)})
        guard = SpotRiskGuard(ledger, "synthetic", "BTC/USDT", rules, limits)
        make_book = lambda now,bid,ask: Book("synthetic", "BTC/USDT", (Level(bid,100),), (Level(ask,100),), now,0,now-10_000_000)
        initial = make_book(1_000_000_000,99,100)
        guard.submit("synthetic-buy", "buy", Decimal(1), Decimal(101), book=initial,
                     now_ns=initial.received_ns, latency_ns=100_000_000, ttl_ns=3_000_000_000)
        guard.on_book(make_book(1_200_000_000,99,100))
        low = make_book(2_000_000_000,70,71)
        guard.on_book(low)
        rejected = None
        try:
            guard.submit("synthetic-blocked", "buy", Decimal(1), Decimal(71), book=low,
                         now_ns=low.received_ns, latency_ns=100_000_000, ttl_ns=3_000_000_000)
        except DataError as exc:
            rejected = str(exc)
    finally:
        ledger.close()
    ledger = PaperLedger(path)
    try:
        restarted = SpotRiskGuard(ledger, "synthetic", "BTC/USDT", rules, limits)
        return {**ledger.report(), "input": "synthetic_loss_and_restart_fixture", "risk": restarted.state(),
                "entry_rejection": rejected, "restart_verified": restarted.state()["halt"] == "daily_loss"}
    finally:
        ledger.close()
