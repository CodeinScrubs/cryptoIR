import copy
from dataclasses import replace
from decimal import Decimal as D, localcontext
import gzip
import json
from pathlib import Path
import tempfile
import unittest

from cryptoir import maker_spread as m, triangular as t
from cryptoir.domain import Book, DataError, Level
from cryptoir.streams import dumps
import test_triangular as fixtures

ROOT = Path(__file__).resolve().parents[1]
NS = fixtures.NS


class MakerWidthTests(unittest.TestCase):
    def setUp(self):
        self.plan = json.loads((ROOT/'research/maker-spread-v1.json').read_bytes())
        self.plan.update(quote_budget_usdt='100', maximum_fraction_of_displayed_top_level='1')
        self.rules = dict(quantity_step='.001', price_tick='.01', minimum_notional='1')
        self.book = Book('nobitex', 'BTC/USDT', (Level(100, 2),), (Level(102, 2),), 100*NS, NS, 99*NS)

    def quote(self, scenario=1, book=None, rules=None):
        return m.quote_width(book or self.book, rules or self.rules, self.plan, self.plan['scenarios'][scenario], 100*NS)

    def test_exact_received_asset_fees_reserve_and_cash_identity(self):
        r = self.quote()
        self.assertEqual(r['gross_buy_quantity'], 1)
        self.assertEqual(r['buy_fee_base'], D('.001'))
        self.assertEqual(r['sell_quantity'], D('.999'))
        self.assertEqual(r['gross_sell_usdt'], D('101.898'))
        self.assertEqual(r['sell_fee_usdt'], D('.101898'))
        self.assertEqual(r['hypothetical_cost_reserve_usdt'], D('.302847'))
        self.assertEqual(r['quoted_width_net_usdt'], D('1.493255'))
        self.assertEqual(r['spent_usdt']+r['unspent_usdt'], 100)
        self.assertFalse(r['passive_fills_verified'])
        self.assertFalse(r['execution_ready'])
        with localcontext() as c:
            c.prec = 5
            self.assertEqual(self.quote(), r)

    def test_dust_and_post_fee_minimum_are_not_fabricated_sales(self):
        rules = {**self.rules, 'quantity_step': '.01'}
        r = self.quote(rules=rules)
        self.assertEqual(r['sell_quantity'], D('.99'))
        self.assertEqual(r['residual_base_zero_valuation'], D('.009'))
        self.assertEqual(r['gross_buy_quantity'], r['sell_quantity']+r['buy_fee_base']+r['residual_base_zero_valuation'])
        book = replace(self.book, asks=(Level(101, 2),))
        with self.assertRaisesRegex(DataError, 'sell_below_minimum'):
            self.quote(book=book, rules={**rules, 'minimum_notional': '100'})
        self.plan['maximum_fraction_of_displayed_top_level'] = '.10'
        self.assertEqual(self.quote()['gross_buy_quantity'], D('.2'))

    def test_positive_gross_spread_can_fail_both_cost_scenarios(self):
        book = replace(self.book, asks=(Level('100.01', 2),))
        self.assertGreater(self.quote(0, book)['quoted_width_net_usdt'], 0)
        base, stress = [self.quote(s, book)['quoted_width_net_usdt'] for s in (1, 2)]
        self.assertLess(base, 0)
        self.assertLess(stress, base)

    def test_unknown_stale_wrong_venue_and_tick_data_cannot_qualify(self):
        for book in (replace(self.book, exchange_ns=None), replace(self.book, exchange_ns=90*NS),
                     replace(self.book, exchange_ns=101*NS), replace(self.book, venue='wallex'),
                     replace(self.book, request_duration_ns=6*NS), replace(self.book, asks=(Level('102.001', 2),))):
            with self.subTest(book=book), self.assertRaises(DataError): self.quote(book=book)

    def paths(self, root):
        capture, catalog = fixtures.fixture()
        capture_path, cat_path, rec = root/'capture.json', root/'catalog.json', root/'capture.gz'
        capture_path.write_text(dumps(capture), encoding='utf-8'); cat_path.write_bytes(catalog)
        helper = fixtures.TriangleTests(); helper.recording(rec, capture_path)
        universe = copy.deepcopy(capture); universe['universe'] = t.CATALOG_UNIVERSE
        universe['replay_recording'] = dict(sha256=t.digest(rec.read_bytes()),
            capture_plan_sha256=t.digest(capture_path.read_bytes()), evaluation_type='exploratory_existing_recording')
        plan = copy.deepcopy(self.plan)
        for key in ('catalog_sha256', 'replay_recording', 'samples', 'interval_seconds', 'max_age_ms', 'max_logical_mb'):
            plan[key] = copy.deepcopy(universe[key])
        plan['follow_up_rule']['minimum_valid_samples_per_cost'] = 2
        uni_path, plan_path = root/'universe.json', root/'plan.json'
        uni_path.write_text(dumps(universe), encoding='utf-8')
        plan['universe_plan']['sha256'] = t.digest(uni_path.read_bytes())
        plan_path.write_text(dumps(plan), encoding='utf-8')
        return plan_path, uni_path, cat_path, rec

    def repin(self, paths, rows):
        plan_path, uni_path, _, rec = paths
        with gzip.open(rec, 'wt', encoding='utf-8') as stream:
            stream.write('\n'.join(dumps(r) for r in rows)+'\n')
        plan, uni = [json.loads(p.read_bytes()) for p in (plan_path, uni_path)]
        for p in (plan, uni): p['replay_recording']['sha256'] = t.digest(rec.read_bytes())
        uni_path.write_text(dumps(uni), encoding='utf-8')
        plan['universe_plan']['sha256'] = t.digest(uni_path.read_bytes())
        plan_path.write_text(dumps(plan), encoding='utf-8')

    def test_valid_source_reconstructs_but_only_selects_public_observation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); paths = self.paths(root)
            r = m.run_screen(*paths, root/'out')
            self.assertTrue(r['capture_complete'])
            self.assertEqual(r['status'], 'completed_quote_width_screen')
            self.assertEqual(len(r['markets_and_scenarios']), 3)
            self.assertEqual(r['selected_for_observation'][0]['asset'], 'BTC')
            self.assertEqual(r['selected_for_observation'][0]['purpose'], 'fresh_public_observation_only')
            for flag in ('execution_ready', 'strategy_accepted', 'passive_fills_verified', 'portfolio_return_available'):
                self.assertFalse(r[flag])
            self.assertEqual(len((root/'out/samples.jsonl').read_text().splitlines()), 6)
            with self.assertRaises(FileExistsError): m.run_screen(*paths, root/'out')

    def test_integrity_errors_never_leave_a_selected_candidate(self):
        for variant in ('raw', 'index', 'spacing', 'after_footer', 'wrong_pin'):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); paths = self.paths(root)
                with gzip.open(paths[-1], 'rt', encoding='utf-8') as f: rows = [json.loads(line) for line in f]
                if variant == 'raw': rows[1]['record']['payload']['BTCUSDT']['bids'][0][0] = '1'
                if variant == 'index': rows[2]['index'] = 0
                if variant == 'spacing': rows[2]['record']['started_ns'] -= NS
                if variant == 'after_footer': rows.append(rows[-1])
                if variant == 'wrong_pin':
                    with paths[-1].open('ab') as f: f.write(b'changed')
                else: self.repin(paths, rows)
                with self.assertRaises(DataError): m.run_screen(*paths, root/'out')
                report = json.loads((root/'out/report.json').read_bytes())
                self.assertFalse(report['capture_complete'])
                self.assertFalse(report['universe_coverage_complete'])
                self.assertEqual(report['selected_for_observation'], [])

    def test_interrupted_and_stale_recordings_do_not_qualify(self):
        for variant in ('interrupted', 'stale'):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); paths = self.paths(root)
                with gzip.open(paths[-1], 'rt', encoding='utf-8') as f: rows = [json.loads(line) for line in f]
                if variant == 'interrupted': rows.pop()
                else:
                    for row in rows[1:-1]:
                        r = row['record']; r['payload']['BTCUSDT']['lastUpdate'] = 1
                        r['raw_body'] = dumps(r['payload']); r['body_sha256'] = t.digest(r['raw_body'].encode())
                self.repin(paths, rows)
                r = m.run_screen(*paths, root/'out')
                self.assertEqual(r['selected_for_observation'], [])
                self.assertEqual(r['capture_complete'], variant == 'stale')
                if variant == 'stale':
                    self.assertFalse(r['universe_coverage_complete'])
                    self.assertTrue(all(s['valid_samples'] == 0 and sum(s['rejections'].values()) == 2 for s in r['markets_and_scenarios']))

    def test_fixed_follow_up_thresholds_and_top_three_tie_break(self):
        rows = [{'asset':a, 'scenario':s, 'valid_samples':20, 'positive_samples':10,
                 'median_net_width_usdt':D(1), 'median_width_per_spent_usdt':D('.1')}
                for a in ('BTC', 'ETH', 'SOL', 'XRP') for s in m.SCENARIOS]
        self.assertEqual([r['asset'] for r in m.select_follow_up(rows, self.plan)], ['BTC','ETH','SOL'])
        for row in rows:
            if row['asset'] == 'BTC' and row['scenario'] == 'assumed_base': row['positive_samples'] = 9
            if row['asset'] == 'ETH' and row['scenario'] == 'assumed_stress': row['valid_samples'] = 9
            if row['asset'] == 'SOL' and row['scenario'] == 'assumed_stress': row['median_net_width_usdt'] = D(0)
        self.assertEqual([r['asset'] for r in m.select_follow_up(rows, self.plan)], ['XRP'])

    def test_changed_scope_cost_order_or_universe_pin_fails_before_output(self):
        for variant in ('scope', 'costs', 'universe', 'footprint', 'threshold'):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); paths = self.paths(root)
                p = json.loads(paths[0].read_bytes())
                if variant == 'scope': p['interval_seconds'] = 1
                if variant == 'costs': p['scenarios'][2]['maker_fee'] = '0'
                if variant == 'universe': p['universe_plan']['sha256'] = '0'*64
                if variant == 'footprint': p['maximum_fraction_of_displayed_top_level'] = 'NaN'
                if variant == 'threshold': p['follow_up_rule']['maximum_candidates'] = 4
                paths[0].write_text(dumps(p), encoding='utf-8')
                with self.assertRaises(DataError): m.run_screen(*paths, root/'out')
                self.assertFalse((root/'out').exists())


if __name__ == '__main__': unittest.main()
