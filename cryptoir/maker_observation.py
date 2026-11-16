"""One public connection, raw evidence and causal activity audit; no orders/fills."""

import asyncio
from collections import Counter, deque
from decimal import localcontext
import gzip
from pathlib import Path
from statistics import median
import time

from . import catalog, maker_spread, triangular as t
from .domain import Book, DataError, Level, number
from .feeds import public_response
from .streams import EventWriter, RecordingIOError, RecordingLimit, URLS, dumps, millis

NS = 1_000_000_000
REFERENCES = {
    'selection': 'research/evidence/maker-spread-v1-summary.json',
    'quote_plan': 'research/maker-spread-v1.json',
    'universe_plan': 'research/triangular-v2-universe.json',
}


def load_plan(path, root=None):
    raw = Path(path).read_bytes()
    plan = t.strict_json(raw)
    root = root or Path(__file__).resolve().parents[1]
    refs = {}
    for key, name in REFERENCES.items():
        ref = plan['reference_files'][key]
        if ref['repository_path'] != name:
            raise DataError('unexpected observation reference path')
        content = (root/name).read_bytes()
        if t.digest(content) != ref['sha256']:
            raise DataError('observation reference hash mismatch')
        refs[key] = t.strict_json(content)
    quote, universe = refs['quote_plan'], refs['universe_plan']
    maker_spread.validate_plan(quote, universe)
    assets = [s['asset'] for s in refs['selection']['selected_for_observation']]
    if (plan.get('schema') != 1 or plan.get('venue') != 'nobitex'
            or plan.get('experiment') != 'maker-observation-v1-public-book-trade-activity'
            or plan.get('assets') != assets or assets != ['RDNT', 'G', 'SKL']
            or plan.get('catalog_sha256') != universe['catalog_sha256']
            or plan.get('channels') != channels(assets)
            or plan.get('websocket_url') != URLS['nobitex']
            or plan.get('public_options_url') != catalog.ENDPOINTS['nobitex']):
        raise DataError('observation scope differs from fixed registration')
    fixed = dict(duration_seconds=900, max_logical_mb=64, minimum_free_disk_mb=512,
                 max_age_ms=5000, maximum_metadata_age_seconds=60,
                 maximum_clock_error_ns=NS, connect_timeout_seconds=10,
                 subscription_timeout_seconds=10, reconnects=0)
    if any(type(plan.get(k)) is not int or plan[k] != v for k, v in fixed.items()):
        raise DataError('observation bounds differ from fixed registration')
    rule = plan['further_measurement_rule']
    if (any(rule.get(k) is not True for k in ('all_six_subscriptions_accepted',
            'normal_full_duration_required', 'no_detected_continuity_or_clock_failure',
            'positive_median_stressed_quote_width_required'))
            or type(rule.get('minimum_fresh_book_publications_per_asset')) is not int
            or rule['minimum_fresh_book_publications_per_asset'] != 10
            or type(rule.get('minimum_positive_volume_fresh_trade_publications_per_asset')) is not int
            or rule['minimum_positive_volume_fresh_trade_publications_per_asset'] != 5):
        raise DataError('observation follow-up rule changed')
    return raw, plan, quote, universe


def channels(assets):
    return [f'public:{kind}-{t.pair_symbol(asset+"/USDT")}'
            for asset in assets for kind in ('orderbook', 'trades')]


def verify_current(record, plan, universe, now_ns):
    t.verify_response(record, plan['public_options_url'])
    if not 0 <= now_ns-record['received_ns'] <= plan['maximum_metadata_age_seconds']*NS:
        raise DataError('current metadata unavailable or stale')
    markets = {m['native_symbol']: m for m in catalog.parse_catalog('nobitex', record['payload'])}
    for asset in plan['assets']:
        pair = asset+'/USDT'
        market = markets.get(t.pair_symbol(pair))
        if (not market or market['base_currency_active'] is not True
                or number(market['native_scale']) != 1
                or any(number(market[k]) != number(universe['market_rules'][pair][k])
                       for k in ('quantity_step', 'price_tick', 'minimum_notional'))):
            raise DataError('selected market metadata changed or unavailable')


class Protocol:
    """Raw Centrifuge JSON, without delta/recovery or inferred aggressor labels."""
    def __init__(self, assets):
        self.channels = channels(assets)
        self.assets = {channel: asset for asset in assets for channel in channels([asset])}
        self.connected = False
        self.acks, self.offsets, self.source_times = set(), {}, {}
        self.expected_commands = deque([{'id': 1, 'connect': {}}])

    def command(self, value):
        if not self.expected_commands or value != self.expected_commands.popleft():
            raise DataError('unexpected outgoing public command')

    def parse_frame(self, raw, received_ns):
        if not isinstance(raw, str) or not raw.strip():
            raise DataError('expected nonempty public text frame')
        output = []
        for line in raw.splitlines():
            if line.strip():
                output += self.parse(t.strict_json(line), received_ns)
        return output

    def parse(self, message, received_ns):
        if not isinstance(message, dict):
            raise DataError('invalid public message envelope')
        if message == {}:
            self.expected_commands.append({})
            return []
        if any(command != {} for command in self.expected_commands):
            raise DataError('response before required commands')
        if 'id' in message:
            ident = message['id']
            if type(ident) is not int:
                raise DataError('invalid acknowledgement id')
            if ident == 1 and not self.connected and set(message) == {'id', 'connect'}:
                if not isinstance(message['connect'], dict):
                    raise DataError('invalid connection acknowledgement')
                self.connected = True
                self.expected_commands.extend({'id': i, 'subscribe': {'channel': c}}
                                              for i, c in enumerate(self.channels, 2))
                return []
            if (not self.connected or not 2 <= ident < len(self.channels)+2
                    or set(message) != {'id', 'subscribe'} or not isinstance(message['subscribe'], dict)):
                raise DataError('unexpected command acknowledgement')
            channel = self.channels[ident-2]
            ack = message['subscribe']
            if channel in self.acks or ack.get('publications') or ack.get('recovered') or ack.get('delta'):
                raise DataError('duplicate acknowledgement or unsolicited recovery/delta')
            self.acks.add(channel)
            self.offset(channel, ack.get('offset'), initial=True)
            return []
        if set(message) != {'push'} or not isinstance(message['push'], dict):
            raise DataError('unexpected public protocol message')
        push = message['push']
        if set(push) != {'channel', 'pub'}:
            raise DataError('invalidated or unknown public subscription')
        channel, pub = push['channel'], push['pub']
        if channel not in self.acks or not isinstance(pub, dict) or pub.get('delta'):
            raise DataError('unacknowledged channel or unsupported delta')
        offset = pub.get('offset')
        self.offset(channel, offset)
        data = pub['data']
        if isinstance(data, str):
            data = t.strict_json(data)
        if not isinstance(data, dict):
            raise DataError('invalid public publication data')
        asset = self.assets[channel]
        is_book = channel.startswith('public:orderbook-')
        source_ns = millis(data['lastUpdate'] if is_book else data['time'])
        if source_ns < self.source_times.get(channel, source_ns):
            raise DataError('source timestamp regression')
        self.source_times[channel] = source_ns
        result = dict(channel=channel, asset=asset, source_ns=source_ns, offset=offset, received_ns=received_ns,
                      kind='book' if is_book else 'trade')
        if is_book:
            def levels(rows):
                if not isinstance(rows, list) or any(not isinstance(r, list) or len(r) != 2 for r in rows):
                    raise DataError('invalid public book levels')
                return tuple(Level(*row) for row in rows)
            result['book'] = Book('nobitex', asset+'/USDT', levels(data['bids']), levels(data['asks']),
                                  received_ns, 0, source_ns)
        else:
            price, volume = number(data['price']), number(data['volume'])
            if price <= 0 or volume < 0 or data['type'] not in ('buy', 'sell'):
                raise DataError('invalid trade price volume or type')
            result.update(price=price, volume=volume, raw_type=data['type'])
        return [result]

    def offset(self, channel, value, initial=False):
        if value is None:
            return
        if type(value) is not int or value < 0:
            raise DataError('invalid public offset')
        previous = self.offsets.get(channel)
        if not initial and previous is not None and value != previous+1:
            raise DataError('publication offset gap or regression')
        self.offsets[channel] = value


async def capture_socket(plan, writer, *, url=None, seconds=None):
    """Overrides are for loopback tests only; CLI uses registered URL/duration."""
    from websockets.asyncio.client import connect
    protocol = Protocol(plan['assets'])
    reason = 'interrupted'
    def write(event):
        row = writer.write('nobitex', event)
        if abs(row['clock_error_ns']) > plan['maximum_clock_error_ns']:
            raise DataError('local clock discontinuity')
        return row
    try:
        async with asyncio.timeout(seconds if seconds is not None else plan['duration_seconds']) as lifetime:
            async with connect(url or plan['websocket_url'], open_timeout=plan['connect_timeout_seconds'],
                               close_timeout=2, max_size=2**20, max_queue=16, ping_interval=20, ping_timeout=20) as ws:
                deadline = time.monotonic()+plan['connect_timeout_seconds']
                while True:
                    while protocol.expected_commands:
                        command = protocol.expected_commands[0]
                        write({'kind': 'command', 'command': command})
                        protocol.command(command)
                        await ws.send(dumps(command))
                    if len(protocol.acks) == len(protocol.channels):
                        raw = await ws.recv()
                    else:
                        async with asyncio.timeout(max(0, deadline-time.monotonic())):
                            raw = await ws.recv()
                    received = time.time_ns()
                    row = write({'kind': 'frame', 'received_ns': received, 'raw': raw,
                                 'sha256': t.digest(raw.encode() if isinstance(raw, str) else raw)})
                    was_connected = protocol.connected
                    protocol.parse_frame(raw, row['received_ns'])
                    if protocol.connected and not was_connected:
                        deadline = time.monotonic()+plan['subscription_timeout_seconds']
    except TimeoutError:
        reason = 'duration_completed' if lifetime.expired() else 'handshake_timeout'
    except (RecordingLimit, RecordingIOError):
        raise
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        reason = 'clock_discontinuity' if str(exc) == 'local clock discontinuity' else 'invalid_stream'
        writer.write('nobitex', {'kind': 'failure', 'reason': reason, 'error_type': type(exc).__name__,
                                'detail': str(exc) if isinstance(exc, DataError) else 'invalid source schema'})
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        reason = 'transport_error'
        writer.write('nobitex', {'kind': 'failure', 'reason': reason, 'error_type': type(exc).__name__})
    finally:
        writer.write('nobitex', {'kind': 'recording_end', 'reason': reason})
    return reason


def capture(plan_path, catalog_path, directory):
    raw, plan, _, universe = load_plan(plan_path)
    t.verify_catalog(Path(catalog_path), universe)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    (directory/'plan.json').write_bytes(raw)
    # Exactly one public request; preserve even failures before validating.
    current = public_response(plan['public_options_url'])
    current_raw = dumps(current).encode()
    (directory/'current-options.json').write_bytes(current_raw)
    verify_current(current, plan, universe, time.time_ns())
    writer = EventWriter(directory/'frames.jsonl.gz', plan['max_logical_mb']*1024*1024)
    try:
        writer.write('nobitex', {'kind': 'recording_start', 'plan_sha256': t.digest(raw),
                     'options_sha256': t.digest(current_raw), 'source_sha256': t.source_hashes(),
                     'clock_origin_wall_ns': writer.start_wall, 'clock_origin_mono_ns': writer.start_mono})
        reason = asyncio.run(capture_socket(plan, writer))
    finally:
        writer.close()
    return {'status': reason, 'execution_ready': False, 'strategy_accepted': False}


class Metrics:
    def __init__(self, plan, quote, universe):
        self.plan, self.quote, self.universe = plan, quote, universe
        self.counts = {asset: Counter() for asset in plan['assets']}
        self.channel_counts = {channel: Counter() for channel in plan['channels']}
        self.books, self.widths, self.exclusions = {}, {}, {}
        for asset in plan['assets']:
            for scenario in quote['scenarios']:
                self.widths[(asset, scenario['name'])] = []
                self.exclusions[(asset, scenario['name'])] = Counter()

    def observe(self, event, available_ns):
        asset, source = event['asset'], event['source_ns']
        counts, channel = self.counts[asset], self.channel_counts[event['channel']]
        channel['publications'] += 1
        channel['with_offset' if event['offset'] is not None else 'without_offset'] += 1
        channel.setdefault('first_source_ns', source)
        channel['last_source_ns'] = source
        age = available_ns-source
        channel['min_source_age_ns'] = min(channel.get('min_source_age_ns', age), age)
        channel['max_source_age_ns'] = max(channel.get('max_source_age_ns', age), age)
        max_age = self.plan['max_age_ms']*1_000_000
        fresh = 0 <= age <= max_age and 0 <= available_ns-event['received_ns'] <= max_age
        if event['kind'] == 'book':
            book, previous = event['book'], self.books.get(asset)
            counts['book_publications'] += 1
            counts['fresh_book_publications'] += int(fresh)
            if previous:
                for side in ('bids', 'asks'):
                    before, after = getattr(previous, side)[0], getattr(book, side)[0]
                    counts[side+'_top_price_changes'] += int(before.price != after.price)
                    counts[side+'_same_price_quantity_decreases'] += int(
                        before.price == after.price and after.quantity < before.quantity)
            self.books[asset] = book
            for scenario in self.quote['scenarios']:
                key = asset, scenario['name']
                try:
                    value = maker_spread.quote_width(book, self.universe['market_rules'][book.pair],
                                                    self.quote, scenario, available_ns)
                    self.widths[key].append(value['quoted_width_net_usdt'])
                except DataError as exc:
                    self.exclusions[key][str(exc)] += 1
        else:
            counts['trade_publications'] += 1
            counts['raw_type_'+event['raw_type']] += 1
            counts['zero_volume_trade_publications'] += int(event['volume'] == 0)
            counts['positive_volume_fresh_trade_publications'] += int(fresh and event['volume'] > 0)
            book = self.books.get(asset)
            try:
                if not fresh or book is None or book.exchange_ns > source:
                    raise DataError('no causal fresh book')
                book.validate_age(available_ns, self.plan['max_age_ms']*1_000_000)
                price, bid, ask = event['price'], book.bids[0].price, book.asks[0].price
                relation = 'at_bid' if price == bid else 'at_ask' if price == ask else 'inside' if bid < price < ask else 'outside'
                counts['trade_prior_book_'+relation] += 1
            except DataError:
                counts['trade_prior_book_unavailable'] += 1

    def result(self, complete, protocol):
        rows, selected = [], []
        with localcontext() as context:
            context.prec = 60
            for asset in self.plan['assets']:
                counts = self.counts[asset]
                costs = []
                for scenario in self.quote['scenarios']:
                    key = asset, scenario['name']
                    values = self.widths[key]
                    costs.append({'scenario': scenario['name'], 'valid': len(values),
                                  'positive': sum(v > 0 for v in values),
                                  'median_conditional_width_usdt': median(values) if values else None,
                                  'exclusions': dict(self.exclusions[key])})
                stress = costs[-1]['median_conditional_width_usdt']
                eligible = bool(complete and counts['fresh_book_publications'] >= 10
                    and counts['positive_volume_fresh_trade_publications'] >= 5 and stress is not None and stress > 0)
                rows.append({'asset': asset, 'counts': dict(counts), 'cost_scenarios': costs,
                             'unique_trade_count': None, 'eligible_for_further_measurement': eligible})
                if eligible:
                    selected.append(asset)
        return {'assets': rows, 'channels': {c: {'accepted': c in protocol.acks, **dict(v)}
                for c, v in self.channel_counts.items()}, 'selected_for_further_measurement': selected}


def audit(directory, output):
    directory, output = Path(directory), Path(output)
    raw, plan, quote, universe = load_plan(directory/'plan.json')
    options_raw = (directory/'current-options.json').read_bytes()
    current = t.strict_json(options_raw)
    protocol, metrics = Protocol(plan['assets']), Metrics(plan, quote, universe)
    path = directory/'frames.jsonl.gz'
    if path.stat().st_size > (plan['max_logical_mb']+1)*1024*1024:
        raise DataError('compressed observation exceeds bound')
    header, footer, previous, failure, parse_error = None, None, None, None, None
    logical, count, max_clock_error = 0, 0, 0
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        while line := stream.readline(2**22+1):
            logical += len(line.encode())
            if len(line) > 2**22 or logical > plan['max_logical_mb']*1024*1024:
                raise DataError('logical observation exceeds bound')
            row = t.strict_json(line)
            count += 1
            if (footer is not None or row.get('schema') != 2 or row.get('venue') != 'nobitex'
                    or type(row.get('seq')) is not int or row['seq'] != count
                    or any(type(row.get(k)) is not int or row[k] <= 0 for k in ('observed_ns', 'monotonic_ns'))):
                raise DataError('invalid observation record envelope or ordering')
            if header is None:
                header = row
                if (row.get('kind') != 'recording_start' or row.get('plan_sha256') != t.digest(raw)
                        or row.get('options_sha256') != t.digest(options_raw)
                        or not isinstance(row.get('source_sha256'), dict)
                        or not isinstance(row.get('run_id'), str) or not row['run_id']):
                    raise DataError('observation header provenance mismatch')
                verify_current(current, plan, universe, row['observed_ns'])
            if (row['run_id'] != header['run_id'] or previous and row['monotonic_ns'] < previous['monotonic_ns']):
                raise DataError('observation run or monotonic ordering mismatch')
            clock_error = row['observed_ns']-header['clock_origin_wall_ns']-(row['monotonic_ns']-header['clock_origin_mono_ns'])
            max_clock_error = max(max_clock_error, abs(clock_error))
            # After a discontinuity EventWriter reanchors its diagnostic; the audit
            # still measures every record against the original session clock.
            if max_clock_error <= NS and row.get('clock_error_ns') != clock_error:
                raise DataError('observation clock diagnostic mismatch')
            kind = row['kind']
            if kind == 'command':
                if failure or parse_error:
                    raise DataError('commands after observation failure')
                protocol.command(row['command'])
            elif kind == 'frame':
                if failure or parse_error:
                    raise DataError('frames after observation failure')
                if (not isinstance(row.get('raw'), str) or t.digest(row['raw'].encode()) != row.get('sha256')
                        or type(row.get('received_ns')) is not int
                        or not header['observed_ns'] <= row['received_ns'] <= row['observed_ns']
                        or previous and row['received_ns'] < previous['observed_ns']):
                    raise DataError('raw frame hash or receive time mismatch')
                try:
                    events = protocol.parse_frame(row['raw'], row['received_ns'])
                    for event in events:
                        metrics.observe(event, row['observed_ns'])
                except (ValueError, KeyError, TypeError, IndexError) as exc:
                    parse_error = str(exc) if isinstance(exc, DataError) else 'invalid source schema'
            elif kind == 'failure':
                if failure is not None:
                    raise DataError('duplicate observation failure')
                failure = row
            elif kind == 'recording_end':
                footer = row
            elif kind != 'recording_start' or count != 1:
                raise DataError('unexpected observation record')
            previous = row
    if header is None:
        raise DataError('missing observation header')
    reason = footer['reason'] if footer else 'interrupted'
    if reason not in ('duration_completed', 'handshake_timeout', 'transport_error', 'invalid_stream',
                       'clock_discontinuity', 'interrupted'):
        raise DataError('unknown observation terminal reason')
    elapsed = (footer['monotonic_ns']-header['monotonic_ns'])/NS if footer else None
    complete = bool(reason == 'duration_completed' and elapsed >= plan['duration_seconds']
                    and len(protocol.acks) == len(protocol.channels) and not protocol.expected_commands
                    and failure is None and parse_error is None and max_clock_error <= NS)
    if reason == 'duration_completed' and not complete:
        raise DataError('claimed full duration observation fails audit')
    report = {'schema': 1, 'experiment': plan['experiment'], 'status': reason, 'capture_complete': complete,
              'plan_sha256': t.digest(raw), 'options_sha256': t.digest(options_raw),
              'recording_sha256': t.digest(path.read_bytes()), 'source_sha256': header['source_sha256'],
              'audit_source_sha256': t.source_hashes(), 'records': count, 'logical_bytes': logical,
              'elapsed_seconds': elapsed, 'max_clock_error_ns': max_clock_error, 'parse_error': parse_error,
              'execution_ready': False, 'strategy_accepted': False, 'passive_fills_verified': False,
              'portfolio_return_available': False, 'matching_engine_completeness': 'unknown',
              'aggressor_semantics': 'unknown', 'capture_start_ns': header['observed_ns'],
              'capture_end_ns': footer['observed_ns'] if footer else None,
              **metrics.result(complete, protocol)}
    with output.open('x', encoding='utf-8', newline='\n') as target:
        target.write(dumps(report)+'\n')
    return report
