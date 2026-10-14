"""Evidence-first evaluation. Model observations are claims, never ground truth."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal, localcontext, ROUND_HALF_UP
from functools import partial
from pathlib import Path
import re

from .common import digest, write_json, read_json
from .judging import validate_judge_verdict, JUDGE_VALIDATOR_VERSION
from .rubric import aggregate
from . import prompts

PROTOCOL = 'evidence_v1'
VISUAL_MODES = {'visual_readability', 'chart_label_integrity', 'visual_hierarchy'}


def ownership_errors(rubric):
    modes = rubric.get('failure_modes')
    items = {i['id']: i for i in rubric.get('items', [])}
    if not isinstance(modes, list) or not modes:
        return ['failure_modes must define exclusive defect ownership before scoring']
    errors, ids, owners = [], set(), set()
    groups = {g['id']: g['dimension'] for g in rubric.get('groups', [])}
    for m in modes:
        if not isinstance(m, dict):
            errors.append('Invalid failure mode'); continue
        mid = m.get('id')
        if not isinstance(mid, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', mid) or mid in ids:
            errors.append('Failure mode ids must be unique stable identifiers')
        else:
            ids.add(mid)
        owner = m.get('owner')
        if not isinstance(owner, str) or owner not in items:
            errors.append('Each failure mode needs one existing owner')
        else:
            owners.add(owner)
        for k in ('definition', 'exclusions'):
            if not isinstance(m.get(k), str) or not m[k].strip():
                errors.append(f'Failure mode requires {k}')
        # Export remains judge_prompt + score, so boundaries must travel with it.
        if isinstance(owner, str) and owner in items and (not isinstance(m.get('definition'), str)
                or m['definition'] not in items[owner].get('judge_prompt', '')
                or not isinstance(m.get('exclusions'), str)
                or m['exclusions'] not in items[owner].get('judge_prompt', '')):
            errors.append(f'{owner}: definition and exclusions must appear verbatim in judge_prompt')
        if isinstance(mid, str) and mid in VISUAL_MODES and isinstance(owner, str) and owner in items and groups.get(items[owner]['group_id']) != 'visual':
            errors.append('Visual failure modes must be owned by visual criteria')
    if 'reasoning_validity' not in ids:
        errors.append('Define reasoning_validity for applicable computations and inferences')
    if set(items) - owners:
        errors.append('Every criterion needs an owned failure mode')
    if not VISUAL_MODES <= ids:
        errors.append('Define all three visual modes, with no overlap between their definitions')
    return errors


def arithmetic_check(spec):
    """Recompute a declared relation; operand selection and units still need review."""
    if not isinstance(spec, dict):
        raise ValueError('Invalid arithmetic record')
    number = r'-?\d{1,16}(?:\.\d{1,16})?'
    if any(not isinstance(spec.get(k), str) or not re.fullmatch(number, spec[k])
           for k in ('left', 'right', 'reported')):
        raise ValueError('Arithmetic operands must be bounded decimal strings')
    if not isinstance(spec.get('meaning'), str) or not spec['meaning'].strip():
        raise ValueError('Arithmetic relation needs its meaning and baseline')
    with localcontext() as ctx:
        ctx.prec = 80
        a, b, reported = (Decimal(spec[k]) for k in ('left', 'right', 'reported'))
        op = spec.get('operation')
        if op == 'add': actual = a + b
        elif op == 'subtract': actual = a - b
        elif op == 'multiply': actual = a * b
        elif op == 'divide': actual = None if b == 0 else a / b
        elif op == 'excess_over_baseline_percent':
            # Current = baseline * (1 + p/100); excess is not current * p/100.
            if b < 0:
                raise ValueError('Excess percentage must be nonnegative')
            actual = a * b / (100 + b)
        else: raise ValueError('Unknown arithmetic operation')
        rounded = None if actual is None else actual.quantize(
            Decimal(1).scaleb(reported.as_tuple().exponent), rounding=ROUND_HALF_UP)
        return {'computed': str(actual) if actual is not None else None,
                'matches_reported_precision': actual is not None and rounded == reported,
                'limit': 'Checks declared operands/relation, not their grounding or unit compatibility.'}


def validate_page(result, page, sources, modes):
    if not isinstance(result, dict) or type(result.get('page')) is not int or result['page'] != page:
        raise ValueError('Evidence must preserve original page number')
    if type(result.get('complete')) is not bool:
        raise ValueError('Page evidence requires an explicit completeness flag')
    rows = result.get('observations')
    if not isinstance(rows, list) or not rows:
        raise ValueError('Describe at least the visible page, including an empty page')
    source_map = {s['id']: s['text'] for s in sources}
    anchors = set()
    for row in rows:
        if not isinstance(row, dict): raise ValueError('Invalid evidence observation')
        for key in ('quote', 'location', 'statement'):
            if not isinstance(row.get(key), str) or not row[key].strip():
                raise ValueError('Evidence needs visible quote, location and statement')
        if row.get('kind') not in {'fact', 'inference', 'assumption', 'proposal', 'visual'}:
            raise ValueError('Unknown evidence kind')
        if 'failure_mode' not in row: raise ValueError('Evidence must explicitly declare failure_mode or null')
        mode = row.get('failure_mode')
        if mode is not None and (not isinstance(mode, str) or mode not in modes):
            raise ValueError('Observation refers to an unowned failure mode')
        if mode is not None:
            anchor = tuple(re.sub(r'\s+', '', row[k]) for k in ('location', 'quote'))
            if anchor in anchors:
                raise ValueError('Same visible defect anchor assigned more than once; split independent defects precisely')
            anchors.add(anchor)
        refs = row.get('source_refs')
        if not isinstance(refs, list): raise ValueError('source_refs must be a list')
        for ref in refs:
            if (not isinstance(ref, dict) or not isinstance(ref.get('source_id'), str)
                    or ref['source_id'] not in source_map or not isinstance(ref.get('quote'), str)
                    or not ref['quote'] or ref['quote'] not in source_map[ref['source_id']]):
                raise ValueError('Source evidence must be a verbatim supplied quote')
        quantities = row.get('quantities')
        if not isinstance(quantities, list): raise ValueError('quantities must be a list')
        for q in quantities:
            if not isinstance(q, dict) or any(not isinstance(q.get(k), str) or not q[k].strip()
                    for k in ('value', 'unit', 'subject', 'denominator', 'time_origin')):
                raise ValueError('Quantity needs value, unit, subject, denominator and time origin')
        calculations = row.get('calculations')
        if not isinstance(calculations, list): raise ValueError('calculations must be a list')
        for calc in calculations: arithmetic_check(calc)


def collect_evidence(deck, rubric, sources, client):
    modes = {m['id']: m for m in rubric['failure_modes']}
    pages, ledger = [], []
    for slide in deck['slides']:
        check = partial(validate_page, page=slide['page'], sources=sources, modes=modes)
        try:
            result = client.ask('judge', prompts.EVIDENCE_PAGE,
                {'sources': sources, 'page': slide['page'], 'failure_modes': rubric['failure_modes']},
                [(f"Slide {slide['page']}", slide['image'])], validate=check)
            check(result)
            for n, row in enumerate(result['observations'], 1):
                computed = [arithmetic_check(c) for c in row['calculations']]
                flagged = any(not c['matches_reported_precision'] for c in computed)
                ledger.append({**row, 'id': f"p{slide['page']}_e{n}", 'page': slide['page'],
                               'failure_mode': 'reasoning_validity' if flagged else row['failure_mode'],
                               'failure_mode_origin': 'arithmetic_check' if flagged else 'model_observation',
                               'arithmetic_checks': computed})
        except Exception as e:
            result = {'page': slide['page'], 'complete': False, 'observations': [],
                      'infrastructure_error': type(e).__name__}
        pages.append(result)
    return {'complete': bool(pages) and all(p['complete'] for p in pages), 'pages': pages, 'ledger': ledger}


def validate_evidence_verdict(result, page_count, item, ledger, modes):
    validate_judge_verdict(result, page_count)
    by_id = {r['id']: r for r in ledger}
    refs = result.get('evidence_ids')
    if (not isinstance(refs, list) or any(not isinstance(r, str) or r not in by_id for r in refs)
            or len(refs) != len(set(refs)) or (result['answer'] != 'unknown' and not refs)):
        raise ValueError('Verdict must cite registered evidence')
    required = {r['id'] for r in ledger if r['failure_mode'] is not None
                and modes[r['failure_mode']]['owner'] == item['id']}
    resolutions = result.get('resolutions')
    if (not isinstance(resolutions, list) or any(not isinstance(r, dict) or not isinstance(r.get('evidence_id'), str) for r in resolutions)
            or len(resolutions) != len(required) or {r['evidence_id'] for r in resolutions} != required):
        raise ValueError('Resolve each owned potential defect exactly once')
    confirmed = set()
    for r in resolutions:
        if r.get('decision') not in {'confirm', 'reject', 'uncertain'} or not isinstance(r.get('reason'), str) or not r['reason'].strip():
            raise ValueError('Resolution needs decision and reason')
        if r['decision'] == 'confirm': confirmed.add(r['evidence_id'])
        if r['decision'] == 'uncertain' and result['answer'] == 'yes':
            raise ValueError('Unresolved owned evidence cannot pass')
    failures = result.get('failure_ids')
    if (not isinstance(failures, list) or any(not isinstance(f, str) for f in failures)
            or len(failures) != len(set(failures)) or set(failures) != confirmed):
        raise ValueError('Failures must equal confirmed defects owned by this criterion')
    if (result['answer'] == 'no') != bool(confirmed):
        raise ValueError('A failure requires a confirmed owned defect, and cannot be ignored')
    if not confirmed <= set(refs): raise ValueError('Failure evidence must be cited')
    if any(by_id[r]['page'] not in result['pages'] for r in refs):
        raise ValueError('Evidence pages must match verdict localization')


def validate_consistency(result, candidate_evidence):
    if not isinstance(result, dict) or type(result.get('consistent')) is not bool or not isinstance(result.get('issues'), list):
        raise ValueError('Invalid consistency review')
    if result['consistent'] != (not result['issues']):
        raise ValueError('Consistency answer contradicts issues')
    for issue in result['issues']:
        if not isinstance(issue, dict) or not isinstance(issue.get('reason'), str) or not issue['reason'].strip():
            raise ValueError('Consistency issue requires a reason')
        refs = issue.get('evidence_refs')
        if not isinstance(refs, dict) or not refs or set(refs) - set(candidate_evidence):
            raise ValueError('Consistency issue cites unknown candidates')
        for cid, ids in refs.items():
            if (not isinstance(ids, list) or not ids or any(not isinstance(i, str) or i not in candidate_evidence[cid] for i in ids)):
                raise ValueError('Consistency issue needs supplied candidate evidence')


def unknown(item_id, reason):
    return {'rubric_id': item_id, 'answer': 'unknown', 'pages': [], 'evidence': reason,
            'evidence_ids': [], 'failure_ids': [], 'resolutions': []}


def evaluate_evidence(case, rubric, config, client, decks, sources, output, limit=None):
    errors = ownership_errors(rubric)
    if errors: raise ValueError('; '.join(errors))
    output = Path(output) / case['id']; output.mkdir(parents=True, exist_ok=True)
    modes = {m['id']: m for m in rubric['failure_modes']}
    items = rubric['items'][:limit] if limit is not None else rubric['items']
    evidence, verdicts, history = {}, {}, {}
    # A crash during the new run must not leave a previous complete score visible.
    for cid in decks:
        old = output / f'{cid}.json'
        if old.exists(): old.rename(output / f'{cid}-superseded-{digest(read_json(old))[:16]}.json')
    for cid, deck in decks.items():
        evidence[cid] = collect_evidence(deck, rubric, sources, client)
        write_json(output / f'{cid}-evidence.json', evidence[cid])

    def judge(cid, item, feedback=None):
        deck = decks[cid]; record = evidence[cid]
        if not record['complete']:
            return unknown(item['id'], 'Evidence collection incomplete')
        check = partial(validate_evidence_verdict, page_count=len(deck['slides']), item=item,
                        ledger=record['ledger'], modes=modes)
        owned = [m for m in rubric['failure_modes'] if m['owner'] == item['id']]
        payload = {'sources': sources, 'ledger': record['ledger'], 'criterion': item['judge_prompt'],
                   'scope': item['scope'], 'owned_failure_modes': owned,
                   'required_resolution_ids': [r['id'] for r in record['ledger']
                        if r['failure_mode'] is not None and modes[r['failure_mode']]['owner'] == item['id']]}
        if feedback is not None: payload['consistency_feedback'] = feedback
        try:
            result = client.ask('judge', prompts.EVIDENCE_JUDGE, payload,
                [(f"Slide {s['page']}", s['image']) for s in deck['slides']], validate=check)
            check(result)
            return {'rubric_id': item['id'], **{k: result[k] for k in (
                'answer', 'pages', 'evidence', 'evidence_ids', 'failure_ids', 'resolutions')}}
        except Exception as e:
            return unknown(item['id'], f'Judge infrastructure error: {type(e).__name__}')

    for cid in decks:
        verdicts[cid] = {}
        with ThreadPoolExecutor(max_workers=config['api_concurrency']) as pool:
            tasks = {pool.submit(judge, cid, item): item for item in items}
            for future in as_completed(tasks):
                v = future.result(); verdicts[cid][v['rubric_id']] = v
                write_json(output / f'{cid}-initial.json', verdicts[cid])

    for item in items:
        rid = item['id']; rounds = []
        def review():
            candidates = {}
            for cid in decks:
                v = verdicts[cid][rid]
                # Only evidence and same-criterion verdict: no other scores or labels.
                selected = set(v.get('evidence_ids', []))
                selected.update(r['id'] for r in evidence[cid]['ledger'] if r['failure_mode'] is not None
                                and modes[r['failure_mode']]['owner'] == rid)
                candidates[cid] = {'verdict': {k: v[k] for k in (
                    'answer', 'pages', 'evidence', 'evidence_ids', 'failure_ids', 'resolutions')}, 'evidence': [r for r in evidence[cid]['ledger'] if r['id'] in selected]}
            check = partial(validate_consistency, candidate_evidence={
                c: {r['id'] for r in d['evidence']} for c, d in candidates.items()})
            result = client.ask('reviewer', prompts.CONSISTENCY_REVIEW,
                {'sources': sources, 'criterion': item['judge_prompt'], 'scope': item['scope'],
                 'owned_failure_modes': [m for m in rubric['failure_modes'] if m['owner'] == rid],
                 'candidates': candidates}, validate=check)
            check(result)
            return result
        try:
            if any(verdicts[c][rid]['answer'] == 'unknown' for c in decks):
                raise ValueError('Initial verdict incomplete; comparison cannot be validated')
            first = review(); rounds.append({'review': first})
            if not first['consistent']:
                affected = {cid for issue in first['issues'] for cid in issue['evidence_refs']}
                for cid in sorted(affected):
                    v = judge(cid, item, first['issues']); verdicts[cid][rid] = v
                    rounds.append({'candidate': cid, 'rejudgment': v})
                if any(verdicts[c][rid]['answer'] == 'unknown' for c in decks):
                    raise ValueError('Rejudgment unresolved')
                second = review(); rounds.append({'review': second})
                if not second['consistent']: raise ValueError('Consistency conflict persists after one repair')
        except Exception as e:
            # No forced equal scores or winner. Same criterion remains unresolved for all.
            rounds.append({'unresolved': type(e).__name__, 'reason': str(e)})
            for cid in decks:
                verdicts[cid][rid] = unknown(rid, 'Cross-candidate consistency not established')
        history[rid] = rounds
        write_json(output / 'consistency.json', history)
    reports = {}
    for cid, deck in decks.items():
        ordered = [verdicts[cid][i['id']] for i in items]
        report = {'case_id': case['id'], 'candidate_id': cid, 'candidate_hash': deck['identity']['sha256'],
                  'rubric_hash': rubric['rubric_hash'], 'judge_model': config['judge_model'],
                  'config_hash': digest(config), 'judge_validator_version': JUDGE_VALIDATOR_VERSION,
                  'evaluation_protocol': PROTOCOL,
                  'judge_prompt_fingerprint': digest([prompts.EVIDENCE_PAGE, prompts.EVIDENCE_JUDGE, prompts.CONSISTENCY_REVIEW]),
                  'summary': aggregate(rubric, ordered), 'verdicts': ordered}
        write_json(output / f'{cid}.json', report); reports[cid] = report
    return reports
