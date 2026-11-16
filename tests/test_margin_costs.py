from decimal import Decimal as D, localcontext
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cryptoir.domain import DataError
from cryptoir import margin_costs as m, products as p
from test_products import quote_response, response


def artifact(side='long'):
    r, request = quote_response(side)
    if side == 'short':
        fields = r['payload']['result']
        for key, value, currency in (
            ('loan', '0.00833333', 'BTC'), ('trade_fee', '1.25', 'USDT'),
            ('open_fee', '0.00000117', 'BTC'), ('extend_fee', '0.00000117', 'BTC'),
        ):
            fields[key] = {'value': value, 'currency': currency}
        r = response(r['url'], r['payload'])
        r.update(method='POST', request=request)
    return p.encoded({'schema': 1, 'kind': 'public_margin_quote', 'venue': 'wallex',
                      'status': 'observed', 'request': request, 'response': r,
                      'quote': p.parse_quote(r, request)}).encode()


class MarginCostTests(unittest.TestCase):
    def test_long_fee_assets_and_renewal_count_preserve_exact_values(self):
        report = m.fee_scenario(artifact(), 6, {'USDT': '1', 'BTC': '60000'})
        self.assertEqual(report['native_fee_totals'], {'BTC': D('0.00002'), 'USDT': D('0.56')})
        self.assertEqual(report['marked_known_fees_in_collateral'], D('1.76'))
        self.assertEqual(report['marked_known_fees_percent_of_collateral'], D('1.76'))
        self.assertEqual(report['nominal_renewal_time_seconds'], 86400)
        self.assertEqual(report['components']['extend_fee']['native_total'], D('.48'))

    def test_short_native_fees_reprice_and_do_not_become_usdt_fees(self):
        raw = artifact('short')
        a = m.fee_scenario(raw, 6, {'USDT': 1, 'BTC': '60000'})
        b = m.fee_scenario(raw, 6, {'USDT': 1, 'BTC': '90000'})
        self.assertEqual(a['native_fee_totals'], {'BTC': D('.00000819'), 'USDT': D('1.25')})
        self.assertEqual(a['native_fee_totals'], b['native_fee_totals'])
        self.assertEqual(a['marked_known_fees_in_collateral'], D('1.7414'))
        self.assertEqual(b['marked_known_fees_in_collateral'], D('1.9871'))

    def test_no_renewals_charges_open_and_trade_once_only(self):
        report = m.fee_scenario(artifact(), 0, {'USDT': 1, 'BTC': 60000})
        self.assertEqual(report['components']['extend_fee']['count'], 0)
        self.assertEqual(report['components']['extend_fee']['native_total'], 0)
        self.assertEqual(report['marked_known_fees_in_collateral'], D('1.28'))
        self.assertEqual(report['nominal_renewal_time_seconds'], 0)

    def test_one_extra_renewal_changes_only_extension_cost(self):
        for side in ('long', 'short'):
            a = m.fee_scenario(artifact(side), 41, {'USDT': 1, 'BTC': 60000})
            b = m.fee_scenario(artifact(side), 42, {'USDT': 1, 'BTC': 60000})
            for fee in ('trade_fee', 'open_fee'):
                self.assertEqual(a['components'][fee], b['components'][fee])
            unit = b['components']['extend_fee']['quoted_unit_amount']
            currency = b['components']['extend_fee']['native_currency']
            self.assertEqual(b['marked_known_fees_in_collateral'] - a['marked_known_fees_in_collateral'],
                             unit * D(str({'USDT': 1, 'BTC': 60000}[currency])))

    def test_expiry_boundary_and_invalid_counts_are_rejected(self):
        raw = artifact()
        self.assertEqual(m.fee_scenario(raw, 125, {'USDT': 1, 'BTC': 60000})['renewals'], 125)
        for n in (-1, True, D(6), 6.0, '6', 126, 127, 1_000_001):
            with self.subTest(n=n), self.assertRaises(DataError):
                m.fee_scenario(raw, n, {'USDT': 1, 'BTC': 60000})

    def test_unknown_costs_survival_and_execution_never_become_verified(self):
        report = m.fee_scenario(artifact(), 42, {'USDT': 1, 'BTC': 60000})
        for key in ('exit_fee', 'other_unquoted_costs', 'net_pnl'):
            self.assertIsNone(report[key])
        for key in ('settlement_conversion_rule_verified', 'survival_until_renewal_verified',
                    'historical_cost_model_verified', 'liquidation_model_verified',
                    'quote_is_position', 'fees_applied_to_paper', 'execution_ready'):
            self.assertIs(report[key], False)
        self.assertTrue(all(report['assumptions'].values()))

    def test_missing_extra_duplicate_or_inexact_marks_rejected(self):
        for marks in ({'USDT': 1}, {'BTC': 60000}, {'USDT': 1, 'BTC': 60000, 'ETH': 2000},
                      {'USDT': 10, 'BTC': 60000}, {'USDT': 1, 'BTC': 0},
                      {'USDT': 1, 'BTC': -1}, {'USDT': 1, 'BTC': 'NaN'},
                      {'USDT': 1, 'BTC': float('inf')}, {'USDT': True, 'BTC': 60000},
                      {'USDT': 1, 'BTC': 60000.0}):
            with self.subTest(marks=marks), self.assertRaises(DataError):
                m.fee_scenario(artifact(), 6, marks)
        for marks in (['BTC=1', 'BTC=2'], ['BTC=1=2'], ['BTC'], ['../BTC=1']):
            with self.subTest(marks=marks), self.assertRaises(DataError):
                m.parse_marks(marks)
        self.assertEqual(m.parse_marks(['USDT=1', 'BTC=60000.01']), {'USDT': D(1), 'BTC': D('60000.01')})

    def test_decimal_arithmetic_does_not_depend_on_callers_context(self):
        expected = m.fee_scenario(artifact('short'), 42, {'USDT': 1, 'BTC': '60000.1234567890123456789'})
        with localcontext() as context:
            context.prec = 5
            actual = m.fee_scenario(artifact('short'), 42, {'USDT': 1, 'BTC': '60000.1234567890123456789'})
        self.assertEqual(actual, expected)

    def test_tampered_quote_rejected_before_output_and_no_network(self):
        raw = json.loads(artifact())
        raw['quote']['amounts']['open_fee']['value'] = '0'
        with tempfile.TemporaryDirectory() as tmp, patch.object(p, 'public_calculation') as fetch:
            source, dest = Path(tmp) / 'quote.json', Path(tmp) / 'cost.json'
            source.write_text(json.dumps(raw))
            with self.assertRaises(DataError):
                m.write_scenario(source, dest, 6, {'USDT': 1, 'BTC': 60000})
            self.assertFalse(dest.exists())
            fetch.assert_not_called()

    def test_artifact_reconstruction_tamper_detection_and_exclusive_write(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(p, 'public_calculation') as fetch:
            source, dest = Path(tmp) / 'quote.json', Path(tmp) / 'cost.json'
            source.write_bytes(artifact())
            summary = m.write_scenario(source, dest, 6, {'USDT': 1, 'BTC': 60000})
            self.assertNotIn('collateral', summary)
            self.assertEqual(m.audit_scenario(source, dest)['status'], 'reconstructed')
            with self.assertRaises(FileExistsError):
                m.write_scenario(source, dest, 6, {'USDT': 1, 'BTC': 60000})
            original = dest.read_text()
            for key, value in (('renewals', 5), ('net_pnl', '100'), ('execution_ready', True),
                               ('source_received_ns', 99), ('source_quote_sha256', '0' * 64)):
                record = json.loads(original); record[key] = value
                dest.write_text(json.dumps(record))
                with self.subTest(key=key), self.assertRaises(DataError):
                    m.audit_scenario(source, dest)
            dest.write_text(original)
            source.write_bytes(artifact('short'))
            with self.assertRaises(DataError):
                m.audit_scenario(source, dest)
            fetch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
