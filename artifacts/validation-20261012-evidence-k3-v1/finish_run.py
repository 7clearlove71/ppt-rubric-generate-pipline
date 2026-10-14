"""Score approved suites within the confirmed shared budget, then compare labels."""
from pathlib import Path
import collections
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent.parent
sys.path.insert(0, str(PROJECT))
from ppt_rubric.common import read_json, write_json, file_digest
from ppt_rubric.operations import plan_run
from ppt_rubric.evaluation import agreement


def budget():
    result = {}
    for provider, stage, cap in [('bailian', 'rubrics', 120), ('maas', 'judgments', 900)]:
        path = ROOT / stage / 'requests.json'
        rows = read_json(path) if path.exists() else []
        result[provider] = {
            'authorized_cap': cap, 'actual_requests': len(rows), 'remaining_requests': cap-len(rows),
            'statuses': dict(collections.Counter(x['status'] for x in rows)),
            'reported_usage': {k: sum(x.get('usage', {}).get(k, 0) for x in rows)
                               for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')},
            'usage_missing_requests': sum(not x.get('usage') for x in rows), 'cost_estimate': None}
        if len(rows) > cap:
            raise RuntimeError(f'{provider} shared budget exceeded')
    write_json(ROOT / 'shared-budget.json', result)
    return result


def main():
    authorization = read_json(ROOT / 'authorization.json')
    if authorization['status'] != 'authorized' and '--summarize-only' not in sys.argv:
        raise RuntimeError('Current authorization is not active')
    snapshot = read_json(ROOT / 'version-snapshot.json')
    changed = [p for p, sha in snapshot['files'].items() if file_digest(PROJECT / p) != sha]
    if changed:
        raise RuntimeError(f'Frozen source/input changed: {changed}')
    manifest = read_json(PROJECT / 'data/manifest.json')
    cases = [c for c in manifest['cases'] if c['id'] in authorization['scope']]
    assert len(cases) == 7 and all(c['split'] == 'dev' for c in cases)
    config = read_json(ROOT / 'judge-config.json')
    plan = plan_run(cases, config, 'judge', ROOT / 'rubrics')
    write_json(ROOT / 'judge-plan.json', plan)
    eligible = [r['case_id'] for r in plan['cases'] if not r['errors']]
    remaining = budget()['maas']['remaining_requests']
    print({'eligible': eligible, 'judge_plan': plan['totals'], 'remaining_maas_requests': remaining}, flush=True)
    if eligible and remaining > 0 and '--summarize-only' not in sys.argv:
        command = [sys.executable, '-m', 'ppt_rubric', 'judge', '--cases', ','.join(eligible),
                   '--config', str(ROOT / 'judge-config.json'), '--rubrics', str(ROOT / 'rubrics'),
                   '--out', str(ROOT / 'judgments'), '--workers', '1', '--max-api-requests', str(remaining)]
        result = subprocess.run(command, cwd=PROJECT)
        write_json(ROOT / 'judge-exit.json', {'returncode': result.returncode})
    comparison = agreement(manifest, read_json(ROOT / 'human-labels-dev.json'),
                           ROOT / 'judgments', config['tie_margin'], 'dev')
    write_json(ROOT / 'agreement.json', comparison)
    candidate_results = {}
    for case in cases:
        candidate_results[case['id']] = {}
        for candidate in case['candidates']:
            path = ROOT / 'judgments' / case['id'] / (candidate['id'] + '.json')
            if path.exists():
                candidate_results[case['id']][candidate['id']] = read_json(path)['summary']
    generation = read_json(ROOT / 'rubrics/run-summary.json')
    summary = {
        'status': 'finished', 'protocol': 'evidence_v1', 'prompt_version': snapshot['prompt_version'],
        'generation_models': 'Bailian kimi-k3', 'judge_model': 'MaaS kimi-k2.6',
        'generation': generation, 'approved_suites': len(eligible), 'requested_suites': len(cases),
        'candidate_results': candidate_results,
        'complete_candidates': sum(s['status'] == 'complete' for r in candidate_results.values() for s in r.values()),
        'requested_candidates': 14, 'agreement': comparison, 'budget': budget(),
        'historical_baseline': {'matches': 2, 'decided': 6, 'agreement': 2/6},
        'holdout_run': False,
        'caveat': 'Development-set experiment. Incomplete evaluations are not wins or losses. Historical baseline also used source refinement, while this frozen evidence_v1 configuration does not.'}
    write_json(ROOT / 'validation-summary.json', summary)
    print(summary, flush=True)


if __name__ == '__main__':
    main()
