"""Shared unknown exclusions must preserve one comparable scoring denominator."""
import copy

import pytest

from ppt_rubric.rubric import aggregate
from ppt_rubric.v25.comparison_scoring import score_candidates


@pytest.fixture
def cohort():
    rubric = {'items': [
        {'id': rid, 'dimension': dimension, 'score': score}
        for rid, dimension, score in [('R1', 'fulfillment', 40), ('R2', 'correctness', 30),
                                      ('R3', 'integrity', 20), ('V1', 'visual', 10)]]}
    answers = {'A': ['unknown', 'yes', 'no', 'yes'],
               'B': ['yes', 'unknown', 'yes', 'yes'],
               'C': ['no', 'no', 'yes', 'no']}
    verdicts = {cid: [{'rubric_id': item['id'], 'answer': answer, 'evidence': 'original'}
                      for item, answer in zip(rubric['items'], values)]
                for cid, values in answers.items()}
    return rubric, verdicts


def test_three_candidates_share_union_of_unknown_exclusions_and_original_weights(cohort):
    rubric, verdicts = cohort
    original = copy.deepcopy(verdicts)
    comparison, totals = score_candidates(rubric, verdicts, ['A', 'B', 'C'])
    assert comparison['excluded_rubric_ids'] == ['R1', 'R2']
    assert comparison['included_rubric_ids'] == ['R3', 'V1']
    assert comparison['excluded_items'] == [
        {'rubric_id': 'R1', 'score': 40, 'unknown_candidates': ['A']},
        {'rubric_id': 'R2', 'score': 30, 'unknown_candidates': ['B']}]
    assert comparison['score_possible'] == 30 and comparison['excluded_weight'] == 70
    assert {cid: s['score'] for cid, s in totals.items()} == {'A': 10, 'B': 30, 'C': 20}
    assert all(s['status'] == 'complete' and s['unresolved_weight'] == 0 for s in totals.values())
    assert all(sum(d['possible'] for d in s['dimensions'].values()) == 30 for s in totals.values())
    assert original == verdicts  # Neither unknowns nor other candidates' no/yes are rewritten.


def test_shared_scoring_is_independent_of_candidate_order(cohort):
    rubric, verdicts = cohort
    assert score_candidates(rubric, verdicts, ['A', 'B', 'C']) == score_candidates(
        rubric, dict(reversed(list(verdicts.items()))), ['C', 'B', 'A'])


def test_no_unknown_preserves_original_scores_and_full_budget(cohort):
    rubric, verdicts = cohort
    for rows in verdicts.values():
        for row in rows:
            if row['answer'] == 'unknown':
                row['answer'] = 'no'
    comparison, totals = score_candidates(rubric, verdicts, verdicts)
    assert comparison['excluded_rubric_ids'] == [] and comparison['score_possible'] == 100
    assert totals == {cid: aggregate(rubric, rows) for cid, rows in verdicts.items()}


def test_all_excluded_is_not_a_zero_point_tie(cohort):
    rubric, verdicts = cohort
    for row in verdicts['A']:
        row['answer'] = 'unknown'
    comparison, totals = score_candidates(rubric, verdicts, verdicts)
    assert comparison['status'] == 'not_comparable' and comparison['score_possible'] == 0
    for summary in totals.values():
        assert summary['status'] == 'not_comparable'
        assert summary['score'] is summary['score_lower'] is summary['score_upper'] is None


@pytest.mark.parametrize('change', ['candidate_missing', 'candidate_extra', 'verdict_missing',
                                  'verdict_duplicate', 'verdict_extra', 'invalid_answer'])
def test_incomplete_or_malformed_cohort_cannot_silently_publish_scores(cohort, change):
    rubric, verdicts = cohort
    if change == 'candidate_missing':
        verdicts.pop('B')
    elif change == 'candidate_extra':
        verdicts['D'] = copy.deepcopy(verdicts['A'])
    elif change == 'verdict_missing':
        verdicts['A'].pop()
    elif change == 'verdict_duplicate':
        verdicts['A'].append(copy.deepcopy(verdicts['A'][0]))
    elif change == 'verdict_extra':
        verdicts['A'].append({'rubric_id': 'other', 'answer': 'yes'})
    else:
        verdicts['A'][0]['answer'] = 'unknow'
    with pytest.raises(ValueError):
        score_candidates(rubric, verdicts, ['A', 'B', 'C'])


def test_separate_cases_do_not_share_exclusions(cohort):
    rubric, verdicts = cohort
    first, _ = score_candidates(rubric, verdicts, verdicts)
    second, totals = score_candidates(rubric, {'C': verdicts['C']}, ['C'])
    assert first['excluded_rubric_ids'] == ['R1', 'R2']
    assert second['excluded_rubric_ids'] == [] and second['score_possible'] == 100
    assert totals['C']['score'] == 20
