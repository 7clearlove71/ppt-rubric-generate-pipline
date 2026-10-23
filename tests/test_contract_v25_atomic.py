"""Source-only atomicity contract, revision ownership and scoring budgets."""
import copy
import json
import math
from pathlib import Path

import pytest

from ppt_rubric.common import digest
from ppt_rubric.v25 import contract_v2 as contract


ROOT = Path(__file__).resolve().parents[1]


def wording(question):
    return {'requirement_id': 'Q01', 'title': '检查点', 'question': question}


@pytest.mark.parametrize('question', [
    '是否介绍技术路线？是否提出下一步计划？',
    '是否介绍技术路线? 是否提出下一步计划?',
    '是否介绍技术路线',
    '是否介绍技术路线？另需提出计划。',
])
def test_wording_rejects_explicit_multiple_question_sentences(question):
    with pytest.raises(ValueError, match='one question sentence'):
        contract.validate_wording(wording(question), {'id': 'Q01'})


@pytest.mark.parametrize('question', [
    '是否满足：\n1. 介绍技术路线\n2. 提出下一步计划？',
    '是否满足：\n- 介绍技术路线\n- 提出下一步计划？',
    '是否满足：\n（1）介绍技术路线\n（2）提出下一步计划？',
])
def test_wording_rejects_subquestion_lists_with_only_one_question_mark(question):
    with pytest.raises(ValueError, match='list of subquestions'):
        contract.validate_wording(wording(question), {'id': 'Q01'})


@pytest.mark.parametrize('question', [
    '是否准确呈现2025年收入为100万元？',
    '是否准确呈现良率从78%提升至92%（限定近三年，允许正常舍入）？',
    '是否说明O表示渐近上界（允许语义等价表达）?',
])
def test_wording_preserves_single_fact_qualifiers_and_equivalence(question):
    contract.validate_wording(wording(question), {'id': 'Q01'})


def test_atomicity_prompts_require_semantic_split_and_complete_coverage():
    assert '必须拆成不同requirement' in contract.EXTRACT
    assert '全部保留' in contract.EXTRACT
    assert '同一事实的存在与准确性不拆分' in contract.EXTRACT
    assert '不能列子问题' in contract.WRITE_CRITERION
    assert '后者虽只有一个问号，仍是三个评分点' in contract.WRITE_CRITERION
    assert '必要时在question中分行列出子问题' not in contract.WRITE_CRITERION
    assert '可以独立满足或违反' in contract.AUDIT_TEMPLATE
    assert '通读整套rubric' in contract.AUDIT_TEMPLATE
    assert '保留原条目中所有合理要求' in contract.AUDIT_TEMPLATE
    assert '保留全部来源义务' in contract.REVISION_INSTRUCTION


def test_single_question_mark_does_not_claim_semantic_atomicity():
    # One question mark is only the structural check. The independent source
    # audit must reject these two independently satisfiable deliverables.
    contract.validate_wording(wording('是否介绍技术路线并提出下一步计划？'), {'id': 'Q01'})
    assert '多个可以独立满足或违反的要求' in contract.AUDIT_TEMPLATE


def test_fixed_items_split_original_points_without_inflating_family_budgets():
    items = {item['id']: item for item in contract.QUALITY_ITEMS + contract.VISUAL_ITEMS}
    families = {
        'reason': (['C_CONSISTENCY', 'C_CALC', 'C_COMPARE', 'C_REASON'], 2),
        'boundary': (['I_BOUNDARY'], 2),
        'narrative': (['N_FLOW', 'N_RELEVANCE', 'N_REPEAT'], 1),
        'read': (['V_READ', 'V_LABEL', 'V_RESIDUE'], 3),
        'layout': (['V_LAYOUT', 'V_ENCODING'], 1),
    }
    assert set(items) == {rid for ids, _ in families.values() for rid in ids}
    for ids, original_importance in families.values():
        assert math.isclose(sum(items[rid]['importance'] for rid in ids), original_importance)
    for item in items.values():
        assert len([ch for ch in item['pass_condition'] if ch in '?？']) == 1
        assert item['pass_condition'].endswith('？')

    # Include source criteria in the same dimensions: a split must preserve the
    # original fixed family's share of the dimension, not merely its total.
    design = {'items': [
        {'id': 'R_' + dimension, 'dimension': dimension, 'importance': 3,
         'pass_condition': '是否满足这个独立的来源目标？'}
        for dimension in contract.FIXED_WEIGHTS if dimension != 'visual'
    ]}
    suite = contract.compile_suite(design, contract.FIXED_WEIGHTS)
    scores = {item['id']: item['score'] for item in suite['items']}
    assert math.isclose(sum(scores[rid] for rid in families['reason'][0]), 25 * 2 / 5)
    assert math.isclose(scores['I_BOUNDARY'], 10 * 2 / 5)
    assert math.isclose(sum(scores[rid] for rid in families['narrative'][0]), 15 / 4)
    assert math.isclose(sum(scores[rid] for rid in families['read'][0]), 15)
    assert math.isclose(sum(scores[rid] for rid in families['layout'][0]), 5)
    for dimension, budget in contract.FIXED_WEIGHTS.items():
        assert math.isclose(sum(item['score'] for item in suite['items']
                                if item['dimension'] == dimension), budget)


class AtomicRevisionClient:
    """A source audit finds a semantic bundle that punctuation cannot catch."""

    def __init__(self, config):
        self.config = config
        self.calls = []
        self.round = 0

    def ask(self, role, system, payload, validate):
        self.calls.append((role, system, copy.deepcopy(payload)))
        if system == contract.EXTRACT:
            self.round += 1
            source_id = payload['source_catalog'][0]['id']
            goals = (['介绍技术路线并提出下一步计划'] if self.round == 1
                     else ['介绍技术路线', '提出下一步计划'])
            result = {'task_summary': '项目汇报', 'source_policy': '按给定来源编写',
                      'requirements': [
                          {'id': f'Q{n:02}', 'kind': 'deliverable', 'text': goal,
                           'source_ids': [source_id], 'importance': 2}
                          for n, goal in enumerate(goals, 1)]}
        elif system == contract.WRITE_CRITERION:
            requirement = payload['requirement']
            result = {'requirement_id': requirement['id'], 'title': requirement['text'],
                      'question': '是否' + requirement['text'] + '？'}
        else:
            raise AssertionError('Unexpected source-generation system')
        validate(result)
        return result

    def ask_text(self, role, system, payload, validate):
        self.calls.append((role, system, copy.deepcopy(payload)))
        assert system == contract.AUDIT
        result = {'issues': ([{
            'item_ids': ['R_Q01'],
            'problem': '有路线但无计划，或有计划但无路线，分别独立违反两个目标。',
            'suggestion': '将Q01拆为技术路线和下一步计划两个Q，保留全部义务。'}]
            if self.round == 1 else [])}
        text = json.dumps(result, ensure_ascii=False)
        validate(text)
        return text


def test_audit_revision_splits_compound_requirement_and_retains_ownership(tmp_path):
    config = json.loads((ROOT / 'configs/contract-v25-k3.json').read_text())
    config['pipeline_mode'] = 'full'
    case = {'id': 'atomic', 'prompt': '请介绍技术路线并提出下一步计划。', 'attachments': []}
    client = AtomicRevisionClient(config)
    suite = contract.generate_contract(case, config, client, tmp_path)
    contract.verify_suite(case, suite, config)
    requirements = suite['requirements']['requirements']
    assert [row['text'] for row in requirements] == ['介绍技术路线', '提出下一步计划']
    assert requirements[0]['source_ids'] == requirements[1]['source_ids']
    source_items = [item for item in suite['items'] if item['requirement_ids']]
    assert [item['requirement_ids'] for item in source_items] == [['Q01'], ['Q02']]
    assert [item['judge_prompt'] for item in source_items] == ['是否介绍技术路线？', '是否提出下一步计划？']
    assert sum(item['score'] for item in source_items) == 30
    extraction_calls = [payload for _, system, payload in client.calls if system == contract.EXTRACT]
    assert extraction_calls[1]['revision_instruction'] == contract.REVISION_INSTRUCTION
    assert extraction_calls[1]['previous_requirements']['requirements'][0]['text'] == '介绍技术路线并提出下一步计划'
    assert bool(extraction_calls[1]['audit_feedback']['issues'])


def test_old_atomicity_approval_requires_new_source_audit(tmp_path):
    config = json.loads((ROOT / 'configs/contract-v25-k3.json').read_text())
    config['pipeline_mode'] = 'full'
    case = {'id': 'atomic', 'prompt': '请介绍技术路线并提出下一步计划。', 'attachments': []}
    suite = contract.generate_contract(case, config, AtomicRevisionClient(config), tmp_path)
    assert contract.VALIDATOR_VERSION == 10
    suite['provenance']['validator_version'] = 9
    suite['signature'] = digest(suite['provenance'])
    suite['rubric_hash'] = digest({k: v for k, v in suite.items() if k != 'rubric_hash'})
    with pytest.raises(ValueError, match='current contract audit'):
        contract.verify_suite(case, suite, config)


def test_requested_weight_transfer_preserves_other_fixed_visual_scores():
    design = {'items': [{'id': 'R_Q01', 'dimension': 'fulfillment',
                         'importance': 1, 'pass_condition': '是否完成交付？'}]}
    suite = contract.compile_suite(design, contract.FIXED_WEIGHTS)
    scores = {item['id']: item['score'] for item in suite['items']}
    assert scores['I_BOUNDARY'] == pytest.approx(10)
    assert scores['V_READ'] == pytest.approx(25 / 3)
    assert scores['V_LABEL'] == scores['V_RESIDUE'] == pytest.approx(10 / 3)
    assert scores['V_LAYOUT'] == scores['V_ENCODING'] == pytest.approx(2.5)
    assert sum(scores.values()) == pytest.approx(100)


def test_source_audit_invalid_id_feedback_lists_actual_rubric_ids():
    review = {'issues': [{'item_ids': ['Q01'],
        'problem': '两个目标可分别违反', 'suggestion': '拆分需求'}]}
    with pytest.raises(ValueError) as error:
        contract.validate_review(review, {'items': [{'id': 'R_Q01'}]}, [])
    assert "supplied rubric criterion IDs: ['R_Q01']" in str(error.value)
    assert 'not requirement IDs such as Q01' in str(error.value)
    assert '不要填写需求编号' in contract.AUDIT_TEMPLATE
    assert review['issues'][0]['item_ids'] == ['Q01']
