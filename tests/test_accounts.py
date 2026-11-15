import base64
from contextlib import redirect_stdout
from decimal import Decimal
import hashlib
import hmac
from http.client import IncompleteRead
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cryptoir import accounts as a


NOW=1_800_000_000_123_456_789
SEED=bytes(range(32))  # Public synthetic fixture; never a user credential.
NOB=a.Credentials('nobitex_ed25519','synthetic-public',base64.urlsafe_b64encode(SEED).decode())
WAL=a.Credentials('wallex_key','synthetic-wallex')
TAB=a.Credentials('tabdeal_hmac','synthetic-tabdeal','synthetic-secret')


def headers(request):
    return {k.lower():v for k,v in request.header_items()}


class Response(io.BytesIO):
    status=200


class SigningTests(unittest.TestCase):
    def test_nobitex_signs_exact_get_and_readonly_post_bytes(self):
        public=Ed25519PrivateKey.from_private_bytes(SEED).public_key()
        for operation,method,path,body in (
            ('profile','GET','/users/profile',None),
            ('balances','POST','/users/wallets/list',b'{"type":"spot"}')):
            req=a.build_read_request('nobitex',operation,NOB,now_ns=NOW)
            hdr=headers(req)
            self.assertEqual(req.full_url,'https://apiv2.nobitex.ir'+path)
            self.assertEqual(req.method,method)
            self.assertEqual(req.data,body)
            self.assertEqual(hdr['nobitex-timestamp'],'1800000000')
            self.assertNotIn('authorization',hdr)
            public.verify(base64.urlsafe_b64decode(hdr['nobitex-signature']),
                          b'1800000000'+method.encode()+path.encode()+(body or b''))

    def test_tabdeal_hmac_uses_milliseconds_and_exact_query(self):
        req=a.build_read_request('tabdeal','account',TAB,now_ns=NOW)
        query='timestamp=1800000000123'
        signature=hmac.new(b'synthetic-secret',query.encode(),hashlib.sha256).hexdigest()
        self.assertEqual(req.full_url,'https://api1.tabdeal.org/r/api/v1/account?'+query+'&signature='+signature)
        self.assertEqual(headers(req)['x-mbx-apikey'],TAB.key)
        self.assertIsNone(req.data)
        self.assertEqual(req.method,'GET')

    def test_wallex_and_legacy_token_are_distinct(self):
        req=a.build_read_request('wallex','fees',WAL,now_ns=NOW)
        self.assertEqual(headers(req)['x-api-key'],WAL.key)
        token=a.Credentials('nobitex_token','synthetic-token')
        req=a.build_read_request('nobitex','profile',token,now_ns=NOW)
        self.assertEqual(headers(req)['authorization'],'Token synthetic-token')
        self.assertNotIn('nobitex-signature',headers(req))

    def test_no_arbitrary_endpoint_or_cross_venue_credentials(self):
        for venue,operation,credential in (
            ('nobitex','order',NOB),('nobitex','/users/profile',NOB),
            ('wallex','balances',TAB),('versland','balances',WAL),
            ('tabdeal','withdraw',TAB)):
            with self.subTest(venue=venue,operation=operation),self.assertRaises(a.AccountReadError):
                a.build_read_request(venue,operation,credential,now_ns=NOW)
        for stamp in (0,-1,True,1.5):
            with self.assertRaises(a.AccountReadError):
                a.build_read_request('wallex','fees',WAL,now_ns=stamp)

    def test_invalid_secret_and_key_never_echo(self):
        self.assertEqual(repr(TAB),'<Credentials redacted>')
        for key in ('','secret\nheader','space here','فارسی'):
            with self.assertRaisesRegex(a.AccountReadError,'^invalid_credential_format$'):
                a.Credentials('wallex_key',key)
        for seed in ('bad',base64.urlsafe_b64encode(bytes(31)).decode()):
            with self.assertRaisesRegex(a.AccountReadError,'^invalid_ed25519_private_key$'):
                a.build_read_request('nobitex','profile',a.Credentials('nobitex_ed25519','key',seed),now_ns=NOW)

    def test_hidden_prompt_refuses_echo_fallback_and_bad_scheme(self):
        import warnings
        def fallback(_):
            warnings.warn('would echo',a.getpass.GetPassWarning)
        with patch.object(a.getpass,'getpass',side_effect=fallback):
            with self.assertRaisesRegex(a.AccountReadError,'interactive_hidden_input_required'):
                a.prompt_credentials('wallex')
        with patch.object(a.getpass,'getpass') as prompt:
            with self.assertRaises(a.AccountReadError):
                a.prompt_credentials('wallex','tabdeal_hmac')
            prompt.assert_not_called()


class AccountTransportTests(unittest.TestCase):
    def read(self,body):
        with patch.object(a,'build_opener') as factory:
            factory.return_value.open.return_value=Response(body)
            return a.read_operation('wallex','fees',WAL)

    def test_raw_is_hashed_but_not_in_evidence_and_numbers_exact(self):
        body=b'{"success":true,"secretProfile":"private","value":0.100000000000000001}'
        raw,evidence=self.read(body)
        self.assertEqual(raw['value'],Decimal('0.100000000000000001'))
        self.assertEqual(evidence['body_sha256'],hashlib.sha256(body).hexdigest())
        self.assertFalse(evidence['raw_response_retained'])
        self.assertNotIn('private',json.dumps(evidence))

    def test_invalid_and_oversized_json_rejected(self):
        for body in (b'{"x":1,"x":2}',b'{"x":NaN}',b'[]',b'not json',b'\xff',b' '* (a.MAX_RESPONSE+1),b'['*1100):
            with self.subTest(body=body[:20]),self.assertRaises(a.AccountReadError):
                self.read(body)

    def test_transport_exception_cannot_disclose_query_or_body(self):
        for error in (URLError('SECRET-in-url'),IncompleteRead(b'SECRET-in-body'),
                      HTTPError('SECRET-url',401,'SECRET-message',{},io.BytesIO(b'SECRET-body'))):
            with patch.object(a,'build_opener') as factory:
                factory.return_value.open.side_effect=error
                with self.assertRaises(a.AccountReadError) as caught:
                    a.read_operation('wallex','fees',WAL)
                self.assertNotIn('SECRET',str(caught.exception))

    def test_redirect_is_refused_without_following_or_forwarding_credentials(self):
        hits=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):
                pass
            def do_GET(self):
                hits.append(self.path)
                self.send_response(302)
                self.send_header('Location','/must-not-follow')
                self.end_headers()
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            opener=build_opener(a.NoAccountRedirect())
            with self.assertRaisesRegex(a.AccountReadError,'redirect_refused'):
                opener.open(Request('http://127.0.0.1:'+str(server.server_port)+'/start',headers={'X-API-Key':'synthetic'}),timeout=2)
            self.assertEqual(hits,['/start'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)


class AccountNormalizationTests(unittest.TestCase):
    def test_profile_pii_is_discarded_and_legacy_fee_unit_unknown(self):
        data=a.parse_account('nobitex','profile',{'status':'ok','profile':{
            'email':'PRIVATE','bankAccounts':['PRIVATE'],'options':{'fee':'0.35','feeUsdt':'0.2','websocketToken':'PRIVATE'}}})
        self.assertNotIn('PRIVATE',json.dumps(data,default=str))
        self.assertFalse(data['fee_rates_usable'])
        self.assertTrue(all(r['normalized_rate'] is None for r in data['fee_observations']))

    def test_nobitex_balance_consistency_is_not_assumed(self):
        value={'status':'ok','wallets':[{'currency':'btc','balance':'3','blockedBalance':'1','activeBalance':'2'},
                                       {'currency':'usdt','balance':'3','activeBalance':'3'},
                                       {'currency':'irr','balance':'3','blockedBalance':'1','activeBalance':'3'}]}
        rows=a.parse_account('nobitex','balances',value)['balances']
        self.assertEqual(rows[0]['balance_state'],'internally_consistent')
        self.assertEqual(rows[1]['balance_state'],'unverified_or_inconsistent')
        self.assertIsNone(rows[1]['reported_locked'])
        self.assertEqual(rows[2]['balance_state'],'unverified_or_inconsistent')

    def test_wallex_ratios_but_no_guessed_spendable_balance(self):
        fee={'success':True,'result':{'BTCUSDT':{'makerFeeRate':'0.001','takerFeeRate':'0.002','recent_days_sum':'PRIVATE'}}}
        data=a.parse_account('wallex','fees',fee)
        self.assertEqual(data['fee_observations'][0]['taker_rate'],Decimal('.002'))
        self.assertEqual(data['fee_observations'][0]['charged_asset'],'unverified')
        balance={'success':True,'result':{'balances':{'USDT':{'value':'10','locked':'2','fiat_value':'PRIVATE'}}}}
        row=a.parse_account('wallex','balances',balance)['balances'][0]
        self.assertIsNone(row['free'])
        self.assertIsNone(row['total'])
        fee['result']['BTCUSDT']['makerFeeRate']='1'
        with self.assertRaises(a.AccountReadError):
            a.parse_account('wallex','fees',fee)

    def test_tabdeal_units_and_permission_flags_are_not_guessed(self):
        data=a.parse_account('tabdeal','account',{'accountType':'SPOT','makerCommission':40,'takerCommission':40,
            'canTrade':True,'canWithdraw':'true','balances':[{'asset':'USDT','free':'1.00000001','freeze':'.00000002'}]})
        self.assertEqual(data['balances'][0]['total'],Decimal('1.00000003'))
        self.assertIsNone(data['fee_observations'][0]['normalized_rate'])
        self.assertIsNone(data['reported_capabilities']['canWithdraw'])
        self.assertEqual(data['capability_scope'],'account_response_not_proof_of_key_permissions')

    def test_wallex_mixed_fee_mapping_preserves_explicit_rates_and_unknown_entries(self):
        value={'success':True,'result':{
            'BTCUSDT':{'makerFeeRate':'0.001','takerFeeRate':'0.002'},
            'ETHTMN':{'makerFeeRate':'0.003','takerFeeRate':'0.004'},
            'PRIVATE-ENTRY':[], 'PRIVATE-OPTIONS':{'PRIVATE':[1,2],'another':3}}}
        data=a.parse_account('wallex','fees',value)
        self.assertEqual(len(data['fee_observations']),2)
        self.assertEqual(data['fee_observations'][0]['taker_rate'],Decimal('.002'))
        self.assertTrue(data['fee_rates_usable'])
        self.assertEqual(data['unparsed_entry_count'],2)
        self.assertFalse(data['all_result_entries_parsed'])
        self.assertFalse(data['market_coverage_verified'])
        self.assertNotIn('PRIVATE',json.dumps(data,default=str))
        self.assertTrue(all(f['charged_asset']=='unverified' for f in data['fee_observations']))

    def test_wallex_incomplete_or_bad_rate_cannot_hide_as_unparsed_metadata(self):
        for bad in ({'makerFeeRate':'0.001'}, {'makerFeeRate':'1','takerFeeRate':'0'},
                    {'makerFeeRate':'NaN','takerFeeRate':'0'}):
            with self.subTest(bad=bad),self.assertRaises(ValueError):
                a.parse_account('wallex','fees',{'success':True,'result':{
                    'BTCUSDT':{'makerFeeRate':'0.001','takerFeeRate':'0.002'},'ETHUSDT':bad}})
        with self.assertRaisesRegex(a.AccountReadError,'unrecognized_fee_mapping'):
            a.parse_account('wallex','fees',{'success':True,'result':{'unknown':[]}})

    def test_wallex_duplicate_fee_market_is_not_merged(self):
        row={'makerFeeRate':'0.001','takerFeeRate':'0.002'}
        with self.assertRaisesRegex(a.AccountReadError,'duplicate_native_fee_market'):
            a.parse_account('wallex','fees',{'success':True,'result':{'BTCUSDT':row,'btcusdt':row}})

    def test_duplicate_assets_are_not_merged_or_double_counted(self):
        samples=[('nobitex','balances',{'status':'ok','wallets':[{'currency':s,'balance':'1'} for s in ('BTC','btc')]}),
                 ('wallex','balances',{'success':True,'result':{'balances':{s:{'value':'1','locked':'0'} for s in ('BTC','btc')}}}),
                 ('tabdeal','account',{'accountType':'SPOT','balances':[{'asset':s,'free':'1','freeze':'0'} for s in ('BTC','btc')]})]
        for venue,op,value in samples:
            with self.subTest(venue=venue),self.assertRaisesRegex(a.AccountReadError,'duplicate_native_asset'):
                a.parse_account(venue,op,value)

    def test_malformed_balances_fail_and_exact_totals_do_not_round(self):
        for value in ('-1','NaN','1e9999',True,0.1):
            with self.subTest(value=value),self.assertRaises(ValueError):
                a.parse_account('tabdeal','account',{'accountType':'SPOT','balances':[{'asset':'BTC','free':value,'freeze':'0'}]})
        data=a.parse_account('tabdeal','account',{'accountType':'SPOT','balances':[
            {'asset':'USDT','free':'9999999999999999999999999999','freeze':'0.00000001'}]})
        self.assertEqual(str(data['balances'][0]['total']),'9999999999999999999999999999.00000001')

    def test_rejected_and_missing_responses_stay_unavailable(self):
        samples=({'success':False,'message':'PRIVATE'}, {'success':True,'result':{'secret':'PRIVATE'}}, [])
        for raw in samples:
            with patch.object(a,'read_operation',return_value=(raw,{})):
                report=a.inspect_account('wallex',WAL)
            self.assertFalse(report['all_requested_reads_accepted'])
            self.assertNotIn('PRIVATE',json.dumps(report))
            self.assertFalse(report['live_trading_enabled'])
            self.assertFalse(report['key_permissions_verified'])
            self.assertFalse(report['paper_capital_modified'])


class AccountPrivateOutputTests(unittest.TestCase):
    def test_fee_failure_retains_only_allowlisted_shape_and_response_hash(self):
        raw={'success':True,'result':{'PRIVATE-EMAIL':{'makerFeeRate':'PRIVATE-AMOUNT',
             'PRIVATE-KEY':'PRIVATE-SECRET','nested-private':{'takerFeeRate':'PRIVATE'}}}}
        evidence={'body_sha256':'synthetic-hash','raw_response_retained':False}
        with patch.object(a,'read_operation',return_value=(raw,evidence)):
            report=a.inspect_account('wallex',WAL)
        text=json.dumps(report)
        self.assertNotIn('PRIVATE',text)
        fee=report['results'][0]
        self.assertEqual(fee['status'],'unavailable')
        self.assertEqual(fee['evidence'],evidence)
        self.assertIn('makerFeeRate',json.dumps(fee['diagnostic']))
        self.assertNotIn('diagnostic',report['results'][1])

    def test_fee_shape_bounds_and_values_do_not_escape_via_dictionary_keys(self):
        value={'SECRET-'+str(i):{'makerFeeRate':str(i),'unlisted':'SECRET'} for i in range(600)}
        shape=a.fee_schema_shape(value)
        self.assertTrue(shape['truncated'])
        self.assertEqual(len(shape['groups']),1)
        self.assertEqual(shape['groups'][0]['count'],512)
        self.assertNotIn('SECRET',json.dumps(shape))
        for _ in range(20): value={'result':value}
        self.assertLess(len(json.dumps(a.fee_schema_shape(value))),2000)

    def report(self):
        with patch.object(a,'read_operation',return_value=({'accountType':'SPOT','balances':[]},{})):
            return a.inspect_account('tabdeal',TAB)

    def test_exclusive_private_file_and_no_raw_profile_or_credentials(self):
        report=self.report()
        with tempfile.TemporaryDirectory() as tmp,patch.object(a,'__file__',str(Path(tmp)/'cryptoir/accounts.py')):
            one=a.save_assessment(report)
            two=a.save_assessment(report)
            self.assertNotEqual(one,two)
            self.assertEqual(one.parent,Path(tmp).resolve()/'.local/accounts')
            self.assertEqual(json.loads(one.read_text()),report)
            self.assertNotIn(TAB.secret,one.read_text())
            if os.name!='nt':
                self.assertEqual(one.stat().st_mode&0o777,0o600)
                self.assertEqual(one.parent.stat().st_mode&0o777,0o700)

    def test_refuses_redirected_private_directory(self):
        report=self.report()
        with tempfile.TemporaryDirectory() as tmp,patch.object(a,'__file__',str(Path(tmp)/'cryptoir/accounts.py')):
            with patch.object(Path,'is_symlink',return_value=True):
                with self.assertRaisesRegex(a.AccountReadError,'private_directory_redirected'):
                    a.save_assessment(report)

    def test_console_excludes_balance_and_exception_details(self):
        out=io.StringIO()
        report=self.report()
        report['results'][0]['data']['balances']=[{'free':'PRIVATE-AMOUNT'}]
        with patch.object(a,'prompt_credentials',return_value=TAB),patch.object(a,'inspect_account',return_value=report),\
                patch.object(a,'save_assessment',return_value=Path('tabdeal-test.json')),redirect_stdout(out):
            self.assertEqual(a.account_check('tabdeal'),0)
        self.assertNotIn('PRIVATE',out.getvalue())
        self.assertNotIn(TAB.key,out.getvalue())
        out=io.StringIO()
        with patch.object(a,'prompt_credentials',side_effect=RuntimeError('PRIVATE exception')),redirect_stdout(out):
            self.assertEqual(a.account_check('tabdeal'),1)
        self.assertNotIn('PRIVATE',out.getvalue())
        self.assertIn('details_redacted',out.getvalue())

    def test_cli_has_no_key_argument_and_legacy_flag_is_scoped(self):
        out=io.StringIO()
        with patch.object(a,'prompt_credentials') as prompt,redirect_stdout(out):
            self.assertEqual(a.account_check('wallex',legacy_token=True),1)
            prompt.assert_not_called()
        from cryptoir.__main__ import main
        with patch('sys.argv',['cryptoir','account-check','--venue','nobitex','--legacy-token']),\
                patch.object(a,'account_check',return_value=0) as command:
            self.assertEqual(main(),0)
            command.assert_called_once_with('nobitex',legacy_token=True)


if __name__=='__main__':
    unittest.main()
