"""Quoted maker-width feasibility only. No passive fills, orders or portfolio P&L."""

from collections import Counter
from decimal import localcontext
import gzip
from pathlib import Path
import re
from statistics import median

from .backtest import floor_quantity
from .domain import DataError, number
from .streams import dumps
from . import triangular as t

SCENARIOS = ('zero_cost_diagnostic', 'assumed_base', 'assumed_stress')


def validate_plan(plan, universe):
    t.validate_plan(universe)
    if (plan.get('schema') != 1 or plan.get('venue') != 'nobitex'
            or plan.get('evaluation_type') != 'exploratory_existing_recording'
            or universe.get('universe') != t.CATALOG_UNIVERSE):
        raise DataError('unsupported maker-width provenance or universe')
    for key in ('catalog_sha256', 'replay_recording', 'samples', 'interval_seconds', 'max_age_ms', 'max_logical_mb'):
        if plan[key] != universe[key]:
            raise DataError('maker-width capture scope differs from pinned universe')
    if not re.fullmatch('[a-f0-9]{64}', plan['universe_plan']['sha256']):
        raise DataError('invalid universe plan hash')
    if number(plan['quote_budget_usdt']) <= 0 or not 0 < number(plan['maximum_fraction_of_displayed_top_level']) <= 1:
        raise DataError('invalid hypothetical quote footprint')
    scenarios = plan['scenarios']
    if [s['name'] for s in scenarios] != list(SCENARIOS):
        raise DataError('three fixed cost scenarios required')
    for s in scenarios:
        if not 0 <= number(s['maker_fee']) < 1 or not 0 <= number(s['reserve_bps_per_leg']) < 10000:
            raise DataError('invalid maker cost')
    for key in ('maker_fee', 'reserve_bps_per_leg'):
        if number(scenarios[0][key]) != 0 or number(scenarios[2][key]) < number(scenarios[1][key]):
            raise DataError('invalid zero/stress cost ordering')
    rule = plan['follow_up_rule']
    if (type(rule['minimum_valid_samples_per_cost']) is not int
            or not 1 <= rule['minimum_valid_samples_per_cost'] <= plan['samples']
            or not 0 < number(rule['minimum_positive_fraction_in_both_costs']) <= 1
            or rule['strictly_positive_median_stressed_margin_required'] is not True
            or type(rule['maximum_candidates']) is not int or not 1 <= rule['maximum_candidates'] <= 3
            or rule['ranking'] != 'descending median of per-sample stressed_margin_usdt / spent_usdt, then ascending asset'):
        raise DataError('invalid fixed observation follow-up rule')


def quote_width(book, rules, plan, scenario, now_ns):
    if book.venue != 'nobitex' or not book.pair.endswith('/USDT') or book.exchange_ns is None:
        raise DataError('invalid_book_identity_or_unknown_time')
    book.validate_age(now_ns, plan['max_age_ms']*1_000_000)
    with localcontext() as context:
        context.prec = 60
        tick, step, minimum = (number(rules[k]) for k in ('price_tick', 'quantity_step', 'minimum_notional'))
        if min(tick, step, minimum) <= 0:
            raise DataError('invalid_market_rules')
        if any(level.price % tick for level in book.bids+book.asks):
            raise DataError('book_price_tick_mismatch')
        bid, ask = book.bids[0], book.asks[0]
        budget = number(plan['quote_budget_usdt'])
        fraction = number(plan['maximum_fraction_of_displayed_top_level'])
        fee, reserve_rate = number(scenario['maker_fee']), number(scenario['reserve_bps_per_leg'])/10000
        if budget <= 0 or not 0 < fraction <= 1 or not 0 <= fee < 1 or not 0 <= reserve_rate < 1:
            raise DataError('invalid_quote_or_cost_assumptions')
        quantity = floor_quantity(min(budget/bid.price, bid.quantity*fraction, ask.quantity*fraction), step)
        spent = quantity*bid.price
        if quantity <= 0 or spent < minimum:
            raise DataError('buy_below_minimum_or_quote_footprint')
        base_fee = quantity*fee
        sell_quantity = floor_quantity(quantity-base_fee, step)
        proceeds = sell_quantity*ask.price
        if sell_quantity <= 0 or proceeds < minimum:
            raise DataError('sell_below_minimum_after_received_fee')
        sell_fee = proceeds*fee
        reserve = (spent+proceeds)*reserve_rate
        width = proceeds-sell_fee-spent-reserve
        if spent > budget or sell_quantity > quantity-base_fee:
            raise AssertionError('maker hypothetical cash/base conservation')
        return {'observed_ns': now_ns, 'bid': bid.price, 'ask': ask.price,
                'displayed_bid_quantity': bid.quantity, 'displayed_ask_quantity': ask.quantity,
                'gross_buy_quantity': quantity, 'buy_fee_base': base_fee,
                'sell_quantity': sell_quantity, 'residual_base_zero_valuation': quantity-base_fee-sell_quantity,
                'spent_usdt': spent, 'unspent_usdt': budget-spent, 'gross_sell_usdt': proceeds,
                'sell_fee_usdt': sell_fee, 'hypothetical_cost_reserve_usdt': reserve,
                'quoted_width_net_usdt': width, 'quoted_width_per_spent_usdt': width/spent,
                'passive_fills_verified': False, 'execution_ready': False}


def select_follow_up(summaries, plan):
    grouped = {(s['asset'], s['scenario']):s for s in summaries}
    rule, eligible = plan['follow_up_rule'], []
    for asset in sorted({s['asset'] for s in summaries}):
        costs = [grouped[(asset, name)] for name in SCENARIOS[1:]]
        if any(s['valid_samples'] < rule['minimum_valid_samples_per_cost']
               or s['positive_samples'] < number(rule['minimum_positive_fraction_in_both_costs'])*s['valid_samples']
               for s in costs):
            continue
        stress = costs[-1]
        if stress['median_net_width_usdt'] <= 0:
            continue
        eligible.append({'asset': asset, 'median_stressed_width_per_spent_usdt': stress['median_width_per_spent_usdt'],
                         'purpose': 'fresh_public_observation_only', 'strategy_accepted': False})
    eligible.sort(key=lambda s: (s['median_stressed_width_per_spent_usdt'].copy_negate(), s['asset']))
    return eligible[:rule['maximum_candidates']]


def run_screen(plan_path, universe_path, catalog_path, recording, output):
    plan_raw, universe_raw = Path(plan_path).read_bytes(), Path(universe_path).read_bytes()
    plan, universe = t.strict_json(plan_raw), t.strict_json(universe_raw)
    if t.digest(universe_raw) != plan['universe_plan']['sha256']:
        raise DataError('universe plan differs from registered hash')
    validate_plan(plan, universe)
    t.verify_catalog(Path(catalog_path), universe)
    output, recording = Path(output), Path(recording)
    output.mkdir(parents=True, exist_ok=False)
    (output/'plan.json').write_bytes(plan_raw)
    report = {'schema': 1, 'experiment': plan['experiment'], 'status': 'incomplete_observation',
              'plan_sha256': t.digest(plan_raw), 'universe_plan_sha256': t.digest(universe_raw),
              'catalog_sha256': plan['catalog_sha256'], 'source_sha256': t.source_hashes(),
              'execution_ready': False, 'strategy_accepted': False, 'passive_fills_verified': False,
              'portfolio_return_available': False, 'selected_for_observation': [], 'capture_complete': False}
    counts, values, rejects, footer = Counter(), {}, {}, None
    for asset in universe['assets']:
        for scenario in SCENARIOS:
            values[(asset, scenario)], rejects[(asset, scenario)] = [], Counter()
    try:
        if recording.stat().st_size > (plan['max_logical_mb']+1)*1024*1024:
            raise DataError('compressed recording exceeds bound')
        report['recording_sha256'] = t.digest(recording.read_bytes())
        if report['recording_sha256'] != plan['replay_recording']['sha256']:
            raise DataError('recording differs from registered hash')
        total, previous_received = 0, 0
        with gzip.open(recording, 'rt', encoding='utf-8') as stream, (output/'samples.jsonl').open('x', encoding='utf-8', newline='\n') as samples:
            def read_line():
                nonlocal total
                line = stream.readline(t.MAX_LINE+1)
                total += len(line.encode())
                if len(line) > t.MAX_LINE or total > (plan['max_logical_mb']+1)*1024*1024:
                    raise DataError('logical recording exceeds bound')
                return line
            header = t.strict_json(read_line())
            if (header.get('schema') != 1 or header.get('kind') != 'triangular_capture_start'
                    or header.get('plan_sha256') != plan['replay_recording']['capture_plan_sha256']
                    or type(header.get('observed_ns')) is not int or header['observed_ns'] <= 0):
                raise DataError('capture header differs from registered source')
            report['capture'] = header
            while line := read_line():
                row = t.strict_json(line)
                if footer is not None:
                    raise DataError('records after capture end')
                if row.get('kind') == 'triangular_capture_end':
                    footer = row
                    if type(row['samples']) is not int or row['samples'] != counts['samples']:
                        raise DataError('capture count mismatch')
                    continue
                if (row.get('kind') != 'sample' or type(row.get('index')) is not int
                        or row['index'] != counts['samples'] or counts['samples'] >= plan['samples']):
                    raise DataError('invalid capture sample order')
                record = row['record']; counts['samples'] += 1
                if (type(record['started_ns']) is not int or type(record['received_ns']) is not int
                        or record['started_ns'] < header['observed_ns']
                        or record['received_ns'] < record['started_ns']):
                    raise DataError('invalid sample time')
                if previous_received and record['started_ns']-previous_received < plan['interval_seconds']*1_000_000_000:
                    raise DataError('capture spacing differs from registered source')
                previous_received = record['received_ns']
                if record.get('kind') != 'snapshot':
                    counts['source_errors'] += 1
                    continue
                t.verify_response(record, t.ALL_BOOKS)
                for asset in universe['assets']:
                    pair = asset+'/USDT'
                    try:
                        book = t.snapshot_book(record, pair)
                        error = None
                    except (ValueError, KeyError, TypeError, IndexError):
                        error = 'missing_or_invalid_book'
                    for scenario in plan['scenarios']:
                        key = (asset, scenario['name'])
                        sample = {'sample': row['index'], 'asset': asset, 'scenario': scenario['name']}
                        try:
                            if error:
                                raise DataError(error)
                            result = quote_width(book, universe['market_rules'][pair], plan, scenario, record['received_ns'])
                            values[key].append(result)
                            sample.update(status='conditional_width_only', **result)
                        except DataError as exc:
                            reason = str(exc); rejects[key][reason] += 1
                            sample.update(status='excluded', reason=reason)
                        samples.write(dumps(sample)+'\n')
        report['capture_complete'] = bool(footer and footer.get('stop_reason') == 'sample_limit'
            and counts['samples'] == plan['samples'] and not counts['source_errors'])
        if report['capture_complete']:
            report['status'] = 'completed_quote_width_screen'
    except Exception as exc:
        report['error_type'] = type(exc).__name__
        if isinstance(exc, DataError):
            report['reason'] = str(exc)
        raise
    finally:
        summaries = []
        for (asset, scenario), rows in values.items():
            with localcontext() as context:
                context.prec = 60
                summaries.append({'asset': asset, 'scenario': scenario, 'valid_samples': len(rows),
                    'positive_samples': sum(r['quoted_width_net_usdt'] > 0 for r in rows),
                    'rejections': dict(rejects[(asset, scenario)]),
                    'median_net_width_usdt': median([r['quoted_width_net_usdt'] for r in rows]) if rows else None,
                    'median_width_per_spent_usdt': median([r['quoted_width_per_spent_usdt'] for r in rows]) if rows else None})
        report.update(counts=dict(counts), capture_end=footer, markets_and_scenarios=summaries,
                      universe_coverage_complete='error_type' not in report and all(s['valid_samples'] for s in summaries))
        if report['capture_complete']:
            report['selected_for_observation'] = select_follow_up(summaries, plan)
        (output/'report.json').write_text(dumps(report), encoding='utf-8', newline='\n')
    return report
