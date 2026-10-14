"""Offline capacity planning and machine-readable run inspection; no model calls."""
from __future__ import annotations

from collections import Counter
import copy
import math
from pathlib import Path

from .common import digest, file_digest, read_json, write_json, write_jsonl
from .render import case_sources, extract_pptx
from .rubric import VALIDATOR_VERSION, validate_rubric, assign_scores


def plan_run(cases, config, operation='generate', rubrics='artifacts/rubrics'):
    rows = []
    for case in cases:
        row = {'case_id':case['id'], 'split':case['split'], 'errors':[]}
        try:
            sources = case_sources(case, config)
            row['source_characters'] = sum(len(s['text']) for s in sources)
            pages = {}
            for candidate in case['candidates']:
                if file_digest(candidate['pptx']) != candidate['sha256']:
                    raise ValueError('Candidate hash differs from frozen manifest')
                slides = extract_pptx(candidate['pptx'])
                if not slides or any(s['hidden'] for s in slides):
                    raise ValueError('Empty/hidden slides require an explicit policy')
                if len(slides) > config['max_images_per_request']:
                    raise ValueError('Candidate exceeds per-request image limit')
                pages[candidate['id']] = len(slides)
            if len(pages) < 2:
                raise ValueError('At least two uniquely identified candidates required')
            row['candidate_pages'] = pages
            count = len(pages)
            if operation == 'generate':
                # requirements, baseline, N observations, synthesis, review;
                # counterexample check; each repair can add revision + 2 reviews.
                first, maximum = count + 5, count + 5 + 3 * config['max_revisions']
                # An optional source-only refinement replaces the requirements
                # call and rebuilds the suite once; it can never loop unboundedly.
                maximum *= 1 + config.get('max_source_refinements', 0)
                row['max_source_refinements'] = config.get('max_source_refinements', 0)
                images = sum(pages.values())
            elif operation == 'audit':
                read_json(Path(rubrics) / case['id'] / 'rubric.json')
                first, maximum = 2, 2 + 3 * config['max_revisions']
                images = 0
            else:
                directory = Path(rubrics) / case['id']
                rubric = read_json(directory / 'rubric.json')
                problems = inspect_suite(rubric, read_json(directory / 'requirements.json'))
                if problems:
                    raise ValueError('; '.join(problems))
                if rubric['case_id'] != case['id'] or rubric['provenance']['source_hash'] != digest(sources):
                    raise ValueError('Rubric and task sources differ')
                first = maximum = count * len(rubric['items'])
                row['rubric_items'] = len(rubric['items'])
                images = sum(pages.values()) * len(rubric['items'])
            row.update(first_pass_logical_calls=first, max_logical_calls=maximum,
                       max_http_attempts=maximum * config['max_retries'],
                       first_pass_image_presentations=images)
        except (OSError, ValueError, KeyError, TypeError) as e:
            row['errors'].append(f'{type(e).__name__}: {e}')
        rows.append(row)
    fields = ['first_pass_logical_calls','max_logical_calls','max_http_attempts','first_pass_image_presentations']
    return {'operation':operation, 'offline':True, 'cases':rows,
            'ready_cases':sum(not row['errors'] for row in rows), 'total_cases':len(rows),
            'totals':{key:sum(row.get(key,0) for row in rows if not row['errors']) for key in fields},
            'configured_request_cap':config.get('max_api_requests'),
            'token_estimate':None, 'cost_estimate':None,
            'assumptions':['Cold cache; completed cache hits reduce calls. Early failures can stop sooner.',
                           'Image presentations count page images across requests, not billed tokens.',
                           'max_retries is the total allowed HTTP attempts per logical call.',
                           'No credentials read, PPT rendering, or API calls; estimates confer no bulk authorization.',
                           'Token/cost estimates need measured provider usage and verified pricing.']}


def inspect_suite(rubric, requirements):
    errors = validate_rubric(rubric, requirements)
    if rubric.get('status') != 'approved':
        errors.append('Suite is not approved')
    if rubric.get('provenance', {}).get('validator_version') != VALIDATOR_VERSION:
        errors.append('Suite was approved under an obsolete validator')
    if rubric.get('rubric_hash') != digest({k:v for k,v in rubric.items() if k != 'rubric_hash'}):
        errors.append('Suite content differs from its approved hash')
    if not errors:
        try:
            expected = assign_scores(copy.deepcopy(rubric),rubric['dimension_weights'])
            if any(not math.isclose(a['score'],b['score'],abs_tol=1e-8) or a['dimension']!=b['dimension']
                   for a,b in zip(rubric['items'],expected['items'])):
                errors.append('Published scores differ from the configured weighting rule')
        except (KeyError,TypeError,ValueError):
            errors.append('Published scoring metadata is invalid')
    return errors


def export_suites(cases, config, rubrics, output, skip_unapproved=False):
    rows, failures = [], []
    for case in cases:
        directory=Path(rubrics)/case['id']
        try:
            suite=read_json(directory/'rubric.json')
            errors=inspect_suite(suite,read_json(directory/'requirements.json'))
            if errors:
                raise ValueError('; '.join(errors))
            if suite['case_id']!=case['id'] or suite['provenance']['source_hash']!=digest(case_sources(case,config)):
                raise ValueError('Rubric source/case mismatch')
            rows.append({'case_id':case['id'],'rubric_hash':suite['rubric_hash'],
                         'rubrics':[{'judge_prompt':i['judge_prompt'],'score':i['score']} for i in suite['items']]})
        except (OSError,ValueError,KeyError,TypeError) as e:
            failures.append({'case_id':case['id'],'reason':f'{type(e).__name__}: {e}'})
    report={'requested':len(cases),'eligible':len(rows),'omitted':failures,
            'written':bool(rows) and (not failures or skip_unapproved)}
    write_json(str(output)+'.report.json',report)
    if not report['written']:
        raise ValueError('Export not written: some cases are unapproved, or no eligible suites; inspect export report')
    write_jsonl(output,rows)
    return report


def run_report(directory):
    root = Path(directory)
    summary = read_json(root / 'run-summary.json')
    rows = []
    for cid, status in sorted(summary.items()):
        row = {'case_id':cid, **status}
        suite_path = root / cid / 'rubric.json'
        if status.get('status') == 'approved':
            try:
                suite = read_json(suite_path)
                errors = inspect_suite(suite, read_json(root / cid / 'requirements.json'))
                if errors:
                    row.update(status='invalid_approval', errors=errors)
                else:
                    row.update(items=len(suite['items']), score_sum=sum(i['score'] for i in suite['items']))
            except (OSError, ValueError, KeyError, TypeError) as e:
                row.update(status='invalid_approval', errors=[f'{type(e).__name__}: {e}'])
        if not row.get('status'):
            # Judge runs store one complete/incomplete summary per candidate.
            row['status'] = 'complete' if status and all(v.get('status')=='complete' for v in status.values()) else 'incomplete'
        history = root / cid / 'reviews.json'
        if history.exists():
            reviews = read_json(history)
            row['review_rounds'] = len(reviews)
            row['semantic_reviews'] = sum(not h['review'].get('skipped',False) for h in reviews)
            row['counterexample_reviews'] = sum(h.get('counterexample_review') is not None for h in reviews)
        rows.append(row)
    calls = read_json(root / 'usage.json') if (root / 'usage.json').exists() else []
    requests_path = root / 'requests.json'
    # New journals include failed/retried responses too. Legacy logs only
    # recorded successful logical calls; report that limitation explicitly.
    attempts = read_json(requests_path) if requests_path.exists() else None
    live = attempts if attempts is not None else [c for c in calls if not c.get('cached')]
    models = {}
    for request in live:
        group = models.setdefault(request['model'], {'http_attempts':0, 'failed_attempts':0,
                                  'reported_prompt_tokens':0, 'reported_completion_tokens':0,
                                  'reported_total_tokens':0, 'usage_missing_attempts':0})
        group['http_attempts'] += 1 if attempts is not None else request.get('attempts',1)
        group['failed_attempts'] += request.get('status')=='failed'
        usage = request.get('usage', {})
        if not usage:
            group['usage_missing_attempts'] += 1
        for key in ['prompt_tokens','completion_tokens','total_tokens']:
            if isinstance(usage.get(key), (int,float)):
                group['reported_'+key] += usage[key]
    counts = dict(Counter(row['status'] for row in rows))
    return {'cases':rows, 'status_counts':counts, 'total_cases':len(rows),
            'approved_fraction':counts.get('approved',0)/len(rows) if rows else None,
            'logical_cache_hits':sum(bool(c.get('cached')) for c in calls), 'models':models,
            'attempt_journal_available':attempts is not None,
            'usage_scope':'Cumulative journals for this output directory; includes resumed invocations.',
            'usage_caveat':None if attempts is not None else 'Legacy log: failed-call usage may be missing.',
            'failed_or_incomplete_cases':[r['case_id'] for r in rows if r['status'] not in {'approved','complete'}],
            'human_agreement':None, 'acceptance':'Automated operational report only; no human alignment claim.'}
