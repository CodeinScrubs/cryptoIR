"""Public capability observations; never silently substitute unknown rules."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from .domain import DataError, number
from .feeds import public_response

ENDPOINTS = {
    "nobitex": "https://apiv2.nobitex.ir/v2/options",
    "wallex": "https://api.wallex.ir/hector/web/v1/markets",
    "tabdeal": "https://api1.tabdeal.org/r/api/v1/exchangeInfo",
}


def precision_step(value):
    if type(value) is not int or not 0 <= value <= 30:
        raise DataError("invalid decimal-place metadata")
    return str(Decimal(10) ** -value)


def parse_catalog(venue: str, payload) -> list[dict]:
    markets = []
    if venue == "nobitex":
        if payload.get("status") != "ok":
            raise DataError("options request failed")
        options = payload["nobitex"]
        coins = {coin["coin"].upper(): coin for coin in payload["coins"]}
        active = {value.upper() for value in options["activeCurrencies"]}
        for symbol, amount_step in options["amountPrecisions"].items():
            quote = "USDT" if symbol.endswith("USDT") else "IRT" if symbol.endswith("IRT") else None
            if quote is None:
                raise DataError("unrecognized quote suffix; inspect schema")
            base = symbol[:-len(quote)]
            markets.append({"native_symbol": symbol, "base": base, "quote": quote,
                            "quantity_step": str(number(amount_step)),
                            "price_tick": str(number(options["pricePrecisions"][symbol])),
                            "minimum_notional": str(number(options["minOrders"]["usdt"])) if quote == "USDT" else None,
                            "native_scale": coins.get(base, {}).get("scale"),
                            "base_currency_active": base in active, "spot_allowed": None,
                            "status": "currency_metadata_only_not_market_status"})
    elif venue == "wallex":
        if payload.get("success") is not True:
            raise DataError("market request failed")
        for item in payload["result"]["markets"]:
            markets.append({"native_symbol": item["symbol"], "base": item["base_asset"],
                            "quote": item["quote_asset"],
                            "quantity_step": precision_step(item["amount_precision"]),
                            "price_tick": precision_step(item["price_precision"]),
                            # otc_min_notional is NOT a spot minimum.
                            "minimum_notional": None, "native_scale": None,
                            "spot_allowed": item["is_spot"], "margin_advertised": item["is_margin"],
                            "max_leverage_advertised": str(item.get("max_leverage")),
                            "status": "spot_enabled" if item["is_spot"] else "spot_disabled"})
    elif venue == "tabdeal":
        for item in payload:
            filters = {entry["filterType"]: entry for entry in item["filters"]}
            markets.append({"native_symbol": item["symbol"], "base": item["baseAsset"],
                            "quote": item["quoteAsset"],
                            "quantity_step": str(number(filters["LOT_SIZE"]["stepSize"])),
                            "minimum_quantity": str(number(filters["LOT_SIZE"]["minQty"])),
                            "price_tick": str(number(filters["PRICE_FILTER"]["tickSize"])),
                            "minimum_notional": str(number(filters["MIN_NOTIONAL"]["minNotional"])),
                            "spot_allowed": item["isSpotTradingAllowed"],
                            "margin_advertised": item["isMarginTradingAllowed"],
                            "status": item["status"], "native_scale": None})
    else:
        raise DataError("unsupported catalog venue")
    symbols = set()
    for market in markets:
        symbol = market["native_symbol"]
        if symbol in symbols:
            raise DataError("duplicate catalog symbol")
        symbols.add(symbol)
        if market["spot_allowed"] is not None and type(market["spot_allowed"]) is not bool:
            raise DataError("spot flag is not boolean")
        if "margin_advertised" in market and type(market["margin_advertised"]) is not bool:
            raise DataError("margin flag is not boolean")
        if any(number(market[name]) <= 0 for name in ("quantity_step", "price_tick")):
            raise DataError("invalid market increments")
        if market["minimum_notional"] is not None and number(market["minimum_notional"]) <= 0:
            raise DataError("invalid minimum")
        market.update(venue=venue, execution_ready=False, account_fee_rate=None,
                      asset_identity_cross_venue_verified=False)
    return markets


def fetch_catalog(venue: str) -> dict:
    result = {**public_response(ENDPOINTS[venue]), "venue": venue, "dataset": "market_catalog"}
    if result["kind"] == "snapshot":
        try:
            result["markets"] = parse_catalog(venue, result["payload"])
        except (ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
            result.update(kind="error", error_type=type(exc).__name__)
            if isinstance(exc, DataError):
                result["reason"] = str(exc)
    return result


def collect_catalogs() -> list[dict]:
    with ThreadPoolExecutor(max_workers=3) as executor:
        return list(executor.map(fetch_catalog, ENDPOINTS))
