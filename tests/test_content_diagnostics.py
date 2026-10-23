import copy

import pytest

from ppt_rubric.common import read_json
from ppt_rubric.content_diagnostics import run_diagnostics
from ppt_rubric.content_evidence import EXTRACT_PROMPT, TASK_PROMPT
from ppt_rubric.contract_judge import CONTENT_PROMPT


class Client:
    def __init__(self):
        self.calls = []

    def ask(self, role, system, payload, images, validate):
        assert not images
        self.calls.append((system, copy.deepcopy(payload)))
        if system == EXTRACT_PROMPT:
            return {'coverage': [{'page': 1, 'channels': ['text'], 'areas_checked': ['all']}],
                    'observations': [{'id': 'o1', 'page': 1, 'statement': '已完成工程样机',
                         'category': 'achievement', 'source_ids': [],
                         'attributes': {k: '未说明' for k in ('subject', 'unit', 'time_origin', 'state', 'assumptions')}}]}
        row = {'rubric_id': 'I_BOUNDARY', 'answer': 'no', 'pages': [1], 'evidence': 'source does not support this claim',
               'defect': {'key': 'document/1/prototype', 'description': 'unsupported achievement'}}
        if system == TASK_PROMPT:
            row['checks'] = {'observation_ids': ['batch0/o1'], 'counterevidence_ids': [], 'searched_pages': [1],
                             'applicability': 'applies', 'exception_quote': None, 'reasoning_checks': []}
        else:
            assert system == CONTENT_PROMPT
        return {'verdicts': [row]}


def test_diagnostics_withhold_expectations_and_never_share_responses(tmp_path):
    sample = read_json('tests/fixtures/content-diagnostics-v24.json')['samples'][0]
    client = Client()
    result = run_diagnostics({'samples': [sample]}, client, {'judge_model': 'test'}, tmp_path)
    assert len(client.calls) == 3
    assert client.calls[0][1]['criteria'][0]['judge_prompt'] == client.calls[-1][1]['criteria'][0]['judge_prompt']
    assert client.calls[0][1]['pages'] == client.calls[-1][1]['pages']
    for _, payload in client.calls:
        assert not {'expected', 'expectation_basis', 'mechanism', 'winner', 'other_answer'} & payload.keys()
    assert 'evidence_ledger' not in client.calls[1][1]
    assert result['monolithic']['matches'] == result['evidence_tasks']['matches'] == 1
    assert result['production_acceptance'] is False


def test_preregistered_contrasts_have_both_normal_and_defect_controls():
    data = read_json('tests/fixtures/content-diagnostics-v24.json')
    mechanisms = {}
    for row in data['samples']:
        mechanisms.setdefault(row['mechanism'], []).append(row)
        assert row['criterion']['evidence_mode'] == 'text'
    assert len(mechanisms) == 10
    assert all({r['expected'] for r in pair} == {'yes', 'no'} for pair in mechanisms.values())
    assert all(len({r['criterion']['judge_prompt'] for r in pair}) == 1 for pair in mechanisms.values())


@pytest.mark.parametrize('failure_stage', ['monolithic', 'extraction', 'task'])
def test_rejected_or_partial_responses_cannot_count_as_valid_matches(tmp_path, failure_stage):
    class InvalidClient(Client):
        def ask(self, role, system, payload, images, validate):
            response = super().ask(role, system, payload, images, validate)
            if failure_stage == 'monolithic' and system == CONTENT_PROMPT:
                response['verdicts'][0]['pages'] = [999]
            elif failure_stage == 'extraction' and system == EXTRACT_PROMPT:
                response['coverage'][0]['page'] = 999
            elif failure_stage == 'task' and system == TASK_PROMPT:
                response['verdicts'][0]['pages'] = [999]
            return response

    sample = read_json('tests/fixtures/content-diagnostics-v24.json')['samples'][0]
    report = run_diagnostics({'samples': [sample]}, InvalidClient(), {'judge_model': 'test'}, tmp_path)
    record = read_json(tmp_path / 'results.json')['records'][0]
    failed_method = 'monolithic' if failure_stage == 'monolithic' else 'evidence_tasks'
    assert record[failed_method]['status'] != 'success'
    assert record[failed_method + '_answer'] is None
    assert record[failed_method + '_matches'] is None
    assert report[failed_method]['matches'] == 0
    assert report[failed_method]['unavailable'] == 1
    assert report[failed_method]['unknown'] == 0
    if failure_stage == 'monolithic':
        # Preserve the original model answer even though the interface rejected it.
        assert record['monolithic']['raw']['verdicts'][0]['answer'] == sample['expected']
        assert record['monolithic']['raw']['verdicts'][0]['pages'] == [999]
    else:
        assert record['monolithic_matches'] is True


def test_valid_unknown_is_distinct_from_an_unavailable_response(tmp_path):
    class UnknownClient(Client):
        def ask(self, role, system, payload, images, validate):
            response = super().ask(role, system, payload, images, validate)
            if system != EXTRACT_PROMPT:
                response['verdicts'][0].update(answer='unknown', defect=None)
            return response

    sample = read_json('tests/fixtures/content-diagnostics-v24.json')['samples'][0]
    report = run_diagnostics({'samples': [sample]}, UnknownClient(), {'judge_model': 'test'}, tmp_path)
    for method in ('monolithic', 'evidence_tasks'):
        assert report[method]['unknown'] == 1
        assert report[method]['unavailable'] == 0
        assert report[method]['matches'] == 0
