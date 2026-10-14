from __future__ import annotations

import math
from pathlib import Path

from .common import read_json


def agreement(manifest, labels, report_root, tie_margin=3.0, split='dev'):
    if labels.get('manifest_id') != manifest['id']:
        raise ValueError('Human labels use a different manifest / blind mapping')
    if not math.isfinite(tie_margin) or tie_margin < 0:
        raise ValueError('tie_margin must be nonnegative and frozen before holdout evaluation')
    rows, skipped = [], []
    total_cases, decided_labels = 0, 0
    label_rows = labels.get('labels', [])
    if len({r['case_id'] for r in label_rows}) != len(label_rows):
        raise ValueError('Duplicate human labels')
    lookup = {r['case_id']: r for r in label_rows}
    for case in manifest['cases']:
        if split != 'all' and case['split'] != split:
            continue
        total_cases += 1
        label = lookup.get(case['id'])
        if label and label.get('label_status') == 'undecided':
            if label.get('winner') is not None:
                raise ValueError('Undecided human label cannot carry a winner')
            skipped.append({'case_id': case['id'], 'reason': 'human undecided'})
            continue
        if not label or not label.get('winner'):
            skipped.append({'case_id': case['id'], 'reason': 'unlabeled'})
            continue
        if len(case['candidates']) != 2:
            raise ValueError('Pairwise agreement expects two candidates; generation/evaluation support more')
        expected = {c['id']: c['sha256'] for c in case['candidates']}
        if label.get('candidate_hashes') != expected:
            raise ValueError('Human labels refer to different candidate files')
        if label['winner'] not in {*expected, 'tie'} or not label.get('reasons', '').strip():
            raise ValueError('Human label must have a valid winner and reasons')
        decided_labels += 1
        reports = {}
        for cid in expected:
            path = Path(report_root) / case['id'] / f'{cid}.json'
            if path.exists():
                reports[cid] = read_json(path)
        if len(reports) != 2 or any(r['summary']['status'] != 'complete' for r in reports.values()):
            skipped.append({'case_id': case['id'], 'reason': 'incomplete automated evaluation'})
            continue
        if any(r['candidate_hash'] != expected[cid] for cid, r in reports.items()) or len({r['rubric_hash'] for r in reports.values()}) != 1:
            raise ValueError('Candidate or rubric mismatch in evaluation reports')
        if len({r.get('config_hash') for r in reports.values()}) != 1 or len({r.get('judge_model') for r in reports.values()}) != 1:
            raise ValueError('Candidates were evaluated with different judge settings')
        if any(len({r.get(key) for r in reports.values()}) != 1
               for key in ('judge_validator_version', 'judge_prompt_fingerprint')):
            raise ValueError('Candidates were evaluated with different judge versions')
        if any(not isinstance(r['summary']['score'], (int,float)) or not math.isfinite(r['summary']['score'])
               or not 0 <= r['summary']['score'] <= 100.000001 for r in reports.values()):
            raise ValueError('Invalid complete score')
        a, b = sorted(expected)
        delta = reports[a]['summary']['score'] - reports[b]['summary']['score']
        prediction = 'tie' if abs(delta) <= tie_margin else (a if delta > 0 else b)
        rows.append({'case_id': case['id'], 'human': label['winner'], 'predicted': prediction,
                     'scores': {cid: r['summary']['score'] for cid, r in reports.items()},
                     'difference': delta, 'match': prediction == label['winner'], 'reasons': label['reasons'],
                     'reviewer_to_manifest': label.get('reviewer_to_manifest'),
                     'reasons_candidate_ids': label.get('reasons_candidate_ids')})
    n, wins = len(rows), sum(r['match'] for r in rows)
    interval = None
    if n:
        p, z = wins / n, 1.96
        center = (p + z*z/(2*n)) / (1 + z*z/n)
        half = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / (1+z*z/n)
        interval = [max(0, center-half), min(1, center+half)]
    return {'split': split, 'tie_margin': tie_margin, 'n': n, 'matches': wins,
            'total_cases': total_cases, 'decided_labels': decided_labels,
            'evaluation_coverage': n/decided_labels if decided_labels else None,
            'all_decided_cases_evaluated': bool(decided_labels) and n == decided_labels,
            'all_cases_comparable': bool(total_cases) and n == total_cases,
            'matched_fraction_of_decided_labels': wins/decided_labels if decided_labels else None,
            'agreement': wins/n if n else None, 'wilson_95_interval': interval,
            'human_ties': sum(r['human'] == 'tie' for r in rows),
            'predicted_ties': sum(r['predicted'] == 'tie' for r in rows), 'cases': rows, 'skipped': skipped}
