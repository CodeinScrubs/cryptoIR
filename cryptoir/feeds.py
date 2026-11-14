"""Public spot snapshots only; adapters never authenticate or submit orders."""

import hashlib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from . import __version__
from .domain import Book, DataError, Level, number

VENUES = ("nobitex", "wallex", "tabdeal")
MAX_BYTES = 4 * 1024 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise DataError("unexpected API redirect; investigate endpoint")


def endpoint(venue: str, pair: str) -> str:
    # Fiat and contract-unit mappings need separate evidence before enabling.
    if not re.fullmatch(r"[A-Z0-9]{2,20}/USDT", pair):
        raise DataError("bootstrap collector supports explicit BASE/USDT spot pairs only")
    base, quote = pair.split("/")
    if base == quote:
        raise DataError("identical assets")
    symbol = base + quote
    if venue == "nobitex":
        return "https://apiv2.nobitex.ir/v3/orderbook/" + symbol
    if venue == "wallex":
        return "https://api.wallex.ir/v1/depth?" + urlencode({"symbol": symbol})
    if venue == "tabdeal":
        return "https://api1.tabdeal.org/r/api/v1/depth?" + urlencode({"symbol": symbol, "limit": 50})
    raise DataError("unsupported venue")


def normalize(record: dict) -> Book:
    if record.get("schema") != 1 or record.get("kind") != "snapshot":
        raise DataError("not a versioned successful snapshot")
    venue, pair, payload = record["venue"], record["pair"], record["payload"]
    if not isinstance(payload, dict):
        raise DataError("order-book payload must be an object")
    if record["url"] != endpoint(venue, pair):
        raise DataError("endpoint provenance mismatch")
    exchange_ns = None
    if venue == "nobitex":
        if payload.get("status") != "ok":
            raise DataError("exchange rejected snapshot")
        timestamp = number(payload["lastUpdate"])
        if timestamp != timestamp.to_integral_value():
            raise DataError("invalid millisecond timestamp")
        exchange_ns = int(timestamp) * 1_000_000
        bids, asks = payload["bids"], payload["asks"]
    elif venue == "wallex":
        if payload.get("success") is not True:
            raise DataError("exchange rejected snapshot")
        bids = [(x["price"], x["quantity"]) for x in payload["result"]["bid"]]
        asks = [(x["price"], x["quantity"]) for x in payload["result"]["ask"]]
    else:
        bids, asks = payload["bids"], payload["asks"]

    def levels(rows):
        return tuple(Level(number(row[0]), number(row[1])) for row in rows)

    return Book(venue, pair, levels(bids), levels(asks), record["received_ns"],
                record["duration_ns"], exchange_ns)


def public_response(url: str, timeout: float = 10) -> dict:
    """Bounded unauthenticated GET. Callers supply fixed official endpoints."""
    started_ns, started_mono = time.time_ns(), time.monotonic_ns()
    record = {"schema": 1, "url": url, "started_ns": started_ns}
    try:
        request = Request(url, headers={"User-Agent": "cryptoIR/" + __version__, "Accept": "application/json"}, method="GET")
        with build_opener(NoRedirect).open(request, timeout=timeout) as response:
            raw = response.read(MAX_BYTES + 1)
            received_ns = time.time_ns()
            duration_ns = time.monotonic_ns() - started_mono
            if len(raw) > MAX_BYTES:
                raise DataError("response exceeds bounded size")
            record.update({"kind": "snapshot", "received_ns": received_ns,
                           "duration_ns": duration_ns, "status_code": response.status,
                           "body_sha256": hashlib.sha256(raw).hexdigest(),
                           "raw_body": raw.decode("utf-8"),
                           "payload": json.loads(raw, parse_float=Decimal),
                           "source": "public_rest_poll"})
            if received_ns < started_ns or abs(received_ns - started_ns - duration_ns) > 1_000_000_000:
                raise DataError("wall clock changed during request")
    except Exception as exc:
        # Store bounded diagnostics; never headers, cookies, environment or secrets.
        record.update({"kind": "error", "received_ns": time.time_ns(),
                       "duration_ns": time.monotonic_ns() - started_mono,
                       "error_type": type(exc).__name__})
        if isinstance(exc, HTTPError):
            record["status_code"] = exc.code
        if isinstance(exc, DataError):
            record["reason"] = str(exc)
    return record


def fetch(venue: str, pair: str, timeout: float = 10) -> dict:
    record = {**public_response(endpoint(venue, pair), timeout), "venue": venue, "pair": pair}
    if record["kind"] == "snapshot":
        try:
            normalize(record)
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            record.update(kind="error", error_type=type(exc).__name__)
            if isinstance(exc, DataError):
                record["reason"] = str(exc)
    return record


def collect(pair: str) -> list[dict]:
    with ThreadPoolExecutor(max_workers=len(VENUES)) as executor:
        futures = [executor.submit(fetch, venue, pair) for venue in VENUES]
        return sorted((f.result() for f in as_completed(futures)), key=lambda x: x["received_ns"])
