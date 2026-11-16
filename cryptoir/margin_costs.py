"""Offline, fixed-quote fee sensitivity. Neither a position model nor a P&L model."""

from decimal import Decimal, localcontext
import hashlib
from pathlib import Path

from .domain import DataError
from .products import encoded, identifier, numeric, strict_json, verified_quote


def parse_marks(items):
    """Explicit collateral units per native unit; never infer units from a symbol."""
    marks = {}
    for item in items:
        if not isinstance(item, str) or item.count('=') != 1:
            raise DataError('mark must be CURRENCY=collateral_units_per_unit')
        currency, value = item.split('=')
        identifier(currency)
        if currency in marks:
            raise DataError('duplicate mark currency')
        marks[currency] = numeric(value, positive=True)
    return marks


def fee_scenario(raw: bytes, renewals: int, marks: dict):
    record, quote = verified_quote(raw)
    if type(renewals) is not int or not 0 <= renewals <= 1_000_000:
        raise DataError('renewals must be a bounded nonnegative integer')
    nominal_time = renewals * quote['extend_time_seconds']
    if nominal_time >= quote['max_age_seconds']:
        raise DataError('renewal scenario reaches or exceeds quoted expiry')
    amounts = quote['amounts']
    collateral = amounts['collateral']
    collateral_currency = collateral['native_currency']
    fee_names = ('trade_fee', 'open_fee', 'extend_fee')
    needed = {amounts[name]['native_currency'] for name in fee_names} | {collateral_currency}
    if not isinstance(marks, dict) or set(marks) != needed:
        raise DataError('marks must cover exactly the fee and collateral currencies')
    # Floats could arrive from callers other than the CLI; require exact inputs.
    if any(isinstance(v, (float, bool)) for v in marks.values()):
        raise DataError('marks require exact decimal inputs')
    marks = {identifier(k): numeric(v, positive=True) for k, v in marks.items()}
    if marks[collateral_currency] != 1:
        raise DataError('collateral currency mark must equal one')
    components, totals = {}, {}
    # At most 28 coefficient digits / +/-28 exponent per input and seven-digit
    # renewal multiplier. This also preserves exact sums across those scales.
    with localcontext() as context:
        context.prec = 200
        for name in fee_names:
            amount = amounts[name]
            currency = amount['native_currency']
            count = renewals if name == 'extend_fee' else 1
            value = amount['value'] * count
            components[name] = {
                'native_currency': currency, 'quoted_unit_amount': amount['value'],
                'count': count, 'native_total': value,
                'marked_collateral_value': value * marks[currency],
            }
            totals[currency] = totals.get(currency, Decimal(0)) + value
        marked_total = sum((c['marked_collateral_value'] for c in components.values()), Decimal(0))
        percent = marked_total / collateral['value'] * 100
    return {
        'schema': 1, 'kind': 'margin_fee_sensitivity', 'venue': record['venue'],
        'status': 'conditional_cost_only',
        'source_quote_sha256': hashlib.sha256(raw).hexdigest(),
        'source_body_sha256': record['response']['body_sha256'],
        'source_received_ns': record['response']['received_ns'],
        'market': quote['market'], 'side': quote['side'],
        'native_credit_ratio': quote['native_credit_ratio'],
        'collateral': collateral, 'renewals': renewals,
        'quoted_renewal_interval_seconds': quote['extend_time_seconds'],
        'nominal_renewal_time_seconds': nominal_time,
        'quoted_max_age_seconds': quote['max_age_seconds'],
        'marks_collateral_units_per_native_unit': marks,
        'components': components, 'native_fee_totals': dict(sorted(totals.items())),
        'marked_known_fees_in_collateral': marked_total,
        'marked_known_fees_percent_of_collateral': percent,
        'assumptions': {
            'quoted_fee_amounts_held_constant': True,
            'renewal_count_explicit_not_inferred_from_holding_time': True,
            'all_fees_marked_at_supplied_scenario_rates': True,
            'marks_are_hypothetical_not_execution_prices': True,
        },
        'exit_fee': None, 'other_unquoted_costs': None, 'net_pnl': None,
        'settlement_conversion_rule_verified': False,
        'survival_until_renewal_verified': False,
        'historical_cost_model_verified': False,
        'liquidation_model_verified': False,
        'quote_is_position': False, 'fees_applied_to_paper': False,
        'execution_ready': False,
    }


def write_scenario(quote_path: Path, output: Path, renewals: int, marks: dict):
    raw = Path(quote_path).read_bytes()
    report = fee_scenario(raw, renewals, marks)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as stream:
        stream.write(encoded(report) + '\n')
    # Scenario amounts and source paths remain in the local artifact.
    return {'kind': report['kind'], 'status': report['status'],
            'source_quote_sha256': report['source_quote_sha256'],
            'output': str(output), 'execution_ready': False}


def audit_scenario(quote_path: Path, scenario_path: Path):
    raw = Path(quote_path).read_bytes()
    scenario_raw = Path(scenario_path).read_bytes()
    report = strict_json(scenario_raw)
    expected = fee_scenario(raw, report['renewals'], report['marks_collateral_units_per_native_unit'])
    if encoded(report) != encoded(expected):
        raise DataError('fee scenario does not reconstruct from quote and assumptions')
    return {'kind': 'margin_fee_sensitivity_audit', 'status': 'reconstructed',
            'sha256': hashlib.sha256(scenario_raw).hexdigest(),
            'source_quote_sha256': expected['source_quote_sha256'],
            'execution_ready': False}
