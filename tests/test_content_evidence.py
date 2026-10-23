import copy

import pytest

from ppt_rubric import content_evidence as ce
from ppt_rubric.contract_judge import _assess_document, validate_visual_batch
from ppt_rubric.source_catalog import source_catalog


CATALOG = source_catalog([{'id': 'prompt', 'text': '允许合理未来计划，不能伪造已完成成果。'}])
ITEM = {'id': 'I_BOUNDARY', 'dimension': 'integrity', 'score': 100, 'evidence_mode': 'text',
        'judge_prompt': '新增成果需要来源。明确未来计划允许。没有新增事实时不适用。'}


def observation(page=1, oid='p1_o1', category='achievement'):
    return {'id': oid, 'page': page, 'statement': '已完成工程样机。', 'category': category,
            'source_ids': [], 'attributes': dict(subject='团队', unit='不适用', time_origin='今年',
                                                state='已完成', assumptions='未说明')}


def ledger():
    return {'coverage': [{'page': 1, 'channels': ['text'], 'areas_checked': ['正文', '页脚']}],
            'observations': [observation()]}


def response():
    return {'verdicts': [{'rubric_id': 'I_BOUNDARY', 'answer': 'no', 'pages': [1],
        'evidence': '已完成的新增具体成果没有来源。', 'defect': {'key': 'doc/1/工程样机', 'description': '无来源成果'},
        'checks': {'observation_ids': ['p1_o1'], 'counterevidence_ids': [], 'searched_pages': [1],
                   'applicability': 'applies', 'exception_quote': None, 'reasoning_checks': []}}]}


def test_text_extraction_cannot_claim_image_coverage_or_skip_pages():
    ce.validate_ledger(ledger(), {1}, CATALOG, images_provided=False)
    bad = ledger(); bad['coverage'][0]['channels'].append('image')
    with pytest.raises(ValueError, match='actually supplied'):
        ce.validate_ledger(bad, {1}, CATALOG, images_provided=False)
    with pytest.raises(ValueError, match='every supplied page'):
        ce.validate_ledger(ledger(), {1, 2}, CATALOG, images_provided=False)


def test_exemption_must_cite_actual_frozen_clause_and_references_are_bound():
    result = response()
    ce.validate_task(result, [ITEM], [1], CATALOG, ledger(), 'boundary')
    result['verdicts'][0]['checks'].update(applicability='condition_not_met', exception_quote='这个主题不重要所以可省略')
    with pytest.raises(ValueError, match='frozen criterion'):
        ce.validate_task(result, [ITEM], [1], CATALOG, ledger(), 'boundary')


def test_heading_substring_and_mandatory_topic_cannot_grant_an_exemption():
    item = {**ITEM, 'id': 'R_TOPICS', 'applicability': '用户明确要求两个话题，必须呈现。',
            'exclusions': '允许等价表达。', 'judge_prompt': '目标：两个话题。用户明确要求两个话题，必须呈现。允许等价表达。'}
    result = response(); row = result['verdicts'][0]
    row.update(rubric_id='R_TOPICS', answer='yes', defect=None)
    row['checks'].update(observation_ids=[], applicability='condition_not_met', exception_quote='目标')
    with pytest.raises(ValueError, match='complete authorized clause'):
        ce.validate_task(result, [item], [1], CATALOG, ledger(), 'source_targets')
    row['checks']['exception_quote'] = item['applicability']
    with pytest.raises(ValueError, match='Mandatory targets cannot be waived'):
        ce.validate_task(result, [item], [1], CATALOG, ledger(), 'source_targets')
    row['checks'].update(applicability='applies', exception_quote=item['exclusions'])
    with pytest.raises(ValueError, match='exclusions cannot substitute for evidence'):
        ce.validate_task(result, [item], [1], CATALOG, ledger(), 'source_targets')


def test_typed_optional_target_has_actual_condition_authority():
    item = {**ITEM, 'id': 'R_OPTIONAL', 'applicability': '只有出现可选标杆时核验，未出现自然满足。',
            'semantic_target': {'condition': {'mode': 'if_present'}}}
    result = response(); row = result['verdicts'][0]
    row.update(rubric_id='R_OPTIONAL', answer='yes', defect=None)
    row['checks'].update(observation_ids=[], applicability='condition_not_met', exception_quote=item['applicability'])
    ce.validate_task(result, [item], [1], CATALOG, ledger(), 'source_targets')


def test_support_reference_cannot_silently_point_to_a_different_page():
    result = response(); result['verdicts'][0]['pages'] = [2]
    result['verdicts'][0]['checks']['searched_pages'] = [1, 2]
    with pytest.raises(ValueError, match='supporting observation pages'):
        ce.validate_task(result, [ITEM], [1, 2], CATALOG, ledger(), 'boundary')
    result = response(); result['verdicts'][0]['checks']['observation_ids'] = ['other-document-observation']
    with pytest.raises(ValueError, match='actual unique observations'):
        ce.validate_task(result, [ITEM], [1], CATALOG, ledger(), 'boundary')


def test_reasoning_requires_every_extracted_chain_and_complete_search():
    data = ledger(); data['observations'][0]['category'] = 'reasoning'
    result = response()
    with pytest.raises(ValueError, match='every extracted reasoning claim'):
        ce.validate_task(result, [ITEM], [1], CATALOG, data, 'reasoning')
    result['verdicts'][0]['checks']['reasoning_checks'] = [{'claim_id': 'p1_o1', 'expression': '48-36=12',
        'premise_status': 'incompatible', 'unit_check': '同为小时', 'time_check': '起点不同', 'result': '缺时间偏移'}]
    ce.validate_task(result, [ITEM], [1], CATALOG, data, 'reasoning')
    result['verdicts'][0]['checks']['searched_pages'] = []
    with pytest.raises(ValueError, match='all supplied pages'):
        ce.validate_task(result, [ITEM], [1], CATALOG, data, 'reasoning')


class EvidenceClient:
    config = {'judge_model': 'test', 'max_images_per_request': 8}

    def __init__(self, fail_page=None):
        self.fail_page = fail_page
        self.calls = []

    def ask(self, role, prompt, payload, images, **kwargs):
        self.calls.append(copy.deepcopy(payload))
        if prompt == ce.EXTRACT_PROMPT:
            if payload['pages'][0]['page'] == self.fail_page:
                raise TimeoutError('fixture extraction failure')
            return {'coverage': [{'page': p['page'], 'channels': payload['evidence_channels'],
                                  'areas_checked': ['all text']} for p in payload['pages']],
                    'observations': [observation(p['page'], f"p{p['page']}_o1") for p in payload['pages']]}
        result = response()
        row = result['verdicts'][0]
        row.update(answer='yes', defect=None)
        row['checks']['observation_ids'] = [o['id'] for o in payload['evidence_ledger']['observations']]
        row['checks']['searched_pages'] = [p['page'] for p in payload['pages']]
        return result


def test_partial_extraction_cannot_certify_global_yes_and_retains_raw_answer():
    deck = {'identity': {'sha256': 'abc'}, 'slides': [
        {'page': p, 'text': '团队计划', 'image': '/does/not/need/to/exist.png'} for p in (1, 2)]}
    rubric = {'items': [ITEM], 'dimension_weights': {'integrity': 100}}
    cfg = {'content_strategy': 'evidence_tasks', 'content_evidence_channel': 'text', 'content_evidence_batch_size': 1}
    assessor = EvidenceClient(fail_page=2)
    result = _assess_document(deck, rubric, cfg, assessor, CATALOG)
    assert result['verdicts'][0]['answer'] == 'unknown'
    assert result['verdicts'][0]['original_answer'] == 'yes'
    assert result['content']['tasks'][0]['raw']['verdicts'][0]['answer'] == 'yes'
    assert result['content']['missing_pages'] == [2]
    assert result['summary']['score'] is None


def test_each_assessor_extracts_independently_and_text_never_certifies_image_criterion():
    deck = {'identity': {'sha256': 'abc'}, 'slides': [{'page': 1, 'image': '/unused', 'text': '原始输入'}]}
    rubric = {'items': [{**ITEM, 'evidence_mode': 'both'}], 'dimension_weights': {'integrity': 100}}
    cfg = {'content_strategy': 'evidence_tasks', 'content_evidence_channel': 'text'}
    one, two = EvidenceClient(), EvidenceClient()
    for client in (one, two):
        result = _assess_document(deck, rubric, cfg, client, CATALOG)
        assert result['summary']['score'] is None
        assert client.calls[0]['pages'][0]['extracted_text'] == '原始输入'
        assert 'evidence_ledger' not in client.calls[0]
    assert one.calls == two.calls


def test_visual_diagnostics_require_actual_loss_without_changing_binary_policy():
    row = {'rubric_id': 'V_READ', 'answer': 'no', 'pages': [1], 'evidence': '页边金额被截掉',
           'defect': {'key': 'doc/1/金额', 'description': '金额不可见'}}
    data = {'pages': [{'page': 1, 'verdicts': [row],
                     'inspection': {'page_edges': True, 'footer_last_rows': True, 'axes': 'absent'}}]}
    with pytest.raises(ValueError, match='actual object'):
        validate_visual_batch(data, [{'id': 'V_READ'}], [1], require_diagnostics=True)
    row['defect'].update(object='金额', region='右下页边', lost_information='金额末位', extent='local',
                         task_role='财务信息', repair='将金额移回页面')
    validate_visual_batch(data, [{'id': 'V_READ'}], [1], require_diagnostics=True)
