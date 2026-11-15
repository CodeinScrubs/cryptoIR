import copy
from decimal import Decimal as D
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from cryptoir.domain import DataError
from cryptoir import products as p


NOB={'status':'ok','markets':{'BTCUSDT':{'srcCurrency':'btc','dstCurrency':'usdt','maxLeverage':'5',
    'positionFeeRate':'0.0005','sellEnabled':True,'buyEnabled':True}}}
WAL={'success':True,'result':[{'symbol':'BTCUSDT','base':'BTC','quote':'USDT','min_risk_coef':'1',
    'max_risk_coef':'10','risk_coef_step':'0.5','liquidity_margin':'0.15','max_age':1814400}]}
TAB={'symbols':[{'symbol':'BTC_USDT','status':'TRADING','baseAsset':'BTC','quoteAsset':'USDT',
    'pricePrecision':1,'quantityPrecision':5,'quotePrecision':3}]}
VALUES={'nobitex_margin':NOB,'wallex_margin':WAL,'tabdeal_fapi':TAB}
DEPTH={'lastUpdateId':10,'E':0,'bids':[['99','2']],'asks':[['100','3']]}
REQUEST={'market':'BTCUSDT','side':'long','collateral':'100','risk_coef':'5','open_price':'60000'}


def response(url,value):
    raw=json.dumps(value)
    return {'schema':1,'kind':'snapshot','url':url,'status_code':200,'raw_body':raw,'payload':value,
            'body_sha256':hashlib.sha256(raw.encode()).hexdigest(),'started_ns':1_000_000_000,
            'received_ns':1_100_000_000,'duration_ns':100_000_000}


def quote_response(side='long'):
    request=dict(REQUEST,side=side)
    fields={'market':'BTCUSDT','side':side,'risk_coef':'5','open_price':'60000',
            'max_age':1814400,'extend_time':14400,'call_price':'55000' if side=='long' else '65000',
            'liquidity_price':'53000' if side=='long' else '67000'}
    for key,value,currency in [('collateral','100','USDT'),('loan','500','USDT'),('trade_fee','0.00002','BTC'),
                               ('open_fee','0.08','USDT'),('extend_fee','0.08','USDT')]:
        fields[key]={'value':value,'currency':currency}
    result=response(p.WALLEX_CALCULATIONS['dry-run'],{'success':True,'result':fields})
    result.update(method='POST',request=request)
    return result,request


class ProductCatalogTests(unittest.TestCase):
    def test_unknown_rules_stay_unknown_not_favorable(self):
        for family,value in VALUES.items():
            item=p.parse_products(family,value)[0]
            self.assertIsNone(item['contract_multiplier'])
            self.assertIsNone(item['quantity_step'])
            self.assertIsNone(item['price_tick'])
            self.assertIsNone(item['maintenance_margin_schedule'])
            self.assertFalse(item['faithful_backtest_ready'])
            self.assertFalse(item['account_verified'])
            self.assertTrue(item['product_id'].startswith(family+':'))
        nob=p.parse_products('nobitex_margin',NOB)[0]
        self.assertEqual(nob['public_max_leverage'],D('5'))
        self.assertIsNone(nob['renewal_fields']['sellPositionFeeRate'])
        wal=p.parse_products('wallex_margin',WAL)[0]
        self.assertIsNone(wal['public_max_leverage'])
        self.assertTrue(wal['credit_ratio_is_not_cross_venue_leverage'])

    def test_catalog_symbols_are_used_verbatim(self):
        cat=p.catalog_record('tabdeal_fapi',response(p.CATALOGS['tabdeal_fapi'],TAB))
        self.assertEqual(p.resolve_product(cat,'BTC/USDT')['native_symbol'],'BTC_USDT')
        duplicate=copy.deepcopy(TAB)
        duplicate['symbols'].append(dict(duplicate['symbols'][0],symbol='BTCUSDT'))
        cat=p.catalog_record('tabdeal_fapi',response(p.CATALOGS['tabdeal_fapi'],duplicate))
        with self.assertRaisesRegex(DataError,'ambiguous'):
            p.resolve_product(cat,'BTC/USDT')

    def test_duplicates_bad_flags_and_reversed_ranges_rejected(self):
        tab=copy.deepcopy(TAB);tab['symbols']*=2
        wal=copy.deepcopy(WAL);wal['result'][0]['min_risk_coef']='11'
        nob=copy.deepcopy(NOB);nob['markets']['BTCUSDT']['sellEnabled']='true'
        for family,value in [('tabdeal_fapi',tab),('wallex_margin',wal),('nobitex_margin',nob)]:
            with self.subTest(family=family),self.assertRaises(DataError):
                p.parse_products(family,value)

    def test_inactive_or_missing_or_injected_pair_rejected(self):
        cat=p.catalog_record('tabdeal_fapi',response(p.CATALOGS['tabdeal_fapi'],TAB))
        for pair in ('BTCUSDT','btc/USDT','ETH/USDT','BTC/USDT?key=value','BTC/BTC'):
            with self.subTest(pair=pair),self.assertRaises(DataError):
                p.resolve_product(cat,pair)
        cat['markets'][0]['status']='HALT'
        with self.assertRaises(DataError):p.resolve_product(cat,'BTC/USDT')

    def test_hash_payload_url_and_clocks_are_verified(self):
        for field,value in [('url','https://example.invalid'),('body_sha256','0'*64),('payload',{}),
                            ('received_ns',1),('duration_ns',True)]:
            record=response(p.CATALOGS['tabdeal_fapi'],TAB);record[field]=value
            with self.subTest(field=field),self.assertRaises(DataError):
                p.checked_payload(record,p.CATALOGS['tabdeal_fapi'])
        for raw in ('{"symbols":[],"symbols":[]}','{"x":NaN}'):
            with self.assertRaises(DataError):p.strict_json(raw)

    def test_precision_is_not_a_tick_or_contract_specification(self):
        item=p.parse_products('tabdeal_fapi',TAB)[0]
        self.assertEqual(item['reported_precision']['pricePrecision'],1)
        self.assertIsNone(item['price_tick'])
        self.assertTrue(item['precision_is_not_order_step'])
        for invalid in (-1,True,31,'2'):
            value=copy.deepcopy(TAB);value['symbols'][0]['quantityPrecision']=invalid
            with self.assertRaises(DataError):p.parse_products('tabdeal_fapi',value)


class ProductBookTests(unittest.TestCase):
    def book(self,value):
        product=p.parse_products('tabdeal_fapi',TAB)[0]
        return p.product_book_record(product,response(p.TABDEAL_DEPTH+'?symbol=BTC_USDT&limit=5',value))

    def test_zero_event_time_is_unknown_and_never_a_spot_book(self):
        book=self.book(DEPTH)
        self.assertEqual(book['status'],'observed')
        self.assertIsNone(book['exchange_ns'])
        self.assertEqual(book['reported_event_time'],D(0))
        self.assertFalse(book['spot_compatible'])
        self.assertFalse(book['executable'])
        self.assertFalse(book['source_freshness_verified'])
        nob=p.parse_products('nobitex_margin',NOB)[0]
        with self.assertRaises(DataError):p.product_book_record(nob,{})

    def test_crossed_empty_duplicate_levels_and_invalid_values_rejected(self):
        for bids in ([],[['100','1']],[['99','1'],['99','2']],[['99','-1']],[['NaN','1']]):
            self.assertEqual(self.book(dict(DEPTH,bids=bids))['status'],'unavailable')


class ProductArtifactTests(unittest.TestCase):
    def fake_public(self,url):
        for family,endpoint in p.CATALOGS.items():
            if url==endpoint:return response(url,VALUES[family])
        self.assertEqual(url,p.TABDEAL_DEPTH+'?symbol=BTC_USDT&limit=5')
        return response(url,DEPTH)

    def test_collect_replay_and_refuse_overwrite_before_network(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(p,'public_response',side_effect=self.fake_public) as fetch:
            path=Path(tmp)/'products.jsonl'
            result=p.collect_products(path,'BTC/USDT')
            self.assertTrue(result['all_requests_observed'])
            self.assertEqual(fetch.call_count,4)
            self.assertEqual(p.audit_products(path)['observed_records'],4)
            with self.assertRaises(FileExistsError):p.collect_products(path)
            self.assertEqual(fetch.call_count,4)
            rows=path.read_text().splitlines();row=json.loads(rows[1]);row['markets'][0]['public_max_leverage']='100'
            rows[1]=json.dumps(row);path.write_text('\n'.join(rows))
            with self.assertRaises(DataError):p.audit_products(path)

    def test_partial_catalog_failures_are_explicit(self):
        def fetch(url):
            return {'schema':1,'url':url,'kind':'error','status_code':503}
        with tempfile.TemporaryDirectory() as tmp,patch.object(p,'public_response',side_effect=fetch):
            path=Path(tmp)/'products.jsonl'
            self.assertFalse(p.collect_products(path)['all_requests_observed'])
            audit=p.audit_products(path)
            self.assertEqual(audit['unavailable_records'],3)
            self.assertFalse(audit['all_requests_observed'])

    def test_truncated_and_missing_requested_book_are_not_complete(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(p,'public_response',side_effect=self.fake_public):
            path=Path(tmp)/'products.jsonl';p.collect_products(path,'BTC/USDT')
            original=path.read_text().splitlines()
            for rows in (original[:-1],original[:-2]+original[-1:],original[:-1]+[original[-2],original[-1]]):
                path.write_text('\n'.join(rows))
                with self.assertRaises(DataError):p.audit_products(path)

    def test_unresolved_requested_pair_is_preserved_as_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(p,'public_response',side_effect=self.fake_public) as fetch:
            path=Path(tmp)/'products.jsonl'
            self.assertFalse(p.collect_products(path,'ETH/USDT')['all_requests_observed'])
            self.assertEqual(fetch.call_count,3)
            result=p.audit_products(path)
            self.assertFalse(result['all_requests_observed'])
            self.assertEqual(result['unavailable_records'],1)


class PublicMarginQuoteTests(unittest.TestCase):
    def test_only_documented_calculations_can_be_requested(self):
        req=p.calculation_request('dry-run',REQUEST)
        self.assertEqual(req.method,'POST')
        self.assertEqual(req.full_url,'https://api.wallex.ir/margin-trade/v1/public/dry-run')
        self.assertEqual(json.loads(req.data),REQUEST)
        self.assertNotIn('authorization',{k.lower() for k,v in req.header_items()})
        for op in ('open','order','transfer','../dry-run'):
            with self.assertRaises(DataError):p.calculation_request(op,REQUEST)
        for request in (dict(REQUEST,key='secret'),dict(REQUEST,market='BTCUSDT?x=1'),dict(REQUEST,side='buy'),dict(REQUEST,collateral='-1')):
            with self.assertRaises(DataError):p.calculation_request('dry-run',request)

    def test_fee_currencies_remain_separate_and_exit_fee_unknown(self):
        response,request=quote_response()
        quote=p.parse_quote(response,request)
        self.assertEqual(quote['amounts']['trade_fee']['native_currency'],'BTC')
        self.assertEqual(quote['amounts']['extend_fee']['native_currency'],'USDT')
        self.assertIsNone(quote['exit_fee'])
        self.assertEqual(quote['extend_time_seconds'],14400)
        self.assertFalse(quote['fees_applied_to_paper'])
        self.assertFalse(quote['quote_is_position'])

    def test_changed_terms_and_inconsistent_liquidation_prices_fail(self):
        for field,value in [('open_price','60001'),('risk_coef','10'),('market','ETHUSDT'),
                            ('side','short'),('liquidity_price','70000'),('extend_time',True)]:
            r,request=quote_response();payload=r['payload'];payload['result'][field]=value
            rebuilt=response(r['url'],payload);rebuilt.update(method='POST',request=request)
            with self.subTest(field=field),self.assertRaises(DataError):p.parse_quote(rebuilt,request)
        r,request=quote_response('short');self.assertTrue(p.parse_quote(r,request)['quoted_liquidation_price']>D('60000'))

    def test_public_validation_error_is_recorded_without_retry(self):
        raw=b'{"success":false,"result":{"open_price":"outside allowed range"}}'
        error=HTTPError(p.WALLEX_CALCULATIONS['dry-run'],422,'invalid',{},io.BytesIO(raw))
        with patch.object(p,'build_opener') as opener:
            opener.return_value.open.side_effect=error
            result=p.public_calculation('dry-run',REQUEST)
            self.assertEqual(opener.return_value.open.call_count,1)
        self.assertEqual(result['kind'],'http_error')
        self.assertEqual(result['status_code'],422)
        self.assertEqual(result['body_sha256'],hashlib.sha256(raw).hexdigest())
        with self.assertRaises(DataError):p.parse_quote(result,REQUEST)

    def test_quote_artifact_replays_and_detects_tampering(self):
        r,request=quote_response()
        with tempfile.TemporaryDirectory() as tmp,patch.object(p,'public_calculation',return_value=r) as fetch:
            path=Path(tmp)/'quote.json'
            result=p.margin_quote(path,'BTCUSDT','long',100,5,60000)
            self.assertEqual(result['status'],'observed')
            self.assertEqual(p.audit_quote(path)['status'],'observed')
            with self.assertRaises(FileExistsError):p.margin_quote(path,'BTCUSDT','long',100,5,60000)
            self.assertEqual(fetch.call_count,1)
            record=json.loads(path.read_text());record['quote']['amounts']['extend_fee']['value']='0'
            path.write_text(json.dumps(record))
            with self.assertRaises(DataError):p.audit_quote(path)


if __name__=='__main__':unittest.main()
