"""Activity-aware quoted-width screen. No orders, passive fills or portfolio P&L."""

from collections import Counter
from decimal import localcontext
import gzip
from pathlib import Path
import shutil
from statistics import median
import time

from . import catalog, maker_spread as maker, triangular as t
from .domain import DataError, number
from .feeds import public_response
from .maker_observation import trade_payload
from .streams import EventWriter, dumps

NS = 1_000_000_000
STATS = 'https://apiv2.nobitex.ir/market/stats'
TRADES = 'https://apiv2.nobitex.ir/v2/trades/'


def load_plan(path, root=None):
    raw = Path(path).read_bytes()
    plan = t.strict_json(raw)
    root = root or Path(__file__).resolve().parents[1]
    ref = plan['quote_plan']
    if ref['repository_path'] != 'research/maker-spread-v1.json':
        raise DataError('unexpected quote reference path')
    quote_raw = (root/ref['repository_path']).read_bytes()
    if t.digest(quote_raw) != ref['sha256']:
        raise DataError('quote assumptions changed')
    quote = t.strict_json(quote_raw)
    if (plan.get('schema') != 1 or plan.get('experiment') != 'maker-activity-v1'
            or plan.get('venue') != 'nobitex'
            or plan.get('public_options_url') != catalog.ENDPOINTS['nobitex']
            or plan.get('public_stats_url') != STATS or plan.get('public_books_url') != t.ALL_BOOKS
            or plan.get('public_trades_prefix') != TRADES):
        raise DataError('activity screen scope differs from registration')
    fixed = dict(maximum_markets=20, book_samples=10, book_interval_seconds=30,
        recent_interval_seconds=2, recent_window_seconds=300,
        minimum_distinct_positive_recent_fingerprints=5, maximum_recent_rows=1000,
        max_logical_mb=64, minimum_free_disk_mb=512, maximum_clock_error_ns=NS)
    if any(type(plan.get(k)) is not int or plan[k] != v for k, v in fixed.items()):
        raise DataError('activity screen bounds changed')
    rule = plan['follow_up_rule']
    if (rule.get('complete_capture_required') is not True
            or type(rule.get('minimum_valid_quotes_in_each_cost')) is not int
            or rule['minimum_valid_quotes_in_each_cost'] != 10
            or number(rule['minimum_positive_fraction_in_both_costs']) != number('0.50')
            or rule.get('positive_median_stressed_width_required') is not True
            or type(rule.get('maximum_candidates')) is not int or rule['maximum_candidates'] != 3
            or rule.get('ranking') != 'descending recent distinct positive fingerprints, then descending median stressed width per spent USDT, then ascending native symbol'):
        raise DataError('activity screen follow-up rule changed')
    return raw, plan, quote


def select_markets(options, stats, plan):
    t.verify_response(options, plan['public_options_url'])
    t.verify_response(stats, STATS)
    records = stats['payload'].get('stats')
    if not isinstance(records, dict):
        raise DataError('invalid public stats envelope')
    eligible, excluded = [], []
    for market in catalog.parse_catalog('nobitex', options['payload']):
        if market['quote'] != 'USDT':
            continue
        try:
            if market['base_currency_active'] is not True:
                raise DataError('inactive_or_unknown_currency')
            if number(market['native_scale']) != 1:
                raise DataError('scaled_currency')
            if any(number(market[k]) <= 0 for k in ('price_tick', 'quantity_step', 'minimum_notional')):
                raise DataError('invalid_native_rules')
            row = records.get(market['base'].lower()+'-usdt')
            if not isinstance(row, dict):
                raise DataError('missing_market_stats')
            if row.get('isClosed') is not False:
                raise DataError('closed_or_unknown_market')
            volume = number(row.get('volumeDst'))
            if volume <= 0:
                raise DataError('nonpositive_reported_quote_volume')
            eligible.append({**market, 'reported_24h_quote_volume_usdt': volume,
                             'stats_source_time_known': False})
        except DataError as exc:
            excluded.append({'native_symbol': market['native_symbol'], 'reason': str(exc)})
    eligible.sort(key=lambda x: (x['reported_24h_quote_volume_usdt'].copy_negate(), x['native_symbol']))
    return {'selected': eligible[:plan['maximum_markets']],
            'eligible_below_rank_cutoff': [x['native_symbol'] for x in eligible[plan['maximum_markets']:]],
            'excluded': excluded, 'eligible_count': len(eligible)}


def request_spec(selection, plan):
    return [('book', i, '', t.ALL_BOOKS) for i in range(plan['book_samples'])]+[
        ('recent', i, market['native_symbol'], TRADES+market['native_symbol'])
        for i, market in enumerate(selection['selected'])]


def capture(plan_path, directory):
    raw, plan, _ = load_plan(plan_path)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    (directory/'plan.json').write_bytes(raw)
    writer = EventWriter(directory/'capture.jsonl.gz', plan['max_logical_mb']*1024*1024)
    reason, requests = 'interrupted', 0
    def write(event):
        needed = len(dumps(event).encode())+4096
        if shutil.disk_usage(directory).free < plan['minimum_free_disk_mb']*1024*1024+needed:
            raise DataError('disk reserve reached')
        row = writer.write('nobitex', event)
        if abs(row['clock_error_ns']) > plan['maximum_clock_error_ns']:
            raise DataError('local clock discontinuity')
        return row
    def fetch(operation, index, symbol, url):
        nonlocal requests
        start = time.monotonic_ns()
        record = public_response(url)
        finish = time.monotonic_ns()
        requests += 1
        write({'kind': 'response', 'operation': operation, 'index': index, 'native_symbol': symbol,
               'started_mono_ns': start, 'finished_mono_ns': finish, 'record': record})
        t.verify_response(record, url)
        print(dumps({'operation': operation, 'completed_requests': requests}), flush=True)
        return record
    try:
        write({'kind': 'screen_start', 'plan_sha256': t.digest(raw), 'source_sha256': t.source_hashes(),
               'clock_origin_wall_ns': writer.start_wall, 'clock_origin_mono_ns': writer.start_mono})
        options = fetch('options', 0, '', plan['public_options_url'])
        stats = fetch('stats', 0, '', STATS)
        selection = select_markets(options, stats, plan)
        write({'kind': 'selection', 'native_symbols': [x['native_symbol'] for x in selection['selected']]})
        if not selection['selected']:
            reason = 'no_eligible_markets'
        else:
            for operation, index, symbol, url in request_spec(selection, plan):
                if index:
                    time.sleep(plan['book_interval_seconds'] if operation == 'book' else plan['recent_interval_seconds'])
                fetch(operation, index, symbol, url)
            reason = 'completed'
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        reason = 'source_or_validation_failure'
        writer.write('nobitex', {'kind': 'failure', 'error_type': type(exc).__name__,
            'reason': str(exc) if isinstance(exc, DataError) else 'invalid source schema'})
    finally:
        try:
            writer.write('nobitex', {'kind': 'screen_end', 'reason': reason, 'requests': requests})
        finally:
            writer.close()
    return {'status': reason, 'requests': requests, 'execution_ready': False}


def read_journal(path, plan, plan_raw):
    path = Path(path)
    if path.stat().st_size > (plan['max_logical_mb']+1)*1024*1024:
        raise DataError('compressed screen exceeds bound')
    header, previous, terminal, size, seq = None, None, False, 0, 0
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        while line := stream.readline(t.MAX_LINE+1):
            size += len(line.encode()); seq += 1
            if len(line) > t.MAX_LINE or size > plan['max_logical_mb']*1024*1024:
                raise DataError('logical screen exceeds bound')
            row = t.strict_json(line)
            if (terminal or row.get('schema') != 2 or row.get('venue') != 'nobitex'
                    or type(row.get('seq')) is not int or row['seq'] != seq
                    or any(type(row.get(k)) is not int or row[k] <= 0 for k in ('observed_ns', 'monotonic_ns'))):
                raise DataError('invalid screen envelope or order')
            if header is None:
                header = row
                if (row.get('kind') != 'screen_start' or row.get('plan_sha256') != t.digest(plan_raw)
                        or not isinstance(row.get('run_id'), str) or not row['run_id']
                        or not isinstance(row.get('source_sha256'), dict)):
                    raise DataError('screen header provenance mismatch')
            if (row['run_id'] != header['run_id'] or previous and row['monotonic_ns'] < previous['monotonic_ns']):
                raise DataError('screen run or monotonic ordering mismatch')
            error = row['observed_ns']-header['clock_origin_wall_ns']-(row['monotonic_ns']-header['clock_origin_mono_ns'])
            if row.get('clock_error_ns') != error or abs(error) > plan['maximum_clock_error_ns']:
                raise DataError('screen clock discontinuity or diagnostic mismatch')
            if row['kind'] == 'response':
                start, finish = row['started_mono_ns'], row['finished_mono_ns']
                record = row['record']
                if (any(type(v) is not int for v in (start, finish))
                        or not previous['monotonic_ns'] <= start <= finish <= row['monotonic_ns']
                        or not previous['observed_ns'] <= record['started_ns'] <= record['received_ns'] <= row['observed_ns']):
                    raise DataError('invalid request timing or availability')
            terminal = row['kind'] == 'screen_end'
            previous = row
            yield row


def recent_activity(record, symbol, plan, available_ns):
    t.verify_response(record, TRADES+symbol)
    rows = record['payload'].get('trades')
    if not isinstance(rows, list) or len(rows) > plan['maximum_recent_rows']:
        raise DataError('invalid recent list')
    values = [trade_payload(row) for row in rows]
    times = [row['source_ns'] for row in values]
    if times != sorted(times, reverse=True) or any(x > record['received_ns'] for x in times):
        raise DataError('recent list unsorted or future timed')
    fingerprints = [(r['source_ns'], r['price'], r['volume'], r['raw_type']) for r in values
                    if r['volume'] > 0 and 0 <= available_ns-r['source_ns'] <= plan['recent_window_seconds']*NS]
    return {'status': 'observed', 'available_ns': available_ns, 'returned_rows': len(values),
        'positive_recent_occurrences': len(fingerprints), 'distinct_positive_recent_fingerprints': len(set(fingerprints)),
        'identical_recent_occurrences': len(fingerprints)-len(set(fingerprints)),
        'zero_volume_rows': sum(r['volume'] == 0 for r in values),
        'oldest_source_ns': min(times) if times else None, 'newest_source_ns': max(times) if times else None,
        'unique_trade_count': None, 'complete_trade_flow': False}


def follow_up(markets, plan, complete):
    if not complete:
        return []
    selected = []
    for row in markets:
        activity = row['recent_activity']
        costs = row['cost_scenarios'][1:]
        if (activity.get('status') != 'observed'
                or activity['distinct_positive_recent_fingerprints'] < plan['minimum_distinct_positive_recent_fingerprints']
                or any(s['valid'] < 10 for s in row['cost_scenarios'])
                or any(2*s['positive'] < s['valid'] for s in costs)
                or costs[-1]['median_conditional_width_usdt'] <= 0):
            continue
        selected.append({'native_symbol': row['native_symbol'],
            'recent_distinct_positive_fingerprints': activity['distinct_positive_recent_fingerprints'],
            'median_stressed_width_per_spent_usdt': costs[-1]['median_width_per_spent_usdt'],
            'strategy_accepted': False, 'purpose': 'separately_registered_prospective_observation'})
    selected.sort(key=lambda x: (-x['recent_distinct_positive_fingerprints'],
                  x['median_stressed_width_per_spent_usdt'].copy_negate(), x['native_symbol']))
    return selected[:3]


def audit(directory, output):
    directory, output = Path(directory), Path(output)
    raw, plan, quote = load_plan(directory/'plan.json')
    output.mkdir(parents=True, exist_ok=False)
    path = directory/'capture.jsonl.gz'
    report = {'schema': 1, 'experiment': plan['experiment'], 'status': 'incomplete', 'capture_complete': False,
        'plan_sha256': t.digest(raw), 'quote_plan_sha256': plan['quote_plan']['sha256'],
        'audit_source_sha256': t.source_hashes(), 'execution_ready': False, 'strategy_accepted': False,
        'portfolio_return_available': False, 'passive_fills_verified': False, 'selected_for_observation': []}
    expected = [('options', 0, '', plan['public_options_url']), ('stats', 0, '', STATS)]
    count, footer, failure, selection = 0, None, None, None
    source_records, values, exclusions, activities, last_response = {}, {}, {}, {}, {}
    try:
        with (output/'comparisons.jsonl').open('x', encoding='utf-8', newline='\n') as comparisons:
            for row in read_journal(path, plan, raw):
                kind = row['kind']
                if kind == 'screen_start' and row['seq'] == 1:
                    report.update(source_sha256=row['source_sha256'], capture_start_ns=row['observed_ns'])
                elif kind == 'selection':
                    if count != 2 or selection is not None or failure:
                        raise DataError('selection out of fixed order')
                    selection = select_markets(source_records['options'], source_records['stats'], plan)
                    if row['native_symbols'] != [x['native_symbol'] for x in selection['selected']]:
                        raise DataError('recorded selection differs from raw metadata ranking')
                    expected += request_spec(selection, plan) if selection['selected'] else []
                    for market in selection['selected']:
                        for scenario in maker.SCENARIOS:
                            key = market['native_symbol'], scenario
                            values[key], exclusions[key] = [], Counter()
                elif kind == 'response':
                    if failure or count >= len(expected) or count >= 2 and selection is None:
                        raise DataError('request outside registered sequence')
                    operation, index, symbol, url = expected[count]
                    if (row['operation'], row['index'], row['native_symbol']) != (operation, index, symbol):
                        raise DataError('request scope or order mismatch')
                    if index:
                        interval = plan['book_interval_seconds'] if operation == 'book' else plan['recent_interval_seconds']
                        if row['started_mono_ns']-last_response[operation]['finished_mono_ns'] < interval*NS:
                            raise DataError('request spacing shorter than registered interval')
                    last_response[operation] = row
                    count += 1
                    record = row['record']
                    if record.get('kind') != 'snapshot':
                        failure = 'public_request_failed'
                        continue
                    t.verify_response(record, url)
                    if operation in ('options', 'stats'):
                        source_records[operation] = record
                    elif operation == 'recent':
                        try:
                            activities[symbol] = recent_activity(record, symbol, plan, row['observed_ns'])
                        except (ValueError, KeyError, TypeError, IndexError) as exc:
                            activities[symbol] = {'status': 'unavailable', 'reason': str(exc) if isinstance(exc, DataError) else 'invalid_recent_schema'}
                    else:
                        for market in selection['selected']:
                            native, pair = market['native_symbol'], market['base']+'/USDT'
                            for scenario in quote['scenarios']:
                                key = native, scenario['name']
                                sample = {'sample': index, 'native_symbol': native, 'scenario': scenario['name']}
                                try:
                                    book = t.snapshot_book(record, pair)
                                    result = maker.quote_width(book, market, quote, scenario, row['observed_ns'])
                                    values[key].append(result)
                                    sample.update(status='conditional_width_only', **result)
                                except (ValueError, KeyError, TypeError, IndexError) as exc:
                                    reason = str(exc) if isinstance(exc, DataError) else 'missing_or_invalid_book'
                                    exclusions[key][reason] += 1
                                    sample.update(status='excluded', reason=reason)
                                comparisons.write(dumps(sample)+'\n')
                elif kind == 'failure':
                    failure = row.get('reason', 'capture_failure')
                elif kind == 'screen_end':
                    footer = row
                    if (type(row['requests']) is not int or row['requests'] != count
                            or row['reason'] not in ('completed', 'no_eligible_markets',
                                                      'source_or_validation_failure', 'interrupted')):
                        raise DataError('terminal request count mismatch')
                else:
                    raise DataError('unexpected screen record')
        complete = bool(footer and footer['reason'] in ('completed', 'no_eligible_markets')
                        and selection is not None and not failure and count == len(expected)
                        and (footer['reason'] == 'no_eligible_markets') == (not selection['selected']))
        if footer and footer['reason'] in ('completed', 'no_eligible_markets') and not complete:
            raise DataError('claimed complete screen fails audit')
        report.update(status=footer['reason'] if footer else 'interrupted', capture_complete=complete)
    except Exception as exc:
        report.update(error_type=type(exc).__name__, reason=str(exc) if isinstance(exc, DataError) else 'invalid_screen_artifact')
        raise
    finally:
        sha = t.digest(path.read_bytes()) if path.stat().st_size <= (plan['max_logical_mb']+1)*1024*1024 else None
        report.update(recording_sha256=sha, requests=count, capture_end=footer,
                      failure=failure, selection=selection, markets=[])
        with localcontext() as context:
            context.prec = 60
            for market in (selection or {}).get('selected', []):
                native, costs = market['native_symbol'], []
                for scenario in maker.SCENARIOS:
                    key = native, scenario
                    rows = values.get(key, [])
                    costs.append({'scenario': scenario, 'valid': len(rows),
                        'positive': sum(x['quoted_width_net_usdt'] > 0 for x in rows),
                        'median_conditional_width_usdt': median(x['quoted_width_net_usdt'] for x in rows) if rows else None,
                        'median_width_per_spent_usdt': median(x['quoted_width_per_spent_usdt'] for x in rows) if rows else None,
                        'exclusions': dict(exclusions.get(key, {}))})
                report['markets'].append({'native_symbol': native, 'cost_scenarios': costs,
                    'recent_activity': activities.get(native, {'status': 'missing'})})
        report['selected_for_observation'] = follow_up(report['markets'], plan, report['capture_complete'])
        (output/'report.json').write_text(dumps(report)+'\n', encoding='utf-8', newline='\n')
    return report
