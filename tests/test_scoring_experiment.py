from copy import deepcopy

import pytest

from ppt_rubric.rubric import aggregate, assign_scores
from ppt_rubric.scoring_experiment import (
    POLICY, binary_diagnostics, compare_fixed_answers, compile_units,
    score_experiment, stability_checks,
)


def fixture(facts=1):
    groups = [{'id': dimension, 'dimension': dimension, 'importance': 1}
              for dimension in POLICY['dimension_budgets']]
    items = [{'id': 'F' + str(number), 'group_id': 'correctness', 'importance': 3}
             for number in range(facts)]
    items.append({'id': 'C_REASON', 'group_id': 'correctness', 'importance': 3})
    items.extend({'id': dimension.upper(), 'group_id': dimension, 'importance': 1}
                 for dimension in POLICY['dimension_budgets'] if dimension != 'correctness')
    rubric = assign_scores({'groups': groups, 'items': items}, POLICY['dimension_budgets'])
    verdicts = [{'rubric_id': item['id'], 'answer': 'no' if item['id'] == 'C_REASON' else 'yes'}
                for item in rubric['items']]
    return rubric, verdicts


def test_binary_control_preserves_answers_and_default_scores_without_mutation():
    rubric, verdicts = fixture()
    original = deepcopy((rubric, verdicts))
    comparison = compare_fixed_answers(rubric, verdicts)
    assert (rubric, verdicts) == original
    assert comparison['baseline'] == aggregate(rubric, verdicts)
    assert comparison['baseline']['score'] == 87.5
    assert comparison['binary_fixed_answers']['score'] == 90
    assert comparison['production_policy_changed'] is False
    assert comparison['graded_impact'] is None


def test_reasoning_budget_stays_ten_with_zero_or_many_unrelated_facts():
    old_weights = []
    for count in (0, 1, 15):
        rubric, verdicts = fixture(count)
        comparison = compare_fixed_answers(rubric, verdicts)
        old_weights.append(comparison['legacy_general_reasoning_weight'])
        assert comparison['binary_fixed_answers']['score'] == pytest.approx(90)
        assert comparison['experimental_general_reasoning_budget'] == 10
    assert old_weights == [25, 12.5, 25 / 16]


def test_unknown_retains_full_interval_not_a_half_credit_guess():
    rubric, verdicts = fixture()
    next(row for row in verdicts if row['rubric_id'] == 'C_REASON')['answer'] = 'unknown'
    comparison = compare_fixed_answers(rubric, verdicts)
    score = comparison['binary_fixed_answers']
    assert score['status'] == 'incomplete'
    assert score['score'] is None
    assert score['score_lower'] == 90
    assert score['score_upper'] == 100
    assert score['unresolved_weight'] == 10


def test_one_declared_harm_cannot_deduct_both_content_and_visual_units():
    rubric, verdicts = fixture()
    _, mapping, _ = compile_units(rubric)
    diagnostics = binary_diagnostics(verdicts, mapping)
    for row in diagnostics:
        if row['rubric_id'] in {'C_REASON', 'VISUAL'}:
            row.update(severity='key_unusable', harm_id='cropped-required-result', owner_unit_id='VISUAL')
    result = score_experiment(rubric, diagnostics)
    assert result['score'] == 85
    assert result['harm_owners'] == {'cropped-required-result': 'VISUAL'}
    assert score_experiment(rubric, diagnostics * 5)['score'] == 85


def test_harm_cannot_disappear_by_naming_an_owner_without_an_observation():
    rubric, verdicts = fixture()
    _, mapping, _ = compile_units(rubric)
    diagnostics = binary_diagnostics(verdicts, mapping)
    next(row for row in diagnostics if row['rubric_id'] == 'C_REASON')['owner_unit_id'] = 'VISUAL'
    with pytest.raises(ValueError, match='own diagnosis'):
        score_experiment(rubric, diagnostics)


def test_conflicting_harm_owners_are_rejected():
    rubric, verdicts = fixture()
    _, mapping, _ = compile_units(rubric)
    records = binary_diagnostics(verdicts, mapping)
    for row in records:
        if row['rubric_id'] in {'C_REASON', 'VISUAL'}:
            row.update(severity='key_unusable', harm_id='same-harm')
    with pytest.raises(ValueError, match='conflicting'):
        score_experiment(rubric, records)


def test_local_impact_needs_an_anchor_and_uses_a_fixed_level():
    rubric, verdicts = fixture()
    _, mapping, _ = compile_units(rubric)
    records = binary_diagnostics(verdicts, mapping)
    row = next(row for row in records if row['rubric_id'] == 'C_REASON')
    row.update(severity='local_material', anchor_ref='E1: isolated subsidiary inference; core decision unaffected')
    assert score_experiment(rubric, records)['score'] == 95
    row['anchor_ref'] = ''
    with pytest.raises(ValueError, match='anchor reference'):
        score_experiment(rubric, records)


def test_equivalent_fact_duplication_does_not_dilute_an_existing_defect():
    rubric, verdicts = fixture()
    verdicts[0]['answer'] = 'no'
    before = compare_fixed_answers(rubric, verdicts)['binary_fixed_answers']['score']
    copy = dict(rubric['items'][0], id='F0-copy')
    rubric['items'].append(copy)
    # An equivalent wording is not an additional obligation requiring a new answer.
    result = compare_fixed_answers(rubric, verdicts, aliases={'F0-copy': 'F0'})
    assert result['binary_fixed_answers']['score'] == before


def test_equivalent_aliases_cannot_cross_scoring_dimensions():
    rubric, _ = fixture()
    with pytest.raises(ValueError, match='preserve'):
        compile_units(rubric, aliases={'VISUAL': 'F0'})


def test_missing_units_remain_unknown_and_invalid_verdicts_are_rejected():
    rubric, verdicts = fixture()
    comparison = compare_fixed_answers(rubric, verdicts[1:])
    assert comparison['binary_fixed_answers']['unresolved_weight'] == 15
    for rows in (verdicts * 2, [dict(verdicts[0], answer='maybe')], [{'rubric_id': 'alien', 'answer': 'yes'}]):
        with pytest.raises(ValueError, match='Verdicts require'):
            compare_fixed_answers(rubric, rows)


def test_metamorphic_suite_reports_real_checks_and_repairs():
    rubric, verdicts = fixture()
    result = stability_checks(rubric, verdicts)
    assert result['passed']
    assert all(result['checks'].values())
    assert result['not_applicable'] == []
