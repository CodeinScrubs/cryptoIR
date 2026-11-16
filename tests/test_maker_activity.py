import copy
from contextlib import redirect_stdout
from decimal import localcontext
import gzip
from io import StringIO
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from cryptoir import maker_activity as a
from cryptoir.domain import DataError
from cryptoir.streams import dumps
import test_triangular as fixtures

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT/'research/maker-activity-v1.json'
NS = a.NS


def option_payload(assets=('G',)):
    return {'status': 'ok', 'coins': [{'coin': x, 'scale': '1'} for x in assets],
        'nobitex': {'activeCurrencies': list(assets), 'minOrders': {'usdt': '0.5'},
            'amountPrecisions': {x+'USDT': '0.01' for x in assets},
            'pricePrecisions': {x+'USDT': '0.01' for x in assets}}}


def stats_payload(assets=('G',)):
    return {'status': 'ok', 'stats': {x.lower()+'-usdt': {'isClosed': False, 'volumeDst': '100'} for x in assets}}


def book_payload(now, future=False):
    return {'status': 'ok', 'GUSDT': {'lastUpdate': (now+10*NS if future else now)//1_000_000,
                                    'bids': [['1', '100']], 'asks': [['2', '100']]}}


def recent_payload(now, count=5):
    return {'status': 'ok', 'trades': [{'time': (now-(i+1)*NS)//1_000_000, 'price': '1',
                                       'volume': '1', 'type': 'buy'} for i in range(count)]}


class ActivityTests(unittest.TestCase):
    def setUp(self): self.raw, self.plan, self.quote = a.load_plan(PLAN)

    def test_registered_scope_quote_pin_and_bounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'plan.json'
            for update in ({'book_samples': 9}, {'maximum_markets': True}, {'public_stats_url': 'https://example.invalid'}):
                path.write_text(dumps({**self.plan, **update}), encoding='utf-8')
                with self.assertRaises(DataError): a.load_plan(path)
            bad = copy.deepcopy(self.plan)
            bad['quote_plan']['sha256'] = '0'*64
            path.write_text(dumps(bad), encoding='utf-8')
            with self.assertRaises(DataError): a.load_plan(path)

    def test_selection_metadata_exclusions_exact_volume_rank_and_cap(self):
        assets = [f'A{i:02}' for i in range(25)]
        options, stats = option_payload(assets), stats_payload(assets)
        options['nobitex']['activeCurrencies'].remove('A00')
        options['coins'][1]['scale'] = '1000'
        stats['stats']['a02-usdt']['isClosed'] = True
        stats['stats']['a03-usdt']['isClosed'] = 'false'
        stats['stats']['a04-usdt']['volumeDst'] = '0'
        stats['stats']['a24-usdt']['volumeDst'] = '100.000000000000001'
        with localcontext() as ctx:
            ctx.prec = 6
            result = a.select_markets(fixtures.response(options, self.plan['public_options_url']),
                                     fixtures.response(stats, a.STATS), self.plan)
        self.assertEqual(len(result['selected']), 20)
        self.assertEqual(result['selected'][0]['native_symbol'], 'A24USDT')
        self.assertEqual(len(result['excluded']), 5)
        self.assertFalse(result['selected'][0]['stats_source_time_known'])
        result = a.select_markets(fixtures.response(option_payload(assets), self.plan['public_options_url']),
                                 fixtures.response(stats_payload(assets), a.STATS), self.plan)
        self.assertEqual(result['eligible_count'], 25)
        self.assertEqual(result['eligible_below_rank_cutoff'], [f'A{i:02}USDT' for i in range(20, 25)])

    def test_recent_duplicates_zero_old_future_and_availability(self):
        now = 500*NS
        payload = recent_payload(now)
        payload['trades'].insert(0, copy.deepcopy(payload['trades'][0]))
        payload['trades'] += [{'time': 100000, 'price': '1', 'volume': '1', 'type': 'buy'},
                             {'time': 99000, 'price': '1', 'volume': '0', 'type': 'sell'}]
        record = fixtures.response(payload, a.TRADES+'GUSDT', now)
        result = a.recent_activity(record, 'GUSDT', self.plan, now+NS)
        self.assertEqual(result['positive_recent_occurrences'], 6)
        self.assertEqual(result['distinct_positive_recent_fingerprints'], 5)
        self.assertEqual(result['identical_recent_occurrences'], 1)
        self.assertEqual(result['zero_volume_rows'], 1)
        self.assertIsNone(result['unique_trade_count'])
        late = a.recent_activity(record, 'GUSDT', self.plan, now+301*NS)
        self.assertEqual(late['positive_recent_occurrences'], 0)
        payload['trades'][0]['time'] = 501000
        with self.assertRaises(DataError):
            a.recent_activity(fixtures.response(payload, a.TRADES+'GUSDT', now), 'GUSDT', self.plan, now+2*NS)

    def make_rows(self, root, future_book=False, duplicate_recent=False):
        (root/'plan.json').write_bytes(self.raw)
        rows, clock = [], 100*NS
        def add(event):
            rows.append(dict(schema=2, run_id='synthetic', seq=len(rows)+1, venue='nobitex',
                observed_ns=clock, monotonic_ns=clock, clock_error_ns=0, **event))
        def response(operation, index, symbol, url, factory, delay=0):
            nonlocal clock
            start = clock+delay*NS
            end = start+NS
            record = fixtures.response(factory(end), url, end)
            clock = end+1_000_000
            add({'kind': 'response', 'operation': operation, 'index': index, 'native_symbol': symbol,
                 'started_mono_ns': start, 'finished_mono_ns': end+500000, 'record': record})
        add({'kind': 'screen_start', 'plan_sha256': a.t.digest(self.raw), 'source_sha256': {},
             'clock_origin_wall_ns': clock, 'clock_origin_mono_ns': clock})
        response('options', 0, '', self.plan['public_options_url'], lambda _: option_payload())
        response('stats', 0, '', a.STATS, lambda _: stats_payload())
        add({'kind': 'selection', 'native_symbols': ['GUSDT']})
        for i in range(10):
            response('book', i, '', a.t.ALL_BOOKS,
                     lambda end, i=i: book_payload(end, future_book and i == 9), 30 if i else 0)
        def recent(end):
            result = recent_payload(end)
            if duplicate_recent: result['trades'] = [result['trades'][0]]*5
            return result
        response('recent', 0, 'GUSDT', a.TRADES+'GUSDT', recent)
        add({'kind': 'screen_end', 'reason': 'completed', 'requests': 13})
        return rows

    def save_rows(self, root, rows):
        with gzip.open(root/'capture.jsonl.gz', 'wt', encoding='utf-8') as f:
            f.write('\n'.join(dumps(row) for row in rows)+'\n')

    def test_raw_audit_complete_quotes_later_activity_and_exclusive_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.save_rows(root, self.make_rows(root))
            result = a.audit(root, root/'result')
            self.assertTrue(result['capture_complete'])
            self.assertEqual(result['requests'], 13)
            self.assertEqual(result['selected_for_observation'][0]['native_symbol'], 'GUSDT')
            self.assertFalse(result['selected_for_observation'][0]['strategy_accepted'])
            self.assertFalse(result['portfolio_return_available'])
            self.assertEqual(len((root/'result/comparisons.jsonl').read_text().splitlines()), 30)
            with self.assertRaises(FileExistsError): a.audit(root, root/'result')

    def test_future_quote_and_duplicate_activity_prevent_follow_up(self):
        for future, duplicate in ((True, False), (False, True)):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self.save_rows(root, self.make_rows(root, future, duplicate))
                result = a.audit(root, root/'result')
                self.assertTrue(result['capture_complete'])
                self.assertEqual(result['selected_for_observation'], [])
                if future:
                    self.assertEqual(result['markets'][0]['cost_scenarios'][-1]['valid'], 9)
                else:
                    self.assertEqual(result['markets'][0]['recent_activity']['distinct_positive_recent_fingerprints'], 1)

    def test_tampering_selection_request_order_spacing_clock_and_footer(self):
        for mutation in ('raw', 'selection', 'order', 'spacing', 'clock', 'footer', 'after'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                rows = self.make_rows(root)
                if mutation == 'raw': rows[5]['record']['body_sha256'] = '0'*64
                if mutation == 'selection': rows[3]['native_symbols'] = ['BTCUSDT']
                if mutation == 'order': rows[5]['index'] = 7
                if mutation == 'spacing': rows[5]['started_mono_ns'] = rows[4]['finished_mono_ns']+29*NS
                if mutation == 'clock': rows[5]['observed_ns'] += 2*NS
                if mutation == 'footer': rows[-1]['requests'] = 12
                if mutation == 'after': rows.append(copy.deepcopy(rows[-1]))
                self.save_rows(root, rows)
                with self.assertRaises(DataError): a.audit(root, root/'result')
                report = json.loads((root/'result/report.json').read_bytes())
                self.assertFalse(report['capture_complete'])
                self.assertEqual(report['selected_for_observation'], [])

    def test_missing_footer_stays_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.save_rows(root, self.make_rows(root)[:-1])
            result = a.audit(root, root/'result')
            self.assertFalse(result['capture_complete'])
            self.assertEqual(result['selected_for_observation'], [])

    def test_network_budget_sequence_sleeps_and_source_failure_no_retry(self):
        assets = [f'A{i:02}' for i in range(25)]
        def response(url):
            payload = option_payload(assets) if url == self.plan['public_options_url'] else stats_payload(assets) if url == a.STATS else {'status': 'ok'}
            return fixtures.response(payload, url, time.time_ns())
        with tempfile.TemporaryDirectory() as tmp, patch.object(a, 'public_response', side_effect=response) as get, \
                patch.object(a.time, 'sleep') as sleep, redirect_stdout(StringIO()):
            result = a.capture(PLAN, Path(tmp)/'run')
            self.assertEqual(result['requests'], 32)
            urls = [x.args[0] for x in get.call_args_list]
            self.assertEqual(urls, [self.plan['public_options_url'], a.STATS]+[a.t.ALL_BOOKS]*10+
                             [a.TRADES+f'A{i:02}USDT' for i in range(20)])
            self.assertEqual([x.args[0] for x in sleep.call_args_list], [30]*9+[2]*19)
        with tempfile.TemporaryDirectory() as tmp, patch.object(a, 'public_response', return_value={'kind': 'error'}) as get, redirect_stdout(StringIO()):
            result = a.capture(PLAN, Path(tmp)/'run')
            self.assertEqual(get.call_count, 1)
            self.assertEqual(result['status'], 'source_or_validation_failure')


if __name__ == '__main__': unittest.main()
