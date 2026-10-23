import copy

import pytest

from ppt_rubric.contract_v2 import (compile_suite, compile_item, compile_requirements, validate_design,
                                   validate_requirements, validate_review, generate_contract)
from ppt_rubric.source_catalog import source_catalog
from source_contract_fixtures import add_ledger, approval


def fixture_contract():
    catalog = source_catalog([{'id': 'prompt', 'text': '做一份简报，说明项目进展。'}])
    kinds = ('deliverable', 'fact', 'information_boundary', 'presentation')
    reqs = {'task_summary': '简报', 'source_policy': '不虚构成果', 'requirements': [
        {'id': f'Q{n+1}', 'kind': kind, 'text': '项目进展', 'importance': 3,
         'source_ids': [catalog[0]['id']]} for n, kind in enumerate(kinds)]}
    reqs = add_ledger(reqs, catalog)
    return catalog, reqs, compile_requirements(reqs)


def test_unique_requirement_owner_and_no_second_definition():
    catalog, reqs, design = fixture_contract()
    validate_requirements(reqs, catalog)
    validate_design(design, reqs, catalog)
    duplicate = copy.deepcopy(design)
    duplicate['items'].append({**copy.deepcopy(duplicate['items'][0]), 'id': 'R_extra'})
    with pytest.raises(ValueError, match='exactly one'):
        validate_design(duplicate, reqs, catalog)
    design['items'][0]['fail_condition'] = '与通过定义不同的独立失败边界'
    with pytest.raises(ValueError, match='duplicate criterion'):
        validate_design(design, reqs, catalog)


def test_key_fact_cannot_be_charged_as_fulfillment_or_generic_quality():
    catalog, reqs, design = fixture_contract()
    design['items'][1]['dimension'] = 'fulfillment'
    with pytest.raises(ValueError, match='key facts belong to correctness'):
        validate_design(design, reqs, catalog)
    design['items'][1]['dimension'] = 'correctness'
    design['items'][1]['requirement_ids'] = []
    with pytest.raises(ValueError, match='supplied IDs'):
        validate_design(design, reqs, catalog)


def test_compilation_uses_single_contract_and_fixed_dimension_budget():
    _, _, design = fixture_contract()
    weights = dict(fulfillment=30, correctness=25, integrity=15, narrative=15, visual=15)
    suite = compile_suite(design, weights)
    assert sum(i['score'] for i in suite['items']) == pytest.approx(100)
    assert all(i['judge_prompt'] == compile_item(i) for i in suite['items'])
    assert [i['score'] for i in suite['items'] if i['dimension'] == 'visual'] == [10, 5]
    design['items'].append({**copy.deepcopy(design['items'][1]), 'id': 'R_extra'})
    expanded = compile_suite(design, weights)
    assert sum(i['score'] for i in expanded['items'] if i['dimension'] == 'correctness') == 25


def test_audit_must_provide_grounded_actionable_counterexample():
    catalog, _, design = fixture_contract()
    suite = compile_suite(design, dict(fulfillment=30, correctness=25, integrity=15, narrative=15, visual=15))
    with pytest.raises(ValueError, match='contradicts'):
        validate_review({'approved': True, 'issues': [{}]}, suite, catalog)
    with pytest.raises(ValueError):
        validate_review({'approved': False, 'issues': [{'item_ids': ['R0'], 'source_ids': ['fabricated'],
            'counterexample': '反例', 'fix': '修正'}]}, suite, catalog)


def test_generator_cannot_access_candidates_or_human_labels(tmp_path, monkeypatch):
    catalog, reqs, design = fixture_contract()
    calls = []
    class FakeClient:
        def ask(self, role, system, payload, validate):
            calls.append(payload)
            result = [reqs, approval(reqs)][len(calls) - 1]
            validate(result)
            return result
    monkeypatch.setattr('ppt_rubric.contract_v2.case_sources', lambda *args: [{'id': 'prompt', 'text': '做一份简报，说明项目进展。'}])
    case = {'id': '01', 'candidates': object(), 'human_labels': 'NEVER_INCLUDE_THIS'}
    suite = generate_contract(case, {'dimension_weights': dict(fulfillment=30, correctness=25, integrity=15, narrative=15, visual=15),
        'generator_model': 'fixture', 'max_revisions': 2}, FakeClient(), tmp_path)
    assert suite['status'] == 'approved'
    assert 'NEVER_INCLUDE_THIS' not in repr(calls)
    assert not suite['provenance']['candidate_input']
