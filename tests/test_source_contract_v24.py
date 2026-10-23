"""Source-only development regressions for five confirmed v2.3 failures.

The fake audit records test the gate, not a claim of model semantic accuracy.
"""
import copy
import json

import pytest

from ppt_rubric import contract_v2 as contract
from ppt_rubric.source_catalog import source_catalog, validate_source_coverage
from source_contract_fixtures import add_ledger, approval


def ledger(text, rows):
    catalog = source_catalog([{'id': 'prompt', 'text': text}])
    requirements = {'task_summary': '仅用于来源机制回归', 'source_policy': '保留对象、状态、条件和关系',
                    'requirements': [{'id': f'Q{n}', 'kind': kind, 'text': summary, 'importance': 2,
                                      'source_ids': [catalog[0]['id']]}
                                     for n, (kind, summary) in enumerate(rows, 1)]}
    requirements = add_ledger(requirements, catalog)
    return catalog, requirements


def suite(requirements):
    return contract.compile_suite(contract.compile_requirements(requirements), contract.FIXED_WEIGHTS)


def test_03_one_numeric_fact_has_one_owner_and_module_only_references_it():
    catalog, requirements = ledger('3号线空载能耗12；分析节能改造方案。',
                                   [('fact', '3号线空载12'), ('deliverable', '分析改造方案')])
    fact, module = requirements['source_targets']
    fact.update(object='3号线', attribute='空载能耗', value='12', unit='kWh', state='给定事实')
    requirements['requirements'][1]['reference_target_ids'] = [fact['id']]
    contract.validate_requirements(requirements, catalog)
    built = suite(requirements)
    assert '12' in built['items'][0]['pass_condition']
    assert '12' not in built['items'][1]['pass_condition']
    contract.validate_review(approval(requirements), built, catalog)
    # A second owner cannot be hidden behind a new Q, changed value or unit.
    duplicate = copy.deepcopy(fact)
    duplicate.update(id='T3', scoring_owner='Q3', value='10', unit='MWh')
    requirements['source_targets'].append(duplicate)
    requirements['requirements'].append({**copy.deepcopy(requirements['requirements'][0]),
                                       'id': 'Q3', 'target_ids': ['T3']})
    with pytest.raises(ValueError, match='Duplicate semantic'):
        contract.validate_requirements(requirements, catalog)
    duplicate['relation'] = {'type': 'depends_on', 'target_ids': [module['id']]}
    with pytest.raises(ValueError, match='Duplicate semantic'):
        contract.validate_requirements(requirements, catalog)


def test_03_optional_benchmark_has_no_unconditional_existence_prefix():
    catalog, requirements = ledger('可引用行业标杆作为参考，如引用需保持数值准确。另须交付节能分析。',
                                   [('fact', '参考标杆8'), ('deliverable', '节能分析')])
    benchmark = requirements['source_targets'][0]
    benchmark['mandatory'] = False
    benchmark['condition'] = {'mode': 'if_present', 'text': '引用行业标杆作为参考',
                              'source_ids': benchmark['source_ids']}
    contract.validate_requirements(requirements, catalog)
    built = suite(requirements)
    criterion = built['items'][0]
    assert '未出现自然满足' in criterion['applicability']
    assert '事实存在且准确' not in criterion['pass_condition']
    assert '始终检查' not in criterion['judge_prompt']
    review = approval(requirements)
    contract.validate_review(review, built, catalog)
    absent = next(row for row in review['counterexample_checks'] if row['operation'] == 'condition_absent')
    absent['actual_failed_item_ids'] = ['R_Q1']
    with pytest.raises(ValueError, match='only its owner'):
        contract.validate_review(review, built, catalog)


def test_05_uncertain_state_stays_with_fact_and_cannot_be_hidden_in_q_summary():
    catalog, requirements = ledger('族谱所记来历待考；请列出下一步核验办法。',
                                   [('fact', '族谱来历待考'), ('information_boundary', '核验办法；族谱来历待考'),
                                    ('deliverable', '历史来源简报')])
    fact, disclosure, _module = requirements['source_targets']
    fact.update(object='族谱来历', attribute='来源说法', value='族谱所记', state='待考')
    disclosure.update(object='核验办法', attribute='披露动作', value='列出下一步核验办法')
    requirements['requirements'][1]['reference_target_ids'] = [fact['id']]
    contract.validate_requirements(requirements, catalog)
    built = suite(requirements)
    assert '状态=待考' in built['items'][0]['judge_prompt']
    assert '待考' not in built['items'][1]['pass_condition']
    review = approval(requirements)
    contract.validate_review(review, built, catalog)
    review['counterexample_checks'][0]['actual_failed_item_ids'].append('R_Q2')
    with pytest.raises(ValueError, match='same repair'):
        contract.validate_review(review, built, catalog)


def test_06_global_style_has_one_owner_and_module_can_share_the_same_source():
    catalog, requirements = ledger('面向政府评审，按公文汇报风格说明改造任务。',
                                   [('presentation', '全篇公文文体'), ('deliverable', '按公文汇报方式呈现改造任务')])
    style, module = requirements['source_targets']
    style.update(object='全篇', attribute='文体', value='适合政府评审的正式公文汇报风格')
    module.update(object='改造任务模块', attribute='交付内容', value='说明改造任务')
    requirements['requirements'][1]['reference_target_ids'] = [style['id']]
    contract.validate_requirements(requirements, catalog)
    built = suite(requirements)
    assert '公文' in built['items'][0]['pass_condition']
    assert '公文' not in built['items'][1]['pass_condition']
    assert built['items'][0]['source_ids'] == built['items'][1]['source_ids']
    review = approval(requirements)
    review['counterexample_checks'][0].update(candidate='只在改造模块加入明显俚俗讥讽，其余内容正确',
                                             actual_failed_item_ids=['R_Q1', 'R_Q2'])
    with pytest.raises(ValueError, match='same repair'):
        contract.validate_review(review, built, catalog)


def test_10_relative_order_is_independent_and_missing_source_goal_blocks_approval():
    catalog, requirements = ledger('事实时间线要先立住，然后是影响评估。',
                                   [('deliverable', '事实时间线'), ('deliverable', '影响评估'),
                                    ('deliverable', '时间线先于影响评估')])
    first, second, relation = requirements['source_targets']
    first.update(object='事实时间线', attribute='模块内容', value='建立完整时间线')
    second.update(object='影响评估', attribute='模块内容', value='提供影响评估')
    relation.update(object='时间线与影响评估', attribute='相对顺序', value='时间线在前，影响评估在后',
                    relation={'type': 'before', 'target_ids': [first['id'], second['id']]})
    requirements['requirements'][2]['reference_target_ids'] = [first['id'], second['id']]
    contract.validate_requirements(requirements, catalog)
    built = suite(requirements)
    assert 'before(T1 → T2)' in built['items'][2]['judge_prompt']
    assert '组件缺失只由组件所有者负责' in built['items'][2]['judge_prompt']
    review = approval(requirements)
    contract.validate_review(review, built, catalog)
    review['counterexample_checks'] = [row for row in review['counterexample_checks']
                                       if row['operation'] != 'reverse_relation']
    with pytest.raises(ValueError, match='omitted required'):
        contract.validate_review(review, built, catalog)
    review = approval(requirements)
    # The reviewer independently finds this relation even if extraction omitted it.
    review['coverage_checks'][0]['source_goals'][-1]['target_ids'] = []
    with pytest.raises(ValueError, match='without an owner'):
        contract.validate_review(review, built, catalog)


@pytest.mark.parametrize('change', ['no_evidence', 'mandatory_conflict', 'empty_text'])
def test_conditional_exemption_needs_source_support_and_consistent_fields(change):
    catalog, requirements = ledger('如需引用标杆，应保持准确。还需给出节能方案。',
                                   [('fact', '标杆值'), ('deliverable', '节能方案')])
    target = requirements['source_targets'][0]
    target['mandatory'] = False
    target['condition'] = {'mode': 'source_condition', 'text': '需要引用标杆', 'source_ids': target['source_ids']}
    if change == 'no_evidence':
        target['condition']['source_ids'] = []
    elif change == 'mandatory_conflict':
        target['mandatory'] = True
    else:
        target['condition']['text'] = ''
    with pytest.raises(ValueError, match='source-backed'):
        contract.validate_requirements(requirements, catalog)


def test_background_fragment_requires_explicit_disposition_and_independent_review():
    catalog, requirements = ledger('提交项目简报。\n背景：当前处于调研期。', [('deliverable', '项目简报')])
    contract.validate_requirements(requirements, catalog)
    contract.validate_review(approval(requirements), suite(requirements), catalog)
    requirements['source_coverage'].pop()
    with pytest.raises(ValueError, match='omitted source fragments'):
        validate_source_coverage(catalog, requirements['source_coverage'], requirements['source_targets'])


def test_approved_without_checkable_semantic_audit_is_rejected():
    catalog, requirements = ledger('提交项目简报。', [('deliverable', '项目简报')])
    with pytest.raises(ValueError, match='coverage_checks'):
        contract.validate_review({'approved': True, 'issues': []}, suite(requirements), catalog)
    review = approval(requirements)
    review['target_checks'][0]['status'] = 'disputed'
    with pytest.raises(ValueError, match='grounded unique'):
        contract.validate_review(review, suite(requirements), catalog)


def test_historical_unstructured_extraction_is_not_silently_upgraded():
    catalog, requirements = ledger('提交项目简报。', [('deliverable', '项目简报')])
    requirements.pop('source_targets')
    with pytest.raises(ValueError, match='old extraction cannot be reused'):
        contract.validate_requirements(requirements, catalog)


def test_distinct_deliverables_can_share_container_and_attribute_from_real_k3_response():
    # Simplifies the unchanged T05/T06 from the first formal K3 case03 output.
    catalog, requirements = ledger('先把四条线的能耗对比摆出来，再聚焦1号线做深挖。',
                                   [('deliverable', '四线能耗对比'), ('deliverable', '1号线深挖分析')])
    comparison, analysis = requirements['source_targets']
    comparison.update(object='PPT结构', attribute='模块交付', value='四条产线能耗对比模块')
    analysis.update(object='PPT结构', attribute='模块交付', value='聚焦1号线的深挖分析模块')
    contract.validate_requirements(requirements, catalog)
    built = suite(requirements)
    contract.validate_design(contract.compile_requirements(requirements), requirements, catalog)
    contract.validate_review(approval(requirements), built, catalog)
    assert len(built['source_targets']) == 2
    assert built['items'][0]['pass_condition'] != built['items'][1]['pass_condition']
    analysis['value'] = comparison['value']
    with pytest.raises(ValueError, match='Duplicate semantic'):
        contract.validate_requirements(requirements, catalog)


def test_cross_kind_targets_are_not_merged_by_a_fact_only_ownership_heuristic():
    catalog, requirements = ledger('保留来历待考状态，并说明核验办法。',
                                   [('fact', '来历待考'), ('information_boundary', '说明核验办法')])
    fact, disclosure = requirements['source_targets']
    fact.update(object='来历', attribute='核验', value='待考', state='待考')
    disclosure.update(object='来历', attribute='核验', value='列出核验办法')
    # Distinct kinds are not a structural duplicate; an auditor must still
    # check that the disclosure does not also own the fact's uncertainty.
    contract.validate_requirements(requirements, catalog)


def redundant_graph_fixture():
    catalog, raw = ledger('先四线对比，再深挖1号线。\n四条线的给定数据。',
                           [('deliverable', '四线对比'), ('deliverable', '1号线深挖'),
                            ('presentation', '模块先后顺序')])
    comparison, analysis, order = raw['source_targets']
    comparison['source_ids'].append(catalog[1]['id'])
    order['relation'] = {'type': 'before', 'target_ids': [comparison['id'], analysis['id']]}
    # The three actual K3 failure classes: Q metadata only names the mandate,
    # relation references are not repeated, and reverse source links diverge.
    raw['source_coverage'][0]['target_ids'] = ['T1', 'T2']
    raw['source_coverage'][1].update(role='required', target_ids=['T2'], rationale='数据支撑四线对比目标')
    return catalog, raw


def test_target_graph_derivation_fixes_only_redundant_metadata_from_real_k3_classes():
    catalog, raw = redundant_graph_fixture()
    snapshot = copy.deepcopy(raw)
    canonical, changes = contract.normalize_source_graph(raw, catalog)
    contract.validate_requirements(canonical, catalog)
    assert raw == snapshot
    assert canonical is not raw
    assert canonical['source_targets'] == raw['source_targets']
    assert canonical['requirements'][0]['source_ids'] == raw['source_targets'][0]['source_ids']
    assert canonical['requirements'][2]['reference_target_ids'] == ['T1', 'T2']
    assert canonical['source_coverage'][0]['target_ids'] == ['T1', 'T2', 'T3']
    assert canonical['source_coverage'][1]['target_ids'] == ['T1']
    assert len(changes) == 4
    assert {row['path'] for row in changes} == {'requirements[0].source_ids',
        'requirements[2].reference_target_ids', 'source_coverage[0].target_ids', 'source_coverage[1].target_ids'}
    for old, new in zip(raw['requirements'], canonical['requirements']):
        for field in ('id', 'kind', 'importance', 'text', 'target_ids'):
            assert old[field] == new[field]
    for old, new in zip(raw['source_coverage'], canonical['source_coverage']):
        assert (old['role'], old['rationale']) == (new['role'], new['rationale'])
    repeated, second_changes = contract.normalize_source_graph(canonical, catalog)
    assert repeated == canonical and second_changes == []


@pytest.mark.parametrize('location', ['q_source', 'q_reference', 'target_source', 'condition_source',
                                      'relation_target', 'coverage_source', 'coverage_target'])
def test_graph_derivation_never_erases_invalid_raw_references(location):
    catalog, raw = redundant_graph_fixture()
    if location == 'q_source':
        raw['requirements'][0]['source_ids'] = ['fabricated']
    elif location == 'q_reference':
        raw['requirements'][0]['reference_target_ids'] = ['fabricated']
    elif location == 'target_source':
        raw['source_targets'][0]['source_ids'] = ['fabricated']
    elif location == 'condition_source':
        raw['source_targets'][0]['condition']['source_ids'] = ['fabricated']
    elif location == 'relation_target':
        raw['source_targets'][2]['relation']['target_ids'] = ['fabricated']
    elif location == 'coverage_source':
        raw['source_coverage'][0]['source_id'] = 'fabricated'
    else:
        raw['source_coverage'][0]['target_ids'] = ['fabricated']
    snapshot = copy.deepcopy(raw)
    with pytest.raises(ValueError):
        contract.normalize_source_graph(raw, catalog)
    assert raw == snapshot


def test_context_role_conflict_remains_a_failure_after_graph_derivation():
    catalog, raw = redundant_graph_fixture()
    raw['source_coverage'][1]['role'] = 'context'
    canonical, changes = contract.normalize_source_graph(raw, catalog)
    assert canonical['source_coverage'][1]['role'] == 'context'
    assert canonical['source_coverage'][1]['target_ids'] == ['T1']
    with pytest.raises(ValueError, match='Only context'):
        contract.validate_requirements(canonical, catalog)


def test_existing_nonowning_references_are_preserved_in_relation_union():
    catalog, raw = redundant_graph_fixture()
    raw['requirements'][2]['reference_target_ids'] = ['T2']
    canonical, _changes = contract.normalize_source_graph(raw, catalog)
    assert canonical['requirements'][2]['reference_target_ids'] == ['T2', 'T1']
    contract.validate_requirements(canonical, catalog)


def test_publisher_keeps_raw_json_and_passes_canonical_with_derivations_to_auditor(tmp_path, monkeypatch):
    catalog, raw = redundant_graph_fixture()
    snapshot = copy.deepcopy(raw)
    canonical, derivations = contract.normalize_source_graph(raw, catalog)
    calls = []

    class Client:
        def ask(self, role, _system, payload, validate):
            calls.append((role, copy.deepcopy(payload)))
            result = raw if role == 'generator' else approval(canonical)
            validate(result)
            return result

    monkeypatch.setattr(contract, 'case_sources', lambda *_: [
        {'id': 'prompt', 'text': '先四线对比，再深挖1号线。\n四条线的给定数据。'}])
    published = contract.generate_contract({'id': 'fixture'}, {
        'dimension_weights': dict(contract.FIXED_WEIGHTS), 'generator_model': 'fixture',
        'reviewer_model': 'fixture', 'max_revisions': 0}, Client(), tmp_path)
    directory = tmp_path / 'fixture'
    assert raw == snapshot
    assert json.loads((directory / 'raw-requirements-0.json').read_text()) == snapshot
    assert json.loads((directory / 'requirements-0.json').read_text()) == canonical
    assert json.loads((directory / 'source-graph-derivations-0.json').read_text()) == derivations
    assert calls[1][1]['requirements'] == {key: canonical[key]
                                           for key in ('task_summary', 'source_policy', 'requirements')}
    assert calls[1][1]['rubric']['source_targets'] == canonical['source_targets']
    assert calls[1][1]['source_graph_derivations'] == derivations
    assert published['requirements'] == canonical
    assert published['source_graph_derivations'] == derivations


def fifteen_target_audit_fixture():
    # Same interface pattern as the real K3 response: two optional targets,
    # one ordering relation, and a source fragment with scoring plus background.
    catalog, requirements = ledger('完成15个独立来源目标；背景材料无需逐字呈现。',
                                   [('fact' if n in {2, 14} else 'presentation' if n == 15 else 'deliverable',
                                     f'独立来源目标{n}') for n in range(1, 16)])
    for index in (1, 13):
        target = requirements['source_targets'][index]
        target.update(mandatory=False, condition={'mode': 'if_present', 'text': '可选参考出现',
                                                  'source_ids': target['source_ids']})
    requirements['source_targets'][14]['relation'] = {'type': 'sequence', 'target_ids': ['T3', 'T4', 'T5']}
    requirements['requirements'][14]['reference_target_ids'] = ['T3', 'T4', 'T5']
    requirements['source_coverage'][0]['role'] = 'mixed'
    requirements['source_coverage'][0]['rationale'] = '独立目标需要检查；背景材料无需逐字呈现'
    contract.validate_requirements(requirements, catalog)
    return catalog, requirements, suite(requirements)


def test_required_audit_checks_enumerates_all_18_operations_without_answers():
    _catalog, _requirements, built = fifteen_target_audit_fixture()
    checklist = contract.required_audit_checks(built)
    operations = checklist['counterexample_checks']
    assert len(operations) == 18
    assert len({(row['target_id'], row['operation']) for row in operations}) == 18
    pairs = {(row['target_id'], row['operation']) for row in operations}
    assert {('T2', 'change_owned_property'), ('T2', 'condition_absent'),
            ('T14', 'change_owned_property'), ('T14', 'condition_absent'),
            ('T15', 'change_owned_property'), ('T15', 'reverse_relation')} <= pairs
    assert all(set(row) == {'target_id', 'operation'} for row in operations)
    assert checklist['counts'] == {'source_fragments': 1, 'targets': 15, 'counterexamples': 18}
    assert len(checklist['target_checks']) == 15


def test_mixed_checklist_requires_separate_context_goal_and_cannot_be_replaced_by_rationale():
    catalog, requirements, built = fifteen_target_audit_fixture()
    checklist = contract.required_audit_checks(built)
    coverage = checklist['coverage_checks'][0]
    assert coverage['role_to_audit'] == 'mixed'
    assert sum(slot['disposition'] == 'scored' for slot in coverage['source_goal_slots']) == 15
    assert {'disposition': 'context', 'target_ids': []} in coverage['source_goal_slots']
    review = approval(requirements)
    review['coverage_checks'][0]['rationale'] = '已核对所有目标，另有背景材料无需逐字呈现'
    with pytest.raises(ValueError, match='both scoring goals and context'):
        contract.validate_review(review, built, catalog)
    review['coverage_checks'][0]['source_goals'].append({
        'goal': '背景材料无需逐字呈现', 'disposition': 'context', 'target_ids': [],
        'rationale': '原文明确为背景，无独立硬性呈现义务'})
    contract.validate_review(review, built, catalog)
    # A reviewer who disagrees with the extracted role must reject rather than
    # invent background merely to match the interface checklist.
    contract.validate_review({'approved': False, 'issues': [{
        'item_ids': [], 'source_ids': [catalog[0]['id']],
        'counterexample': '该原文实际全部是明确目标，mixed分类没有背景依据',
        'fix': '按真实来源修订片段角色后再审核'}]}, built, catalog)


def test_optional_and_relation_checks_do_not_replace_change_owned_property():
    catalog, requirements, built = fifteen_target_audit_fixture()
    review = approval(requirements)
    review['coverage_checks'][0]['source_goals'].append({
        'goal': '背景材料', 'disposition': 'context', 'target_ids': [], 'rationale': '非评分上下文'})
    review['counterexample_checks'] = [check for check in review['counterexample_checks']
        if not (check['target_id'] in {'T2', 'T14', 'T15'} and check['operation'] == 'change_owned_property')]
    assert len(review['counterexample_checks']) == 15
    with pytest.raises(ValueError, match='omitted required'):
        contract.validate_review(review, built, catalog)


def test_audit_payload_contains_one_source_graph_and_one_fixed_library_copy():
    catalog, requirements, built = fifteen_target_audit_fixture()
    snapshot = copy.deepcopy((catalog, requirements, built))
    payload = contract.build_audit_payload(catalog, requirements, built,
        source_graph_derivations=[{'path': 'requirements[0].source_ids', 'reason': 'derived metadata'}])
    assert set(payload['requirements']) == {'task_summary', 'source_policy', 'requirements'}
    assert 'source_targets' not in payload and 'source_coverage' not in payload
    assert 'fixed_quality_criteria' not in payload and 'fixed_visual_criteria' not in payload
    assert payload['rubric']['source_targets'] == requirements['source_targets']
    assert payload['rubric']['source_coverage'] == requirements['source_coverage']
    assert payload['rubric']['items'] == built['items']
    assert payload['required_audit_checks']['counts']['counterexamples'] == 18
    assert payload['audit_interface_fingerprint'] == contract.audit_interface_fingerprint()
    payload['requirements']['requirements'][0]['text'] = 'changed copy'
    assert (catalog, requirements, built) == snapshot
