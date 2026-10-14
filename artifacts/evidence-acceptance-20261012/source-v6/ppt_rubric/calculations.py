"""Conservative checks of explicitly anchored calculations, never defect verdicts."""
from decimal import Decimal, localcontext, ROUND_HALF_UP
import re

NUMBER = r'-?\d{1,16}(?:\.\d{1,16})?'
UNITS = {
    '1': ('ratio', '1'), '%': ('ratio', '0.01'), '倍': ('ratio', '1'),
    '元': ('money', '1'), '万元': ('money', '10000'), '亿元': ('money', '100000000'),
    '人': ('people', '1'), '万人': ('people', '10000'),
    '次': ('count', '1'), '万次': ('count', '10000'),
    '月': ('time', '1'), '年': ('time', '12'),
}


def unit(value):
    if not isinstance(value, str) or value not in UNITS:
        raise ValueError('Unit unsupported or unspecified')
    dimension, scale = UNITS[value]
    return dimension, Decimal(scale)


def unverified(reason):
    return {'status': 'unverified', 'computed': None, 'matches_reported_precision': None,
            'reason': reason, 'limit': 'An unverified extraction is not a defect in the presentation.'}


def anchored_quantity(value, declared_unit, quote, evidence_text):
    """Require the displayed unit next to its value, rather than trusting a label."""
    units = '|'.join(re.escape(u) for u in sorted(UNITS, key=len, reverse=True) if u != '1')
    text = evidence_text.replace(',', '')
    spans = [(m.start(), m.end()) for m in re.finditer(re.escape(quote.replace(',', '')), text)]
    matches = re.finditer(r'(?<![\d.])(-?\d+(?:\.\d+)?)(?![\d.])(?:\s*(' + units + r'))?', text)
    # Match in the original context: quoting "28.1" out of "28.1%" must not
    # turn a percent into a dimensionless decimal, nor quote 90 out of 190.
    return any(Decimal(m[1]) == Decimal(value) and (m[2] or '1') == declared_unit
               and any(start <= m.start() and m.end() <= end for start,end in spans) for m in matches)


def arithmetic_check(spec, evidence_text=None):
    """Evaluate in normalized units, then round in the displayed result unit.

    Anchors prove only that a quote was supplied, not that the model chose the
    right relation. Even a verified mismatch still requires semantic judgment.
    Older untyped calculations remain diagnostic data, never automatic failures.
    """
    if not isinstance(spec, dict):
        return unverified('Invalid calculation record')
    try:
        for k in ('left', 'right', 'reported'):
            if not isinstance(spec.get(k), str) or not re.fullmatch(NUMBER, spec[k]):
                raise ValueError('Expected bounded decimal strings')
        dimensions, scales = {}, {}
        for k in ('left', 'right', 'reported'):
            dimensions[k], scales[k] = unit(spec.get(k + '_unit'))
        if not isinstance(spec.get('meaning'), str) or not spec['meaning'].strip():
            raise ValueError('Missing relation meaning')
        # Production callers always supply the observation and original sources.
        if evidence_text is None:
            raise ValueError('No supplied evidence to anchor the calculation')
        for k in ('left', 'right', 'reported', 'relation'):
            quote = spec.get(k + '_quote')
            if not isinstance(quote, str) or not quote.strip() or quote not in evidence_text:
                raise ValueError('Calculation anchor is not supplied evidence')
            if k != 'relation':
                values = re.findall(r'(?<![\d.])-?\d+(?:\.\d+)?(?![\d.])', quote.replace(',', ''))
                if Decimal(spec[k]) not in [Decimal(v) for v in values]:
                    raise ValueError('Operand/result value absent from its quote')
                if not anchored_quantity(spec[k], spec[k + '_unit'], quote, evidence_text):
                    raise ValueError('Operand/result unit is not explicit beside its value')
        op = spec.get('operation')
        dl, dr, dout = (dimensions[k] for k in ('left', 'right', 'reported'))
        with localcontext() as ctx:
            ctx.prec = 80
            a, b = (Decimal(spec[k]) * scales[k] for k in ('left', 'right'))
            if op in {'add', 'subtract'}:
                if not dl == dr == dout: raise ValueError('Addition/subtraction units differ')
                actual = a + b if op == 'add' else a - b
            elif op in {'divide', 'ratio_percent', 'growth_percent'}:
                if dl != dr or dout != 'ratio': raise ValueError('Ratio units are incompatible')
                if not b: raise ValueError('Zero denominator')
                actual = (a-b)/b if op == 'growth_percent' else a/b
                if op in {'ratio_percent', 'growth_percent'} and spec['reported_unit'] != '%':
                    raise ValueError('Percentage result must declare percent unit')
            elif op in {'multiply', 'percent_of'}:
                if dr == 'ratio' and dl == dout: actual = a*b
                elif dl == 'ratio' and dr == dout: actual = a*b
                else: raise ValueError('Multiplication units are unsupported')
                if op == 'percent_of' and spec['right_unit'] != '%':
                    raise ValueError('percent_of requires a percentage right operand')
            elif op == 'excess_over_baseline_percent':
                # This computes an excess AMOUNT, never an ordinary growth rate.
                if dl != dout or spec['right_unit'] != '%' or b < 0:
                    raise ValueError('Excess amount units are incompatible')
                if not re.search(r'(?:高|超出|超过|above|exceed).{0,30}%|高出百分之',
                                 spec['relation_quote'], re.I):
                    raise ValueError('Excess-amount relation not explicitly anchored')
                actual = a*b/(1+b)
            else:
                raise ValueError('Unsupported operation')
            displayed = actual / scales['reported']
            reported = Decimal(spec['reported'])
            rounded = displayed.quantize(Decimal(1).scaleb(reported.as_tuple().exponent), rounding=ROUND_HALF_UP)
            matches = rounded == reported
            if not matches and re.search(r'约|≈|~|about|approximately', spec['reported_quote'], re.I):
                # Approximate integer rounding (e.g. ~3000 vs 2997) does not
                # specify an exact tolerance. Do not manufacture a precision error.
                return unverified('Approximation tolerance not specified; inspect source precision')
            return {'status': 'computed', 'computed': str(displayed),
                    'computed_unit': spec['reported_unit'], 'matches_reported_precision': matches,
                    'limit': 'Checks the declared grounded relation and units, not its semantic applicability; never an automatic deduction.'}
    except (ValueError, ArithmeticError) as e:
        return unverified(str(e))
