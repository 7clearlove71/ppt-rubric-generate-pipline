"""Input isolation and end-to-end offline checks for the v2.3-derived v2.5."""
import copy
import json
from pathlib import Path

import pytest

from ppt_rubric.common import digest
from ppt_rubric.v25 import contract_v2 as contract
from ppt_rubric.v25 import contract_judge as judge


ROOT = Path(__file__).resolve().parents[1]
HISTORICAL = json.loads((ROOT / 'tests/fixtures/contract-v25-case01-v23.json').read_text())


class BuildClient:
    def __init__(self, cfg):
        self.config = cfg
        self.calls = []

    def ask(self, role, system, payload, validate):
        self.calls.append((role, system, copy.deepcopy(payload)))
        if system == contract.EXTRACT:
            reply = copy.deepcopy(HISTORICAL['rubric']['requirements'])
        elif system == contract.WRITE_CRITERION:
            q = payload['requirement']
            reply = {'requirement_id': q['id'], 'title': q['text'][:16],
                     'question': f'是否满足以下要求：{q["text"]}？'}
        else:
            raise AssertionError('Source audit must use strict visible text transport')
        validate(reply)
        return reply

    def ask_text(self, role, system, payload, validate):
        self.calls.append((role, system, copy.deepcopy(payload)))
        assert system == contract.AUDIT
        reply = '{"issues": []}'
        validate(reply)
        return reply


class JudgeClient:
    def __init__(self, cfg):
        self.config = cfg
        self.calls = []

    def ask_text(self, role, system, payload, images, validate):
        self.calls.append((system, copy.deepcopy(payload), list(images)))
        if payload['evaluation_scope'] == 'document':
            assert system.startswith(judge.JUDGE_PROMPT.rstrip() + '\n')
        assert payload['evaluation_scope'] in {'document', 'page'}
        reply = 'Offline transport fixture, not a quality judgment.\n' + r'\boxed{yes}'
        if 'criteria' in payload:
            reply = json.dumps({'verdicts': [{'rubric_id': c['id'], 'answer': 'yes',
                'evidence': 'Independent fixture ' + c['id']} for c in payload['criteria']]})
        validate(reply)
        return reply


@pytest.fixture
def prepared(tmp_path):
    cfg = json.loads((ROOT / 'configs/contract-v25-k3.json').read_text())
    cfg['pipeline_mode'] = 'full'
    old = copy.deepcopy(HISTORICAL['rubric'])
    sources = copy.deepcopy(HISTORICAL['source_catalog'])
    case = {'id': '01', 'prompt': sources[0]['text'], 'attachments': [],
            'candidates': [{'id': 'A', 'sha256': digest('candidate-A')}]}
    suite = contract.generate_contract(case, cfg, BuildClient(cfg), tmp_path / 'rubrics')
    deck = {'identity': {'sha256': case['candidates'][0]['sha256']}, 'slides': [
        {'page': n, 'image': f'/fixture/page-{n}.png',
         'text': 'PPTX_ONLY_SENTINEL_DO_NOT_SEND', 'rendered_text': f'PDF text page {n}'}
        for n in range(1, 14)]}
    return case, cfg, old, suite, deck


def test_v25_keeps_source_ownership_and_dimension_budgets_when_fixed_items_split(prepared):
    case, cfg, old, suite, _ = prepared
    contract.verify_suite(case, suite, cfg)
    for field in ('requirements',
                  'source_catalog_hash', 'scoring_policy'):
        assert suite[field] == old[field]
    assert suite['dimension_weights'] == contract.FIXED_WEIGHTS
    current = {item['id']: item for item in suite['items']}
    for previous in old['items']:
        if not previous['requirement_ids']:
            continue
        new = current[previous['id']]
        for field in ('id', 'source_ids', 'requirement_ids', 'dimension', 'importance',
                      'scope', 'scope_type', 'applicability', 'exclusions'):
            assert new[field] == previous[field]
        scale = contract.FIXED_WEIGHTS[new['dimension']] / old['dimension_weights'][new['dimension']]
        assert new['score'] == pytest.approx(previous['score'] * scale)
        assert new['judge_prompt'] == new['pass_condition']
    assert suite['provenance']['prompt_version'] == '2026-10-23.contract-v2.5.2'
    assert suite['rubric_hash'] != old['rubric_hash']


def test_single_k3_assessor_receives_only_pdf_text(prepared, tmp_path):
    case, _, _, suite, deck = prepared
    cfg = json.loads((ROOT / 'configs/contract-v25-judge.json').read_text())
    primary = JudgeClient(cfg)
    sources = [{'id': 'prompt', 'text': case['prompt']}]
    result = judge.evaluate_contract(case, suite, cfg, primary, {'A': deck}, sources,
                                     tmp_path / 'judgments')
    content_count = sum(not judge.visual_item(item) for item in suite['items'])
    visual_count = len(suite['items']) - content_count
    for client in (primary,):
        content = [(p, images) for system, p, images in client.calls if p['evaluation_scope'] == 'document']
        assert len(content) == content_count
        assert {p['criterion']['id'] for p, _ in content} == {
            item['id'] for item in suite['items'] if not judge.visual_item(item)}
        for payload, images in content:
            assert len(payload['pages']) == len(images) == 13
            assert 'criteria' not in payload
            assert 'score' not in payload['criterion']
            assert 'task' not in payload and 'response_rules' not in payload
            assert payload['source_catalog'] == contract.source_catalog(sources)
            assert payload['criterion'] == {'id': payload['criterion']['id']}
            assert payload['input_notes'] == judge.CONTENT_INPUT_NOTES
            for n, page in enumerate(payload['pages'], 1):
                assert set(page) == {'page', 'page_id', 'rendered_text'}
                assert page['rendered_text'] == f'PDF text page {n}'
        assert 'PPTX_ONLY_SENTINEL' not in json.dumps(client.calls)
        assert 'extracted_text' not in json.dumps(client.calls)
    assert len(primary.calls) == content_count + len(deck['slides'])
    for system, payload, _ in primary.calls:
        if 'criterion' in payload:
            item = next(i for i in suite['items'] if i['id'] == payload['criterion']['id'])
            assert system == judge.compile_judge_prompt(item)
        else:
            assert system == judge.compile_batch_prompt([i for i in suite['items'] if judge.visual_item(i)])
        assert 'response_rules' not in payload
    report = result['A']
    assert report['pipeline_version'] == contract.VERSION
    assert report['base_pipeline_version'] == contract.BASE_VERSION
    assert report['summary']['status'] == 'complete'
    assert report['scoring_method'] == 'single_k3'
    assert report['summary'] == report['assessment']['summary']
    assert not {'primary', 'verification', 'consensus'} & set(report)
    assert report['judge_prompt_fingerprint'] == digest([contract.VERSION, judge.prompt_bundle()])


@pytest.mark.parametrize('missing', [True, False])
def test_pdf_text_unavailable_fails_before_any_request(prepared, tmp_path, missing):
    case, cfg, _, suite, deck = prepared
    if missing:
        del deck['slides'][0]['rendered_text']
    else:
        deck['slides'][0]['rendered_text'] = None
    client = JudgeClient(cfg)
    with pytest.raises(ValueError, match='rendered PDF'):
        judge.evaluate_contract(case, suite, cfg, client, {'A': deck},
                                [{'id': 'prompt', 'text': case['prompt']}], tmp_path)
    assert client.calls == []


def test_empty_pdf_text_is_not_replaced_with_pptx_text():
    deck = {'identity': {'sha256': digest('image-only')}, 'slides': [
        {'page': 1, 'text': 'PPTX ONLY', 'rendered_text': ''}]}
    assert judge.page_catalog(deck)[0]['rendered_text'] == ''


def test_old_approval_rejected_before_any_request(prepared, tmp_path):
    case, cfg, old, _, deck = prepared
    client = JudgeClient(cfg)
    with pytest.raises(ValueError, match='current contract audit'):
        judge.evaluate_contract(case, old, cfg, client, {'A': deck},
                                [{'id': 'prompt', 'text': case['prompt']}], tmp_path)
    assert client.calls == []


def test_prompt_defines_pdf_text_origin_and_limits():
    for phrase in ('原始PPTX渲染为PDF', 'PyMuPDF', '文本层', '不是OCR',
                   '阅读顺序', '字符', '以原始页面图片为依据'):
        assert phrase in judge.CONTENT_INPUT_NOTES
    assert 'extracted_text' not in judge.CONTENT_INPUT_NOTES


def test_presentbench_shell_keeps_evaluation_task_separate_from_input_protocol(prepared, tmp_path):
    _, cfg, _, suite, deck = prepared
    client = JudgeClient(cfg)
    judge._assess_document(deck, suite, cfg, client, [])
    for system, payload, _ in client.calls:
        if 'criteria' in payload:
            assert system == judge.compile_batch_prompt([i for i in suite['items'] if judge.visual_item(i)])
            assert payload['criteria'] == [{'id': i['id']} for i in suite['items'] if judge.visual_item(i)]
            continue
        item = next(i for i in suite['items'] if i['id'] == payload['criterion']['id'])
        assert system == judge.JUDGE_PROMPT.rstrip() + '\n' + item['pass_condition']
    assert '一个AI智能体的任务是' in judge.JUDGE_PROMPT
    assert '部分满足' in judge.JUDGE_PROMPT and '你必须判为“no”' in judge.JUDGE_PROMPT
    assert r'\boxed{yes}' in judge.JUDGE_PROMPT and r'\boxed{no}' in judge.JUDGE_PROMPT
    assert judge.JUDGE_PROMPT.rstrip().endswith('以下是本次检查的要求。')
    assert all(field not in judge.JUDGE_PROMPT for field in
               ('rubric_id', 'defect', 'source_catalog', 'rendered_text', 'criterion.judge_prompt'))
    page_calls = [payload for _, payload, _ in client.calls if payload['evaluation_scope'] == 'page']
    assert all(p['input_notes'] == judge.VISUAL_INPUT_NOTES for p in page_calls)
    assert all('response_format' not in p and len(p['pages']) == 1 for p in page_calls)


@pytest.mark.parametrize('role', ['k3', 'auditor', 'judge'])
def test_configs_are_explicitly_versioned(role):
    cfg = json.loads((ROOT / f'configs/contract-v25-{role}.json').read_text())
    operation = 'judge' if role in {'judge', 'verifier'} else 'generate'
    contract.validate_config(cfg, operation)
    cfg.pop('contract_version')
    with pytest.raises(ValueError, match='explicit contract-v2.5'):
        contract.validate_config(cfg, operation)


def test_generation_writes_each_extracted_requirement_before_independent_audit(prepared, tmp_path):
    case, cfg, _, _, _ = prepared
    primary, auditor = BuildClient(cfg), BuildClient(cfg)
    suite = contract.generate_contract(case, cfg, primary, tmp_path / 'new', reviewer_client=auditor)
    writings = [p for _, system, p in primary.calls if system == contract.WRITE_CRITERION]
    requirements = suite['requirements']['requirements']
    assert [p['requirement'] for p in writings] == requirements
    assert len(primary.calls) == 1 + len(requirements)
    assert len(auditor.calls) == 1
    assert auditor.calls[0][2] == contract.audit_payload(case, contract.case_sources(case, cfg), suite)
    assert 'candidates' not in json.dumps(primary.calls + auditor.calls)
    for requirement, item in zip(requirements, suite['contract_design']['items']):
        assert item['objective'] == requirement['text'][:16]
        assert item['pass_condition'] == f'是否满足以下要求：{requirement["text"]}？'
    same = contract.generate_contract(case, cfg, primary, tmp_path / 'new', reviewer_client=auditor)
    assert same == suite and len(primary.calls) == 1 + len(requirements)


@pytest.mark.parametrize('change', ['wrong_id', 'extra_score', 'empty_question'])
def test_wording_cannot_supply_scoring_metadata_or_wrong_requirement(change):
    q = {'id': 'Q01'}
    wording = {'requirement_id': 'Q01', 'title': '收入', 'question': '是否准确呈现收入？'}
    if change == 'wrong_id':
        wording['requirement_id'] = 'Q02'
    elif change == 'extra_score':
        wording['score'] = 20
    else:
        wording['question'] = ' '
    with pytest.raises(ValueError):
        contract.validate_wording(wording, q)


def test_generated_wording_cannot_change_ownership_or_exclusions(prepared):
    case, cfg, _, suite, _ = prepared
    catalog = contract.source_catalog(contract.case_sources(case, cfg))
    for field, value in [('exclusions', '忽略所有例外'), ('importance', 3), ('id', 'NEW_ID')]:
        design = copy.deepcopy(suite['contract_design'])
        if design['items'][0][field] == value:
            value = 1
        design['items'][0][field] = value
        with pytest.raises(ValueError):
            contract.validate_design(design, suite['requirements'], catalog, suite['criterion_wordings'])


def test_rehashed_derived_question_must_match_approved_generated_wording(prepared):
    case, cfg, _, suite, _ = prepared
    changed = copy.deepcopy(suite)
    changed['contract_design']['items'][0]['pass_condition'] = '是否添加来源没有要求的内容？'
    rebuilt = contract.compile_suite(changed['contract_design'], cfg['dimension_weights'])
    changed['items'] = rebuilt['items']
    changed['rubric_hash'] = digest({k: v for k, v in changed.items() if k != 'rubric_hash'})
    with pytest.raises(ValueError, match='Generated wording cannot change'):
        contract.verify_suite(case, changed, cfg)
    changed['criterion_wordings'][0]['question'] = changed['contract_design']['items'][0]['pass_condition']
    changed['rubric_hash'] = digest({k: v for k, v in changed.items() if k != 'rubric_hash'})
    with pytest.raises(ValueError, match='approved wording hash'):
        contract.verify_suite(case, changed, cfg)


def test_failed_single_content_item_does_not_discard_other_verdicts(prepared):
    case, cfg, _, suite, deck = prepared
    failed = suite['items'][0]['id']

    class FailOne(JudgeClient):
        def ask_text(self, role, system, payload, images, validate):
            if payload.get('criterion', {}).get('id') == failed:
                self.calls.append((system, copy.deepcopy(payload), list(images)))
                raise RuntimeError('Offline failure')
            return super().ask_text(role, system, payload, images, validate)

    client = FailOne(cfg)
    result = judge._assess_document(deck, suite, cfg, client,
        contract.source_catalog(contract.case_sources(case, cfg)))
    unknown = [v['rubric_id'] for v in result['verdicts'] if v['answer'] == 'unknown']
    assert unknown == [failed]
    assert result['summary']['unresolved_weight'] == suite['items'][0]['score']
    content_count = sum(not judge.visual_item(item) for item in suite['items'])
    assert len(result['content']['records']) == content_count
    assert len(client.calls) == content_count + len(deck['slides'])


def test_non_json_free_text_is_accepted_and_retained_without_structured_citations(prepared):
    _, cfg, _, suite, deck = prepared
    result = judge._assess_document(deck, suite, cfg, JudgeClient(cfg), [])
    records = result['content']['records']
    assert all(row['status'] == 'success' and isinstance(row['raw'], str) for row in records)
    for record in records:
        row = next(v for v in result['verdicts'] if v['rubric_id'] == record['rubric_id'])
        assert row['answer'] == 'yes' and row['pages'] == []
        assert row['evidence'] == record['raw']



def test_page_visual_batches_merge_per_criterion_and_charge_each_weight_once(prepared):
    _, cfg, _, suite, deck = prepared

    class VisualOutcomes(JudgeClient):
        def ask_text(self, role, system, payload, images, validate):
            reply = super().ask_text(role, system, payload, images, validate)
            if payload['evaluation_scope'] == 'page':
                assert len(payload['pages']) == 1
                page = payload['pages'][0]['page']
                rows = json.loads(reply)['verdicts']
                for row in rows:
                    if row['rubric_id'] == 'V_READ' and page == 7:
                        row.update(answer='no', evidence='Offline overlap fixture.')
                    if row['rubric_id'] == 'V_LAYOUT' and page == 10:
                        row.update(answer='unknown', evidence='Offline unavailable image fixture.')
                reply = json.dumps({'verdicts': rows})
            validate(reply)
            return reply

    client = VisualOutcomes(cfg)
    result = judge._assess_document(deck, suite, cfg, client, [])
    visual_ids = {item['id'] for item in suite['items'] if judge.visual_item(item)}
    assert len(result['visual_pages']) == 13
    assert len(result['visual_batches']) == len(deck['slides'])
    assert all({v['rubric_id'] for v in p['verdicts']} == visual_ids
               for p in result['visual_pages'])
    rows = {v['rubric_id']: v for v in result['verdicts']}
    assert rows['V_READ']['answer'] == 'no' and rows['V_READ']['pages'] == [7]
    assert rows['V_LAYOUT']['answer'] == 'unknown' and rows['V_LAYOUT']['pages'] == [10]
    weights = {item['id']: item['score'] for item in suite['items']}
    assert result['summary']['score_lower'] == pytest.approx(100 - weights['V_READ'] - weights['V_LAYOUT'])
    assert result['summary']['score_upper'] == pytest.approx(100 - weights['V_READ'])



def test_audit_feedback_regenerates_wording_within_revision_limit(prepared, tmp_path):
    case, cfg, _, _, _ = prepared

    class RejectOnce(BuildClient):
        def __init__(self, config):
            super().__init__(config)
            self.reviews = 0

        def ask_text(self, role, system, payload, validate):
            if system == contract.AUDIT:
                self.reviews += 1
                if self.reviews == 1:
                    self.calls.append((role, system, copy.deepcopy(payload)))
                    reply = json.dumps({'issues': [{
                        'item_ids': ['R_Q01'],
                        'problem': 'Offline extra-obligation fixture.',
                        'suggestion': 'Only rewrite the question; preserve the extracted requirement.'}]})
                    validate(reply)
                    return reply
            return super().ask_text(role, system, payload, validate)

    client = RejectOnce(cfg)
    suite = contract.generate_contract(case, cfg, client, tmp_path / 'retry')
    n = len(suite['requirements']['requirements'])
    assert client.reviews == 2 and len(client.calls) == 2 * (n + 2)
    writings = [p for _, system, p in client.calls if system == contract.WRITE_CRITERION]
    assert all('audit_feedback' not in p for p in writings[:n])
    assert all(bool(p['audit_feedback']['issues']) for p in writings[n:])
    assert (tmp_path / 'retry' / case['id'] / 'wordings-1.json').exists()


def test_export_contains_common_shell_and_approved_specific_rubrics(prepared):
    case, cfg, _, suite, _ = prepared
    record = contract.export_record(case, suite, cfg)
    assert record['judge_prompts']['common'] == judge.JUDGE_PROMPT
    assert record['judge_prompts']['content'].startswith(judge.JUDGE_PROMPT)
    assert record['judgment_strategy'] == judge.JUDGMENT_STRATEGY
    assert record['scoring_method'] == 'single_k3'
    assert [r['judge_prompt'] for r in record['rubrics']] == [i['judge_prompt'] for i in suite['items']]
    assert [r['system_prompt'] for r in record['rubrics']] == [record['visual_batch_system_prompt'] if judge.visual_item(i) else judge.compile_judge_prompt(i) for i in suite['items']]
    assert all(r['judge_prompt'] == i['pass_condition'] for r, i in zip(record['rubrics'], suite['items']))


def test_offline_plan_does_not_claim_old_two_call_generation_bound(tmp_path, monkeypatch, plan_manifest):
    def no_client(*args, **kwargs):
        raise AssertionError('Offline plan must not create an API client')

    monkeypatch.setattr(contract, 'Client', no_client)
    output = tmp_path / 'plan.json'
    contract.main(['plan', '--manifest', str(plan_manifest),
                   '--config', str(ROOT / 'configs/contract-v25-k3.json'),
                   '--verification-config', str(ROOT / 'configs/contract-v25-auditor.json'),
                   '--cases', '01', '--out', str(output)])
    plan = json.loads(output.read_text())
    assert plan['generation_logical_calls_per_case'] == [2, None]
    assert plan['generation_http_attempts_upper_bound_per_case'] is None
    assert 'generate_each_criterion' in plan['generation_stages']
    assert plan['judge_logical_calls_per_document_formula'] == 'C + P (if V > 0; otherwise C)'
    assert plan['scoring_method'] == 'single_k3'
    assert plan['visual_pages_per_request'] == 1
































def test_actual_judge_inputs_cannot_leak_extractor_policies_or_scoring_metadata(prepared):
    case, cfg, _, suite, deck = prepared
    suite['requirements']['task_summary'] = 'DO_NOT_SEND_GENERATED_SUMMARY'
    suite['requirements']['source_policy'] = 'DO_NOT_SEND_GENERATED_POLICY'
    client = JudgeClient(cfg)
    catalog = contract.source_catalog(contract.case_sources(case, cfg))
    judge._assess_document(deck, suite, cfg, client, catalog)
    encoded = json.dumps(client.calls, ensure_ascii=False)
    assert 'DO_NOT_SEND_GENERATED_' not in encoded
    for system, payload, _ in client.calls:
        if 'criteria' in payload:
            assert system == judge.compile_batch_prompt([i for i in suite['items'] if judge.visual_item(i)])
            assert payload['criteria'] == [{'id': i['id']} for i in suite['items'] if judge.visual_item(i)]
            continue
        item = next(i for i in suite['items'] if i['id'] == payload['criterion']['id'])
        assert system == judge.JUDGE_PROMPT.rstrip() + '\n' + item['pass_condition']
        assert set(payload) == {'evaluation_scope', 'input_notes',
                                'document_id', 'source_catalog', 'pages', 'criterion'}
        assert payload['source_catalog'] == catalog
        assert payload['criterion'] == {'id': item['id']}
        assert all(header not in system for header in ('检查范围：', '适用说明：', '注意：'))


def test_free_text_failure_has_known_request_id_without_invented_content_page_citations(prepared):
    _, cfg, _, suite, deck = prepared
    target = suite['items'][0]['id']
    text = 'The required content is absent in this offline fixture.\n' + r'\boxed{no}'

    class Minimal(JudgeClient):
        def ask_text(self, role, system, payload, images, validate):
            reply = super().ask_text(role, system, payload, images, validate)
            if payload.get('criterion', {}).get('id') == target:
                reply = text
            validate(reply)
            return reply

    result = judge._assess_document(deck, suite, cfg, Minimal(cfg), [])
    record = next(r for r in result['content']['records'] if r['rubric_id'] == target)
    assert record['status'] == 'success' and record['raw'] == text
    attached = next(r for r in result['verdicts'] if r['rubric_id'] == target)
    assert attached['answer'] == 'no' and attached['rubric_id'] == target
    assert attached['evidence'] == text and attached['pages'] == []
    assert attached['defect'] is None



def test_integrity_system_is_exactly_common_shell_then_one_question():
    item = next(i for i in contract.QUALITY_ITEMS if i['id'] == 'I_BOUNDARY')
    question = item['pass_condition']
    compiled = {**item, 'judge_prompt': contract.compile_item(item)}
    assert compiled['judge_prompt'] == question
    assert judge.compile_judge_prompt(compiled) == judge.JUDGE_PROMPT.rstrip() + '\n' + question


def test_prompt_uses_one_final_boxed_and_no_json_output_contract():
    assert 'JSON' not in judge.JUDGE_PROMPT
    assert '只给出一个最终boxed结论' in judge.JUDGE_PROMPT
    assert '不限制回答格式' in judge.JUDGE_PROMPT
    example = judge.JUDGE_PROMPT.split('例如：', 1)[1].split('以下是', 1)[0]
    assert judge.parse_boxed_answer(example) == 'no'
    assert judge.prompt_bundle()['response_mode'] == 'text'
    assert judge.prompt_bundle()['visual_pages_per_request'] == 1



def test_missing_boxed_response_is_unavailable_and_original_text_is_retained(prepared):
    _, cfg, _, suite, deck = prepared
    text = 'Only an explanation, with no explicit boxed conclusion.'

    class NoConclusion(JudgeClient):
        def ask_text(self, role, system, payload, images, validate):
            self.calls.append((system, copy.deepcopy(payload), list(images)))
            return text

    result = judge._assess_document(deck, suite, cfg, NoConclusion(cfg), [])
    assert all(r['status'] == 'unavailable' and r['raw'] == text for r in result['content']['records'])
    assert all(v['answer'] == 'unknown' for v in result['verdicts'])



def test_v25_approval_from_hidden_boundary_protocol_rejected_before_paid_call(prepared, tmp_path):
    case, cfg, _, suite, deck = prepared
    stale = copy.deepcopy(suite)
    stale['provenance']['validator_version'] = 4
    stale['signature'] = digest(stale['provenance'])
    stale['rubric_hash'] = digest({k: v for k, v in stale.items() if k != 'rubric_hash'})
    client = JudgeClient(cfg)
    with pytest.raises(ValueError, match='current contract audit'):
        judge.evaluate_contract(case, stale, cfg, client, {'A': deck},
                                contract.case_sources(case, cfg), tmp_path)
    assert client.calls == []


@pytest.mark.parametrize('text,answer', [
    (r'理由。\boxed{yes}', 'yes'),
    (r'理由。\boxed{ No. }', 'no'),
    (r'理由。\boxed{\text{Yes}}', 'yes'),
    (r'理由。\boxed{\mathrm{no}}', 'no'),
    (r'理由。\boxed{unknown}', 'unknown'),
    (r'\boxed{1} 解释。\boxed{no}', 'no'),
    (r'\boxed{yes} 解释。\boxed{no}', 'yes'),
    (r'\boxed{no} 解释。\boxed{yes}', 'no'),
])
def test_boxed_parser_matches_presentbench_selection_and_normalization(text, answer):
    assert judge.parse_boxed_answer(text) == answer


@pytest.mark.parametrize('text', [None, '', 'A yes without a boxed conclusion.', r'\boxed{maybe}',
                                  r'\boxed{1} explanation \boxed{maybe}'])
def test_missing_or_unrecognized_boxed_answer_is_not_accepted(text):
    with pytest.raises(ValueError):
        judge.parse_boxed_answer(text)


def test_parallel_candidate_assessments_keep_inputs_isolated_and_results_ordered(prepared, tmp_path):
    import threading
    case, _, _, suite, first = prepared
    cfg = json.loads((ROOT / 'configs/contract-v25-judge-fast.json').read_text())
    cfg['assessment_workers'] = 1
    second = copy.deepcopy(first)
    second['identity']['sha256'] = digest('candidate-B')
    case['candidates'].append({'id': 'B', 'sha256': second['identity']['sha256']})
    barrier = threading.Barrier(2)

    class SynchronizedClient(JudgeClient):
        def ask_text(self, role, system, payload, images, validate):
            if payload.get('criterion', {}).get('id') == suite['items'][0]['id']:
                barrier.wait(timeout=5)  # Would fail if A still gated B.
            return super().ask_text(role, system, payload, images, validate)

    client = SynchronizedClient(cfg)
    result = judge.evaluate_contract(case, suite, cfg, client, {'A': first, 'B': second},
                                    [{'id': 'prompt', 'text': case['prompt']}], tmp_path)
    assert list(result) == ['A', 'B']
    content_count = sum(not judge.visual_item(item) for item in suite['items'])
    assert len(client.calls) == 2 * (content_count + len(first['slides']))
    for _, payload, images in client.calls:
        assert all(label.startswith(payload['document_id'] + '/') for label, _ in images)
    assert result['A']['verdicts'] == result['B']['verdicts']


def test_parallel_criteria_and_pages_preserve_full_coverage_and_checkpoint(prepared, tmp_path, monkeypatch):
    import threading
    case, _, _, suite, deck = prepared
    cfg = json.loads((ROOT / 'configs/contract-v25-judge-fast.json').read_text())
    monkeypatch.setattr(judge, 'rendered_text_geometry', lambda d: [])
    monkeypatch.setattr(judge, 'focus_views', lambda *args: [])
    serial = JudgeClient({**cfg, 'assessment_workers': 1})
    expected = judge._assess_document(deck, suite, cfg, serial, [])
    barrier = threading.Barrier(2)
    guard = threading.Lock()
    entrants = []

    class OverlappingClient(JudgeClient):
        def ask_text(self, role, system, payload, images, validate):
            with guard:
                entrants.append(payload.get('criterion', {}).get('id', 'visual_page'))
                early = len(entrants) <= 2
            if early:
                barrier.wait(timeout=5)  # Proves actual within-document overlap.
            return super().ask_text(role, system, payload, images, validate)

    client = OverlappingClient(cfg)
    path = tmp_path / 'checkpoint.json'
    actual = judge._assess_document(deck, suite, cfg, client, [], checkpoint=path)
    assert actual == expected == json.loads(path.read_text())
    assert sorted(digest(c) for c in client.calls) == sorted(digest(c) for c in serial.calls)


@pytest.mark.parametrize('key', ['candidate_workers', 'assessment_workers'])
@pytest.mark.parametrize('value', [0, -1, True, 1.5])
def test_parallel_worker_settings_rejected_before_calls(key, value):
    cfg = json.loads((ROOT / 'configs/contract-v25-judge-fast.json').read_text())
    cfg[key] = value
    with pytest.raises(ValueError, match=key):
        contract.validate_config(cfg, 'judge')
