"""Offline scoring-policy comparison. This module never changes production scores.

The alternative policy and impact anchors are research hypotheses, not validated
weights. Human labels are neither accepted nor read. Binary replay changes only
weights; graded experiments require separately supplied, anchored diagnostics.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path

from .common import digest, file_digest, read_json
from .rubric import DIMENSIONS, aggregate


POLICY = {
    'id': 'experimental-stable-reasoning-v1', 'experimental_only': True,
    'dimension_budgets': {'fulfillment': 30, 'correctness': 25, 'integrity': 15,
                          'narrative': 15, 'visual': 15},
    'correctness_groups': {'source_facts': 15, 'general_reasoning': 10},
    'general_reasoning_item': 'C_REASON',
    'unused_group_policy': 'explicit_vacuous_credit',
    'tie_margin': 3,
    'loss_fraction': {'full': 0, 'local_material': .5, 'key_unusable': 1,
                      'unknown': None},
    'anchors': {
        'full': 'No confirmed material loss of this scoring unit for the intended task.',
        'local_material': 'A specifically identified part is materially defective; '
                          'the core task remains usable, supported by an impact explanation.',
        'key_unusable': 'A specifically identified required result or decision is unusable; '
                        'the impact explanation establishes why the core goal is blocked.',
        'unknown': 'Evidence is insufficient or judgments conflict; retain bounds, never half credit.',
    },
    'limitations': ['10/15 correctness split and .5 local loss require independent human anchors.',
                    'Exact harm identities and equivalent-unit aliases require semantic verification.',
                    'Unused groups receive explicitly reported vacuous credit, without redistribution.',
                    'Counts of pages, diagnostic records, or repeated harms never increase deductions.'],
}


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def compile_units(rubric, aliases=None):
    """Compile explicit equivalent-item aliases without creating additional value.

    Aliases are a declared semantic premise, never inferred from shared sources.
    Equivalent items must have the same dimension, subgroup, and importance.
    """
    aliases = {} if aliases is None else aliases
    if not isinstance(aliases, dict):
        raise ValueError('Equivalent unit aliases must be an object')
    items = rubric.get('items', [])
    ids = [item.get('id') for item in items]
    if not items or any(not _nonempty(i) for i in ids) or len(set(ids)) != len(ids):
        raise ValueError('Unique nonempty rubric item ids required')
    if set(aliases) - set(ids) or any(not _nonempty(v) for v in aliases.values()):
        raise ValueError('Aliases must map existing items to nonempty scoring unit ids')
    units, item_to_unit = {}, {}
    reasoning_unit = aliases.get(POLICY['general_reasoning_item'], POLICY['general_reasoning_item'])
    for item in items:
        rid, dimension, importance = item['id'], item.get('dimension'), item.get('importance')
        if dimension not in DIMENSIONS or type(importance) is not int or importance not in {1, 2, 3}:
            raise ValueError('Invalid scoring dimension or importance')
        uid = aliases.get(rid, rid)
        group = ('general_reasoning' if uid == reasoning_unit else 'source_facts'
                 ) if dimension == 'correctness' else dimension
        unit = units.setdefault(uid, {'id': uid, 'group': group, 'dimension': dimension,
                                     'importance': importance, 'item_ids': []})
        if (unit['group'], unit['dimension'], unit['importance']) != (group, dimension, importance):
            raise ValueError('Equivalent aliases must preserve group, dimension, and importance')
        unit['item_ids'].append(rid)
        item_to_unit[rid] = uid
    budgets = {d: n for d, n in POLICY['dimension_budgets'].items() if d != 'correctness'}
    budgets.update(POLICY['correctness_groups'])
    unused = {}
    for group, budget in budgets.items():
        members = [unit for unit in units.values() if unit['group'] == group]
        total = sum(unit['importance'] for unit in members)
        if not total:
            unused[group] = budget
        for unit in members:
            unit['score'] = budget * unit['importance'] / total
    return units, item_to_unit, unused


def binary_diagnostics(verdicts, item_to_unit):
    seen, result = set(), []
    for verdict in verdicts:
        rid, answer = verdict.get('rubric_id'), verdict.get('answer')
        if rid not in item_to_unit or rid in seen or answer not in {'yes', 'no', 'unknown'}:
            raise ValueError('Verdicts require unique existing ids and yes/no/unknown answers')
        seen.add(rid)
        result.append({'rubric_id': rid,
                       'severity': {'yes': 'full', 'no': 'key_unusable', 'unknown': 'unknown'}[answer],
                       'anchor_ref': 'binary replay: whole item, no graded impact inferred',
                       'harm_id': 'binary:' + item_to_unit[rid],
                       'owner_unit_id': item_to_unit[rid]})
    return result


def score_experiment(rubric, diagnostics, aliases=None):
    """Maximum anchored impact per unit, with exact declared harm ownership.

    Duplicate records or diagnostic splits do not increase loss. Missing items
    remain unknown; an identified harm can be deducted only from its owner unit.
    This enforces declared identity, not semantic deduplication by the program.
    """
    units, item_to_unit, unused = compile_units(rubric, aliases)
    if not isinstance(diagnostics, list) or any(not isinstance(row, dict) for row in diagnostics):
        raise ValueError('Diagnostics must be a list of objects')
    records, owners, occurrences = {uid: [] for uid in units}, {}, {}
    seen_items = set()
    for row in diagnostics:
        rid, severity = row.get('rubric_id'), row.get('severity')
        if rid not in item_to_unit or severity not in POLICY['loss_fraction'] or not _nonempty(row.get('anchor_ref')):
            raise ValueError('Each diagnosis requires an existing item, finite severity, and anchor reference')
        uid = item_to_unit[rid]
        seen_items.add(rid)
        if severity in {'local_material', 'key_unusable'}:
            harm, owner = row.get('harm_id'), row.get('owner_unit_id')
            if not _nonempty(harm) or owner not in units:
                raise ValueError('Material diagnoses require a harm identity and existing owner unit')
            if harm in owners and owners[harm] != owner:
                raise ValueError('The same harm has conflicting scoring owners')
            owners[harm] = owner
            occurrences.setdefault(harm, set()).add(uid)
        else:
            owner = uid
        records[uid].append(row if owner == uid else {'severity': 'full'})
    if any(owner not in occurrences[harm] for harm, owner in owners.items()):
        raise ValueError('A harm owner must have its own diagnosis; cannot discard harm by naming another unit')
    dimensions = {d: {'earned': 0., 'possible': float(b), 'unresolved': 0.}
                  for d, b in POLICY['dimension_budgets'].items()}
    for group, budget in unused.items():
        dimension = 'correctness' if group in POLICY['correctness_groups'] else group
        dimensions[dimension]['earned'] += budget
    for uid, unit in units.items():
        missing = set(unit['item_ids']) - seen_items
        losses = [POLICY['loss_fraction'][row['severity']] for row in records[uid]]
        known = max((loss for loss in losses if loss is not None), default=0)
        uncertain = not records[uid] or any(loss is None for loss in losses)
        maximum = 1 if uncertain else known
        lower, upper = unit['score'] * (1 - maximum), unit['score'] * (1 - known)
        unit.update(score_lower=lower, score_upper=upper, missing_item_ids=sorted(missing),
                    diagnostic_count=len(records[uid]))
        dimensions[unit['dimension']]['earned'] += lower
        dimensions[unit['dimension']]['unresolved'] += upper - lower
    low = sum(row['earned'] for row in dimensions.values())
    unknown = sum(row['unresolved'] for row in dimensions.values())
    return {'status': 'incomplete' if unknown > 1e-9 else 'complete',
            'score': None if unknown > 1e-9 else low,
            'score_lower': low, 'score_upper': low + unknown, 'unresolved_weight': unknown,
            'dimensions': dimensions, 'units': list(units.values()),
            'unused_group_credit': unused, 'harm_owners': owners}


def compare_fixed_answers(rubric, verdicts, aliases=None, diagnostics=None):
    _, item_to_unit, _ = compile_units(rubric, aliases)
    converted = binary_diagnostics(verdicts, item_to_unit)
    baseline = aggregate(rubric, verdicts)
    binary = score_experiment(rubric, converted, aliases)
    reasoning = [item for item in rubric['items'] if item['id'] == 'C_REASON']
    return {'policy': copy.deepcopy(POLICY), 'policy_hash': digest(POLICY),
            'production_policy_changed': False, 'baseline': baseline,
            'binary_fixed_answers': binary,
            'graded_impact': score_experiment(rubric, diagnostics, aliases) if diagnostics is not None else None,
            'legacy_general_reasoning_weight': sum(item['score'] for item in reasoning),
            'experimental_general_reasoning_budget': POLICY['correctness_groups']['general_reasoning'],
            'interpretation': 'Binary replay preserves answers, changing only aggregation. '
                              'No winner labels are read; no severity is inferred from binary answers.'}


def stability_checks(rubric, verdicts):
    """Executable metamorphic checks on fixed diagnostic answers, never new judgments."""
    _, mapping, _ = compile_units(rubric)
    records = binary_diagnostics(verdicts, mapping)
    baseline = score_experiment(rubric, records)

    def bounds(result):
        return [round(result[key], 8) for key in ('score_lower', 'score_upper')]

    checks = {
        'duplicate_same_harm': bounds(score_experiment(rubric, records + copy.deepcopy(records))) == bounds(baseline),
        'repagination': bounds(score_experiment(rubric, [dict(row, pages=[1, 8, 13]) for row in records])) == bounds(baseline),
        'split_diagnostic': bounds(score_experiment(rubric, records + [dict(row, diagnostic_id='part-2') for row in records])) == bounds(baseline),
    }
    first = rubric['items'][0]
    twin = copy.deepcopy(first)
    twin['id'] = first['id'] + '__equivalent'
    expanded = copy.deepcopy(rubric)
    expanded['items'].append(twin)
    twin_records = [dict(row, rubric_id=twin['id']) for row in records if row['rubric_id'] == first['id']]
    aliases = {twin['id']: first['id']}
    checks['equivalent_target_split'] = bounds(score_experiment(expanded, records + twin_records, aliases)) == bounds(baseline)
    facts = [item for item in rubric['items'] if item['dimension'] == 'correctness' and item['id'] != 'C_REASON']
    if facts:
        extra = copy.deepcopy(facts[0])
        extra['id'] += '__unrelated'
        expanded = copy.deepcopy(rubric)
        expanded['items'].append(extra)
        before, _, _ = compile_units(rubric)
        after, _, _ = compile_units(expanded)
        checks['reasoning_budget_with_unrelated_fact'] = all(
            math.isclose(unit['score'], after[uid]['score'], abs_tol=1e-9)
            for uid, unit in before.items() if unit['group'] == 'general_reasoning')
    else:
        checks['reasoning_budget_with_unrelated_fact'] = None
    # Controlled repair removes every occurrence of a single declared harm.
    harmed = next((row for row in records if row['severity'] == 'key_unusable'), None)
    if harmed:
        fixed = [dict(row, severity='full') if row['harm_id'] == harmed['harm_id'] else row for row in records]
        repaired = score_experiment(rubric, fixed)
        checks['repair_cannot_reduce_score_bounds'] = all(
            repaired[key] >= baseline[key] - 1e-9 for key in ('score_lower', 'score_upper'))
        original_units, _, _ = compile_units(rubric)
        expected_gain = original_units[harmed['owner_unit_id']]['score']
        checks['repair_gain_matches_declared_loss'] = all(
            math.isclose(repaired[key] - baseline[key], expected_gain, abs_tol=1e-9)
            for key in ('score_lower', 'score_upper'))
    else:
        checks['repair_cannot_reduce_score_bounds'] = None
        checks['repair_gain_matches_declared_loss'] = None
    return {'checks': checks, 'passed': all(value is not False for value in checks.values()),
            'not_applicable': [key for key, value in checks.items() if value is None],
            'meaning': 'Structural checks only; aliases and impacts still need semantic and human validation.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rubric', required=True)
    parser.add_argument('--judgment', required=True)
    parser.add_argument('--view', choices=['primary', 'consensus'], default='consensus')
    parser.add_argument('--diagnostics', help='Optional anchored diagnostic JSON array; no model inference')
    parser.add_argument('--aliases', help='Optional explicit equivalent item-to-unit JSON object')
    parser.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    rubric, judgment = read_json(args.rubric), read_json(args.judgment)
    if judgment.get('rubric_hash') != rubric.get('rubric_hash') or not rubric.get('rubric_hash'):
        raise ValueError('Judgment and rubric hashes must match')
    result = compare_fixed_answers(rubric, judgment[args.view]['verdicts'],
                                   read_json(args.aliases) if args.aliases else None,
                                   read_json(args.diagnostics) if args.diagnostics else None)
    result.update(schema_version=1, evidence_kind='offline_scoring_policy_experiment',
                  case_id=judgment['case_id'], candidate_id=judgment['candidate_id'], view=args.view,
                  input_hashes={'rubric': file_digest(args.rubric), 'judgment': file_digest(args.judgment)},
                  stability=stability_checks(rubric, judgment[args.view]['verdicts']))
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation protects both historical scores and previous comparisons.
    with output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({'out': str(output), 'experimental_only': True,
                      'stability_passed': result['stability']['passed']}))


if __name__ == '__main__':
    main()
