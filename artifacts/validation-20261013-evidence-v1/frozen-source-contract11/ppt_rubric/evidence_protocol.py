"""Evidence-first evaluation. Model observations are claims, never ground truth."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial
from pathlib import Path
import copy
import re

from .common import digest, write_json, read_json
from .judging import validate_judge_verdict, JUDGE_VALIDATOR_VERSION
from .rubric import aggregate
from . import prompts
from .calculations import arithmetic_check
from .render import rendered_text_geometry
from .visual_regions import focus_images, collect_visual_inspections, image_region_evidence

PROTOCOL = 'evidence_v1'
VISUAL_MODES = {'visual_readability', 'chart_label_integrity', 'visual_hierarchy'}
BOUNDARY_MARKER = '\n\n【程序附加的缺陷归属边界】\n'


def compile_ownership_boundaries(rubric):
    """Attach declared boundaries verbatim; never invent ownership or edit the criterion."""
    result = copy.deepcopy(rubric)
    modes = result.get('failure_modes')
    if not isinstance(modes, list):
        return result
    for item in result.get('items', []):
        text = item.get('judge_prompt')
        if not isinstance(text, str):
            continue
        text = text.split(BOUNDARY_MARKER, 1)[0]
        owned = [m for m in modes if isinstance(m, dict) and m.get('owner') == item.get('id')
                 and all(isinstance(m.get(k), str) and m[k].strip() for k in ('id', 'definition', 'exclusions'))]
        if owned:
            text += BOUNDARY_MARKER + '\n'.join(
                f"{m['id']}：{m['definition']}\n排除：{m['exclusions']}" for m in owned)
        item['judge_prompt'] = text
    return result


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
        if any(not isinstance(calc, dict) for calc in calculations):
            raise ValueError('Calculations must be objects')


def collect_evidence(deck, rubric, sources, client, config=None):
    modes = {m['id']: m for m in rubric['failure_modes']}
    pages, ledger = [], []
    geometry = {p['page']: p for p in rendered_text_geometry(deck)}
    config = config or getattr(client, 'config', {})
    for slide in deck['slides']:
        check = partial(validate_page, page=slide['page'], sources=sources, modes=modes)
        try:
            images = [(f"Slide {slide['page']}", slide['image'])]
            if slide['page'] in geometry:
                images += focus_images({**deck, 'slides': [slide]}, [geometry[slide['page']]], config)
            result = client.ask('judge', prompts.EVIDENCE_PAGE,
                {'sources': sources, 'page': slide['page'], 'failure_modes': rubric['failure_modes'],
                 'rendered_text_geometry': geometry.get(slide['page'])},
                images, validate=check)
            check(result)
            for n, row in enumerate(result['observations'], 1):
                grounded = row['quote'] + '\n' + '\n'.join(s['text'] for s in sources)
                computed = [arithmetic_check(c, grounded) for c in row['calculations']]
                # A model-selected formula is not evidence of a candidate defect.
                # Keep arithmetic diagnostics separate from frozen defect ownership.
                ledger.append({**row, 'id': f"p{slide['page']}_e{n}", 'page': slide['page'],
                               'failure_mode_origin': 'model_observation',
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
        received = [r.get('evidence_id') for r in resolutions if isinstance(r, dict)] if isinstance(resolutions, list) else []
        raise ValueError(f'Resolve each owned potential defect exactly once. Required IDs: {sorted(required)}; '
                         f'received IDs: {received}. Do not resolve observations owned by another criterion.')
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


def prepare_evidence_verdict(raw, page_count, item, ledger, modes, sources, visual_inspections=()):
    """Derive redundant IDs; register new, localized image evidence without changing answers."""
    if not isinstance(raw, dict):
        raise ValueError('Verdict must be an object')
    result = copy.deepcopy(raw)
    required_inspections = {r['id'] for r in visual_inspections if r['status'] == 'observed'}
    inspections = result.get('inspection_resolutions', [])
    if (not isinstance(inspections, list) or any(not isinstance(r, dict) for r in inspections)
            or len(inspections) != len(required_inspections)
            or any(not isinstance(r.get('inspection_id'), str) for r in inspections)
            or {r['inspection_id'] for r in inspections} != required_inspections):
        instruction = ('Set inspection_resolutions=[]; no independent observations were supplied for this criterion.'
                       if not required_inspections else
                       'Use only these IDs in inspection_resolutions, exactly once each.')
        raise ValueError(f'Resolve each independent visual inspection exactly once: {sorted(required_inspections)}. '
                         f'{instruction} Crop IDs and text_overlap_candidates are not independent inspections. '
                         'Do not move inspection IDs into resolutions.')
    for inspection in inspections:
        for key in ('relation_confirmed', 'defect_present'):
            if key not in inspection or (inspection[key] is not None and type(inspection[key]) is not bool):
                raise ValueError(f'Visual inspection {key} must be true, false or null')
        if not isinstance(inspection.get('reason'), str) or not inspection['reason'].strip():
            raise ValueError('Visual inspection resolution needs an image and criterion justification')
        if inspection['defect_present'] is True and result.get('answer') != 'no':
            raise ValueError('A confirmed inspection defect requires no and registered defect evidence')
        if inspection['defect_present'] is None and result.get('answer') == 'yes':
            raise ValueError('An unresolved inspection defect cannot pass')
    result['inspection_resolutions'] = inspections
    resolutions = result.get('resolutions')
    if not isinstance(resolutions, list) or any(not isinstance(x, dict) for x in resolutions):
        raise ValueError('resolutions must be objects with evidence_id, decision, reason')
    known = {r['id'] for r in ledger}
    for resolution in resolutions:
        if 'defect_present' in resolution:
            present = resolution['defect_present']
            if present is not None and type(present) is not bool:
                raise ValueError('defect_present must be true, false or null')
            decision = 'uncertain' if present is None else ('confirm' if present else 'reject')
            if resolution.get('decision', decision) != decision:
                raise ValueError('defect_present contradicts resolution decision')
            resolution['decision'] = decision
        if 'id' in resolution:
            alias = resolution['id']
            if (not isinstance(alias, str) or alias not in known
                    or resolution.get('evidence_id', alias) != alias):
                raise ValueError('Resolution id alias must match a supplied evidence_id')
            resolution['evidence_id'] = resolution.pop('id')
    # Validate original IDs before adding program-generated IDs so retry feedback
    # never asks a model to resolve IDs which were absent from its input ledger.
    required = {r['id'] for r in ledger if r['failure_mode'] is not None
                and modes[r['failure_mode']]['owner'] == item['id']}
    received = [r.get('evidence_id') for r in resolutions]
    if (any(not isinstance(eid, str) for eid in received) or len(received) != len(required)
            or set(received) != required):
        raise ValueError(f'Resolve each owned potential defect exactly once using evidence_id, decision, reason. '
                         f'Required IDs: {sorted(required)}; received IDs: {received}. '
                         'New defects belong in additional_observations, not resolutions.')
    additions = result.get('additional_observations', [])
    if not isinstance(additions, list) or len(additions) > 12:
        raise ValueError('Additional observations must be a bounded list')
    registered = []
    local = copy.deepcopy(ledger)
    # A judge's explicit inspection decision already carries the semantic
    # judgment. Compile its stable image ID and ownership, never a new answer.
    inspection_by_id = {r['id']: r for r in visual_inspections}
    for resolution in inspections:
        rid = resolution['inspection_id']
        present = resolution['defect_present']
        existing_resolution = next((r for r in resolutions if r['evidence_id'] == rid), None)
        decision = 'uncertain' if present is None else ('confirm' if present else 'reject')
        if existing_resolution and existing_resolution['decision'] != decision:
            raise ValueError('Inspection contradicts the resolution of its registered evidence')
        if present is not True:
            continue
        if resolution['relation_confirmed'] is not True:
            raise ValueError('An inspection defect must confirm the observed spatial relation')
        mode = modes.get('visual_readability')
        if mode is None or mode['owner'] != item['id']:
            raise ValueError('An inspected overlap cannot be charged outside its readability owner')
        source = inspection_by_id[rid]
        observed = source['observation']
        enriched = {'id': rid, 'page': source['page'], 'kind': 'visual',
            'quote': observed['description'], 'location': f"PDF rectangle {source['rect']}",
            'statement': resolution['reason'], 'failure_mode': 'visual_readability',
            'failure_mode_origin': 'direct_judge_observation', 'source_refs': [],
            'quantities': [], 'calculations': [], 'arithmetic_checks': []}
        previous = next((r for r in local if r['id'] == rid), None)
        if previous and previous['failure_mode'] not in {None, 'visual_readability'}:
            raise ValueError('Inspection cannot reassign an existing defect owner')
        local = [r for r in local if r['id'] != rid] + [enriched]
        registered.append(enriched)
        if existing_resolution:
            pass  # The already validated resolution preserves the original evidence ID.
        else:
            resolutions.append({'evidence_id': rid, 'decision': 'confirm', 'reason': resolution['reason']})
        if not isinstance(result.get('evidence_ids'), list) or not isinstance(result.get('pages'), list):
            raise ValueError('Verdict requires evidence_ids and pages')
        if rid not in result['evidence_ids']: result['evidence_ids'].append(rid)
        if source['page'] not in result['pages']: result['pages'].append(source['page'])
    for row in additions:
        if not isinstance(row, dict) or type(row.get('page')) is not int or not 1 <= row['page'] <= page_count:
            raise ValueError('Additional evidence requires a valid page')
        validate_page({'page': row['page'], 'complete': True, 'observations': [row]}, row['page'], sources, modes)
        mode = row['failure_mode']
        if mode is None or modes[mode]['owner'] != item['id']:
            raise ValueError('Additional defect must belong to this criterion')
        anchor = lambda x: (x['page'], re.sub(r'\s+', '', x['quote']), re.sub(r'\s+', '', x['location']))
        existing = next((x for x in local if anchor(x) == anchor(row)), None)
        if existing and existing['failure_mode'] is not None and existing['failure_mode'] != mode:
            raise ValueError('New evidence cannot reassign another defect owner')
        rid = existing['id'] if existing else f"p{row['page']}_{item['id']}_direct_{digest(row)[:12]}"
        enriched = {**row, 'id': rid, 'failure_mode_origin': 'direct_judge_observation',
                    'arithmetic_checks': [arithmetic_check(c, row['quote'] + '\n' + '\n'.join(s['text'] for s in sources))
                                          for c in row['calculations']]}
        local = [x for x in local if x['id'] != rid] + [enriched]
        if any(x['id'] == rid for x in registered):
            raise ValueError('Duplicate additional defect')
        registered.append(enriched)
        if not isinstance(result.get('resolutions'), list) or not isinstance(result.get('evidence_ids'), list) or not isinstance(result.get('pages'), list):
            raise ValueError('Verdict requires resolutions, evidence_ids and pages')
        if any(not isinstance(x, dict) for x in result['resolutions']):
            raise ValueError('Invalid resolutions')
        if any(x.get('evidence_id') == rid for x in result['resolutions']):
            raise ValueError('Already registered defect must use its existing resolution')
        result['resolutions'].append({'evidence_id': rid, 'decision': 'confirm', 'reason': row['statement']})
        if rid not in result['evidence_ids']: result['evidence_ids'].append(rid)
        if row['page'] not in result['pages']: result['pages'].append(row['page'])
    resolutions = result.get('resolutions')
    if not isinstance(resolutions, list) or any(not isinstance(x, dict) for x in resolutions):
        raise ValueError('Invalid resolutions')
    # failure_ids are a projection of confirmations, not a second model decision.
    result['failure_ids'] = [x.get('evidence_id') for x in resolutions if x.get('decision') == 'confirm']
    validate_evidence_verdict(result, page_count, item, local, modes)
    result['registered_evidence'] = registered
    return result


def validate_consistency(result, candidate_evidence, candidates=None, criterion=None):
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
        if candidates is not None:
            quote = issue.get('rule_quote')
            if not isinstance(quote, str) or not quote.strip() or quote not in criterion:
                raise ValueError('Conflict must cite the actual criterion')
            changes = issue.get('expected_answers')
            if not isinstance(changes, dict) or not changes or set(changes) - set(refs):
                raise ValueError('Conflict must identify affected candidates and corrective answers')
            for cid, answer in changes.items():
                if answer not in {'yes', 'no'} or answer == candidates[cid]['verdict']['answer']:
                    raise ValueError('Rationale differences without an answer change are advisory only')
            observed = issue.get('observed_answers')
            if observed != {cid: candidates[cid]['verdict']['answer'] for cid in refs}:
                raise ValueError('Conflict misstates supplied verdicts')
            quotes = issue.get('verdict_quotes')
            if not isinstance(quotes, dict) or set(quotes) != set(refs):
                raise ValueError('Conflict must quote the affected reasoning')
            for cid, text in quotes.items():
                if not isinstance(text, str) or not text.strip() or text not in candidates[cid]['verdict']['evidence']:
                    raise ValueError('Conflict quote is not supplied reasoning')


def unknown(item_id, reason):
    return {'rubric_id': item_id, 'answer': 'unknown', 'pages': [], 'evidence': reason,
            'evidence_ids': [], 'failure_ids': [], 'resolutions': []}


def judgment_evidence(row):
    """Keep invalid extraction details in artifacts, out of the judge's math input."""
    result = copy.deepcopy(row)
    checks = row.get('arithmetic_checks', [])
    result['calculations'] = [c for c, check in zip(row.get('calculations', []), checks)
                              if check.get('status') == 'computed']
    return result


def criterion_ledger(ledger, item, modes):
    """Keep page-observer visual descriptions as diagnostics, not judge premises.

    Visual criteria receive all original images and separate neutral crop reads.
    Previous direct-judge registrations survive consistency rejudgment unchanged.
    No entries in the stored evidence ledger are edited or silently relabeled.
    """
    visual = any(m['id'] in VISUAL_MODES and m['owner'] == item['id'] for m in modes.values())
    return [r for r in ledger if not (visual and r['kind'] == 'visual'
            and r.get('failure_mode_origin') not in {'direct_judge_observation', 'image_region'})
            and not (not visual and r.get('failure_mode_origin') == 'image_region')]


def duplicate_confirmations(verdicts, ledger):
    """Detect the same exact visible anchor newly charged by different criteria."""
    anchors = {}
    for rid, verdict in verdicts.items():
        rows = {r['id']: r for r in ledger}
        rows.update({r['id']: r for r in verdict.get('registered_evidence', [])})
        for eid in verdict.get('failure_ids', []):
            row = rows[eid]
            key = (row['page'], re.sub(r'\s+', '', row['quote']), re.sub(r'\s+', '', row['location']))
            anchors.setdefault(key, set()).add(rid)
    return [{'page': key[0], 'quote': key[1], 'location': key[2], 'rubric_ids': sorted(owners)}
            for key, owners in anchors.items() if len(owners) > 1]


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
    geometry = {cid: rendered_text_geometry(deck) for cid, deck in decks.items()}
    for cid, deck in decks.items():
        # Independent image observations precede and never receive ledger claims.
        inspections = collect_visual_inspections(deck, geometry[cid], client, config) if any(
            m['id'] == 'visual_readability' and m['owner'] in {i['id'] for i in items}
            for m in rubric['failure_modes']) else []
        evidence[cid] = collect_evidence(deck, rubric, sources, client, config)
        evidence[cid]['ledger'].extend(image_region_evidence(deck, geometry[cid], config))
        evidence[cid]['visual_inspections'] = inspections
        evidence[cid]['page_visual_claims_policy'] = 'diagnostic_only_for_visual_criteria'
        write_json(output / f'{cid}-evidence.json', evidence[cid])

    def judge(cid, item, feedback=None):
        deck = decks[cid]; record = evidence[cid]
        if not record['complete']:
            return unknown(item['id'], 'Evidence collection incomplete')
        combined = {r['id']: r for r in record['ledger']}
        previous = verdicts.get(cid, {}).get(item['id'], {}).get('registered_evidence', []) if feedback is not None else []
        combined.update({r['id']: r for r in previous})
        ledger = criterion_ledger(list(combined.values()), item, modes)
        owned = [m for m in rubric['failure_modes'] if m['owner'] == item['id']]
        inspections = record.get('visual_inspections', []) if any(m['id'] == 'visual_readability' for m in owned) else []
        prepare = partial(prepare_evidence_verdict, page_count=len(deck['slides']), item=item,
                          ledger=ledger, modes=modes, sources=sources, visual_inspections=inspections)
        payload = {'sources': sources, 'ledger': [judgment_evidence(r) for r in ledger], 'criterion': item['judge_prompt'],
                   'scope': item['scope'], 'owned_failure_modes': owned,
                   'required_resolution_ids': [r['id'] for r in ledger
                        if r['failure_mode'] is not None and modes[r['failure_mode']]['owner'] == item['id']]}
        images = [(f"Slide {s['page']}", s['image']) for s in deck['slides']]
        if any(m['id'] in VISUAL_MODES for m in owned):
            payload['rendered_text_geometry'] = geometry[cid]
            images += focus_images(deck, geometry[cid], config)
            payload['visual_inspections'] = [{k: v for k, v in r.items() if k != 'image'} for r in inspections]
            payload['required_inspection_ids'] = [r['id'] for r in inspections if r['status'] == 'observed']
            payload['inspection_resolution_contract'] = prompts.VISUAL_INSPECTION_CONTRACT
        if feedback is not None: payload['consistency_feedback'] = feedback
        try:
            result = client.ask('judge', prompts.EVIDENCE_JUDGE, payload,
                images, validate=prepare)
            result = prepare(result)
            additions = {r['id']: r for r in previous}
            additions.update({r['id']: r for r in result['registered_evidence']})
            result['registered_evidence'] = list(additions.values())
            return {'rubric_id': item['id'], **{k: result[k] for k in (
                'answer', 'pages', 'evidence', 'evidence_ids', 'failure_ids', 'resolutions', 'registered_evidence',
                'inspection_resolutions')}}
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
                combined = {r['id']: r for r in evidence[cid]['ledger']}
                combined.update({r['id']: r for r in v.get('registered_evidence', [])})
                combined = {r['id']: r for r in criterion_ledger(list(combined.values()), item, modes)}
                # Only evidence and same-criterion verdict: no other scores or labels.
                selected = set(v.get('evidence_ids', []))
                selected.update(r['id'] for r in combined.values() if r['failure_mode'] is not None
                                and modes[r['failure_mode']]['owner'] == rid)
                candidates[cid] = {'verdict': {k: v[k] for k in (
                    'answer', 'pages', 'evidence', 'evidence_ids', 'failure_ids', 'resolutions')}, 'evidence': [judgment_evidence(r) for r in combined.values() if r['id'] in selected]}
            check = partial(validate_consistency, candidate_evidence={
                c: {r['id'] for r in d['evidence']} for c, d in candidates.items()},
                candidates=candidates, criterion=item['judge_prompt'])
            result = client.ask('reviewer', prompts.CONSISTENCY_REVIEW,
                {'sources': sources, 'criterion': item['judge_prompt'], 'scope': item['scope'],
                 'owned_failure_modes': [m for m in rubric['failure_modes'] if m['owner'] == rid],
                 'candidates': candidates}, validate=check)
            check(result)
            return result
        affected = set()
        if any(verdicts[c][rid]['answer'] == 'unknown' for c in decks):
            rounds.append({'status': 'skipped', 'reason': 'Independent verdict incomplete',
                           'affected_candidates': [c for c in decks if verdicts[c][rid]['answer'] == 'unknown']})
            history[rid] = rounds
            write_json(output / 'consistency.json', history)
            continue
        try:
            first = review(); rounds.append({'review': first})
            if not first['consistent']:
                affected = {cid for issue in first['issues'] for cid in issue['expected_answers']}
                for cid in sorted(affected):
                    v = judge(cid, item, first['issues']); verdicts[cid][rid] = v
                    rounds.append({'candidate': cid, 'rejudgment': v})
                if any(verdicts[c][rid]['answer'] == 'unknown' for c in affected):
                    raise ValueError('Rejudgment unresolved')
                second = review(); rounds.append({'review': second})
                if not second['consistent']:
                    affected = {cid for issue in second['issues'] for cid in issue['expected_answers']}
                    raise ValueError('Consistency conflict persists after one repair')
        except Exception as e:
            # A failed auxiliary reviewer is not contrary evidence. A validated
            # unresolved challenge affects only its explicitly targeted candidates.
            rounds.append({'status': 'unresolved' if affected else 'unavailable',
                           'unresolved': type(e).__name__, 'reason': str(e),
                           'affected_candidates': sorted(affected)})
            for cid in affected:
                verdicts[cid][rid] = unknown(rid, 'Cross-candidate consistency not established')
        history[rid] = rounds
        write_json(output / 'consistency.json', history)
    reports = {}
    consistency_summary = {key: sum(bool(v and v[-1].get('status') == key) for v in history.values())
                           for key in ('skipped', 'unavailable', 'unresolved')}
    consistency_summary['passed'] = sum(bool(v and v[-1].get('review', {}).get('consistent'))
                                         for v in history.values())
    consistency_summary['status'] = ('passed' if consistency_summary['passed'] == len(rubric['items'])
                                      else 'incomplete')
    for cid, deck in decks.items():
        ownership_conflicts = duplicate_confirmations(verdicts[cid], evidence[cid]['ledger'])
        for conflict in ownership_conflicts:
            for rid in conflict['rubric_ids']:
                verdicts[cid][rid] = unknown(rid, 'Same visible defect confirmed by multiple criteria; ownership unresolved')
        ordered = [verdicts[cid][i['id']] for i in items]
        report = {'case_id': case['id'], 'candidate_id': cid, 'candidate_hash': deck['identity']['sha256'],
                  'rubric_hash': rubric['rubric_hash'], 'judge_model': config['judge_model'],
                  'config_hash': digest(config), 'judge_validator_version': JUDGE_VALIDATOR_VERSION,
                  'evaluation_protocol': PROTOCOL,
                  'ownership_conflicts': ownership_conflicts,
                  'consistency_summary': consistency_summary,
                  'judge_prompt_fingerprint': digest([prompts.VISUAL_INSPECTION, prompts.VISUAL_INSPECTION_CONTRACT, prompts.EVIDENCE_PAGE,
                                                     prompts.EVIDENCE_JUDGE, prompts.CONSISTENCY_REVIEW]),
                  'summary': aggregate(rubric, ordered), 'verdicts': ordered}
        write_json(output / f'{cid}.json', report); reports[cid] = report
    return reports
