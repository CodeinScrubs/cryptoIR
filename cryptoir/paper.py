"""Durable spot-only paper ledger with conservative next-snapshot IOC fills.

This module cannot communicate with an exchange. Fees have explicit assets.
It is a simulation component, not a validated fill model.
"""

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

from .domain import Book, DataError, number

ZERO = Decimal(0)


@dataclass(frozen=True)
class Rules:
    quantity_step: Decimal
    price_tick: Decimal
    minimum_notional: Decimal
    maximum_notional: Decimal
    fee_rate: Decimal
    buy_fee_mode: str = "quote"

    def __post_init__(self):
        for name in self.__dataclass_fields__:
            if name != "buy_fee_mode":
                object.__setattr__(self, name, number(getattr(self, name)))
        if min(self.quantity_step, self.price_tick, self.minimum_notional) <= 0:
            raise DataError("market increments and minimum must be positive")
        if self.maximum_notional < self.minimum_notional:
            raise DataError("finite maximum must cover minimum")
        if not 0 <= self.fee_rate < 1:
            raise DataError("invalid explicit fee")
        if self.buy_fee_mode not in ("quote", "received_base"):
            raise DataError("unsupported buy fee asset")


def timestamp(value: int) -> int:
    if type(value) is not int or value <= 0:
        raise DataError("timestamp must be positive integer nanoseconds")
    return value


def pair_assets(pair: str) -> tuple[str, str]:
    assets = pair.split("/")
    if len(assets) != 2 or assets[0] == assets[1] or any(not a.isalnum() or not a.isupper() for a in assets):
        raise DataError("invalid explicit asset pair")
    return assets[0], assets[1]


class PaperLedger:
    def __init__(self, path: Path, *, upgrade=False):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS balances (
                venue TEXT, asset TEXT, free TEXT NOT NULL, reserved TEXT NOT NULL,
                PRIMARY KEY (venue, asset));
            CREATE TABLE IF NOT EXISTS orders (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                venue TEXT, pair TEXT, side TEXT, quantity TEXT, price TEXT,
                fee_rate TEXT, step TEXT, minimum TEXT, created_ns INTEGER,
                eligible_ns INTEGER, expires_ns INTEGER, held_asset TEXT, held TEXT,
                status TEXT, filled TEXT DEFAULT '0', notional TEXT DEFAULT '0',
                fee TEXT DEFAULT '0', fingerprint TEXT NOT NULL,
                fee_mode TEXT NOT NULL DEFAULT 'quote', fee_asset TEXT,
                fee_quote_equivalent TEXT DEFAULT '0');
            CREATE TABLE IF NOT EXISTS journal (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, event TEXT, details TEXT);
            CREATE TABLE IF NOT EXISTS snapshots (
                id TEXT PRIMARY KEY, received_ns INTEGER);
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY, value TEXT);
        """)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO metadata VALUES ('schema', '2')")
        schema = self.db.execute("SELECT value FROM metadata WHERE key='schema'").fetchone()[0]
        if schema == "1" and upgrade:
            try:
                # Exclusive, consistent SQLite backup before any migration.
                backup = path.with_name(path.name + ".schema1-backup.sqlite")
                with backup.open("xb"):
                    pass
                destination = sqlite3.connect(backup)
                try:
                    self.db.backup(destination)
                finally:
                    destination.close()
                with self.db:
                    self.db.execute("BEGIN IMMEDIATE")
                    self.db.execute("ALTER TABLE orders ADD COLUMN fee_mode TEXT NOT NULL DEFAULT 'quote'")
                    self.db.execute("ALTER TABLE orders ADD COLUMN fee_asset TEXT")
                    self.db.execute("ALTER TABLE orders ADD COLUMN fee_quote_equivalent TEXT DEFAULT '0'")
                    self.db.execute("UPDATE orders SET fee_asset=substr(pair,instr(pair,'/')+1), fee_quote_equivalent=fee")
                    self.db.execute("UPDATE metadata SET value='2' WHERE key='schema'")
                    self._event("schema_upgraded", old_schema=1, new_schema=2)
                schema = "2"
            except Exception:
                self.close()
                raise
        if schema != "2":
            self.close()
            raise DataError("unsupported paper schema; version 1 requires explicit upgrade=True with automatic backup")

    def close(self):
        self.db.close()

    def _event(self, event: str, **details):
        self.db.execute("INSERT INTO journal(event, details) VALUES (?, ?)",
                        (event, json.dumps(details, default=str, sort_keys=True)))

    def balance(self, venue: str, asset: str) -> tuple[Decimal, Decimal]:
        row = self.db.execute("SELECT free, reserved FROM balances WHERE venue=? AND asset=?", (venue, asset)).fetchone()
        return (number(row[0]), number(row[1])) if row else (ZERO, ZERO)

    def _adjust(self, venue: str, asset: str, free=ZERO, reserved=ZERO):
        available, held = self.balance(venue, asset)
        available, held = available + free, held + reserved
        if min(available, held) < 0:
            raise DataError("insufficient paper balance")
        self.db.execute("INSERT INTO balances VALUES (?, ?, ?, ?) ON CONFLICT(venue, asset) DO UPDATE SET free=excluded.free, reserved=excluded.reserved",
                        (venue, asset, str(available), str(held)))

    def seed(self, balances: dict[tuple[str, str], Decimal]):
        """One initial allocation only: never silently refill depleted capital."""
        if not balances:
            raise DataError("initial allocation is required")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            if self.db.execute("SELECT 1 FROM metadata WHERE key='seeded'").fetchone():
                raise DataError("paper capital already initialized")
            for (venue, asset), amount in balances.items():
                if not venue or not asset or number(amount) < 0:
                    raise DataError("invalid initial balance")
                self._adjust(venue, asset, free=number(amount))
                self._event("seed", venue=venue, asset=asset, amount=number(amount))
            self.db.execute("INSERT INTO metadata VALUES ('seeded', 'true')")

    def order(self, order_id: str) -> dict:
        row = self.db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if row is None:
            raise DataError("unknown paper order")
        return dict(row)

    def submit(self, order_id: str, venue: str, pair: str, side: str,
               quantity: Decimal, limit_price: Decimal, rules: Rules, *,
               now_ns: int, latency_ns: int, ttl_ns: int, preflight=None) -> dict:
        base, quote = pair_assets(pair)
        timestamp(now_ns)
        if type(latency_ns) is not int or type(ttl_ns) is not int or not 0 < latency_ns < ttl_ns:
            raise DataError("require positive latency and longer finite TTL")
        quantity, price = number(quantity), number(limit_price)
        if not order_id or not venue or side not in ("buy", "sell") or min(quantity, price) <= 0:
            raise DataError("invalid paper intent")
        if quantity % rules.quantity_step or price % rules.price_tick:
            raise DataError("order violates quantity step or price tick")
        if not rules.minimum_notional <= quantity * price <= rules.maximum_notional:
            raise DataError("order violates finite notional bounds")
        intent = (venue, pair, side, str(quantity), str(price), str(rules), now_ns, latency_ns, ttl_ns)
        fingerprint = hashlib.sha256(repr(intent).encode()).hexdigest()
        # v1 order identities remain idempotent after an explicit upgrade.
        legacy_rules = (f"Rules(quantity_step={rules.quantity_step!r}, price_tick={rules.price_tick!r}, "
                        f"minimum_notional={rules.minimum_notional!r}, maximum_notional={rules.maximum_notional!r}, "
                        f"quote_fee_rate={rules.fee_rate!r})")
        legacy_intent = (venue, pair, side, str(quantity), str(price), legacy_rules, now_ns, latency_ns, ttl_ns)
        legacy_fingerprint = hashlib.sha256(repr(legacy_intent).encode()).hexdigest()
        asset = quote if side == "buy" else base
        hold = quantity * price * (1 + rules.fee_rate if rules.buy_fee_mode == "quote" else 1) if side == "buy" else quantity
        fee_asset = base if side == "buy" and rules.buy_fee_mode == "received_base" else quote
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            existing = self.db.execute("SELECT fingerprint FROM orders WHERE id=?", (order_id,)).fetchone()
            if existing:
                if existing[0] != fingerprint and not (rules.buy_fee_mode == "quote" and existing[0] == legacy_fingerprint):
                    raise DataError("conflicting reuse of paper order id")
                return self.order(order_id)
            if not self.db.execute("SELECT 1 FROM metadata WHERE key='seeded'").fetchone():
                raise DataError("initial allocation required")
            latest = self.db.execute("SELECT value FROM metadata WHERE key='clock_ns'").fetchone()
            if latest and now_ns < int(latest[0]):
                raise DataError("intent predates simulation clock")
            if preflight is not None:
                self._ensure_book_source_floor()
            self._advance(now_ns)
            # Trusted local guard runs under the same write transaction. Its
            # risk latch must commit even when the intent is rejected.
            rejection = preflight() if preflight is not None else None
            if rejection is not None:
                self._event("risk_rejected", order_id=order_id, reason=str(rejection), now_ns=now_ns)
            else:
                self._adjust(venue, asset, free=-hold, reserved=hold)
                self.db.execute("""INSERT INTO orders
                    (id, venue, pair, side, quantity, price, fee_rate, step, minimum,
                     created_ns, eligible_ns, expires_ns, held_asset, held, status, fingerprint, fee_mode, fee_asset)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (order_id, venue, pair, side, str(quantity), str(price), str(rules.fee_rate),
                     str(rules.quantity_step), str(rules.minimum_notional), now_ns, now_ns + latency_ns,
                     now_ns + ttl_ns, asset, str(hold), "PENDING", fingerprint, rules.buy_fee_mode, fee_asset))
                self._event("submitted", order_id=order_id, now_ns=now_ns, reserved=hold, asset=asset)
        if rejection is not None:
            raise DataError("paper risk rejected: " + str(rejection))
        return self.order(order_id)

    def _release(self, order, status):
        hold = number(order["held"])
        self._adjust(order["venue"], order["held_asset"], free=hold, reserved=-hold)
        self.db.execute("UPDATE orders SET held='0', status=? WHERE id=?", (status, order["id"]))
        self._event(status.lower(), order_id=order["id"])

    def cancel(self, order_id: str):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            order = self.order(order_id)
            if order["status"] == "PENDING":
                self._release(order, "CANCELED")

    def _advance(self, now_ns: int):
        timestamp(now_ns)
        row = self.db.execute("SELECT value FROM metadata WHERE key='clock_ns'").fetchone()
        if row and now_ns < int(row[0]):
            raise DataError("simulation clock regressed")
        self.db.execute("INSERT INTO metadata VALUES ('clock_ns', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(now_ns),))
        expired = self.db.execute("SELECT * FROM orders WHERE status='PENDING' AND expires_ns<=? ORDER BY seq", (now_ns,)).fetchall()
        for order in expired:
            self._release(order, "EXPIRED")

    def advance(self, now_ns: int):
        """Expire reservations on a timer even if all market feeds stop."""
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self._advance(now_ns)

    def _ensure_book_source_floor(self):
        """Bootstrap the cutoff for legacy snapshot hashes without source indexes."""
        row = self.db.execute("SELECT value FROM metadata WHERE key='book_source_floor_ns'").fetchone()
        if row:
            return int(row[0])
        floor = 0
        if self.db.execute("SELECT 1 FROM snapshots LIMIT 1").fetchone():
            clock = self.db.execute("SELECT value FROM metadata WHERE key='clock_ns'").fetchone()
            if clock is None:
                raise DataError("legacy snapshots lack a simulation clock")
            # Legacy fills already required source time <= processing time.
            floor = timestamp(int(clock[0]))
        self.db.execute("INSERT INTO metadata VALUES ('book_source_floor_ns', ?)", (str(floor),))
        return floor

    @staticmethod
    def _book_source_key(book):
        return "book_source:" + json.dumps([book.venue, book.pair], separators=(",", ":"))

    @staticmethod
    def _book_content(book):
        # Decimal equality is exact and ignores trailing zeros without applying
        # the ambient precision, unlike Decimal.normalize().
        return [[(str(level.price), str(level.quantity)) for level in side]
                for side in (book.bids, book.asks)]

    def _book_source_status(self, book):
        floor = self._ensure_book_source_floor()
        if book.exchange_ns <= floor:
            return "book_at_or_before_legacy_cutoff"
        row = self.db.execute("SELECT value FROM metadata WHERE key=?", (self._book_source_key(book),)).fetchone()
        if row is None:
            return "new"
        source_ns, content = json.loads(row[0])
        if book.exchange_ns < source_ns:
            return "book_source_time_regressed"
        if book.exchange_ns > source_ns:
            return "new"
        prices_and_sizes = [[tuple(map(number, level)) for level in side] for side in content]
        incoming = [[(level.price, level.quantity) for level in side] for side in (book.bids, book.asks)]
        return "duplicate" if prices_and_sizes == incoming else "book_source_time_conflict"

    def _remember_book_source(self, book):
        value = json.dumps([book.exchange_ns, self._book_content(book)], separators=(",", ":"))
        self.db.execute("INSERT INTO metadata VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (self._book_source_key(book), value))

    def on_book(self, book: Book, *, max_age_ns: int, participation: Decimal, now_ns: int | None = None) -> list[dict]:
        """Evaluate IOC intents at most once per increasing venue/pair source time."""
        participation = number(participation)
        if not 0 < participation <= 1:
            raise DataError("participation must be in (0,1]")
        now_ns = book.received_ns if now_ns is None else timestamp(now_ns)
        book.validate_age(now_ns, max_age_ns)
        if book.exchange_ns is None:
            raise DataError("paper fill requires known source time")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            source_status = self._book_source_status(book)
            self._advance(now_ns)
            if source_status != "new":
                return []
            self._remember_book_source(book)
            return self._match_book(book, participation, now_ns)

    def _match_book(self, book, participation, now_ns):
        """Match an admitted book inside the caller's write transaction."""
        content = (book.venue, book.pair, book.exchange_ns, *self._book_content(book))
        snapshot_id = hashlib.sha256(repr(content).encode()).hexdigest()
        self.db.execute("INSERT INTO snapshots VALUES (?, ?)", (snapshot_id, book.received_ns))
        changed = []
        sides = {"buy": [[x.price, x.quantity * participation] for x in book.asks],
                 "sell": [[x.price, x.quantity * participation] for x in book.bids]}
        rows = self.db.execute("""SELECT * FROM orders WHERE status='PENDING' AND venue=? AND pair=?
                   AND eligible_ns<=? AND eligible_ns<=? ORDER BY seq""",
                   (book.venue, book.pair, book.received_ns, book.exchange_ns)).fetchall()
        for order in rows:
            quantity, price, step = map(number, (order["quantity"], order["price"], order["step"]))
            filled, notional = ZERO, ZERO
            levels = sides[order["side"]]
            for level in levels:
                marketable = level[0] <= price if order["side"] == "buy" else level[0] >= price
                if not marketable:
                    break
                take = (min(quantity - filled, level[1]) / step).to_integral_value(rounding=ROUND_DOWN) * step
                filled += take
                notional += take * level[0]
                level[1] -= take
                if filled == quantity:
                    break
            base, quote = pair_assets(order["pair"])
            hold, quote_equivalent = number(order["held"]), notional * number(order["fee_rate"])
            fee = filled * number(order["fee_rate"]) if order["fee_asset"] == base else quote_equivalent
            if order["side"] == "buy":
                self._adjust(book.venue, quote, free=hold-notional-(fee if order["fee_asset"] == quote else ZERO), reserved=-hold)
                self._adjust(book.venue, base, free=filled-(fee if order["fee_asset"] == base else ZERO))
            else:
                self._adjust(book.venue, base, free=hold - filled, reserved=-hold)
                self._adjust(book.venue, quote, free=notional - fee)
            status = "FILLED" if filled == quantity else "PARTIAL_CANCELED" if filled else "CANCELED"
            self.db.execute("UPDATE orders SET status=?, held='0', filled=?, notional=?, fee=?, fee_quote_equivalent=? WHERE id=?",
                            (status, str(filled), str(notional), str(fee), str(quote_equivalent), order["id"]))
            self._event("ioc_result", order_id=order["id"], status=status, filled=filled,
                        notional=notional, fee=fee, fee_asset=order["fee_asset"],
                        fee_quote_equivalent=quote_equivalent, quote=quote, snapshot=snapshot_id,
                        participation=participation, source_ns=book.exchange_ns,
                        received_ns=book.received_ns, processed_ns=now_ns)
            changed.append(self.order(order["id"]))
        return changed

    def report(self) -> dict:
        return {"kind": "paper_accounting", "live_execution": False, "profitability_established": False,
                "balances": [dict(row) for row in self.db.execute("SELECT * FROM balances ORDER BY venue, asset")],
                "orders": [dict(row) for row in self.db.execute("SELECT id,venue,pair,side,status,filled,notional,fee,fee_asset,fee_quote_equivalent,fee_mode FROM orders ORDER BY seq")],
                "journal_events": self.db.execute("SELECT count(*) FROM journal").fetchone()[0]}


def demo(path: Path) -> dict:
    """Synthetic accounting exercise; no live feed or calibrated strategy."""
    from .domain import Level

    # Reserve the filename exclusively before SQLite opens it.
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb"):
        pass
    ledger = PaperLedger(path)
    try:
        ledger.seed({("synthetic", "USDT"): Decimal(1000)})
        rules = Rules(Decimal("0.001"), Decimal("0.01"), Decimal(1), Decimal(200), Decimal("0.001"))
        ledger.submit("demo-buy", "synthetic", "BTC/USDT", "buy", Decimal(1), Decimal(101), rules,
                      now_ns=1_000_000_000, latency_ns=100_000_000, ttl_ns=2_000_000_000)
        later = Book("synthetic", "BTC/USDT", (Level(99, 4),), (Level(100, 4),),
                     1_200_000_000, 10_000_000, 1_150_000_000)
        ledger.on_book(later, max_age_ns=1_000_000_000, participation=Decimal("0.1"))
        return {**ledger.report(), "input": "synthetic_fixture_not_market_data"}
    finally:
        ledger.close()
