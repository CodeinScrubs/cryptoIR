import unittest
from copy import deepcopy

from cryptoir.catalog import parse_catalog, precision_step
from cryptoir.domain import DataError


class CatalogTests(unittest.TestCase):
    def wallex(self):
        return {"success": True, "result": {"markets": [{"symbol": "BTCUSDT", "base_asset": "BTC",
                "quote_asset": "USDT", "amount_precision": 8, "price_precision": 2,
                "is_spot": True, "is_margin": True, "max_leverage": 10, "otc_min_notional": 2}]}}

    def nobitex(self):
        return {"status": "ok", "coins": [{"coin": "1k_shib", "scale": "1000"}], "nobitex": {
            "activeCurrencies": ["1k_shib"], "amountPrecisions": {"1K_SHIBUSDT": "0.01", "1K_SHIBIRT": "0.01"},
            "pricePrecisions": {"1K_SHIBUSDT": "0.00001", "1K_SHIBIRT": "10"},
            "minOrders": {"usdt": "0.5", "rls": "500000"}}}

    def tabdeal(self):
        return [{"symbol": "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT", "status": "TRADING",
                 "isSpotTradingAllowed": True, "isMarginTradingAllowed": False,
                 "filters": [{"filterType": "LOT_SIZE", "stepSize": "0.000001", "minQty": "0.000002"},
                             {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                             {"filterType": "MIN_NOTIONAL", "minNotional": "1"}]}]

    def test_otc_minimum_not_assigned_to_spot(self):
        market = parse_catalog("wallex", self.wallex())[0]
        self.assertIsNone(market["minimum_notional"])
        self.assertIsNone(market["account_fee_rate"])
        self.assertFalse(market["execution_ready"])

    def test_preserve_scaled_asset_and_uncertain_fiat_units(self):
        dollar, fiat = parse_catalog("nobitex", self.nobitex())
        self.assertEqual(dollar["base"], "1K_SHIB")
        self.assertEqual(dollar["native_scale"], "1000")
        self.assertIsNone(fiat["minimum_notional"])
        self.assertIsNone(dollar["spot_allowed"])
        self.assertFalse(dollar["asset_identity_cross_venue_verified"])

    def test_explicit_tabdeal_filters(self):
        market = parse_catalog("tabdeal", self.tabdeal())[0]
        self.assertEqual(market["quantity_step"], "0.000001")
        self.assertEqual(market["minimum_quantity"], "0.000002")
        self.assertEqual(market["minimum_notional"], "1")

    def test_precision_validation(self):
        self.assertEqual(precision_step(2), "0.01")
        for value in (-1, 31, True, 1.5, "2"):
            with self.assertRaises(DataError):
                precision_step(value)

    def test_duplicate_symbols_rejected(self):
        payload = self.tabdeal()
        payload.append(deepcopy(payload[0]))
        with self.assertRaises(DataError):
            parse_catalog("tabdeal", payload)

    def test_boolean_string_rejected(self):
        payload = self.wallex()
        payload["result"]["markets"][0]["is_spot"] = "false"
        with self.assertRaises(DataError):
            parse_catalog("wallex", payload)

    def test_missing_filter_is_not_defaulted(self):
        payload = self.tabdeal()
        payload[0]["filters"].pop()
        with self.assertRaises(KeyError):
            parse_catalog("tabdeal", payload)

    def test_failed_exchange_response(self):
        with self.assertRaises(DataError):
            parse_catalog("wallex", {"success": False})


if __name__ == "__main__":
    unittest.main()
