"""Causal, restartable forward paper decisions; transport is kept separate."""

import hashlib
import json
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from pathlib import Path

from .backtest import Signal, STRATEGIES, floor_quantity
from .domain import Book, DataError, number
from .history import Candle, HOUR
from .paper import PaperLedger, Rules
from .risk import RiskLimits, SpotRiskGuard
from .streams import dumps

ZERO = Decimal(0)
NS = 1_000_000_000


@dataclass(frozen=True)
class ForwardConfig:
    venue: str
    pair: str
    strategy: str
    rules: Rules
    risk: RiskLimits
    decision_delay_seconds: int
    decision_expiry_seconds: int
    order_latency_ms: int
    order_ttl_seconds: int
    max_candle_lag_seconds: int
    limit_offset_bps: Decimal
    prior_volume_fraction: Decimal
    mode: str = "paper"
    capital_basis: str = "synthetic_reference_usdt"
    capital_receipt_sha256: str | None = None

    def __post_init__(self):
        if self.mode != "paper" or self.venue != "nobitex" or self.strategy not in STRATEGIES:
            raise DataError("forward runner currently requires paper mode, Nobitex and a fixed baseline signal")
        if self.capital_basis not in ("synthetic_reference_usdt","paper_irt_conversion"):
            raise DataError("unknown capital basis")
        if self.capital_basis=="paper_irt_conversion":
            import re
            if not isinstance(self.capital_receipt_sha256,str) or not re.fullmatch("[0-9a-f]{64}",self.capital_receipt_sha256):
                raise DataError("IRT-funded paper config requires a sealed capital receipt")
        elif self.capital_receipt_sha256 is not None:
            raise DataError("synthetic reference config cannot claim a funding receipt")
        from .feeds import endpoint
        endpoint(self.venue, self.pair)
        if not self.pair.endswith("/USDT"):
            raise DataError("forward strategy requires a USDT quote; FX is separate")
        for key in ("decision_delay_seconds", "decision_expiry_seconds", "order_latency_ms", "order_ttl_seconds", "max_candle_lag_seconds"):
            if type(getattr(self,key)) is not int or getattr(self,key) <= 0:
                raise DataError("positive integer forward timing settings required")
        if not HOUR <= self.decision_delay_seconds <= 24*HOUR:
            raise DataError("hourly hypotheses require at least one full hour of decision delay")
        if not 1 <= self.decision_expiry_seconds <= HOUR or not 1 <= self.order_ttl_seconds <= 60:
            raise DataError("bounded decision expiry and order TTL required")
        if self.order_latency_ms >= self.order_ttl_seconds*1000:
            raise DataError("order latency must be shorter than TTL")
        if not HOUR <= self.max_candle_lag_seconds <= 2*HOUR:
            raise DataError("closed-hour freshness budget must be 1..2 hours")
        object.__setattr__(self,"limit_offset_bps",number(self.limit_offset_bps))
        object.__setattr__(self,"prior_volume_fraction",number(self.prior_volume_fraction))
        if not 0 <= self.limit_offset_bps <= 100 or not 0 < self.prior_volume_fraction <= 1:
            raise DataError("invalid explicit forward liquidity/cost assumptions")
        if self.risk.maximum_open_orders != 1:
            raise DataError("initial forward runner supports one pending order per experiment")

    @classmethod
    def from_dict(cls, value):
        value = dict(value)
        value["rules"] = Rules(**value["rules"])
        value["risk"] = RiskLimits(**value["risk"])
        return cls(**value)


def source_fingerprint():
    directory = Path(__file__).parent
    # Seal all executable Python sources, not local paths, secrets or data.
    return hashlib.sha256(dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in sorted(directory.glob("*.py"))}).encode()).hexdigest()


class ForwardEngine:
    """The caller owns the ledger's OS lock and advances clocks in receive order."""

    def __init__(self, ledger: PaperLedger, config: ForwardConfig, *, source_id=None):
        self.ledger, self.config = ledger, config
        self.latest_book = None
        self.signal = Signal(config.strategy)
        self.guard = SpotRiskGuard(ledger,config.venue,config.pair,config.rules,config.risk)
        identity = hashlib.sha256(dumps({"config":asdict(config),"source":source_id or source_fingerprint()}).encode()).hexdigest()
        with ledger.db:
            ledger.db.execute("BEGIN IMMEDIATE")
            existing = self.guard._get("forward_identity")
            if existing is not None and existing != identity:
                raise DataError("sealed forward configuration or source changed; do not silently resume")
            if existing is None:
                self.guard._set("forward_identity",identity)
                self._save({"schema":1,"bars":[],"long":False,"decision":None,"last_decided_bar":None,
                            "candles_ok":False,"candle_reason":"awaiting_history","candle_received_ns":None,
                            "started_ns":None,"session_count":0})
                ledger._event("forward_initialized",configuration=asdict(config),source_sha256=source_id or source_fingerprint())
        self._restore_signal()

    def state(self):
        state = json.loads(self.guard._get("forward_state"))
        if state.get("schema") != 1:
            raise DataError("unsupported forward state")
        return state

    def _save(self,state):
        self.guard._set("forward_state",dumps(state))

    def _restore_signal(self):
        state = self.state()
        self.signal.bars.clear()
        self.signal.bars.extend(Candle(**bar) for bar in state["bars"])
        self.signal.long = state["long"]

    def start(self, now_ns: int):
        """No modeled fills during downtime; cancel stale orders and decisions."""
        with self.ledger.db:
            self.ledger.db.execute("BEGIN IMMEDIATE")
            self.ledger._advance(now_ns)
            for order in self.ledger.db.execute("SELECT * FROM orders WHERE status='PENDING' ORDER BY seq").fetchall():
                self.ledger._release(order,"CANCELED")
            state=self.state()
            state.update(decision=None,candles_ok=False,candle_reason="session_start",last_decided_bar=None,
                         started_ns=state["started_ns"] or now_ns,session_count=state["session_count"]+1)
            self._save(state)
            self.guard._blocked_data(self.guard.state(),"session_start")
            self.ledger._event("forward_session_start",now_ns=now_ns,session=state["session_count"],missed_fills="unknown_not_simulated")

    def candle_failure(self, now_ns: int, reason: str):
        with self.ledger.db:
            self.ledger.db.execute("BEGIN IMMEDIATE")
            self.ledger._advance(now_ns)
            state=self.state()
            if state["candles_ok"] or state["candle_reason"] != reason:
                self.ledger._event("forward_candle_block",reason=reason,now_ns=now_ns)
            state.update(candles_ok=False,candle_reason=reason,decision=None)
            self._save(state)
            self.guard._cancel_buys()

    def apply_candles(self, rows: list[Candle], *, received_ns: int, now_ns: int):
        if received_ns > now_ns or not rows or any(b.time-a.time != HOUR for a,b in zip(rows,rows[1:])):
            self.candle_failure(now_ns,"missing_or_unordered_candles")
            return
        # The transport filters partial bars; enforce closure again at this boundary.
        if any((bar.time+HOUR)*NS > received_ns for bar in rows):
            self.candle_failure(now_ns,"unclosed_candle")
            return
        saved={bar.time:bar for bar in self.signal.bars}
        if any(bar.time in saved and saved[bar.time] != bar for bar in rows):
            self.candle_failure(now_ns,"closed_candle_revision")
            self.guard.halt(now_ns=now_ns,reason="closed_candle_revision")
            return
        last=self.signal.bars[-1].time if self.signal.bars else None
        fresh=[bar for bar in rows if last is None or bar.time>last]
        if last is not None and fresh and fresh[0].time != last+HOUR:
            self.candle_failure(now_ns,"candle_gap")
            return
        old_bars=list(self.signal.bars)
        old_long=self.signal.long
        try:
            for bar in fresh:
                self.signal.observe(bar)
            with self.ledger.db:
                self.ledger.db.execute("BEGIN IMMEDIATE")
                self.ledger._advance(now_ns)
                state=self.state()
                state.update(bars=[asdict(b) for b in self.signal.bars],long=self.signal.long,candle_received_ns=received_ns)
                latest=self.signal.bars[-1] if self.signal.bars else None
                ready=latest is not None and len(self.signal.bars)>=168 and now_ns-(latest.time+HOUR)*NS<=self.config.max_candle_lag_seconds*NS
                state.update(candles_ok=ready,candle_reason=None if ready else "insufficient_or_stale_warmup")
                if not ready:
                    state["decision"]=None
                    self.guard._cancel_buys()
                elif latest.time != state["last_decided_bar"]:
                    decision=state["decision"]
                    if decision is None or decision["target_long"] != self.signal.long:
                        state["decision"]={"id":f"candle-{latest.time}-{'buy' if self.signal.long else 'sell'}",
                                           "signal_bar":latest.time,"target_long":self.signal.long,
                                           "observed_ns":now_ns,"due_ns":now_ns+self.config.decision_delay_seconds*NS,
                                           "expires_ns":now_ns+(self.config.decision_delay_seconds+self.config.decision_expiry_seconds)*NS}
                        self.ledger._event("forward_decision",**state["decision"])
                    if not self.signal.long:
                        self.guard._cancel_buys()
                    state["last_decided_bar"]=latest.time
                self._save(state)
                if fresh:
                    self.ledger._event("forward_candles_applied",count=len(fresh),last_bar=latest.time,
                                       received_ns=received_ns,processed_ns=now_ns)
        except Exception:
            self.signal.bars.clear()
            self.signal.bars.extend(old_bars)
            self.signal.long=old_long
            raise

    def _finish_decision(self, reason: str, now_ns: int):
        with self.ledger.db:
            self.ledger.db.execute("BEGIN IMMEDIATE")
            state=self.state()
            if state["decision"]:
                self.ledger._event("forward_decision_finished",decision=state["decision"]["id"],reason=reason,now_ns=now_ns)
                state["decision"]=None
                self._save(state)

    def advance(self, now_ns: int):
        self.guard.advance(now_ns)
        state=self.state()
        latest=self.signal.bars[-1] if self.signal.bars else None
        if state["candles_ok"] and (latest is None or now_ns-(latest.time+HOUR)*NS>self.config.max_candle_lag_seconds*NS):
            self.candle_failure(now_ns,"candle_timer_expired")
        if state["decision"] and now_ns>=state["decision"]["expires_ns"]:
            self._finish_decision("expired_without_execution",now_ns)

    def on_book(self, book: Book, *, now_ns: int):
        self.latest_book = book
        self.advance(now_ns)
        fills=self.guard.on_book(book,now_ns=now_ns)
        state,risk=self.state(),self.guard.state()
        decision=state["decision"]
        if not decision or not state["candles_ok"] or not risk["data_ok"] or now_ns<decision["due_ns"]:
            return fills
        if self.ledger.db.execute("SELECT 1 FROM orders WHERE id=?",(decision["id"],)).fetchone():
            self._finish_decision("already_submitted_before_restart",now_ns)
            return fills
        if self.ledger.db.execute("SELECT 1 FROM orders WHERE status='PENDING'").fetchone():
            return fills
        cfg=self.config
        owned=self.ledger.balance(cfg.venue,self.guard.base)[0]
        saleable=floor_quantity(owned,cfg.rules.quantity_step)
        position=saleable*book.bids[0].price>=cfg.rules.minimum_notional
        if decision["target_long"]==position:
            self._finish_decision("target_already_satisfied",now_ns)
            return fills
        buy=decision["target_long"]
        raw_price=(book.asks[0].price*(1+cfg.limit_offset_bps/10000) if buy
                   else book.bids[0].price*(1-cfg.limit_offset_bps/10000))
        price=(raw_price/cfg.rules.price_tick).to_integral_value(rounding=ROUND_UP if buy else ROUND_DOWN)*cfg.rules.price_tick
        if buy:
            equity=number(risk["equity"])
            exposure_remaining=max(ZERO,equity*cfg.risk.max_exposure_fraction-owned*book.asks[0].price)
            projected_cost=price-book.bids[0].price+2*price*cfg.rules.fee_rate
            quantity=exposure_remaining/(price+cfg.risk.max_exposure_fraction*projected_cost)
            cash=self.ledger.balance(cfg.venue,self.guard.quote)[0]
            quantity=min(quantity,cash/(price*(1+cfg.rules.fee_rate if cfg.rules.buy_fee_mode=="quote" else 1)))
        else:
            quantity=saleable
        quantity=floor_quantity(min(quantity,cfg.risk.maximum_order_notional/price,
                                    cfg.rules.maximum_notional/price,
                                    self.signal.bars[-1].volume*cfg.prior_volume_fraction),cfg.rules.quantity_step)
        if quantity<=0 or quantity*price<cfg.rules.minimum_notional:
            self._finish_decision("below_size_or_liquidity_minimum",now_ns)
            return fills
        try:
            self.guard.submit(decision["id"],"buy" if buy else "sell",quantity,price,book=book,now_ns=now_ns,
                              latency_ns=cfg.order_latency_ms*1_000_000,ttl_ns=cfg.order_ttl_seconds*NS)
        except DataError as exc:
            self._finish_decision(str(exc),now_ns)
        else:
            self._finish_decision("submitted_paper_only",now_ns)
        return fills

    def invalidate(self, now_ns: int, reason: str):
        self.guard.invalidate(now_ns=now_ns,reason=reason)

    def stop(self, now_ns: int, *, permanent=False, reason="session_end"):
        if permanent:
            self.guard.halt(now_ns=now_ns,reason=reason)
        with self.ledger.db:
            self.ledger.db.execute("BEGIN IMMEDIATE")
            self.ledger._advance(now_ns)
            for order in self.ledger.db.execute("SELECT * FROM orders WHERE status='PENDING' ORDER BY seq").fetchall():
                self.ledger._release(order,"CANCELED")
            state=self.state()
            state.update(decision=None,candles_ok=False,candle_reason=reason)
            self._save(state)
            self.guard._blocked_data(self.guard.state(),reason)
            self.ledger._event("forward_session_end",reason=reason,now_ns=now_ns,positions="retained_not_liquidated")

    def report(self):
        state=self.state()
        book=asdict(self.latest_book) if self.latest_book else None
        if book:
            book.update(bids=book["bids"][:10],asks=book["asks"][:10])
        return {**self.ledger.report(),"risk":self.guard.state(),
                "risk_limits":asdict(self.config.risk),"venue":self.config.venue,"pair":self.config.pair,
                "last_book":book,
                "forward":{key:value for key,value in state.items() if key!="bars"},
                "last_candle":asdict(self.signal.bars[-1]) if self.signal.bars else None,
                "warmup_bars":len(self.signal.bars),"strategy":self.config.strategy,
                "capital_basis":self.config.capital_basis,
                "research_status":"experimental_unaccepted_hypothesis","irt_performance_available":False}
