"""Summarize immutable single-case pilot reports, without changing verdicts."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ppt_rubric.common import read_json, write_json
from ppt_rubric.evaluation import agreement
from ppt_rubric.operations import run_report

root = Path(__file__).resolve().parent
manifest = read_json('data/manifest.json')
labels = read_json('artifacts/validation-20261010-v6/human-labels-dev.json')
comparison = agreement(manifest, labels, root/'judgments')
write_json(root/'agreement.json', comparison)
counts = {}
for provider, dirs, limit in [('bailian', ['rubrics','rubrics-v2'], 12), ('maas', ['judgments'], 80)]:
    rows = [r for d in dirs for r in read_json(root/d/'requests.json')]
    assert len(rows) <= limit
    counts[provider] = {'actual_requests': len(rows), 'cap': limit,
                        'successes': sum(r['status']=='success' for r in rows),
                        'unsuccessful': sum(r['status']!='success' for r in rows)}
reports = {cid: read_json(root/'judgments/03'/f'{cid}.json') for cid in 'AB'}
old = {cid: read_json(Path('artifacts/validation-20261010-v6/judgments/03')/f'{cid}.json') for cid in 'AB'}
visual = {cid: read_json(root/'judgments/03'/f'{cid}-visual-pages.json') for cid in 'AB'}
checks = {}
for cid, page in [('A',4),('B',3)]:
    record = next(r for r in visual[cid] if r['page']==page)
    checks[f'{cid}_page_{page}'] = record
verdicts = {cid: [v for v in reports[cid]['verdicts'] if v['rubric_id'] in ['R09','R18','R19']] for cid in 'AB'}
rubric = read_json(root/'rubrics-v2/03/rubric.json')
summary = {'scope':'single developmental case 03; not full-dev or held-out agreement',
           'complete':all(r['summary']['status']=='complete' for r in reports.values()),
           'baseline_scores':{c:old[c]['summary']['score'] for c in old},
           'new_scores':{c:reports[c]['summary']['score'] for c in reports},
           'comparison':comparison['cases'], 'budget':counts,
           'rubric_validator':rubric['provenance']['validator_version'],
           'first_model_audit_false_approval_preserved':True,
           'known_math_example_repaired':next(i['judge_prompt'] for i in rubric['items'] if i['id']=='R09'),
           'target_visual_checks':checks, 'target_verdicts':verdicts,
           'raw_scores_manually_adjusted':False, 'holdout_evaluated':False,
           'remaining_risk':'Page evidence is still model-generated; R18/R19 may penalize the same defect twice.'}
write_json(root/'validation-summary.json', summary)
write_json(root/'judge-report.json', run_report(root/'judgments'))
scope = read_json(root/'scope.json');scope.update(status='completed',actual_usage=counts,unused_caps_are_not_future_authorization=True)
write_json(root/'scope.json',scope)
print({k:summary[k] for k in ['complete','baseline_scores','new_scores','budget']})
print([(r['case_id'],r['human'],r['predicted'],r['match']) for r in comparison['cases']])
