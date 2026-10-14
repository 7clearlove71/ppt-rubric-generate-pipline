from __future__ import annotations

import math
import re
from collections import Counter

DIMENSIONS = {"fulfillment", "correctness", "integrity", "narrative", "visual"}
VALIDATOR_VERSION = 6

NUMBER = r'(?:\d+(?:\.\d+)?|[零一二两三四五六七八九十百]+)'
COUNT_UNIT = r'(?:分钟|个|项|页|张|条|段|字|磅|pt)'


def numeric_value(text):
    try:
        return float(text)
    except ValueError:
        digit={c:i for i,c in enumerate('零一二三四五六七八九')}
        digit['两']=2
        value,current=0,0
        for char in text:
            if char in {'十','百'}:
                value += (current or 1)*({'十':10,'百':100}[char]);current=0
            else:
                current=digit[char]
        return value+current


def unsupported_count_thresholds(text, source_quotes):
    """Reject invented exact presentation thresholds, not ordinary fact numbers.

    This is a necessary grounding check, not proof that a cited number applies
    to the right target. Semantic review still checks that relationship.
    """
    source_counts={(numeric_value(m[1]),m[2].lower().replace('pt','磅'))
                   for m in re.finditer('('+NUMBER+r')\s*('+COUNT_UNIT+')',source_quotes,re.I)}
    invalid=[]
    pattern=r'(?:至少|不少于|不低于|至多|不超过|最多|不小于|不大于|大于|小于|超过)\s*('+NUMBER+r')\s*('+COUNT_UNIT+')'
    for match in re.finditer(pattern,text,re.I):
        amount=numeric_value(match[1])
        if amount>1 and (amount,match[2].lower().replace('pt','磅')) not in source_counts:
            invalid.append(match[0])
    return invalid


def valid_importance(value):
    return type(value) is int and value in {1, 2, 3}


def validate_rubric_shape(rubric):
    """Only schema failures trigger provider retry; quality defects use repair."""
    for collection in ['groups','items']:
        values = rubric.get(collection)
        if not isinstance(values,list) or not values or any(not isinstance(v,dict) for v in values):
            raise ValueError(f'{collection} must be a nonempty array of objects')
        if any(not isinstance(v.get('id'),str) or not v['id'] for v in values):
            raise ValueError(f'{collection} entries must have string ids')
    for item in rubric['items']:
        if not isinstance(item.get('source_refs'),list) or any(not isinstance(r,str) for r in item['source_refs']):
            raise ValueError('Item source_refs must be an array of strings')


def normalize_single_source_duplicates(rubric):
    """Remove only exact single-source ownership duplicates, retaining correctness.

    Multi-source/ambiguous overlaps remain errors for the semantic repair loop.
    The next model review must check the retained coverage after normalization.
    """
    import copy
    rubric = copy.deepcopy(rubric)
    groups = {g.get('id'): g for g in rubric.get('groups', [])}
    owners = {}
    for item in rubric.get('items', []):
        refs = item.get('source_refs', [])
        if item.get('basis') == 'explicit' and isinstance(refs, list) and len(refs) == 1:
            if groups.get(item.get('group_id'), {}).get('dimension') == 'correctness':
                owners.setdefault(refs[0], []).append(item['id'])
    # A global-context reference may accompany the duplicated fact. It can be
    # ignored only when another uncontested fulfillment item already owns it.
    context_owners = set()
    for item in rubric.get('items', []):
        refs = item.get('source_refs', [])
        if (item.get('basis') == 'explicit' and isinstance(refs, list) and len(refs) == 1
                and refs[0] not in owners and groups.get(item.get('group_id'), {}).get('dimension') == 'fulfillment'):
            context_owners.add(refs[0])
    removed, kept = [], []
    for item in rubric.get('items', []):
        refs = item.get('source_refs', [])
        duplicated = set(refs) & set(owners) if isinstance(refs, list) else set()
        if (item.get('basis') == 'explicit' and len(duplicated) == 1
                and set(refs) - duplicated <= context_owners
                and groups.get(item.get('group_id'), {}).get('dimension') == 'fulfillment'):
            ref = next(iter(duplicated))
            removed.append({'removed_item':item['id'], 'source_ref':ref, 'retained_items':owners[ref],
                            'separately_covered_context_refs': sorted(set(refs) - duplicated),
                            'reason':'Exact single-source ownership duplicated between fulfillment and correctness'})
        else:
            kept.append(item)
    if removed:
        rubric['items'] = kept
        used = {i.get('group_id') for i in kept}
        rubric['groups'] = [g for g in rubric['groups'] if g.get('id') in used]
    return rubric, removed


def validate_requirements(result, sources):
    by_id = {s['id']: s['text'] for s in sources}
    seen = set()
    reqs = result.get('requirements')
    if not isinstance(reqs, list) or not reqs:
        raise ValueError('Nonempty requirements required')
    for q in reqs:
        if not isinstance(q,dict):
            raise ValueError('Requirements must be objects')
        if not q.get('id') or q['id'] in seen:
            raise ValueError('Requirement ids must be unique')
        seen.add(q['id'])
        if not isinstance(q.get('text'), str) or not q['text'].strip() or not valid_importance(q.get('importance')):
            raise ValueError('Invalid requirement text/importance')
        if not q.get('quote') or q.get('source_id') not in by_id or q['quote'] not in by_id[q['source_id']]:
            raise ValueError(f"Requirement {q['id']}: quote is not verbatim source text")


def validate_rubric(rubric, requirements):
    errors = []
    try:
        validate_rubric_shape(rubric)
    except ValueError as e:
        return [str(e)]
    groups, items = rubric.get('groups'), rubric.get('items')
    if not isinstance(groups, list) or not groups or not isinstance(items, list) or not items:
        return ['Nonempty groups and items arrays required']
    gids = [g.get('id') for g in groups]
    ids = [i.get('id') for i in items]
    if any(not isinstance(x, str) or not x for x in gids + ids):
        return ['All group/item ids must be nonempty strings']
    if len(set(gids)) != len(gids) or len(set(ids)) != len(ids):
        errors.append('Duplicate group/item ids')
    by_group = {g['id']: g for g in groups}
    req_ids = {q['id'] for q in requirements['requirements']}
    source_quotes = '\n'.join(q.get('quote','') for q in requirements['requirements'])
    dimensions = {g.get('dimension') for g in groups}
    if dimensions != DIMENSIONS:
        errors.append('Must cover exactly the five configured dimensions')
    for g in groups:
        if not valid_importance(g.get('importance')) or not g.get('rationale'):
            errors.append(f"Group {g['id']}: invalid importance/rationale")
    covered, prompts = set(), set()
    explicit_by_source = {}
    counts = Counter()
    for item in items:
        rid = item['id']
        if item.get('group_id') not in by_group:
            errors.append(f'{rid}: unknown group')
        else:
            counts[item['group_id']] += 1
        if not valid_importance(item.get('importance')):
            errors.append(f'{rid}: importance must be 1/2/3')
        text = item.get('judge_prompt', '')
        if not isinstance(text, str) or len(text.strip()) < 12:
            errors.append(f'{rid}: empty/too short judge_prompt')
        elif ''.join(text.split()) in prompts:
            errors.append(f'{rid}: duplicate judge_prompt')
        else:
            prompts.add(''.join(text.split()))
        if isinstance(text, str):
            thresholds=unsupported_count_thresholds(text,source_quotes)
            if thresholds:
                errors.append(f'{rid}: exact count thresholds absent from original source quotes: {thresholds}; use source-grounded or qualitative conditions')
            referenced = []
            for other in sorted((set(ids) | req_ids)-{rid}):
                pattern=r'(?<![A-Za-z0-9_])' + re.escape(other) + r'(?![A-Za-z0-9_])'
                # A literal ID can be real source content (e.g. Q1 as a quarter).
                # Only reject internal identifiers absent from the original quotes.
                if re.search(pattern,text) and not re.search(pattern,source_quotes):
                    referenced.append(other)
            if referenced:
                errors.append(f'{rid}: judge_prompt references other rubric ids or internal requirement ids {referenced}; each criterion must be independently judgeable')
        if not isinstance(item.get('scope'), str) or not item['scope'].strip():
            errors.append(f'{rid}: scope required')
        if item.get('evidence_mode') not in {'text', 'visual', 'both'}:
            errors.append(f'{rid}: invalid evidence mode')
        if item.get('basis') not in {'explicit', 'quality'}:
            errors.append(f'{rid}: invalid basis')
        refs = item.get('source_refs')
        if not isinstance(refs, list) or any(not isinstance(x, str) for x in refs):
            errors.append(f'{rid}: invalid source refs')
        else:
            if set(refs) - req_ids:
                errors.append(f'{rid}: unknown source refs')
            if item.get('basis') == 'explicit' and not refs:
                errors.append(f'{rid}: explicit criterion requires refs')
            covered.update(refs)
            if item.get('basis') == 'explicit' and item.get('group_id') in by_group:
                dimension = by_group[item['group_id']]['dimension']
                for ref in refs:
                    explicit_by_source.setdefault(ref, {}).setdefault(dimension, []).append(rid)
    for gid in set(gids) - set(counts):
        errors.append(f'{gid}: empty scoring group')
    if req_ids - covered:
        errors.append(f'Uncovered requirements: {sorted(req_ids - covered)}')
    for ref, owners in explicit_by_source.items():
        if 'fulfillment' in owners and 'correctness' in owners:
            errors.append(f'{ref}: duplicate explicit scoring in fulfillment {owners["fulfillment"]} and correctness {owners["correctness"]}. '
                          'Use one atomic present-and-correct criterion for each required fact in correctness; '
                          'do not repeat it as a separate fulfillment criterion. Keep other task requirements in fulfillment.')
    return errors


def assign_scores(rubric, weights):
    """Splitting an item inside its scoring group cannot increase that group's budget."""
    group_weights = Counter()
    item_weights = Counter()
    groups = {g['id']: g for g in rubric['groups']}
    for g in groups.values():
        group_weights[g['dimension']] += g['importance']
    for i in rubric['items']:
        item_weights[i['group_id']] += i['importance']
    for g in groups.values():
        g['score'] = weights[g['dimension']] * g['importance'] / group_weights[g['dimension']]
    for i in rubric['items']:
        g = groups[i['group_id']]
        i['dimension'] = g['dimension']
        i['score'] = g['score'] * i['importance'] / item_weights[g['id']]
    if not math.isclose(sum(i['score'] for i in rubric['items']), 100, abs_tol=1e-8):
        raise ValueError('Scoring budgets do not sum to 100')
    return rubric


def aggregate(rubric, verdicts):
    results = {v['rubric_id']: v for v in verdicts}
    if len(results) != len(verdicts):
        raise ValueError('Duplicate verdicts')
    if set(results) - {i['id'] for i in rubric['items']}:
        raise ValueError('Unknown verdict ids')
    dimensions = {}
    for d in sorted(DIMENSIONS):
        items = [i for i in rubric['items'] if i['dimension'] == d]
        possible = sum(i['score'] for i in items)
        earned = sum(i['score'] for i in items if results.get(i['id'], {}).get('answer') == 'yes')
        unknown = sum(i['score'] for i in items if results.get(i['id'], {}).get('answer') not in {'yes', 'no'})
        dimensions[d] = {'earned': earned, 'possible': possible, 'unresolved': unknown}
    lower = sum(v['earned'] for v in dimensions.values())
    unresolved = sum(v['unresolved'] for v in dimensions.values())
    return {'status': 'complete' if unresolved == 0 else 'incomplete',
            'score': lower if unresolved == 0 else None, 'score_lower': lower,
            'score_upper': lower + unresolved, 'unresolved_weight': unresolved, 'dimensions': dimensions}
