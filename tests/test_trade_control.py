import asyncio
import copy
import gzip
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from cryptoir import maker_observation as m, trade_control as c
from cryptoir.domain import DataError
from cryptoir.streams import EventWriter, dumps
import test_triangular as fixtures

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT/'research/trade-feed-control-v1.json'
NS = c.NS


def trade(source=200000, price='1.000000000000000001', volume='1', kind='buy'):
    return {'time': source, 'price': price, 'volume': volume, 'type': kind}


def options(now=100*NS):
    payload = {'status': 'ok', 'coins': [{'coin': a, 'scale': '1'} for a in ('BTC', 'USDT')],
        'nobitex': {'activeCurrencies': ['BTC', 'USDT'], 'minOrders': {'usdt': '0.5', 'rls': '10000'},
            'amountPrecisions': {'BTCUSDT': '0.000001', 'USDTIRT': '0.01'},
            'pricePrecisions': {'BTCUSDT': '0.01', 'USDTIRT': '1'}}}
    return fixtures.response(payload, c.catalog.ENDPOINTS['nobitex'], now)


def rest(symbol, rows=None, now=285*NS):
    return fixtures.response({'status': 'ok', 'trades': rows if rows is not None else [trade()]},
                             c.REST_URLS[symbol], now)


def ready_protocol():
    protocol = m.Protocol([], trade_symbols=c.SYMBOLS)
    protocol.command({'id': 1, 'connect': {}})
    protocol.parse({'id': 1, 'connect': {}}, 104*NS)
    while protocol.expected_commands: protocol.command(protocol.expected_commands[0])
    for ident in (2, 3): protocol.parse({'id': ident, 'subscribe': {'offset': 0}}, 104*NS)
    return protocol


def pub(symbol='BTCUSDT', offset=1, payload=None):
    return {'push': {'channel': 'public:trades-'+symbol,
                     'pub': {'offset': offset, 'data': payload or trade()}}}


class TradeControlTests(unittest.TestCase):
    def setUp(self): self.raw, self.plan = c.load_plan(PLAN)

    def test_registration_bounds_routes_and_current_native_membership(self):
        c.verify_options(options(), self.plan, 101*NS)
        for now in (99*NS, 161*NS):
            with self.assertRaises(DataError): c.verify_options(options(), self.plan, now)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'plan.json'
            for update in ({'duration_seconds': 181}, {'reconnects': True}, {'native_symbols': ['GUSDT']},
                           {'rest_urls': {'BTCUSDT': 'https://example.invalid/private'}}):
                path.write_text(dumps({**self.plan, **update}), encoding='utf-8')
                with self.assertRaises(DataError): c.load_plan(path)
        with self.assertRaises(DataError): m.Protocol([], trade_symbols=['BTCUSDT'])
        with self.assertRaises(DataError): m.Protocol(['BTC'], trade_symbols=c.SYMBOLS)
        changed = options()['payload']
        changed['coins'][1]['scale'] = '1000'
        with self.assertRaises(DataError):
            c.verify_options(fixtures.response(changed, self.plan['public_options_url']), self.plan, 101*NS)

    def test_trade_protocol_keeps_native_value_without_fiat_conversion(self):
        protocol = ready_protocol()
        for symbol in c.SYMBOLS:
            event = protocol.parse_frame(dumps(pub(symbol)), 201*NS)[0]
            self.assertEqual(event['native_symbol'], symbol)
            self.assertNotIn('asset', event)
            self.assertEqual(str(event['price']), '1.000000000000000001')
            self.assertEqual(event['raw_type'], 'buy')
        with self.assertRaises(DataError):
            protocol.parse_frame(dumps({'push': {'channel': 'public:orderbook-USDTIRT', 'pub': {}}}), 201*NS)

    def test_rest_schema_hash_order_time_volume_and_duplicates(self):
        self.assertEqual(len(c.parse_rest(rest('BTCUSDT', [trade(), trade()]), 'BTCUSDT', self.plan)), 2)
        self.assertEqual(c.parse_rest(rest('BTCUSDT', [trade(volume='0')]), 'BTCUSDT', self.plan)[0]['volume'], 0)
        for rows in ([trade(volume='-1')], [trade(kind='unknown')], [trade(price='NaN')],
                     [trade(source=100000), trade(source=200000)], [trade(source=300000)], [trade()]*1001):
            with self.assertRaises(DataError): c.parse_rest(rest('BTCUSDT', rows), 'BTCUSDT', self.plan)
        bad = rest('BTCUSDT'); bad['body_sha256'] = '0'*64
        with self.assertRaises(DataError): c.parse_rest(bad, 'BTCUSDT', self.plan)

    def test_multiset_comparison_guarded_window_and_missing_occurrences(self):
        protocol = ready_protocol()
        event = protocol.parse_frame(dumps(pub()), 201*NS)[0]
        ws = [{**event, 'available_ns': 201*NS}]*2
        rows = c.parse_rest(rest('BTCUSDT'), 'BTCUSDT', self.plan)
        capture = {'capture_complete': True, 'capture_end_ns': 283*NS}
        result = c.compare('BTCUSDT', ws, rows, protocol, capture, self.plan)
        self.assertEqual(result['matched_fingerprint_occurrences'], 1)
        self.assertEqual(result['ws_occurrences_without_matching_post_rest'], 1)
        self.assertEqual(result['fresh_positive_ws_publications'], 2)
        self.assertIsNone(result['unique_exchange_trade_count'])
        self.assertFalse(result['post_rest_timestamp_span_covers_guarded_interval'])
        result = c.compare('BTCUSDT', ws[:1], rows*2, protocol, capture, self.plan)
        self.assertEqual(result['status'], 'cross_source_discrepancy')
        self.assertEqual(result['rest_occurrences_without_matching_ws'], 1)
        for source in (108999, 278001):
            outside = [m.trade_payload(trade(source=source))]
            result = c.compare('BTCUSDT', [], outside, protocol, capture, self.plan)
            self.assertEqual(result['guarded_post_rest_occurrences'], 0)
            self.assertEqual(result['status'], 'inconclusive_quiet_or_uncovered_window')

    def test_missing_stale_zero_and_incomplete_never_become_fresh_route(self):
        protocol = ready_protocol()
        event = protocol.parse_frame(dumps(pub()), 201*NS)[0]
        capture = {'capture_complete': True, 'capture_end_ns': 283*NS}
        stale = [{**event, 'available_ns': 207*NS}]
        result = c.compare('BTCUSDT', stale, [], protocol, capture, self.plan)
        self.assertFalse(result['fresh_trade_route_observed'])
        self.assertEqual(result['status'], 'no_fresh_positive_ws_payload')
        self.assertEqual(c.compare('BTCUSDT', [], None, protocol, capture, self.plan)['status'], 'rest_reference_unavailable')
        capture['capture_complete'] = False
        self.assertEqual(c.compare('BTCUSDT', [], [], protocol, capture, self.plan)['status'], 'incomplete_capture')

    def recording(self, root):
        (root/'plan.json').write_bytes(self.raw)
        raw_options = dumps(options()).encode()
        (root/'current-options.json').write_bytes(raw_options)
        for phase, now, source in (('before', 101*NS, 99000), ('after', 285*NS, 200000)):
            records = {s: rest(s, [trade(source=source)], now+i*NS) for i, s in enumerate(c.SYMBOLS)}
            (root/('rest-'+phase+'.json')).write_text(dumps({'schema': 1, 'phase': phase, 'records': records}), encoding='utf-8')
        rows = []
        def add(event, now=104*NS):
            rows.append(dict(schema=2, run_id='synthetic', seq=len(rows)+1, venue='nobitex',
                observed_ns=now, monotonic_ns=now, clock_error_ns=0, **event))
        def frame(message, now=104*NS):
            raw = dumps(message)
            add({'kind': 'frame', 'raw': raw, 'sha256': m.t.digest(raw.encode()), 'received_ns': now}, now)
        add({'kind': 'recording_start', 'plan_sha256': m.t.digest(self.raw), 'options_sha256': m.t.digest(raw_options),
             'source_sha256': {}, 'clock_origin_wall_ns': 103*NS, 'clock_origin_mono_ns': 103*NS}, 103*NS)
        add({'kind': 'command', 'command': {'id': 1, 'connect': {}}})
        frame({'id': 1, 'connect': {}})
        for i, s in enumerate(c.SYMBOLS, 2):
            add({'kind': 'command', 'command': {'id': i, 'subscribe': {'channel': 'public:trades-'+s}}})
        for i in (2, 3): frame({'id': i, 'subscribe': {'offset': 0}})
        for s in c.SYMBOLS: frame(pub(s), 201*NS)
        add({'kind': 'recording_end', 'reason': 'duration_completed'}, 283*NS)
        with gzip.open(root/'frames.jsonl.gz', 'wt', encoding='utf-8') as target:
            target.write('\n'.join(dumps(row) for row in rows)+'\n')

    def test_end_to_end_raw_audit_and_missing_or_mistimed_rest_phases(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.recording(root)
            report = c.audit(root, root/'report.json')
            self.assertTrue(report['capture_complete'])
            self.assertTrue(report['control_complete'])
            for row in report['markets']:
                self.assertEqual(row['matched_fingerprint_occurrences'], 1)
                self.assertTrue(row['fresh_trade_route_observed'])
                self.assertFalse(row['execution_ready'])
            with self.assertRaises(FileExistsError): c.audit(root, root/'report.json')
            path = root/'rest-after.json'
            data = json.loads(path.read_bytes())
            data['records']['BTCUSDT']['started_ns'] = 100*NS
            path.write_text(dumps(data), encoding='utf-8')
            report = c.audit(root, root/'mistimed.json')
            self.assertFalse(report['control_complete'])
            self.assertEqual(report['rest_after']['markets']['BTCUSDT']['status'], 'unavailable')
            path.rename(root/'preserved-after.json')
            report = c.audit(root, root/'missing.json')
            self.assertFalse(report['control_complete'])
            self.assertEqual(report['rest_after']['status'], 'missing')

    def test_capture_network_budget_is_one_options_four_lists_one_session(self):
        calls = []
        def response(url):
            calls.append(url)
            if url == self.plan['public_options_url']: return options(time.time_ns())
            symbol = next(s for s in c.SYMBOLS if c.REST_URLS[s] == url)
            return rest(symbol, now=time.time_ns())
        async def socket(plan, writer, *, protocol):
            calls.append('socket')
            self.assertEqual(protocol.channels, self.plan['channels'])
            writer.write('nobitex', {'kind': 'recording_end', 'reason': 'duration_completed'})
            return 'duration_completed'
        with tempfile.TemporaryDirectory() as tmp, patch.object(c, 'public_response', side_effect=response), \
                patch.object(m, 'capture_socket', side_effect=socket):
            c.capture(PLAN, Path(tmp)/'run')
        self.assertEqual(calls, [self.plan['public_options_url'], *c.REST_URLS.values(), 'socket', *c.REST_URLS.values()])

    def test_loopback_reference_channels_accept_actual_trade_frames(self):
        from websockets.asyncio.server import serve
        async def run(root):
            async def server(ws):
                self.assertEqual(json.loads(await ws.recv()), {'id': 1, 'connect': {}})
                await ws.send(dumps({'id': 1, 'connect': {}}))
                for i, s in enumerate(c.SYMBOLS, 2):
                    self.assertEqual(json.loads(await ws.recv()), {'id': i, 'subscribe': {'channel': 'public:trades-'+s}})
                await ws.send('\n'.join(dumps({'id': i, 'subscribe': {'offset': 0}}) for i in (2, 3)))
                for s in c.SYMBOLS: await ws.send(dumps(pub(s, payload=trade(source=time.time_ns()//1_000_000))))
                await ws.wait_closed()
            async with serve(server, '127.0.0.1', 0) as host:
                writer = EventWriter(root/'test.gz', 1024*1024)
                protocol = m.Protocol([], trade_symbols=c.SYMBOLS)
                try:
                    result = await m.capture_socket(self.plan, writer, protocol=protocol,
                        url=f'ws://127.0.0.1:{host.sockets[0].getsockname()[1]}', seconds=.3)
                finally: writer.close()
            self.assertEqual(result, 'duration_completed')
            self.assertEqual(set(protocol.source_times), set(self.plan['channels']))
        with tempfile.TemporaryDirectory() as tmp: asyncio.run(run(Path(tmp)))


if __name__ == '__main__': unittest.main()
