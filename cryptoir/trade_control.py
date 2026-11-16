"""Bounded REST/WebSocket trade route control; raw native values, no fills/P&L."""

import asyncio
from collections import Counter
from pathlib import Path
import time

from . import catalog, maker_observation as m, triangular as t
from .domain import DataError, number
from .feeds import public_response
from .streams import EventWriter, URLS, dumps

SYMBOLS = ['BTCUSDT', 'USDTIRT']
REST_URLS = {s: 'https://apiv2.nobitex.ir/v2/trades/'+s for s in SYMBOLS}
NS = 1_000_000_000


def load_plan(path):
    raw = Path(path).read_bytes()
    plan = t.strict_json(raw)
    if (plan.get('schema') != 1 or plan.get('experiment') != 'trade-feed-control-v1'
            or plan.get('venue') != 'nobitex' or plan.get('native_symbols') != SYMBOLS
            or plan.get('channels') != ['public:trades-'+s for s in SYMBOLS]
            or plan.get('rest_urls') != REST_URLS
            or plan.get('websocket_url') != URLS['nobitex']
            or plan.get('public_options_url') != catalog.ENDPOINTS['nobitex']):
        raise DataError('trade control scope differs from registration')
    fixed = dict(duration_seconds=180, max_logical_mb=16, minimum_free_disk_mb=512,
        max_age_ms=5000, comparison_boundary_guard_seconds=5, maximum_metadata_age_seconds=60,
        maximum_clock_error_ns=NS, connect_timeout_seconds=10, subscription_timeout_seconds=10,
        reconnects=0, maximum_rest_rows=1000)
    if any(type(plan.get(k)) is not int or plan[k] != v for k, v in fixed.items()):
        raise DataError('trade control bounds differ from registration')
    return raw, plan


def verify_options(record, plan, now_ns):
    t.verify_response(record, plan['public_options_url'])
    if not 0 <= now_ns-record['received_ns'] <= plan['maximum_metadata_age_seconds']*NS:
        raise DataError('reference metadata stale or future')
    markets = {x['native_symbol']: x for x in catalog.parse_catalog('nobitex', record['payload'])}
    for symbol in SYMBOLS:
        market = markets.get(symbol)
        if (not market or market['base_currency_active'] is not True
                or number(market['native_scale']) != 1
                or any(number(market[k]) <= 0 for k in ('quantity_step', 'price_tick'))):
            raise DataError('reference native market unavailable or scaled')


def rest_phase(directory, phase):
    records = {symbol: public_response(REST_URLS[symbol]) for symbol in SYMBOLS}
    raw = dumps({'schema': 1, 'phase': phase, 'records': records}).encode()
    with (directory/('rest-'+phase+'.json')).open('xb') as target:
        target.write(raw)


def capture(plan_path, directory):
    raw, plan = load_plan(plan_path)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    (directory/'plan.json').write_bytes(raw)
    options = public_response(plan['public_options_url'])
    options_raw = dumps(options).encode()
    (directory/'current-options.json').write_bytes(options_raw)
    verify_options(options, plan, time.time_ns())
    rest_phase(directory, 'before')
    verify_options(options, plan, time.time_ns())
    writer = EventWriter(directory/'frames.jsonl.gz', plan['max_logical_mb']*1024*1024)
    try:
        writer.write('nobitex', {'kind': 'recording_start', 'plan_sha256': t.digest(raw),
            'options_sha256': t.digest(options_raw), 'source_sha256': t.source_hashes(),
            'clock_origin_wall_ns': writer.start_wall, 'clock_origin_mono_ns': writer.start_mono})
        reason = asyncio.run(m.capture_socket(plan, writer, protocol=m.Protocol([], trade_symbols=SYMBOLS)))
    finally:
        writer.close()
    rest_phase(directory, 'after')
    return {'status': reason, 'execution_ready': False, 'strategy_accepted': False}


def parse_rest(record, symbol, plan):
    t.verify_response(record, REST_URLS[symbol])
    rows = record['payload'].get('trades')
    if not isinstance(rows, list) or len(rows) > plan['maximum_rest_rows']:
        raise DataError('invalid or excessive recent-trades list')
    result = [m.trade_payload(row) for row in rows]
    times = [row['source_ns'] for row in result]
    if times != sorted(times, reverse=True) or any(x > record['received_ns'] for x in times):
        raise DataError('recent trades unsorted or future timed')
    return result


def audit_phase(directory, phase, plan, capture_report, options):
    path = directory/('rest-'+phase+'.json')
    if not path.exists():
        return {'status': 'missing', 'markets': {s: {'status': 'missing'} for s in SYMBOLS}}, {}
    if path.stat().st_size > 24*1024*1024:
        raise DataError('REST control artifact exceeds bound')
    raw = path.read_bytes()
    data = t.strict_json(raw)
    if data.get('schema') != 1 or data.get('phase') != phase or set(data.get('records', {})) != set(SYMBOLS):
        raise DataError('REST control phase identity mismatch')
    summary, parsed = {}, {}
    previous = options['received_ns'] if phase == 'before' else capture_report['capture_end_ns']
    for symbol in SYMBOLS:
        record = data['records'][symbol]
        try:
            start, end = record['started_ns'], record['received_ns']
            if (any(type(v) is not int for v in (start, end)) or previous is None or start < previous
                    or end < start or phase == 'before' and end > capture_report['capture_start_ns']):
                raise DataError('REST phase timing outside recorded boundary')
            previous = end
            values = parse_rest(record, symbol, plan)
            parsed[symbol] = values
            summary[symbol] = {'status': 'observed', 'rows': len(values),
                'positive_volume_rows': sum(row['volume'] > 0 for row in values),
                'oldest_source_ns': min((r['source_ns'] for r in values), default=None),
                'newest_source_ns': max((r['source_ns'] for r in values), default=None),
                'newest_age_ns_at_receipt': end-max(r['source_ns'] for r in values) if values else None,
                'started_ns': start, 'received_ns': end, 'body_sha256': record['body_sha256']}
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            summary[symbol] = {'status': 'unavailable', 'reason': str(exc) if isinstance(exc, DataError)
                              else 'invalid response schema', 'source_kind': record.get('kind'),
                              'status_code': record.get('status_code')}
    return {'status': 'observed' if len(parsed) == len(SYMBOLS) else 'partial',
            'sha256': t.digest(raw), 'markets': summary}, parsed


def fingerprint(row):
    return row['source_ns'], row['price'], row['volume'], row['raw_type']


def compare(symbol, ws_rows, rest_rows, protocol, report, plan):
    channel = 'public:trades-'+symbol
    acknowledged = protocol.acknowledged_ns.get(channel)
    fresh = [row for row in ws_rows if row['volume'] > 0
             and 0 <= row['available_ns']-row['source_ns'] <= plan['max_age_ms']*1_000_000
             and 0 <= row['available_ns']-row['received_ns'] <= plan['max_age_ms']*1_000_000]
    start = acknowledged+plan['comparison_boundary_guard_seconds']*NS if acknowledged else None
    end = report['capture_end_ns']
    end = end-plan['comparison_boundary_guard_seconds']*NS if end is not None else None
    eligible_interval = start is not None and end is not None and start < end
    def inside(row):
        return eligible_interval and start <= row['source_ns'] <= end and row['volume'] > 0
    ws = Counter(fingerprint(row) for row in ws_rows if inside(row))
    rest = Counter(fingerprint(row) for row in (rest_rows or []) if inside(row))
    matched, missing_ws, missing_rest = sum((ws & rest).values()), sum((rest-ws).values()), sum((ws-rest).values())
    if not report['capture_complete']:
        status = 'incomplete_capture'
    elif rest_rows is None:
        status = 'rest_reference_unavailable'
    elif missing_ws:
        status = 'cross_source_discrepancy'
    elif fresh:
        status = 'fresh_ws_payload_observed'
    elif ws_rows:
        status = 'no_fresh_positive_ws_payload'
    else:
        status = 'inconclusive_quiet_or_uncovered_window'
    times = [row['source_ns'] for row in (rest_rows or [])]
    ages = [row['available_ns']-row['source_ns'] for row in ws_rows]
    return {'native_symbol': symbol, 'status': status, 'subscription_accepted': channel in protocol.acks,
        'acknowledged_available_ns': acknowledged, 'ws_publications': len(ws_rows),
        'ws_positive_volume_publications': sum(r['volume'] > 0 for r in ws_rows),
        'fresh_positive_ws_publications': len(fresh), 'fresh_trade_route_observed': bool(fresh),
        'ws_with_offset': sum(r['offset'] is not None for r in ws_rows),
        'min_ws_source_age_ns': min(ages) if ages else None, 'max_ws_source_age_ns': max(ages) if ages else None,
        'guarded_start_ns': start, 'guarded_end_ns': end, 'comparison_interval_available': eligible_interval,
        'guarded_ws_occurrences': sum(ws.values()), 'guarded_post_rest_occurrences': sum(rest.values()),
        'matched_fingerprint_occurrences': matched, 'rest_occurrences_without_matching_ws': missing_ws,
        'ws_occurrences_without_matching_post_rest': missing_rest,
        'post_rest_timestamp_span_covers_guarded_interval': bool(eligible_interval and times
                                                               and min(times) <= start and max(times) >= end),
        'recent_list_coverage_complete': None, 'unique_exchange_trade_count': None,
        'raw_type_counts': dict(Counter(r['raw_type'] for r in ws_rows)),
        'verified_fiat_conversion': False, 'matching_engine_completeness': 'unknown',
        'strategy_accepted': False, 'execution_ready': False}


def audit(directory, output):
    directory, output = Path(directory), Path(output)
    raw, plan = load_plan(directory/'plan.json')
    options_raw = (directory/'current-options.json').read_bytes()
    options = t.strict_json(options_raw)
    protocol = m.Protocol([], trade_symbols=SYMBOLS)
    events = {s: [] for s in SYMBOLS}
    def observe(event, available_ns):
        events[event['native_symbol']].append({**event, 'available_ns': available_ns})
    report = m.replay_session(directory/'frames.jsonl.gz', raw, options_raw, plan, protocol,
                             observe, lambda current, now: verify_options(current, plan, now))
    before, _ = audit_phase(directory, 'before', plan, report, options)
    after, post = audit_phase(directory, 'after', plan, report, options)
    report.update(rest_before=before, rest_after=after,
        control_complete=report['capture_complete'] and before['status'] == after['status'] == 'observed',
        markets=[compare(s, events[s], post.get(s), protocol, report, plan) for s in SYMBOLS],
        limitation='Fingerprint occurrence comparison only; finite recent list, no stable trade IDs, '
                   'aggressor inference, unit conversion, fills, P&L or venue-completeness claim.')
    with output.open('x', encoding='utf-8', newline='\n') as target:
        target.write(dumps(report)+'\n')
    return report
