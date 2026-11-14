import json
import hashlib
import tempfile
import time
import unittest
from dataclasses import asdict,replace
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch

from cryptoir.domain import Book,DataError,Level
from cryptoir.feeds import endpoint, native_symbol
from cryptoir.forward import ForwardConfig
from cryptoir.funding import fund_paper
from cryptoir.history import download_history
from cryptoir.streams import StreamParser,dumps
from cryptoir.valuation import (ALL_BOOKS,ConversionRules,buy_budget,create_capital_receipt,
                               matched_valuation,sell_value,snapshot_pair,verify_receipt)

NS=1_000_000_000
FX=ConversionRules("0.01",1,"0.0025",1)
SPOT=ConversionRules("0.001",1,"0.0013",1)


def books(now=100*NS,fx_bid=999,fx_ask=1000,spot_bid=99,spot_ask=100):
    return (Book("nobitex","USDT/IRR",(Level(fx_bid,10000),),(Level(fx_ask,10000),),now,0,now-1_000_000),
            Book("nobitex","BTC/USDT",(Level(spot_bid,10000),),(Level(spot_ask,10000),),now,0,now-1_000_000))


def all_response(now=100*NS):
    payload={"status":"ok"}
    for book in books(now):
        payload[native_symbol("nobitex",book.pair)]={"lastUpdate":book.exchange_ns//1_000_000,
            "bids":[[str(l.price),str(l.quantity)] for l in book.bids],
            "asks":[[str(l.price),str(l.quantity)] for l in book.asks]}
    body=dumps(payload)
    return {"schema":1,"kind":"snapshot","url":ALL_BOOKS,"received_ns":now,"duration_ns":0,
            "payload":payload,"raw_body":body,"body_sha256":hashlib.sha256(body.encode()).hexdigest()}


class ValuationTests(unittest.TestCase):
    def receipt(self):
        fx,spot=books()
        return create_capital_receipt(fx,spot,10000,FX,SPOT,now_ns=100*NS,max_age_ns=5*NS,source_sha256="fixture")

    def test_native_irt_suffix_does_not_relabel_rials_as_toman(self):
        self.assertTrue(endpoint("nobitex","USDT/IRR").endswith("USDTIRT"))
        self.assertEqual(StreamParser("nobitex","USDT/IRR").channels,["public:orderbook-USDTIRT"])
        for venue,pair in (("nobitex","USDT/IRT"),("wallex","USDT/IRR"),("tabdeal","USDT/IRR")):
            with self.assertRaises(DataError):
                endpoint(venue,pair)

    def test_all_market_payload_integrity(self):
        record=all_response()
        book=snapshot_pair(record,"USDT/IRR")
        self.assertEqual(book.asks[0].price,1000)
        self.assertEqual(book.pair,"USDT/IRR")
        record["payload"]["USDTIRT"]["asks"][0][0]="100"
        with self.assertRaises(DataError):
            snapshot_pair(record,"USDT/IRR")

    def test_budget_conservation_received_fee_and_factor_ten(self):
        receipt=self.receipt()
        self.assertEqual(receipt["initial_irr"],100000)
        self.assertEqual(receipt["conversion"]["gross_base"],100)
        self.assertEqual(receipt["conversion"]["net_base"],D("99.75"))
        self.assertEqual(receipt["conversion"]["spent_quote"]+receipt["conversion"]["residual_quote"],100000)
        self.assertEqual(receipt["benchmarks"]["usdt_hold"]["usdt"],D("99.75"))
        self.assertLess(receipt["benchmarks"]["btc_hold"]["btc"],1)

    def test_receipt_roundtrip_and_recomputed_hash_cannot_hide_changed_amount(self):
        receipt=json.loads(dumps(self.receipt()))
        verify_receipt(receipt)
        receipt["conversion"]["net_base"]="999"
        original=dict(receipt)
        original.pop("receipt_sha256")
        receipt["receipt_sha256"]=hashlib.sha256(dumps(original).encode()).hexdigest()
        with self.assertRaisesRegex(DataError,"reconstruction"):
            verify_receipt(receipt)

    def test_flat_prices_loss_includes_conversion_and_exit_costs(self):
        receipt=self.receipt()
        fx,spot=books(101*NS)
        value=matched_valuation(receipt,fx,spot,receipt["conversion"]["net_base"],0,now_ns=101*NS,max_age_ns=5*NS)
        self.assertLess(value["portfolio"]["net_return_irt"],0)
        self.assertEqual(value["excess_return_irt_vs_usdt_hold"],0)
        self.assertLess(value["benchmarks"]["btc_hold"]["net_return_irt"],value["benchmarks"]["usdt_hold"]["net_return_irt"])

    def test_fx_appreciation_is_separated_from_strategy_profit(self):
        receipt=self.receipt()
        fx,spot=books(101*NS,fx_bid=1998,fx_ask=2000)
        value=matched_valuation(receipt,fx,spot,receipt["conversion"]["net_base"],0,now_ns=101*NS,max_age_ns=5*NS)
        self.assertEqual(value["fx_return_mid"],1)
        self.assertGreater(value["portfolio"]["net_return_irt"],D("0.9"))
        self.assertLess(value["portfolio"]["net_return_usdt_numeraire"],0)
        self.assertLess(abs(value["attribution_identity_error"]),D("1e-24"))
        self.assertEqual(value["excess_return_irt_vs_usdt_hold"],0)

    def test_dust_is_preserved_but_not_assumed_sellable(self):
        fx,_=books()
        result=sell_value(fx,D("0.001"),FX)
        self.assertEqual(result["net_quote"],0)
        self.assertEqual(result["dust"],D("0.001"))

    def test_liquidity_fraction_and_multilevel_budget(self):
        fx,_=books()
        fx=replace(fx,asks=(Level(1000,1),Level(2000,100)))
        result=buy_budget(fx,2000,replace(FX,participation=D("0.5")))
        self.assertEqual(result["gross_base"],D("1.25"))
        self.assertEqual(result["spent_quote"],2000)
        thin=replace(fx,bids=(Level(999,D("0.1")),))
        with self.assertRaisesRegex(DataError,"exit depth"):
            sell_value(thin,1,FX)

    def test_partial_conversion_keeps_unspent_fiat(self):
        fx,_=books()
        thin=replace(fx,asks=(Level(1000,1),))
        result=buy_budget(thin,10000,FX)
        self.assertEqual(result["residual_quote"],9000)
        self.assertFalse(result["complete_budget_deployment"])

    def test_stale_wrong_pair_and_future_receipt_rejected(self):
        receipt=self.receipt()
        fx,spot=books()
        with self.assertRaises(DataError):
            matched_valuation(receipt,fx,spot,100,0,now_ns=110*NS,max_age_ns=5*NS)
        with self.assertRaises(DataError):
            matched_valuation(receipt,replace(fx,pair="USDT/IRT"),spot,100,0,now_ns=100*NS,max_age_ns=5*NS)

    def test_fiat_history_is_not_accidentally_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(DataError,"historical fiat"):
                download_history(Path(tmp)/"data","nobitex","USDT/IRR",1,100)

    def test_funding_uses_observed_rules_and_never_submits_real_order(self):
        path=Path(__file__).resolve().parents[1]/"examples"/"paper-reference.json"
        config=ForwardConfig.from_dict(json.loads(path.read_bytes()))
        metadata={"kind":"snapshot","payload":{"nobitex":{"minOrders":{"rls":"1"}}},"markets":[
            {"native_symbol":"BTCUSDT","native_scale":"1","base_currency_active":True,"quantity_step":"0.001","price_tick":"0.01","minimum_notional":"1"},
            {"native_symbol":"USDTIRT","native_scale":"1","base_currency_active":True,"quantity_step":"0.01","price_tick":"1","minimum_notional":None}]}
        with tempfile.TemporaryDirectory() as tmp, patch("cryptoir.funding.fetch_catalog",return_value=metadata), patch("cryptoir.funding.public_response",return_value=all_response()),patch("cryptoir.funding.time.time_ns",return_value=100*NS):
            directory=Path(tmp)/"paper"
            result=fund_paper(directory,10000,config,"0.0025")
            saved=ForwardConfig.from_dict(json.loads((directory/"config.json").read_bytes()))
            self.assertEqual(saved.risk.initial_quote,D("99.75"))
            self.assertEqual(saved.rules.minimum_notional,1)
            self.assertEqual(saved.capital_basis,"paper_irt_conversion")
            self.assertFalse(result["live_execution"])
            self.assertFalse((directory/"paper.sqlite").exists())
            with self.assertRaises(FileExistsError):
                fund_paper(directory,10000,config,"0.0025")


if __name__=="__main__":
    unittest.main()
