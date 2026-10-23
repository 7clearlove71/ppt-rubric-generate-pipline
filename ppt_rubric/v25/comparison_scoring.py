"""Score a complete candidate cohort on the same resolved rubric subset.

This postprocessing policy is versioned separately from rubric construction and
model prompts. Raw assessments, including unknown intervals, stay unchanged.
"""
from __future__ import annotations

import math

from ..rubric import aggregate


COMPARISON_POLICY = {
    'version': 'exclude_unknown_across_candidates_v1',
    'scope': 'all candidates of the same case and frozen rubric',
    'unknown': 'exclude a criterion for every candidate if any final criterion verdict is unknown',
    'visual': 'apply exclusions after any_no_else_unknown_else_all_yes page aggregation',
    'weights': 'retain included criterion weights; no redistribution or normalization',
    'empty': 'no score or preference when no criteria remain',
}


def score_candidates(rubric, verdicts_by_candidate, candidate_ids):
    """Return shared exclusions and totals without changing any model verdict."""
    candidate_ids = sorted(candidate_ids)
    if not candidate_ids or len(set(candidate_ids)) != len(candidate_ids):
        raise ValueError('Comparison requires unique candidate identifiers')
    if set(verdicts_by_candidate) != set(candidate_ids):
        raise ValueError('Comparison must cover exactly the frozen case candidates')
    items = rubric['items']
    item_ids = {item['id'] for item in items}
    if not items or len(item_ids) != len(items):
        raise ValueError('Comparison requires a nonempty rubric with unique identifiers')
    indexed = {}
    for cid in candidate_ids:
        verdicts = verdicts_by_candidate[cid]
        indexed[cid] = {v['rubric_id']: v for v in verdicts}
        if len(indexed[cid]) != len(verdicts) or set(indexed[cid]) != item_ids:
            raise ValueError('Comparison requires exactly one final verdict per rubric item')
        if any(v.get('answer') not in {'yes', 'no', 'unknown'} for v in verdicts):
            raise ValueError('Comparison verdict must be yes, no or unknown')
    excluded = []
    for item in items:
        unknown = [cid for cid in candidate_ids if indexed[cid][item['id']]['answer'] == 'unknown']
        if unknown:
            excluded.append({'rubric_id': item['id'], 'score': item['score'],
                             'unknown_candidates': unknown})
    excluded_ids = {row['rubric_id'] for row in excluded}
    included = [item for item in items if item['id'] not in excluded_ids]
    comparison = {
        'policy': COMPARISON_POLICY.copy(),
        'status': 'complete' if included else 'not_comparable',
        'candidate_ids': candidate_ids,
        'included_rubric_ids': [item['id'] for item in included],
        'excluded_rubric_ids': [row['rubric_id'] for row in excluded],
        'excluded_items': excluded,
        'original_possible': math.fsum(item['score'] for item in items),
        'score_possible': math.fsum(item['score'] for item in included),
        'excluded_weight': math.fsum(row['score'] for row in excluded),
    }
    summaries = {}
    for cid in candidate_ids:
        verdicts = [indexed[cid][item['id']] for item in included]
        summary = aggregate({'items': included}, verdicts)
        if not included:
            summary.update(status='not_comparable', score=None, score_lower=None, score_upper=None)
        summaries[cid] = summary
    return comparison, summaries


def validate_comparison(case, rubric, reports):
    """Recompute persisted cohort totals; never migrate historical reports."""
    if any(not isinstance(report.get('comparison'), dict)
           or report['comparison'].get('policy') != COMPARISON_POLICY for report in reports.values()):
        raise ValueError('Cannot compare mismatched comparison scoring policies')
    comparison, summaries = score_candidates(
        rubric, {cid: r['assessment']['verdicts'] for cid, r in reports.items()},
        [c['id'] for c in case['candidates']])
    for cid, report in reports.items():
        if report['assessment']['summary'] != aggregate(rubric, report['assessment']['verdicts']):
            raise ValueError('Raw assessment summary differs from its original verdicts')
        if report['comparison'] != comparison or report['summary'] != summaries[cid]:
            raise ValueError('Comparison exclusions or totals differ from the shared rubric subset')
    return comparison
