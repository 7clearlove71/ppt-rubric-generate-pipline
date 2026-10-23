"""Minimal source-audit boundary, strict approval and real provider transport."""
import copy
import json
from pathlib import Path

import pytest

from ppt_rubric.common import read_json
from ppt_rubric.provider import Client
from ppt_rubric.v25 import contract_v2 as contract
from ppt_rubric.v25.source_audit import AUDIT_TEMPLATE, audit_payload, parse_audit_output


@pytest.mark.parametrize('text', [
    '', '{}', 'null', '[]', '{"issues": null}', '{"issues": [], "approved": true}',
    '{"issues": [], "issues": []}', '{"issues": NaN}',
    '```json\n{"issues": []}\n```',
    '{"issues": [{"item_ids": ["Q01"], "problem": "p", "suggestion": "s"}]}',
    '{"issues": [{"item_ids": ["R_Q01", "R_Q01"], "problem": "p", "suggestion": "s"}]}',
    '{"issues": [{"item_ids": [], "problem": " ", "suggestion": "s"}]}',
    '{"issues": [{"item_ids": [], "problem": "p"}]}',
    '{"issues": [{"item_ids": [], "problem": "p", "suggestion": "s", "fix": "f"}]}',
])
def test_invalid_or_legacy_audit_never_becomes_approval(text):
    with pytest.raises(ValueError):
        parse_audit_output(text, ['R_Q01'])


def test_native_issues_preserved_with_empty_ids_for_missing_requirement():
    assert parse_audit_output('{"issues": []}', []) == {'issues': []}
    issue = {'item_ids': [], 'problem': '遗漏原始要求：实施计划', 'suggestion': '增加实施计划检查'}
    assert parse_audit_output(json.dumps({'issues': [issue]}), []) == {'issues': [issue]}


def test_payload_uses_only_actual_questions_and_user_inputs_without_recursive_replacement():
    case = {'prompt': '请保留原文 {{rubric_json}}'}
    suite = {'requirements': 'HIDDEN_REQUIREMENTS', 'items': [
        {'id': 'R_Q01', 'judge_prompt': '是否包含实施计划？', 'score': 30,
         'exclusions': 'HIDDEN_EXCLUSIONS', 'dimension': 'fulfillment'}]}
    sources = [{'id': 'prompt', 'text': case['prompt']},
               {'id': 'attachment_1', 'text': '附件原文 {{用户原始需求全文}}'}]
    payload = audit_payload(case, sources, suite)
    assert case['prompt'] in payload and sources[1]['text'] in payload
    assert all(word not in payload for word in ('HIDDEN_', 'source_policy', 'task_summary'))
    rubrics = payload.split('【待审核rubric】', 1)[1].split('【审核任务】', 1)[0]
    rows = json.loads(rubrics[rubrics.index('[\n'):].strip())
    assert rows == [{'id': 'R_Q01', 'content': '是否包含实施计划？', 'dimension': 'fulfillment', 'score': 30}]
    assert '【用户提供的来源材料】\n无' in audit_payload(case, sources[:1], suite)
    assert '"approved"' not in AUDIT_TEMPLATE


def test_strict_audit_transport_preserves_prompt_raw_output_retries_and_cache(tmp_path, monkeypatch):
    config = read_json('configs/contract-v25-auditor-fast.json')
    config.update(min_request_interval_seconds=0, api_key_env='AUDIT_OFFLINE_KEY')
    monkeypatch.setenv('AUDIT_OFFLINE_KEY', 'offline-fixture')
    monkeypatch.setattr('ppt_rubric.provider.time.sleep', lambda _: None)
    outputs = iter(['{"issues": [], "issues": []}', '{"issues": []}'])
    requests = []

    class Response:
        ok = True
        status_code = 200

        def json(self):
            return {'choices': [{'message': {'content': next(outputs)}, 'finish_reason': 'stop'}],
                    'usage': {'total_tokens': 10}}

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, *args, **kwargs):
            requests.append(copy.deepcopy(kwargs['json']))
            return Response()

    monkeypatch.setattr('ppt_rubric.provider.requests.Session', Session)
    client = Client(config, tmp_path / 'cache', tmp_path / 'ledger')
    payload = '【用户原始需求prompt】\n测试需求\n保留换行和标点。'
    validate = lambda raw: parse_audit_output(raw, ['R_Q01'])
    assert client.ask_text('reviewer', contract.AUDIT, payload, validate=validate) == '{"issues": []}'
    assert requests[0]['messages'][1]['content'] == [{'type': 'text', 'text': payload}]
    assert len(requests) == 2 and len(requests[1]['messages']) == 3
    assert [row['status'] for row in client.attempts] == ['failed', 'success']
    failure = tmp_path / 'ledger' / client.attempts[0]['invalid_response']
    assert failure.exists() and 'issues' in failure.read_text()
    assert client.ask_text('reviewer', contract.AUDIT, payload, validate=validate) == '{"issues": []}'
    assert len(requests) == 2


def test_fixed_library_rejection_uses_two_revisions_then_publishes_with_warning(tmp_path):
    config = read_json('configs/contract-v25-k3-fast.json')
    config['pipeline_mode'] = 'full'
    case = {'id': 'test', 'prompt': '请提供实施计划。', 'attachments': []}

    class FakeClient:
        def __init__(self):
            self.config = config
            self.extractions = 0

        def ask(self, role, system, payload, validate):
            if system == contract.EXTRACT:
                self.extractions += 1
                result = {'task_summary': '实施计划', 'source_policy': '使用原文', 'requirements': [
                    {'id': 'Q01', 'kind': 'deliverable', 'importance': 1, 'text': '提供实施计划',
                     'source_ids': [payload['source_catalog'][0]['id']]}]}
            else:
                result = {'requirement_id': 'Q01', 'title': '实施计划', 'question': '是否提供实施计划？'}
            validate(result)
            return result

        def ask_text(self, role, system, payload, validate):
            raw = '{"issues":[{"item_ids":["V_LAYOUT"],"problem":"固定库问题","suggestion":"改写固定问题"}]}'
            validate(raw)
            return raw

    client = FakeClient()
    result = contract.generate_contract(case, config, client, tmp_path)
    assert result['status'] == 'ready' and client.extractions == 3
    contract.verify_suite(case, result, config)
    assert result['source_audit']['status'] == 'not_passed'
    assert len(result['source_audit']['history']) == 3
    assert (tmp_path / 'test/rubric.json').exists()
    status = read_json(tmp_path / 'test/status.json')
    assert status['source_audit']['reason'] == 'revision_limit'
    assert read_json(tmp_path / 'test/reviews.json')[0]['review']['issues'][0]['problem'] == '固定库问题'
