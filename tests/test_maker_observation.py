import asyncio
import copy
import gzip
import json
from pathlib import Path
import tempfile
import time
import unittest

from cryptoir import maker_observation as m
from cryptoir.domain import DataError
from cryptoir.streams import EventWriter, dumps
import test_triangular as fixtures

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT/'research/maker-observation-v1.json'
NS = m.NS


def setup_protocol():
    protocol = m.Protocol(['RDNT', 'G', 'SKL'])
    protocol.command({'id': 1, 'connect': {}})
    protocol.parse({'id': 1, 'connect': {}}, 100*NS)
    while protocol.expected_commands:
        protocol.command(protocol.expected_commands[0])
    for ident in range(2, 8):
        protocol.parse({'id': ident, 'subscribe': {'offset': 0}}, 100*NS)
    return protocol


def publication(channel='public:orderbook-GUSDT', offset=1, source_ms=100000, **changes):
    book = channel.startswith('public:orderbook-')
    data = ({'lastUpdate': source_ms, 'bids': [['1', '100']], 'asks': [['2', '100']]}
            if book else {'time': source_ms, 'price': '1', 'volume': '2', 'type': 'buy'})
    data.update(changes)
    return {'push': {'channel': channel, 'pub': {'offset': offset, 'data': data}}}


def current_fixture(plan, universe, now=100*NS):
    payload = {'status': 'ok', 'coins': [{'coin': a, 'scale': '1'} for a in plan['assets']],
        'nobitex': {'activeCurrencies': plan['assets'], 'minOrders': {'usdt': '0.5'},
                    'amountPrecisions': {}, 'pricePrecisions': {}}}
    for asset in plan['assets']:
        rules = universe['market_rules'][asset+'/USDT']
        payload['nobitex']['amountPrecisions'][asset+'USDT'] = rules['quantity_step']
        payload['nobitex']['pricePrecisions'][asset+'USDT'] = rules['price_tick']
        payload['nobitex']['minOrders']['usdt'] = rules['minimum_notional']
    return fixtures.response(payload, plan['public_options_url'], now)


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.raw, self.plan, self.quote, self.universe = m.load_plan(PLAN)

    def test_plan_pins_bounds_and_native_one_character_market(self):
        self.assertIn('public:trades-GUSDT', self.plan['channels'])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'plan.json'
            for update in ({'assets': ['BTC']}, {'reconnects': 1}, {'duration_seconds': True},
                           {'websocket_url': 'wss://example.invalid'}):
                path.write_text(dumps({**self.plan, **update}), encoding='utf-8')
                with self.assertRaises(DataError):
                    m.load_plan(path)
            bad = copy.deepcopy(self.plan)
            bad['reference_files']['selection']['repository_path'] = '../private.json'
            path.write_text(dumps(bad), encoding='utf-8')
            with self.assertRaisesRegex(DataError, 'reference path'):
                m.load_plan(path)

    def test_current_metadata_rechecks_native_rules_and_clock(self):
        record = current_fixture(self.plan, self.universe)
        m.verify_current(record, self.plan, self.universe, 101*NS)
        for now in (99*NS, 161*NS):
            with self.assertRaisesRegex(DataError, 'stale'):
                m.verify_current(record, self.plan, self.universe, now)
        for mutation in ('step', 'inactive', 'scale'):
            payload = copy.deepcopy(record['payload'])
            if mutation == 'step':
                payload['nobitex']['amountPrecisions']['GUSDT'] = '1'
            elif mutation == 'inactive':
                payload['nobitex']['activeCurrencies'].remove('G')
            else:
                payload['coins'][1]['scale'] = '1000'
            changed = fixtures.response(payload, self.plan['public_options_url'])
            with self.assertRaisesRegex(DataError, 'metadata changed'):
                m.verify_current(changed, self.plan, self.universe, 101*NS)

    def test_protocol_full_book_trade_exact_values_and_identical_publications(self):
        protocol = setup_protocol()
        book = protocol.parse_frame(dumps(publication()), 101*NS)[0]
        self.assertEqual(book['book'].pair, 'G/USDT')
        trade = publication('public:trades-GUSDT', price='1.00000000000000001')
        first = protocol.parse_frame(dumps(trade), 101*NS)[0]
        self.assertEqual(str(first['price']), '1.00000000000000001')
        trade['push']['pub']['offset'] = 2
        second = protocol.parse_frame(dumps(trade), 101*NS)[0]
        self.assertEqual(first['source_ns'], second['source_ns'])
        self.assertEqual(second['raw_type'], 'buy')
        protocol.parse_frame('{}\n'+dumps(publication(offset=2)), 101*NS)
        protocol.command({})

    def test_protocol_rejects_gaps_duplicates_regressions_delta_and_bad_schema(self):
        for change in ('gap', 'duplicate', 'regression', 'delta', 'unknown', 'bad_level', 'nan'):
            protocol = setup_protocol()
            protocol.parse_frame(dumps(publication()), 101*NS)
            frame = publication(offset=2)
            pub = frame['push']['pub']
            if change == 'gap': pub['offset'] = 4
            if change == 'duplicate': pub['offset'] = 1
            if change == 'regression': pub['data']['lastUpdate'] = 99999
            if change == 'delta': pub['delta'] = True
            if change == 'unknown': frame['push']['channel'] = 'public:orderbook-BTCUSDT'
            if change == 'bad_level': pub['data']['bids'] = [['1', '100', 'extra']]
            if change == 'nan': pub['data']['bids'][0][0] = 'NaN'
            with self.subTest(change=change), self.assertRaises(DataError):
                protocol.parse_frame(dumps(frame), 101*NS)
        with self.assertRaises(DataError):
            setup_protocol().parse_frame('{"push":{},"push":{}}', 101*NS)
        with self.assertRaises(DataError):
            m.Protocol(['G']).parse_frame(dumps(publication()), 101*NS)
        with self.assertRaises(DataError):
            setup_protocol().parse({'id': 2, 'subscribe': {}}, 101*NS)

    def test_freshness_at_availability_and_only_causal_prior_book(self):
        metrics = m.Metrics(self.plan, self.quote, self.universe)
        protocol = setup_protocol()
        book = protocol.parse_frame(dumps(publication()), 100*NS)[0]
        metrics.observe(book, 106*NS)  # receipt alone cannot hide processing delay
        self.assertEqual(metrics.counts['G']['fresh_book_publications'], 0)
        self.assertEqual(len(metrics.widths[('G', 'assumed_stress')]), 0)
        metrics = m.Metrics(self.plan, self.quote, self.universe)
        metrics.observe(book, 101*NS)
        for offset, source, available in ((1, 99999, 101), (2, 100000, 101), (3, 100000, 106)):
            trade = protocol.parse_frame(dumps(publication('public:trades-GUSDT', offset, source)), available*NS)[0]
            metrics.observe(trade, available*NS)
        self.assertEqual(metrics.counts['G']['trade_prior_book_at_bid'], 1)
        self.assertEqual(metrics.counts['G']['trade_prior_book_unavailable'], 2)
        self.assertEqual(metrics.result(True, protocol)['selected_for_further_measurement'], [])
        # A source timestamp fresh at persistence cannot excuse an old receive.
        delayed = protocol.parse_frame(dumps(publication('public:trades-GUSDT', 4, 106000)), 100*NS)[0]
        metrics.observe(delayed, 106*NS)
        self.assertEqual(metrics.counts['G']['positive_volume_fresh_trade_publications'], 2)

    def test_fixed_gate_counts_publications_not_unique_trades_or_fills(self):
        metrics = m.Metrics(self.plan, self.quote, self.universe)
        protocol = setup_protocol()
        for i in range(1, 11):
            metrics.observe(protocol.parse_frame(dumps(publication(offset=i)), 101*NS)[0], 101*NS)
        for i in range(1, 6):
            metrics.observe(protocol.parse_frame(dumps(publication('public:trades-GUSDT', i)), 101*NS)[0], 101*NS)
        report = metrics.result(True, protocol)
        self.assertEqual(report['selected_for_further_measurement'], ['G'])
        self.assertIsNone(report['assets'][1]['unique_trade_count'])
        self.assertEqual(metrics.result(False, protocol)['selected_for_further_measurement'], [])
        zero = protocol.parse_frame(dumps(publication('public:trades-GUSDT', 6, volume='0')), 101*NS)[0]
        metrics.observe(zero, 101*NS)
        self.assertEqual(metrics.counts['G']['positive_volume_fresh_trade_publications'], 5)

    def make_recording(self, root):
        (root/'plan.json').write_bytes(self.raw)
        options = dumps(current_fixture(self.plan, self.universe)).encode()
        (root/'current-options.json').write_bytes(options)
        rows = []
        def add(event, now=101*NS):
            rows.append(dict(schema=2, run_id='synthetic', seq=len(rows)+1, venue='nobitex',
                observed_ns=now, monotonic_ns=now, clock_error_ns=0, **event))
        def frame(message):
            raw = dumps(message)
            add({'kind': 'frame', 'raw': raw, 'sha256': m.t.digest(raw.encode()), 'received_ns': 101*NS})
        add({'kind': 'recording_start', 'plan_sha256': m.t.digest(self.raw),
             'options_sha256': m.t.digest(options), 'source_sha256': {},
             'clock_origin_wall_ns': 101*NS, 'clock_origin_mono_ns': 101*NS})
        add({'kind': 'command', 'command': {'id': 1, 'connect': {}}})
        frame({'id': 1, 'connect': {}})
        for i, c in enumerate(self.plan['channels'], 2):
            add({'kind': 'command', 'command': {'id': i, 'subscribe': {'channel': c}}})
        for i in range(2, 8): frame({'id': i, 'subscribe': {'offset': 0}})
        frame(publication())
        frame(publication('public:trades-GUSDT'))
        add({'kind': 'recording_end', 'reason': 'duration_completed'}, 1001*NS)
        return rows

    def save_rows(self, root, rows):
        with gzip.open(root/'frames.jsonl.gz', 'wt', encoding='utf-8') as target:
            target.write('\n'.join(dumps(r) for r in rows)+'\n')

    def test_raw_audit_full_duration_and_no_optimistic_activity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = self.make_recording(root)
            self.save_rows(root, rows)
            report = m.audit(root, root/'report.json')
            self.assertTrue(report['capture_complete'])
            self.assertEqual(report['elapsed_seconds'], 900)
            self.assertEqual(report['assets'][1]['counts']['trade_prior_book_at_bid'], 1)
            self.assertEqual(report['selected_for_further_measurement'], [])
            self.assertFalse(report['strategy_accepted'])
            with self.assertRaises(FileExistsError): m.audit(root, root/'report.json')

    def test_audit_rejects_tampering_order_clock_and_false_completion(self):
        for change in ('hash', 'sequence', 'clock', 'received', 'footer', 'after', 'command', 'source'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                rows = self.make_recording(root)
                if change == 'hash': rows[-2]['sha256'] = '0'*64
                if change == 'sequence': rows[5]['seq'] = 100
                if change == 'clock': rows[-1]['observed_ns'] += 2*NS
                if change == 'received': rows[-2]['received_ns'] += 1
                if change == 'footer': rows[-1]['monotonic_ns'] -= NS
                if change == 'after': rows.append(copy.deepcopy(rows[-1]))
                if change == 'command': rows[3]['command']['subscribe']['channel'] = 'private:balance'
                if change == 'source':
                    rows[-2]['raw'] = dumps(publication('public:trades-GUSDT', offset=5))
                    rows[-2]['sha256'] = m.t.digest(rows[-2]['raw'].encode())
                self.save_rows(root, rows)
                with self.assertRaises(DataError): m.audit(root, root/'report.json')

    def test_missing_end_and_detected_protocol_failure_remain_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = self.make_recording(root)
            self.save_rows(root, rows[:-1])
            report = m.audit(root, root/'incomplete.json')
            self.assertFalse(report['capture_complete'])
            rows[-2]['raw'] = dumps(publication('public:trades-GUSDT', offset=5))
            rows[-2]['sha256'] = m.t.digest(rows[-2]['raw'].encode())
            rows[-1]['reason'] = 'invalid_stream'
            self.save_rows(root, rows)
            report = m.audit(root, root/'invalid.json')
            self.assertEqual(report['parse_error'], 'publication offset gap or regression')
            self.assertFalse(report['capture_complete'])

    def test_real_loopback_six_subscriptions_heartbeat_and_quiet_duration(self):
        from websockets.asyncio.server import serve
        async def run(root):
            seen = []
            async def server(ws):
                seen.append(json.loads(await ws.recv()))
                await ws.send(dumps({'id': 1, 'connect': {}}))
                for i in range(2, 8): seen.append(json.loads(await ws.recv()))
                await ws.send('\n'.join(dumps({'id': i, 'subscribe': {}}) for i in range(2, 8)))
                await ws.send('{}')
                seen.append(json.loads(await ws.recv()))
                await ws.wait_closed()
            async with serve(server, '127.0.0.1', 0) as host:
                url = f'ws://127.0.0.1:{host.sockets[0].getsockname()[1]}'
                writer = EventWriter(root/'test.gz', 1024*1024)
                try:
                    reason = await m.capture_socket(self.plan, writer, url=url, seconds=.3)
                finally:
                    writer.close()
            self.assertEqual(reason, 'duration_completed')
            self.assertEqual(seen, [{'id': 1, 'connect': {}}]+[
                {'id': i, 'subscribe': {'channel': c}} for i, c in enumerate(self.plan['channels'], 2)]+[{}])
        with tempfile.TemporaryDirectory() as tmp: asyncio.run(run(Path(tmp)))

    def test_loopback_disconnect_and_rejected_protocol_stop_without_reconnect(self):
        from websockets.asyncio.server import serve
        async def run(root, rejected):
            calls = []
            async def server(ws):
                calls.append(1)
                await ws.recv()
                if rejected:
                    await ws.send(dumps({'id': 1, 'error': {'code': 1}}))
                    await ws.wait_closed()
            async with serve(server, '127.0.0.1', 0) as host:
                writer = EventWriter(root/'test.gz', 1024*1024)
                try:
                    result = await m.capture_socket(self.plan, writer,
                        url=f'ws://127.0.0.1:{host.sockets[0].getsockname()[1]}', seconds=1)
                finally: writer.close()
            self.assertEqual(calls, [1])
            self.assertEqual(result, 'invalid_stream' if rejected else 'transport_error')
        for rejected in (False, True):
            with tempfile.TemporaryDirectory() as tmp: asyncio.run(run(Path(tmp), rejected))

    def test_loopback_missing_ack_is_handshake_timeout_not_full_duration(self):
        from websockets.asyncio.server import serve
        async def run(root):
            async def server(ws):
                await ws.recv()
                await ws.wait_closed()
            async with serve(server, '127.0.0.1', 0) as host:
                writer = EventWriter(root/'test.gz', 1024*1024)
                try:
                    result = await m.capture_socket({**self.plan, 'connect_timeout_seconds': .05}, writer,
                        url=f'ws://127.0.0.1:{host.sockets[0].getsockname()[1]}', seconds=1)
                finally: writer.close()
            self.assertEqual(result, 'handshake_timeout')
        with tempfile.TemporaryDirectory() as tmp: asyncio.run(run(Path(tmp)))


if __name__ == '__main__':
    unittest.main()
