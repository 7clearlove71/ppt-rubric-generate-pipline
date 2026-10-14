from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from functools import partial
import shutil

from . import prompts
from .common import digest, read_json, write_json, validate_operation_profile
from .render import case_sources, prepare_case
from .rubric import VALIDATOR_VERSION, aggregate, assign_scores, validate_requirements, validate_rubric, validate_rubric_shape, normalize_single_source_duplicates
from .rubric import validate_requirement_refinement
from .judging import JUDGE_VALIDATOR_VERSION, validate_judge_verdict, validate_visual_page


def deck_payload(deck):
    return [{"page": s['page'], "extracted_text": s['text'], "rendered_text": s['rendered_text']}
            for s in deck['slides']]


def deck_images(deck):
    return [(f"幻灯片 {s['page']}", s['image']) for s in deck['slides']]


def inspect_visual_pages(deck, items, sources, client):
    """One image per call, all selected visual criteria; cached by the client.

    Infrastructure failures remain unknown, never inferred candidate defects.
    Final judge may dispute observations with explicit localized evidence.
    """
    records = []
    criteria = [{k: item[k] for k in ('id', 'scope', 'judge_prompt')} for item in items]
    for slide in deck['slides']:
        check = partial(validate_visual_page, page=slide['page'], rubric_ids=[i['id'] for i in items])
        try:
            result = client.ask('judge', prompts.VISUAL_PAGE,
                                {'sources': sources, 'page': slide['page'], 'visual_rubrics': criteria},
                                [(f"幻灯片 {slide['page']}", slide['image'])], validate=check)
            check(result)
        except Exception as e:
            result = {'page': slide['page'], 'infrastructure_error': type(e).__name__,
                      'checks': [{'rubric_id': i['id'], 'status': 'unknown',
                                  'evidence': 'Page inspection unavailable; no candidate defect inferred.'} for i in items]}
        records.append(result)
    return records


def prompt_fingerprint():
    return digest({k:v for k,v in vars(prompts).items() if k.isupper() and isinstance(v,str)})


def protocol_prompt(base, context):
    return base + (prompts.OWNERSHIP_POLICY if context.get('evaluation_protocol') == 'evidence_v1' else '')


def provenance(case, config, sources, decks):
    # A request safety budget changes permission/operation, not rubric semantics.
    semantic_config = {k: v for k, v in config.items() if k != 'max_api_requests'}
    return {"case_id": case['id'], "prompt_version": prompts.VERSION,
            "prompt_fingerprint":prompt_fingerprint(),
            "validator_version": VALIDATOR_VERSION,
            "config_hash": digest(semantic_config), "source_hash": digest(sources),
            "candidates": {cid: d['identity'] for cid, d in decks.items()}}


def audit_review(review, requirements, rubric):
    if type(review.get('approved')) is not bool or not isinstance(review.get('issues'), list):
        raise ValueError('Invalid model review response')
    for issue in review['issues']:
        if issue.get('severity') not in {'error', 'warning'} or not issue.get('reason'):
            raise ValueError('Malformed review issue')
        valid_ids = {i['id'] for i in rubric.get('items', [])} | {g['id'] for g in rubric.get('groups', [])}
        if (not isinstance(issue.get('item_ids'), list) or not issue['item_ids']
                or any(r not in valid_ids for r in issue['item_ids']) or not issue.get('fix')):
            raise ValueError('Review issue must identify actual rubric items/groups and a fix')
    if review['approved'] and any(i['severity'] == 'error' for i in review['issues']):
        raise ValueError('Contradictory model review')
    # The graph of declared source references is deterministic. The reviewer
    # still assesses substantive coverage, but need not transcribe this graph.
    coverage = {q['id']: [i['id'] for i in rubric.get('items', [])
                         if q['id'] in i.get('source_refs', [])]
                for q in requirements['requirements']}
    if 'coverage' in review and 'reported_coverage' not in review:
        review['reported_coverage'] = review['coverage']
    review['coverage'] = coverage
    for q in requirements['requirements']:
        if not coverage[q['id']] and review['approved']:
            raise ValueError('Approved rubric has incomplete source coverage')


def validate_observations(result, pages=None, refs=None):
    if not isinstance(result.get('observations'), list) or not isinstance(result.get('render_concerns'), list):
        raise ValueError('Malformed candidate observations')
    for observation in result['observations']:
        if (not isinstance(observation,dict) or not observation.get('finding')
                or not observation.get('suggested_general_criterion') or observation.get('basis') not in {'explicit','quality'}):
            raise ValueError('Malformed candidate observation fields')
        cited = observation.get('pages')
        if not isinstance(cited,list) or not cited or any(type(p) is not int or p<1 or (pages is not None and p>pages) for p in cited):
            raise ValueError('Candidate observation cites invalid pages')
        linked = observation.get('requirement_refs')
        if not isinstance(linked,list) or any(not isinstance(r,str) or (refs is not None and r not in refs) for r in linked):
            raise ValueError('Candidate observation cites unknown requirements')


def generate(case, config, client, cache_root, output):
    """Finite automated repair loop. Only an approved suite is published as rubric.json."""
    validate_operation_profile(config, 'generate')
    output = Path(output) / case['id']
    output.mkdir(parents=True, exist_ok=True)
    source_limit = config.get('max_source_refinements', 0)
    if type(source_limit) is not int or source_limit not in {0, 1}:
        raise ValueError('max_source_refinements must be 0 or 1')
    def progress(stage):
        write_json(output / 'status.json', {'status':'running','stage':stage})
        print(f"[{case['id']}] {stage}", flush=True)
    progress('prepare sources and slide renders')
    sources = case_sources(case, config)
    decks = prepare_case(case, config, cache_root)
    meta = provenance(case, config, sources, decks)
    signature = digest(meta)
    published = output / 'rubric.json'
    if published.exists():
        old = read_json(published)
        intact = old.get('rubric_hash') == digest({k: v for k, v in old.items() if k != 'rubric_hash'})
        if old.get('signature') == signature and old.get('status') == 'approved' and intact:
            write_json(output / 'status.json',{'status':'approved','signature':signature,'items':len(old['items']),'resumed':True})
            return old
        # Do not leave a stale approved file after a failed new generation.
        published.rename(output / f"superseded-{old.get('signature', 'unknown')[:12]}.json")
    write_json(output / 'status.json', {'status': 'running', 'signature': signature})
    progress('extract source-grounded requirements')
    requirements = client.ask('generator', prompts.REQUIREMENTS, {'sources': sources},
                              validate=lambda r: validate_requirements(r, sources))
    validate_requirements(requirements, sources)
    write_json(output / 'requirements.json', requirements)
    result = _generate_from_requirements(case, config, client, output, sources, decks, requirements, meta, progress)
    if result['status'] != 'quality_failed' or not source_limit:
        return result
    history = read_json(output / 'reviews.json')
    errors = history[-1]['structural_errors']
    targets = [q['id'] for q in requirements['requirements']
               if any(e.startswith(q['id'] + ': duplicate explicit scoring') for e in errors)]
    if not targets:
        return result
    # Preserve the failed pass before the single, source-only refinement attempt.
    archive = output / 'source-pass-0'
    archive.mkdir(exist_ok=True)
    for p in output.glob('*.json'):
        shutil.copy2(p, archive / p.name)
    progress('refine composite source requirements once')
    refined = client.ask('generator', prompts.REFINE_REQUIREMENTS,
                         {'sources':sources, 'requirements':requirements, 'refinement_targets':targets},
                         validate=lambda r:validate_requirement_refinement(r, requirements, sources))
    validate_requirement_refinement(refined, requirements, sources)
    write_json(output / 'source-refinement.json', {'round':1, 'targets':targets,
               'original_hash':digest(requirements), 'refined_hash':digest(refined),
               'refinement_map':refined['refinement_map'], 'trigger_errors':errors})
    write_json(output / 'requirements.json', refined)
    return _generate_from_requirements(case, config, client, output, sources, decks, refined, meta, progress,
                                      reviser_role=config.get('source_refinement_reviser_role', 'generator'))


def _generate_from_requirements(case, config, client, output, sources, decks, requirements, meta, progress,
                                reviser_role='generator'):
    context = {'sources': sources, 'requirements': requirements, 'dimension_weights': config['dimension_weights'],
               'evaluation_protocol': config.get('evaluation_protocol', 'legacy')}
    progress('generate candidate-independent baseline')
    baseline = client.ask('generator', protocol_prompt(prompts.BASELINE, context), context, validate=validate_rubric_shape)
    write_json(output / 'baseline.json', baseline)
    observations = {}
    progress(f'observe {len(decks)} anonymous candidates')
    with ThreadPoolExecutor(max_workers=config['api_concurrency']) as pool:
        requirement_ids = {q['id'] for q in requirements['requirements']}
        futures = {pool.submit(client.ask, 'vision', prompts.OBSERVE,
                               {**context, 'baseline': baseline, 'slides': deck_payload(deck)},
                               deck_images(deck), validate=partial(validate_observations,pages=len(deck['slides']),refs=requirement_ids)):
                   cid for cid, deck in decks.items()}
        for future in as_completed(futures):
            result = future.result()
            validate_observations(result)
            observations[futures[future]] = result
    observations = {cid: observations[cid] for cid in sorted(observations)}
    write_json(output / 'observations.json', observations)
    # An overlap or unreadable label can be a real candidate defect. Model concerns
    # are diagnostics, not proof that conversion failed; never exclude poor decks.
    # Conversion failures and page mismatches already raise in prepare_case.
    progress('synthesize one shared rubric')
    rubric = client.ask('generator', protocol_prompt(prompts.SYNTHESIZE, context), {**context, 'baseline': baseline,
                                                       'anonymous_observations': list(observations.values())}, validate=validate_rubric_shape)
    return review_and_repair(case, config, client, output, context, rubric, meta, reviser_role=reviser_role)


def review_and_repair(case, config, client, output, context, rubric, meta, reviser_role='generator'):
    validate_operation_profile(config, 'audit')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    signature = digest(meta)
    published = output / 'rubric.json'
    context = {**context, 'evaluation_protocol': config.get('evaluation_protocol', 'legacy')}
    requirements = context['requirements']
    def progress(stage):
        write_json(output / 'status.json', {'status':'running','stage':stage,'signature':signature})
        print(f"[{case['id']}] {stage}", flush=True)
    # Re-auditing a previously published suite must not leave stale approval behind.
    if published.exists():
        old = read_json(published)
        published.rename(output / f"superseded-{old.get('rubric_hash', 'unknown')[:12]}.json")
    history = []
    for revision in range(config['max_revisions'] + 1):
        progress(f'automated review {revision + 1}/{config["max_revisions"] + 1}')
        if config.get('evaluation_protocol', 'legacy') == 'evidence_v1':
            rubric['evaluation_protocol'] = 'evidence_v1'
        rubric, normalizations = normalize_single_source_duplicates(rubric)
        if config.get('evaluation_protocol') == 'evidence_v1':
            from .evidence_protocol import compile_ownership_boundaries
            rubric = compile_ownership_boundaries(rubric)
        # Persist the current candidate even when a reviewer call fails below.
        write_json(output / f'draft-{revision}.json', rubric)
        structural_errors = validate_rubric(rubric, requirements)
        # Keep the semantic reviewer blind to candidate observations and outcomes.
        if structural_errors:
            # A structurally invalid suite cannot pass. Repair it without paying
            # for a semantic reviewer whose verdict cannot affect publication.
            review = {'approved':False, 'issues':[], 'coverage':{},
                      'summary':'Semantic review skipped until structural errors are fixed', 'skipped':True}
        else:
            review = client.ask('reviewer', protocol_prompt(prompts.REVIEW, context), {**context, 'rubric': rubric,
                                'structural_errors': [], 'normalizations_to_verify': normalizations},
                                validate=lambda r: audit_review(r, requirements, rubric))
            audit_review(review, requirements, rubric)
        counterexample = None
        if not structural_errors and review['approved']:
            counterexample = client.ask('reviewer', protocol_prompt(prompts.COUNTEREXAMPLE_REVIEW, context),
                                        {**context, 'rubric':rubric},
                                        validate=lambda r:audit_review(r, {'requirements':[]}, rubric))
            audit_review(counterexample, {'requirements':[]}, rubric)
            if not counterexample['approved']:
                review = {**review, 'approved':False,
                          'issues':review['issues'] + counterexample['issues'],
                          'summary':counterexample.get('summary','Counterexample audit failed')}
        history.append({'revision': revision, 'structural_errors': structural_errors, 'normalizations':normalizations,
                        'review': review, 'counterexample_review':counterexample})
        write_json(output / f'draft-{revision}.json', rubric)
        write_json(output / 'reviews.json', history)
        if not structural_errors and review['approved']:
            rubric = assign_scores(rubric, config['dimension_weights'])
            rubric.update({'schema_version': 1, 'status': 'approved', 'case_id': case['id'],
                           'signature': signature, 'provenance': meta, 'dimension_weights': config['dimension_weights']})
            rubric.pop('rubric_hash', None)
            rubric['rubric_hash'] = digest(rubric)
            write_json(published, rubric)
            write_json(output / 'status.json', {'status': 'approved', 'signature': signature,
                                               'items': len(rubric['items']), 'revisions': revision})
            return rubric
        if revision < config['max_revisions']:
            progress('revise from automated feedback')
            rubric = client.ask(reviser_role, protocol_prompt(prompts.REVISE, context), {**context, 'rubric': rubric,
                                 'review': review, 'structural_errors': structural_errors}, validate=validate_rubric_shape)
    write_json(output / 'status.json', {'status': 'quality_failed', 'signature': signature,
                                       'reason': 'Automated review did not pass within the revision budget'})
    return {'status': 'quality_failed', 'case_id': case['id']}


def evaluate(case, rubric, config, client, cache_root, output, limit=None):
    validate_operation_profile(config, 'judge')
    if rubric.get('status') != 'approved':
        raise ValueError('Only approved rubric suites may be evaluated')
    if rubric.get('provenance', {}).get('validator_version') != VALIDATOR_VERSION:
        raise ValueError('Rubric requires re-audit under the current validation policy')
    expected = rubric.get('rubric_hash')
    if expected != digest({k: v for k, v in rubric.items() if k != 'rubric_hash'}):
        raise ValueError('Rubric changed after approval')
    sources = case_sources(case, config)
    if rubric.get('case_id') != case['id'] or rubric['provenance']['source_hash'] != digest(sources):
        raise ValueError('Rubric does not match this task/source content')
    decks = prepare_case(case, config, cache_root)
    if rubric.get('evaluation_protocol', 'legacy') != config.get('evaluation_protocol', 'legacy'):
        raise ValueError('Rubric and configured evaluation protocol differ; re-audit first')
    if config.get('evaluation_protocol') == 'evidence_v1':
        from .evidence_protocol import evaluate_evidence
        return evaluate_evidence(case, rubric, config, client, decks, sources, output, limit)
    items = rubric['items'][:limit] if limit is not None else rubric['items']
    output = Path(output) / case['id']
    output.mkdir(parents=True, exist_ok=True)
    reports = {}
    for cid, deck in decks.items():
        verdicts = []
        visual_items = [i for i in items if i['dimension'] == 'visual']
        visual_pages = inspect_visual_pages(deck, visual_items, sources, client) if visual_items else []
        write_json(output / f'{cid}-visual-pages.json', visual_pages)
        def judge(item):
            payload = {'sources': sources, 'slides': deck_payload(deck)}
            checks = None
            if item['dimension'] == 'visual':
                checks = [{'page': r['page'], **c} for r in visual_pages
                          for c in r['checks'] if c['rubric_id'] == item['id']]
                # Failed page calls cannot be silently treated as completed inspection.
                if any(r.get('infrastructure_error') for r in visual_pages):
                    return {'rubric_id': item['id'], 'answer': 'unknown', 'pages': [],
                            'evidence': 'Page visual inspection incomplete; retry with the successful cache retained.'}
                payload['page_visual_checks'] = checks
            validate_verdict = partial(validate_judge_verdict, page_count=len(deck['slides']), visual_checks=checks)
            criterion = {'rubric': {'id': item['id'], 'judge_prompt': item['judge_prompt'], 'scope': item['scope']}}
            if checks is not None:
                criterion['visual_resolution_contract'] = {
                    'required_pages': [c['page'] for c in checks if c['status'] in {'fail', 'unknown'}],
                    'rule': 'visual_resolutions must contain exactly one entry for each required page, '
                            'and no other pages. If required_pages is empty, return visual_resolutions: []. '
                            'Do not copy the example page number from the response template.'}
            result = client.ask('judge', prompts.JUDGE, payload, deck_images(deck), suffix=criterion,
                                validate=validate_verdict)
            validate_verdict(result)
            return {'rubric_id': item['id'], **result}
        with ThreadPoolExecutor(max_workers=config['api_concurrency']) as pool:
            futures = {pool.submit(judge, i): i for i in items}
            for future in as_completed(futures):
                try:
                    verdicts.append(future.result())
                except Exception as e:
                    verdicts.append({'rubric_id': futures[future]['id'], 'answer': 'unknown',
                                     'pages': [], 'evidence': f'Evaluation infrastructure error: {type(e).__name__}'})
                write_json(output / f'{cid}-partial.json', verdicts)
        order = {i['id']: n for n, i in enumerate(rubric['items'])}
        verdicts.sort(key=lambda v: order[v['rubric_id']])
        result = {'case_id': case['id'], 'candidate_id': cid, 'candidate_hash': deck['identity']['sha256'],
                  'rubric_hash': rubric['rubric_hash'], 'judge_model': config['judge_model'],
                  'config_hash': digest(config), 'judge_validator_version': JUDGE_VALIDATOR_VERSION,
                  'judge_prompt_fingerprint': digest({'judge': prompts.JUDGE, 'visual_page': prompts.VISUAL_PAGE}),
                  'summary': aggregate(rubric, verdicts), 'verdicts': verdicts}
        write_json(output / f'{cid}.json', result)
        reports[cid] = result
    return reports
