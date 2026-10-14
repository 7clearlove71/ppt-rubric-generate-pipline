"""Finish this frozen experiment; never broaden cases or reset its request cap."""
from pathlib import Path
import subprocess
import sys
import collections

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent.parent
sys.path.insert(0, str(PROJECT))
from ppt_rubric.common import read_json, write_json, file_digest
from ppt_rubric.operations import plan_run
from ppt_rubric.evaluation import agreement


def request_usage():
    attempts = []
    stages = {}
    for stage in ('rubrics', 'judgments'):
        path = ROOT / stage / 'requests.json'
        rows = read_json(path) if path.exists() else []
        stages[stage] = len(rows)
        attempts.extend(rows)
    usage = {key: sum(r.get('usage', {}).get(key, 0) for r in attempts)
             for key in ('prompt_tokens', 'completion_tokens', 'total_tokens')}
    return {'provider': 'ModelArts MaaS', 'request_cap': 900,
            'actual_requests': len(attempts), 'remaining_requests': 900-len(attempts),
            'stages': stages, 'statuses': dict(collections.Counter(r['status'] for r in attempts)),
            'reported_usage': usage,
            'usage_missing_requests': sum(not r.get('usage') for r in attempts),
            'bailian_requests': 0, 'cost_estimate': None}


def main():
    snapshot = read_json(ROOT / 'version-snapshot.json')
    changed = [p for p, digest in snapshot['files'].items() if file_digest(PROJECT / p) != digest]
    if changed:
        raise RuntimeError(f'Frozen inputs/code changed: {changed}')
    manifest = read_json(PROJECT / 'data/manifest.json')
    cases = [c for c in manifest['cases'] if c['split'] == 'dev']
    config = read_json(ROOT / 'judge-config.json')
    plan = plan_run(cases, config, 'judge', ROOT / 'rubrics')
    write_json(ROOT / 'judge-plan.json', plan)
    eligible = [r['case_id'] for r in plan['cases'] if not r['errors']]
    budget = request_usage()
    write_json(ROOT / 'shared-budget.json', budget)
    print({'eligible': eligible, 'budget': budget}, flush=True)
    if eligible and '--summarize-only' not in sys.argv and budget['remaining_requests'] > 0:
        result = subprocess.run([
            sys.executable, '-m', 'ppt_rubric', 'judge', '--cases', ','.join(eligible),
            '--config', str(ROOT / 'judge-config.json'), '--rubrics', str(ROOT / 'rubrics'),
            '--out', str(ROOT / 'judgments'), '--workers', '1',
            '--max-api-requests', str(budget['remaining_requests'])], cwd=PROJECT)
        write_json(ROOT / 'judge-exit.json', {'returncode': result.returncode})
    comparison = agreement(manifest, read_json(ROOT / 'human-labels-dev.json'),
                           ROOT / 'judgments', config['tie_margin'], 'dev')
    write_json(ROOT / 'agreement.json', comparison)
    budget = request_usage()
    write_json(ROOT / 'shared-budget.json', budget)
    generation = read_json(ROOT / 'rubrics/run-summary.json')
    candidate_results = {}
    for case in cases:
        candidate_results[case['id']] = {}
        for candidate in case['candidates']:
            path = ROOT / 'judgments' / case['id'] / (candidate['id'] + '.json')
            if path.exists():
                candidate_results[case['id']][candidate['id']] = read_json(path)['summary']
    summary = {'status': 'finished', 'protocol': 'evidence_v1',
               'prompt_version': snapshot['prompt_version'],
               'generation': generation, 'candidate_results': candidate_results,
               'agreement': comparison, 'budget': budget,
               'historical_baseline': {'matches': 2, 'decided': 6, 'agreement': 2/6},
               'holdout_run': False,
               'caveat': 'Development-set experiment; incomplete evaluations are not wins or losses. Models differ from the historical Bailian generation baseline.'}
    write_json(ROOT / 'validation-summary.json', summary)
    policy = read_json(ROOT / 'run-policy.json')
    policy.update(status='finished', actual_requests=budget['actual_requests'])
    write_json(ROOT / 'run-policy.json', policy)
    print(summary, flush=True)


if __name__ == '__main__':
    main()
