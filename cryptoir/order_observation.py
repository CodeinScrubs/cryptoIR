"""Bounded, read-only BTC/USDT reconciliation against Nobitex account data."""

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import time

from . import __version__, accounts
from .accounts import AccountReadError, exact_sum, nonnegative


PAIR = "BTC/USDT"
PAGE_SIZE = 10
OPEN_STATES = {"New", "Active", "Inactive"}
TERMINAL_STATES = {"Done", "Canceled"}
ORDER_FIELDS = ("side", "status", "requested_base", "matched_base", "execution", "observed_trade_type",
                "limit_price_quote_per_base", "client_order_id_sha256", "source_created_at_utc")


def exchange_id(value):
    if type(value) is not int or not 0 < value < 2**63:
        raise AccountReadError("invalid_exchange_identifier")
    return value


def verify_pair(row):
    base, quote, market = row.get("srcCurrency"), row.get("dstCurrency"), row.get("market")
    if base not in ("Bitcoin", "btc", "BTC") or quote not in ("Tether", "usdt", "USDT"):
        raise AccountReadError("unexpected_pair_or_unverified_assets")
    if market is not None and market != "BTC-USDT":
        raise AccountReadError("unexpected_pair_or_unverified_assets")


def side(row):
    value = row["type"]
    if value not in ("buy", "sell"):
        raise AccountReadError("unknown_spot_side")
    return value


def optional_fee(row):
    value = row.get("fee")
    return nonnegative(value) if value is not None else None


def utc_time(value):
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.utcoffset() is None:
        raise AccountReadError("source_timestamp_requires_offset")
    return timestamp.astimezone(timezone.utc).isoformat()


def parse_order(row, *, open_only):
    verify_pair(row)
    status, execution, trade_type = row["status"], row["execution"], row.get("tradeType")
    if status not in OPEN_STATES | TERMINAL_STATES or open_only and status not in OPEN_STATES:
        raise AccountReadError("unexpected_or_unknown_order_state")
    if execution not in ("Limit", "Market", "StopLimit", "StopMarket"):
        raise AccountReadError("unknown_order_execution")
    if trade_type is not None and trade_type != "Spot":
        raise AccountReadError("unexpected_order_trade_type")
    amount, matched = nonnegative(row["amount"]), nonnegative(row["matchedAmount"])
    if amount <= 0 or matched > amount or (status == "Done" and matched != amount):
        raise AccountReadError("inconsistent_order_quantity")
    client_id = row.get("clientOrderId")
    # The bridge joins by venue ID. Retain only a digest of a supplied client ID.
    if client_id is not None and (not isinstance(client_id, str) or len(client_id) > 128):
        raise AccountReadError("invalid_client_order_identifier")
    limit = nonnegative(row["price"]) if execution == "Limit" and row.get("price") is not None else None
    if limit is not None and limit <= 0:
        raise AccountReadError("invalid_limit_price")
    return {"exchange_order_id": exchange_id(row["id"]), "side": side(row),
            "status": status, "execution": execution, "observed_trade_type": trade_type,
            "requested_base": amount, "matched_base": matched, "reported_fee": optional_fee(row),
            "limit_price_quote_per_base": limit,
            "source_created_at_utc": utc_time(row["created_at"]) if row.get("created_at") is not None else None,
            "fee_asset": None, "client_order_id_sha256": sha256(client_id.encode()).hexdigest() if client_id else None}


def parse_fill(row):
    verify_pair(row)
    timestamp = utc_time(row["timestamp"])
    amount, price, total = [nonnegative(row[field]) for field in ("amount", "price", "total")]
    if min(amount, price, total) <= 0:
        raise AccountReadError("invalid_fill_quantity_or_price")
    return {"exchange_fill_id": exchange_id(row["id"]), "exchange_order_id": exchange_id(row["orderId"]),
            "side": side(row), "reported_base_quantity": amount, "reported_price_quote_per_base": price,
            "reported_total_quote": total, "reported_fee": optional_fee(row), "fee_asset": None,
            "source_time_utc": timestamp}


def parse_page(operation, value):
    fills = operation == "recent_spot_fills"
    if value.get("status") != "ok":
        raise AccountReadError("venue_rejected_read")
    rows = value["trades" if fills else "orders"]
    if not isinstance(rows, list) or len(rows) > PAGE_SIZE:
        raise AccountReadError("unexpected_order_or_fill_page_size")
    more = value.get("hasNext")
    if more is not None and type(more) is not bool:
        raise AccountReadError("invalid_page_continuation")
    normalized = [parse_fill(row) if fills else parse_order(row, open_only=operation == "open_spot_orders")
                  for row in rows]
    id_field = "exchange_fill_id" if fills else "exchange_order_id"
    if len({row[id_field] for row in normalized}) != len(normalized):
        raise AccountReadError("duplicate_exchange_identifier")
    return {"rows": normalized, "has_next": more, "page": 1, "requested_page_size": PAGE_SIZE}


def reconcile(pages):
    operations = accounts.NOBITEX_ORDER_READS
    if not all(operation in pages for operation in operations):
        return {"status": "incomplete_reads", "order_observations": []}
    open_orders, recent_orders, recent_fills = (pages[operation]["rows"] for operation in operations)
    observations = defaultdict(list)
    fills = defaultdict(list)
    for order in open_orders + recent_orders:
        observations[order["exchange_order_id"]].append(order)
    for fill in recent_fills:
        fills[fill["exchange_order_id"]].append(fill)
    rows = []
    for order_id in sorted(set(observations) | set(fills)):
        orders, executions = observations[order_id], fills[order_id]
        total = Decimal(0)
        for fill in executions:
            total = exact_sum(total, fill["reported_base_quantity"])
        matched = orders[-1]["matched_base"] if orders else None
        if not orders:
            status = "order_not_in_observed_pages"
        elif any(orders[0][field] != orders[-1][field] for field in ORDER_FIELDS):
            status = "order_changed_between_reads"
        elif any(fill["side"] != orders[-1]["side"] for fill in executions):
            status = "side_disagreement"
        elif total > matched:
            status = "observed_fills_exceed_order_match"
        elif total < matched:
            status = "observed_fills_below_order_match"
        else:
            status = "matched_observed_quantities" if executions else "no_fill_evidence"
        rows.append({"exchange_order_id": order_id, "status": status,
                     "latest_observed_order_matched_base": matched, "observed_fill_base_total": total,
                     "observed_fill_ids": [fill["exchange_fill_id"] for fill in executions]})
    if any(r["status"] in ("side_disagreement", "observed_fills_exceed_order_match", "order_changed_between_reads") for r in rows):
        overall = "discrepancy_or_change"
    elif any(r["status"] in ("order_not_in_observed_pages", "observed_fills_below_order_match") for r in rows):
        overall = "partial_observed_coverage"
    elif any(r["status"] == "matched_observed_quantities" for r in rows):
        overall = "matched_observed_quantities"
    else:
        overall = "no_fill_evidence"
    return {"status": overall, "order_observations": rows}


def inspect_orders(credentials):
    results, pages = [], {}
    for operation in accounts.NOBITEX_ORDER_READS:
        try:
            raw, evidence = accounts.read_operation("nobitex", operation, credentials)
            data = parse_page(operation, raw)
            pages[operation] = data
            results.append({"operation": operation, "status": "accepted", "data": data, "evidence": evidence})
        except AccountReadError as exc:
            results.append({"operation": operation, "status": "unavailable", "reason": str(exc)})
        except (ValueError, TypeError, KeyError, ArithmeticError, AttributeError):
            results.append({"operation": operation, "status": "unavailable", "reason": "unrecognized_response_schema"})
    return {"schema": 1, "kind": "private_spot_reconciliation", "venue": "nobitex", "pair": PAIR,
            "observed_ns": time.time_ns(), "client_version": __version__,
            "parser_sha256": sha256(Path(__file__).read_bytes()).hexdigest(), "results": results,
            "reconciliation": reconcile(pages), "atomic_snapshot": False, "complete_order_history": False,
            "local_live_intents_reconciled": False, "fee_assets_verified": False,
            "live_trading_enabled": False, "raw_responses_retained": False}


def order_check():
    """Output only operation statuses and the name of an ignored local artifact."""
    from .credentials import load_credentials

    try:
        credentials = load_credentials("nobitex")
        if credentials is None:
            raise AccountReadError("saved_nobitex_credential_required")
        try:
            report = inspect_orders(credentials)
        finally:
            del credentials
        path = accounts.save_assessment(report)
        summaries = [{k: v for k, v in r.items() if k in ("operation", "status", "reason")} for r in report["results"]]
        print(json.dumps({"kind": "order_read_summary", "venue": "nobitex", "pair": PAIR,
                          "operations": summaries, "private_report": ".local/accounts/"+path.name,
                          "live_trading_enabled": False}))
        return int(any(r["status"] != "accepted" for r in summaries))
    except AccountReadError as exc:
        reason = str(exc)
    except Exception:
        reason = "spot_reconciliation_failed_details_redacted"
    print(json.dumps({"kind": "order_read_unavailable", "reason": reason, "live_trading_enabled": False}))
    return 1
