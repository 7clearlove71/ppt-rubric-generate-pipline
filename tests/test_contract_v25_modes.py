"""Offline flow regression: unaudited/rejected rubrics still reach real judge orchestration."""
import json
from pathlib import Path

import pytest

from ppt_rubric.common import digest, read_json, write_json
from ppt_rubric.contract_report import summarize
from ppt_rubric.v25 import contract_v2 as contract
from ppt_rubric.v25 import contract_judge as judge
from ppt_rubric.v25.source_audit import AUDIT_TEMPLATE

ROOT = Path(__file__).resolve().parents[1]


class FlowClient:
    def __init__(self, config, outcome='reject', revision_failure=False):
        self.config = config
        self.outcome = outcome
        self.revision_failure = revision_failure
        self.rounds = self.audits = self.wordings = 0

    def ask(self, role, system, payload, validate):
        if system == contract.EXTRACT:
            self.rounds += 1
            if self.rounds > 1 and self.revision_failure:
                raise RuntimeError('revision provider unavailable')
            result = {'task_summary': '实施计划', 'source_policy': '按来源', 'requirements': [
                {'id': 'Q01', 'kind': 'deliverable', 'text': '提供实施计划', 'importance': 1,
                 'source_ids': [payload['source_catalog'][0]['id']]}]}
        else:
            self.wordings += 1
            result = {'requirement_id': 'Q01', 'title': '实施计划',
                      'question': f'是否提供实施计划（版本{self.rounds}）？'}
        validate(result)
        return result

    def ask_text(self, role, system, payload, images=(), validate=None):
        if role == 'reviewer':
            self.audits += 1
            if self.outcome == 'error':
                raise RuntimeError('audit timeout after bounded retries')
            if self.outcome == 'invalid':
                raw = '{"issues": [], "approved": true}'
            else:
                issues = [] if self.outcome == 'pass' else [{
                    'item_ids': ['R_Q01'], 'problem': '审核未通过的原始原因', 'suggestion': '修订来源检查问题'}]
                raw = json.dumps({'issues': issues}, ensure_ascii=False)
        elif 'criteria' in payload:
            raw = json.dumps({'verdicts': [{'rubric_id': c['id'], 'answer': 'yes', 'evidence': 'Independent fixture ' + c['id']}
                                         for c in payload['criteria']]})
        else:
            raw = r'Offline fixture, no online quality claim.\boxed{yes}'
        validate(raw)
        return raw


@pytest.fixture
def task():
    config = read_json(ROOT / 'configs/contract-v25-k3.json')
    config['pipeline_mode'] = 'full'
    case = {'id': 'flow', 'prompt': '提供实施计划。', 'attachments': [], 'split': 'dev',
            'candidates': [{'id': cid, 'sha256': digest(cid), 'pptx': f'/fixture/{cid}.pptx'} for cid in ('A', 'B')]}
    return case, config


def manifest_for(case):
    manifest = {'cases': [case]}
    manifest['id'] = digest(manifest)
    return manifest


def test_template_is_exact_latest_user_file_and_all_dimensions_match():
    assert AUDIT_TEMPLATE == (ROOT / 'tests/fixtures/contract-v25-source-audit-template.txt').read_text()
    assert '不得按R_Q、C_、N_' in AUDIT_TEMPLATE


@pytest.mark.parametrize('mode,outcome,expected_status,rounds', [
    ('light', 'error', 'skipped', 1), ('full', 'pass', 'passed', 1),
    ('full', 'reject', 'not_passed', 3), ('full', 'error', 'not_passed', 1),
    ('full', 'invalid', 'not_passed', 1),
])
def test_modes_reach_judge_export_and_summary_with_audit_state(
        task, tmp_path, monkeypatch, mode, outcome, expected_status, rounds):
    case, config = task
    config['pipeline_mode'] = mode
    client = FlowClient(config, outcome)
    suite = contract.generate_contract(case, config, client, tmp_path / 'rubrics')
    contract.verify_suite(case, suite, config)
    assert suite['source_audit']['status'] == expected_status
    assert client.rounds == rounds and client.audits == (0 if mode == 'light' else rounds)
    if outcome == 'reject':
        assert suite['source_audit']['revisions'] == 2
        assert suite['source_audit']['issues'][0]['problem'] == '审核未通过的原始原因'
        assert suite['criterion_wordings'][0]['question'] == '是否提供实施计划（版本3）？'
    if outcome == 'invalid':
        assert suite['source_audit']['reason'] == 'audit_error'
        assert 'issues array' in suite['source_audit']['history'][-1]['message']
    exported = contract.export_record(case, suite, config)
    assert exported['source_audit'] == suite['source_audit']
    assert exported['comparison_policy']['version'] == 'exclude_unknown_across_candidates_v1'
    # No provider requests and no rendering; retain real input validation and scoring/report code.
    monkeypatch.setattr(judge, 'rendered_text_geometry', lambda deck: [])
    monkeypatch.setattr(judge, 'focus_views', lambda *args: [])
    judge_config = read_json(ROOT / 'configs/contract-v25-judge.json')
    decks = {c['id']: {'identity': {'sha256': c['sha256']}, 'slides': [
        {'page': 1, 'image': '/fixture/page.png', 'rendered_text': '实施计划'}]} for c in case['candidates']}
    result = judge.evaluate_contract(case, suite, judge_config, FlowClient(judge_config), decks,
        contract.case_sources(case, config), tmp_path / 'judgments')
    assert all(r['source_audit'] == suite['source_audit'] and r['summary']['score'] == 100
               for r in result.values())
    assert all(r['comparison']['policy'] == exported['comparison_policy'] for r in result.values())
    summary = summarize(manifest_for(case), tmp_path)
    assert summary['cases'][0]['source_audit'] == suite['source_audit']
    assert summary['cases'][0]['pipeline_mode'] == mode
    assert summary['counts']['rubrics_approved'] == (expected_status == 'passed')
    assert summary['counts']['rubrics_audit_' + expected_status] == 1
    # Same-mode resume uses the published result; it cannot silently change audit status.
    before = client.rounds, client.audits
    assert contract.generate_contract(case, config, client, tmp_path / 'rubrics') == suite
    assert (client.rounds, client.audits) == before


def test_failed_revision_uses_previous_complete_rubric_and_preserves_reason(task, tmp_path):
    case, config = task
    client = FlowClient(config, revision_failure=True)
    suite = contract.generate_contract(case, config, client, tmp_path)
    contract.verify_suite(case, suite, config)
    assert suite['status'] == 'ready' and client.rounds == 2 and client.audits == 1
    assert suite['source_audit']['reason'] == 'revision_error'
    assert suite['source_audit']['history'][-1]['message'] == 'revision provider unavailable'
    assert suite['criterion_wordings'][0]['question'] == '是否提供实施计划（版本1）？'
    assert read_json(tmp_path / 'flow/requirements.json') == suite['requirements']


def test_first_generation_failure_cannot_publish_nonexistent_rubric(task, tmp_path):
    case, config = task
    client = FlowClient(config)
    client.ask = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('initial generation failed'))
    with pytest.raises(RuntimeError, match='initial generation failed'):
        contract.generate_contract(case, config, client, tmp_path)
    assert not (tmp_path / 'flow/rubric.json').exists()


def test_switching_modes_archives_rubric_instead_of_reusing_opposite_audit_state(task, tmp_path):
    case, config = task
    config['pipeline_mode'] = 'light'
    client = FlowClient(config, 'pass')
    light = contract.generate_contract(case, config, client, tmp_path)
    config['pipeline_mode'] = 'full'
    full = contract.generate_contract(case, config, client, tmp_path)
    assert full['rubric_hash'] != light['rubric_hash'] and client.audits == 1
    assert full['source_audit']['status'] == 'passed'
    assert len(list((tmp_path / 'flow').glob('rubric-superseded-*.json'))) == 1


def test_light_cli_ignores_auditor_file_and_never_constructs_its_client(task, tmp_path, monkeypatch):
    case, config = task
    manifest, config_path = tmp_path / 'manifest.json', tmp_path / 'config.json'
    write_json(manifest, manifest_for(case))
    config.pop('reviewer_model', None)
    config['pipeline_mode'] = 'light'
    write_json(config_path, config)
    clients = []

    def construct(config, *args, **kwargs):
        clients.append(config)
        return FlowClient(config, 'error')

    monkeypatch.setattr(contract, 'Client', construct)
    args = ['--manifest', str(manifest), '--config', str(config_path),
            '--source-audit-config', str(tmp_path / 'missing-auditor.json')]
    contract.main(['generate', *args, '--out', str(tmp_path / 'rubrics')])
    assert len(clients) == 1
    assert read_json(tmp_path / 'rubrics/run-summary.json')['flow']['source_audit']['status'] == 'skipped'
    contract.main(['plan', *args, '--out', str(tmp_path / 'plan.json')])
    plan = read_json(tmp_path / 'plan.json')
    assert plan['max_revisions'] == 0 and 'audit' not in plan['generation_stages']
    assert plan['generation_logical_calls_formula'] == 'N + 1; N = first extracted requirement count'
    assert plan['generation_request_limits']['auditor_max_retries'] == 0
    assert plan['source_audit_request_budget'] is None
    assert len(clients) == 1


@pytest.mark.parametrize('mode', ['light', 'full'])
def test_cli_judge_displays_audit_warning_without_blocking_score(task, tmp_path, monkeypatch, mode):
    case, config = task
    config['pipeline_mode'] = mode
    suite = contract.generate_contract(case, config, FlowClient(config), tmp_path / 'rubrics')
    manifest = tmp_path / 'manifest.json'
    write_json(manifest, manifest_for(case))
    decks = {c['id']: {'identity': {'sha256': c['sha256']}, 'slides': [
        {'page': 1, 'image': '/fixture/page.png', 'rendered_text': '实施计划'}]} for c in case['candidates']}
    monkeypatch.setattr(contract, 'Client', lambda cfg, *args, **kwargs: FlowClient(cfg))
    monkeypatch.setattr(contract, 'prepare_case', lambda *args: decks)
    monkeypatch.setattr(judge, 'rendered_text_geometry', lambda deck: [])
    monkeypatch.setattr(judge, 'focus_views', lambda *args: [])
    contract.main(['judge', '--manifest', str(manifest), '--config', str(ROOT / 'configs/contract-v25-judge.json'),
                   '--rubrics', str(tmp_path / 'rubrics'), '--out', str(tmp_path / 'judgments')])
    result = read_json(tmp_path / 'judgments/run-summary.json')['flow']
    assert all(row['source_audit'] == suite['source_audit'] and row['score'] == 100 for row in result.values())
