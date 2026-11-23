"""Durable order recovery rehearsal. Inputs are synthetic and there is no transport."""

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import re
import sqlite3

from . import accounts, order_observation as orders
from .domain import DataError, number
from .paper import timestamp
from .runtime import atomic_json
from .streams import dumps


APPLICATION_ID = 0x43524952
OBSERVATION_MAX_AGE_NS = 5_000_000_000
DEMO_EPOCH_NS = 1_767_225_600_000_000_000  # 2026-01-01 UTC


def source_ns(utc_iso):
    dt = datetime.fromisoformat(utc_iso) - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (dt.days*86400+dt.seconds)*1_000_000_000+dt.microseconds*1000


@dataclass(frozen=True)
class Intent:
    client_id: str
    side: str
    quantity: str
    price: str

    def __post_init__(self):
        if not isinstance(self.client_id, str) or not re.fullmatch(r"rehearsal-[a-z0-9-]{1,32}", self.client_id):
            raise DataError("explicit rehearsal client ID required")
        if self.side not in ("buy", "sell"):
            raise DataError("invalid intent side")
        for field in ("quantity", "price"):
            value = accounts.nonnegative(getattr(self, field))
            if not value:
                raise DataError("intent quantity and price must be positive")
            object.__setattr__(self, field, str(value))


class RecoveryJournal:
    def __init__(self, path: Path, *, create=False):
        if create:
            with path.open("xb"):
                pass
        self.db = sqlite3.connect(path.resolve().as_uri()+"?mode=rw", uri=True)
        try:
            self.db.row_factory = sqlite3.Row
            if not create and (self.db.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
                               or self.db.execute("PRAGMA user_version").fetchone()[0] != 1):
                raise DataError("not a supported recovery rehearsal database")
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            if create:
                with self.db:
                    self.db.executescript(f"""BEGIN IMMEDIATE;
                        PRAGMA application_id={APPLICATION_ID};
                        PRAGMA user_version=1;
                        CREATE TABLE intents (
                            client_id TEXT PRIMARY KEY, side TEXT NOT NULL, quantity TEXT NOT NULL,
                            price TEXT NOT NULL, prepared_ns INTEGER NOT NULL, attempted_ns INTEGER,
                            last_observed_ns INTEGER, exchange_order_id INTEGER UNIQUE,
                            order_state TEXT, matched_base TEXT, state TEXT NOT NULL);
                        CREATE TABLE journal (
                            seq INTEGER PRIMARY KEY, client_id TEXT NOT NULL, kind TEXT NOT NULL,
                            observed_ns INTEGER NOT NULL, details TEXT NOT NULL);
                        COMMIT;
                    """)
        except Exception:
            self.db.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.db.close()

    def _row(self, client_id):
        row = self.db.execute("SELECT * FROM intents WHERE client_id=?", (client_id,)).fetchone()
        if row is None:
            raise DataError("unknown rehearsal intent")
        return dict(row)

    def _event(self, client_id, kind, now_ns, **details):
        self.db.execute("INSERT INTO journal(client_id,kind,observed_ns,details) VALUES(?,?,?,?)",
                        (client_id, kind, now_ns, dumps(details)))

    def _check_clock(self, now_ns):
        timestamp(now_ns)
        latest = self.db.execute("SELECT observed_ns FROM journal ORDER BY seq DESC LIMIT 1").fetchone()
        if latest and now_ns < latest[0]:
            raise DataError("rehearsal clock regressed")

    def prepare(self, intent: Intent, now_ns: int):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self._check_clock(now_ns)
            existing = self.db.execute("SELECT side,quantity,price FROM intents WHERE client_id=?", (intent.client_id,)).fetchone()
            if existing:
                if (existing["side"], number(existing["quantity"]), number(existing["price"])) != (
                        intent.side, number(intent.quantity), number(intent.price)):
                    raise DataError("client ID already belongs to a different intent")
                return self._row(intent.client_id)
            if self.db.execute("SELECT 1 FROM intents WHERE state<>'RESOLVED' LIMIT 1").fetchone():
                raise DataError("an unresolved intent blocks another rehearsal order")
            self.db.execute("INSERT INTO intents(client_id,side,quantity,price,prepared_ns,state) VALUES(?,?,?,?,?,?)",
                            (intent.client_id, intent.side, intent.quantity, intent.price, now_ns, "PREPARED"))
            self._event(intent.client_id, "intent_prepared", now_ns)
            return self._row(intent.client_id)

    def begin_attempt(self, client_id: str, now_ns: int):
        """Commit uncertainty before the caller simulates handing the order off."""
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self._check_clock(now_ns)
            row = self._row(client_id)
            if row["state"] != "PREPARED":
                raise DataError("previous attempt exists and cannot be repeated")
            self.db.execute("UPDATE intents SET attempted_ns=?,state='UNCERTAIN' WHERE client_id=?",
                            (now_ns, client_id))
            self._event(client_id, "attempt_boundary_committed", now_ns)

    def observe(self, client_id, raw_pages, *, started_ns, received_ns, now_ns):
        """Apply one synthetic observation set using the production read parsers."""
        timestamp(started_ns)
        timestamp(received_ns)
        if (not started_ns <= received_ns <= timestamp(now_ns)
                or now_ns-started_ns > OBSERVATION_MAX_AGE_NS):
            raise DataError("stale, future or unordered recovery evidence")
        if not isinstance(raw_pages, dict) or set(raw_pages) != set(accounts.NOBITEX_ORDER_READS):
            raise DataError("all three synthetic read pages required")
        try:
            pages = {key: orders.parse_page(key, value) for key, value in raw_pages.items()}
        except (ValueError, KeyError, TypeError, ArithmeticError, AttributeError) as exc:
            raise DataError("invalid recovery pages") from exc
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self._check_clock(now_ns)
            row = self._row(client_id)
            if row["state"] in ("PREPARED", "CONFLICT", "RESOLVED"):
                raise DataError("intent is not awaiting reconciliation")
            if started_ns <= max(row["attempted_ns"], row["last_observed_ns"] or 0):
                raise DataError("recovery evidence overlaps an attempt or prior read")
            state, reason, observed = self._classify(row, pages, received_ns)
            if observed is not None:
                self.db.execute("UPDATE intents SET exchange_order_id=?,order_state=?,matched_base=? WHERE client_id=?",
                                (observed["exchange_order_id"], observed["status"], str(observed["matched_base"]), client_id))
            self.db.execute("UPDATE intents SET state=?,last_observed_ns=? WHERE client_id=?",
                            (state, received_ns, client_id))
            self._event(client_id, "observed", now_ns, state=state, reason=reason,
                        evidence_sha256=sha256(dumps(raw_pages).encode()).hexdigest(),
                        started_ns=started_ns, received_ns=received_ns)
            return {"state": state, "reason": reason}

    def _classify(self, row, pages, received_ns):
        expected_id = sha256(row["client_id"].encode()).hexdigest()
        candidates = [order for name in ("open_spot_orders", "recent_spot_orders") for order in pages[name]["rows"]
                      if order["client_order_id_sha256"] == expected_id]
        if not candidates:
            return row["state"], "not_in_bounded_pages", None
        ids = {order["exchange_order_id"] for order in candidates}
        if len(ids) != 1 or row["exchange_order_id"] not in (None, candidates[-1]["exchange_order_id"]):
            return "CONFLICT", "multiple_venue_ids_or_identity_changed", None
        candidate = candidates[-1]
        if (candidate["side"] != row["side"] or candidate["requested_base"] != number(row["quantity"])
                or candidate["execution"] != "Limit" or candidate["observed_trade_type"] != "Spot"
                or candidate["limit_price_quote_per_base"] != number(row["price"])):
            return "CONFLICT", "observed_order_differs_from_intent", None
        created = candidate["source_created_at_utc"]
        if created is None:
            return row["state"], "creation_time_unverified", None
        created_ns = source_ns(created)
        if not row["attempted_ns"] <= created_ns <= received_ns:
            return "CONFLICT", "order_creation_outside_attempt_window", None
        linked = self.db.execute("SELECT client_id FROM intents WHERE exchange_order_id=?",
                                 (candidate["exchange_order_id"],)).fetchone()
        if linked and linked[0] != row["client_id"]:
            return "CONFLICT", "venue_id_already_linked", None
        comparison = next(x for x in orders.reconcile(pages)["order_observations"]
                          if x["exchange_order_id"] == candidate["exchange_order_id"])
        if comparison["status"] == "order_changed_between_reads":
            return row["state"], "order_changed_between_nonatomic_reads", None
        if row["order_state"] in orders.TERMINAL_STATES:
            if (candidate["status"] != row["order_state"] or candidate["matched_base"] != number(row["matched_base"])):
                return "CONFLICT", "terminal_observation_changed", None
        if row["matched_base"] is not None and candidate["matched_base"] < number(row["matched_base"]):
            return "CONFLICT", "reported_match_regressed", None
        limit = number(row["price"])
        for fill in pages["recent_spot_fills"]["rows"]:
            if fill["exchange_order_id"] != candidate["exchange_order_id"]:
                continue
            if not created_ns <= source_ns(fill["source_time_utc"]) <= received_ns:
                return "CONFLICT", "fill_time_outside_observation_window", None
            price = fill["reported_price_quote_per_base"]
            if (row["side"] == "buy" and price > limit) or (row["side"] == "sell" and price < limit):
                return "CONFLICT", "fill_violates_intent_limit", None
        if comparison["status"] in ("side_disagreement", "observed_fills_exceed_order_match"):
            return "CONFLICT", comparison["status"], None
        if candidate["status"] in orders.OPEN_STATES:
            return "OPEN", "venue_order_observed_open", candidate
        if comparison["status"] == "observed_fills_below_order_match":
            return "TERMINAL_NEEDS_FILLS", "terminal_order_fill_coverage_incomplete", candidate
        return "RESOLVED", "terminal_observed_quantities_agree", candidate

    def report(self):
        rows = [dict(row) for row in self.db.execute("SELECT * FROM intents ORDER BY rowid")]
        return {"kind": "synthetic_order_recovery", "venue_schema": "nobitex", "pair": orders.PAIR,
                "intents": rows, "attempts_committed": self.db.execute(
                    "SELECT count(*) FROM journal WHERE kind='attempt_boundary_committed'").fetchone()[0],
                "unresolved_intents": sum(row["state"] != "RESOLVED" for row in rows),
                "live_trading_enabled": False, "fees_or_balances_reconciled": False}


def synthetic_pages(intent, *, status=None, matched="0", with_fill=False):
    """Small fixed-schema rehearsal fixture, deliberately free of account data."""
    market = {"srcCurrency": "Bitcoin", "dstCurrency": "Tether", "market": "BTC-USDT", "type": intent.side}
    order = {**market, "id": 101, "amount": intent.quantity, "matchedAmount": matched, "status": status,
             "execution": "Limit", "tradeType": "Spot", "price": intent.price, "clientOrderId": intent.client_id,
             "created_at": "2026-01-01T00:00:00.210Z"}
    fill = {**market, "id": 201, "orderId": 101, "amount": matched, "price": intent.price,
            "total": str(number(matched)*number(intent.price)), "timestamp": "2026-01-01T00:00:00.250Z"}
    return {"open_spot_orders": {"status": "ok", "orders": [order] if status in orders.OPEN_STATES else []},
            "recent_spot_orders": {"status": "ok", "orders": [order] if status is not None else []},
            "recent_spot_fills": {"status": "ok", "trades": [fill] if with_fill else []}}


def demo(directory: Path):
    directory.mkdir(parents=True, exist_ok=False)
    path = directory/"rehearsal.sqlite"
    intent = Intent("rehearsal-demo", "buy", "0.002", "50000")
    with RecoveryJournal(path, create=True) as journal:
        journal.prepare(intent, DEMO_EPOCH_NS+100_000_000)
        journal.begin_attempt(intent.client_id, DEMO_EPOCH_NS+200_000_000)
    with RecoveryJournal(path) as journal:
        outcomes = []
        scenarios = ({}, {"status": "Active", "matched": "0.001"},
                     {"status": "Canceled", "matched": "0.001"},
                     {"status": "Canceled", "matched": "0.001", "with_fill": True})
        for sequence, scenario in enumerate(scenarios, start=3):
            at = DEMO_EPOCH_NS+sequence*100_000_000
            outcomes.append(journal.observe(intent.client_id, synthetic_pages(intent, **scenario),
                            started_ns=at-2, received_ns=at-1, now_ns=at))
        report = {**journal.report(), "observations": outcomes}
    atomic_json(directory/"report.json", report)
    return report
