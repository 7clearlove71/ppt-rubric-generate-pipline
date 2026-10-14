import copy
import json
import sys
import threading

import pytest

from ppt_rubric import contract_v2 as contract
from ppt_rubric.common import digest, write_json
from ppt_rubric.source_catalog import source_catalog


SOURCES = [{'id': 'prompt', 'text': '做一份项目简报。'}]


def config(stage='rubric'):
    return {'evaluation_protocol': 'contract_v2', 'pipeline_stage': stage,
            'dimension_weights': dict(contract.FIXED_WEIGHTS), 'base_url': 'https://example.test/v1',
            'generator_model': 'generator', 'reviewer_model': 'auditor', 'judge_model': 'judge',
            'max_revisions': 2, 'api_concurrency': 1, 'max_retries': 3, 'max_output_tokens': 16000,
            'max_images_per_request': 60, 'max_case_text_chars': 100000, 'dpi': 150}


def outputs():
    catalog = source_catalog(SOURCES)
    requirement = {'id': 'Q1', 'kind': 'deliverable', 'text': '项目简报', 'importance': 3,
                   'source_ids': [catalog[0]['id']]}
    requirements = {'task_summary': '简报', 'source_policy': '补充数据需要说明边界',
                    'requirements': [requirement]}
    return [requirements, {'approved': True, 'issues': [], 'summary': '有效'}]


class Client:
    def __init__(self, cfg, replies=None, **_kwargs):
        self.config = cfg
        self.replies = list(replies if replies is not None else outputs())
        self.calls = []

    def ask(self, role, system, payload, validate):
        self.calls.append((role, system, copy.deepcopy(payload)))
        result = copy.deepcopy(self.replies.pop(0))
        validate(result)
        return result


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setattr(contract, 'case_sources', lambda *_args: copy.deepcopy(SOURCES))
    case = {'id': '01', 'prompt': SOURCES[0]['text'], 'split': 'dev',
            'candidates': [{'id': 'A', 'sha256': 'a'}, {'id': 'B', 'sha256': 'b'}]}
    cfg = config()
    suite = contract.generate_contract(case, cfg, Client(cfg), tmp_path / 'rubrics')
    return case, cfg, suite


def rehash(suite):
    suite['signature'] = digest(suite['provenance'])
    suite['rubric_hash'] = digest({key: value for key, value in suite.items() if key != 'rubric_hash'})


def test_approved_contract_checks_full_frozen_definition(prepared):
    case, cfg, suite = prepared
    contract.verify_suite(case, suite, cfg)
    assert suite['source_catalog_hash'] == digest(source_catalog(SOURCES))
    assert suite['provenance']['reviewer_model'] == 'auditor'
    assert suite['requirements']['requirements'][0]['kind'] == 'deliverable'


@pytest.mark.parametrize('change, match', [
    ('score', 'canonical compilation'), ('catalog', 'catalog mismatch'),
    ('prompt', 'fingerprint'), ('compiled_text', 'canonical compilation'),
    ('policy', 'scoring policy'),
])
def test_rehashed_but_incompatible_artifact_is_rejected(prepared, change, match):
    case, cfg, original = prepared
    suite = copy.deepcopy(original)
    if change == 'score':
        suite['items'][0]['score'] += 1
    elif change == 'catalog':
        suite['source_catalog_hash'] = suite['provenance']['source_catalog_hash'] = 'old-catalog'
    elif change == 'prompt':
        suite['provenance']['prompt_fingerprint'] = 'old-prompt'
    elif change == 'compiled_text':
        suite['items'][0]['judge_prompt'] += '\n另外要求'
    else:
        suite['scoring_policy']['page_aggregation'] = 'average'
    rehash(suite)
    with pytest.raises(ValueError, match=match):
        contract.verify_suite(case, suite, cfg)


def test_reviewer_settings_change_archives_old_approval(prepared, tmp_path):
    case, cfg, original = prepared
    published = tmp_path / 'rubrics' / '01' / 'rubric.json'
    original_bytes = published.read_bytes()
    changed = copy.deepcopy(cfg)
    changed['reviewer_model'] = 'independent-auditor'
    changed['request_options'] = {'enable_thinking': True}
    values = outputs()
    generator = Client(cfg, values[:1])
    reviewer = Client(changed, values[1:])
    new = contract.generate_contract(case, cfg, generator, tmp_path / 'rubrics', reviewer_client=reviewer)
    assert new['signature'] != original['signature']
    assert new['provenance']['reviewer_model'] == 'independent-auditor'
    assert new['provenance']['generation_request_hash'] == original['provenance']['generation_request_hash']
    assert new['provenance']['review_request_hash'] != original['provenance']['review_request_hash']
    archives = list(published.parent.glob('rubric-superseded-*.json'))
    assert len(archives) == 1 and archives[0].read_bytes() == original_bytes
    assert [role for role, *_rest in generator.calls] == ['generator']
    assert [role for role, *_rest in reviewer.calls] == ['reviewer']


def test_failed_regeneration_cannot_leave_previous_approved_filename(prepared, tmp_path):
    case, cfg, _suite = prepared
    changed = copy.deepcopy(cfg)
    changed['generator_model'] = 'new-generator'
    class FailedClient(Client):
        def ask(self, *_args, **_kwargs):
            raise RuntimeError('stopped before publication')
    with pytest.raises(RuntimeError):
        contract.generate_contract(case, changed, FailedClient(changed), tmp_path / 'rubrics')
    directory = tmp_path / 'rubrics' / '01'
    assert not (directory / 'rubric.json').exists()
    assert len(list(directory.glob('rubric-superseded-*.json'))) == 1


def test_export_keeps_binary_visual_aggregation_and_source_context(prepared):
    case, cfg, suite = prepared
    result = contract.export_record(case, suite, cfg)
    assert result['schema_version'] == 2
    assert result['source_catalog'] == source_catalog(SOURCES)
    assert result['scoring_policy']['page_aggregation'] == 'any_no_else_unknown_else_all_yes'
    rows = {item['id']: item for item in result['rubrics']}
    assert rows['V_READ']['scope_type'] == 'page'
    assert rows['V_READ']['aggregation'] == 'any_no_else_unknown_else_all_yes'
    assert rows['V_READ']['score'] == 10
    assert rows['R_Q1']['aggregation'] == 'single_deck_verdict'
    assert sum(item['score'] for item in rows.values()) == pytest.approx(100)


@pytest.mark.parametrize('override, operation', [
    ({'pipeline_stage': 'judge'}, 'generate'),
    ({'pipeline_stage': 'rubric'}, 'judge'),
    ({'request_options': {'model': 'override'}}, 'generate'),
    ({'role_request_options': {'generator': {'messages': []}}}, 'generate'),
    ({'max_revisions': -1}, 'generate'), ({'max_revisions': True}, 'generate'),
    ({'max_api_requests': -1}, 'generate'), ({'max_api_requests': True}, 'generate'),
    ({'max_retries': 0}, 'generate'),
    ({'role_max_output_tokens': {'reviewer': float('nan')}}, 'generate'),
    ({'dimension_weights': dict(fulfillment=50, correctness=5, integrity=15, narrative=15, visual=15)}, 'generate'),
])
def test_invalid_profiles_fail_before_paid_work(override, operation):
    cfg = {**config('judge' if operation == 'judge' else 'rubric'), **override}
    with pytest.raises(ValueError):
        contract.validate_config(cfg, operation)


def cli_files(tmp_path, case, cfg):
    manifest = {'schema_version': 1, 'seed': 1, 'cases': [case]}
    manifest['id'] = digest(manifest)
    write_json(tmp_path / 'manifest.json', manifest)
    write_json(tmp_path / 'config.json', cfg)
    return ['--manifest', str(tmp_path / 'manifest.json'), '--config', str(tmp_path / 'config.json')]


def test_cli_judge_incomplete_uses_exit_two(prepared, tmp_path, monkeypatch):
    case, _cfg, _suite = prepared
    options = cli_files(tmp_path, case, config('judge'))
    monkeypatch.setattr(contract, 'Client', lambda cfg, *_args, **_kwargs: Client(cfg))
    monkeypatch.setattr(contract, 'prepare_case', lambda *_args: {})
    import ppt_rubric.contract_judge as judge
    monkeypatch.setattr(judge, 'evaluate_contract', lambda *_args, **_kwargs: {
        'A': {'summary': {'status': 'incomplete', 'score': None}},
        'B': {'summary': {'status': 'complete', 'score': 90}}})
    monkeypatch.setattr(sys, 'argv', ['contract_v2', 'judge', *options, '--out', str(tmp_path / 'judgments'),
                                    '--rubrics', str(tmp_path / 'rubrics')])
    with pytest.raises(SystemExit) as stopped:
        contract.main()
    assert stopped.value.code == 2


def test_cli_passes_verification_client_to_generation(prepared, tmp_path, monkeypatch):
    case, cfg, _suite = prepared
    options = cli_files(tmp_path, case, cfg)
    reviewer_cfg = {**cfg, 'reviewer_model': 'separate-reviewer'}
    write_json(tmp_path / 'reviewer.json', reviewer_cfg)
    calls = []
    def generate(*args, **kwargs):
        calls.append(kwargs['reviewer_client'].config['reviewer_model'])
        return {'status': 'approved', 'items': []}
    monkeypatch.setattr(contract, 'Client', lambda cfg, *_args, **_kwargs: Client(cfg))
    monkeypatch.setattr(contract, 'generate_contract', generate)
    monkeypatch.setattr(sys, 'argv', ['contract_v2', 'generate', *options, '--out', str(tmp_path / 'new'),
                                    '--verification-config', str(tmp_path / 'reviewer.json')])
    contract.main()
    assert calls == ['separate-reviewer']


def test_plan_uses_configured_revision_and_attempt_bounds_without_client(prepared, tmp_path, monkeypatch):
    case, cfg, _suite = prepared
    cfg['max_revisions'] = 0
    options = cli_files(tmp_path, case, cfg)
    def no_client(*_args, **_kwargs):
        pytest.fail('Plan must not construct a provider client')
    monkeypatch.setattr(contract, 'Client', no_client)
    monkeypatch.setattr(sys, 'argv', ['contract_v2', 'plan', *options, '--out', str(tmp_path / 'plan.json')])
    contract.main()
    plan = json.loads((tmp_path / 'plan.json').read_text())
    assert plan['generation_logical_calls_per_case'] == [2, 2]
    assert plan['generation_http_attempts_upper_bound_per_case'] == 6
    assert plan['max_revisions'] == 0


def test_deterministic_compilation_cannot_expand_or_merge_neighbouring_goals():
    requirements = outputs()[0]
    requirements['requirements'].append({
        'id': 'Q2', 'kind': 'fact', 'text': '指标为12%，保留本期口径', 'importance': 2,
        'source_ids': requirements['requirements'][0]['source_ids'],
    })
    design = contract.compile_requirements(requirements)
    assert [item['id'] for item in design['items']] == ['R_Q1', 'R_Q2']
    assert design['items'][0]['requirement_ids'] == ['Q1']
    assert design['items'][1]['requirement_ids'] == ['Q2']
    assert design['items'][1]['dimension'] == 'correctness'
    assert design['items'][1]['importance'] == 2
    assert '12%' not in design['items'][0]['pass_condition']
    assert design['items'][1]['pass_condition'].endswith(requirements['requirements'][1]['text'])
    contract.validate_design(design, requirements, source_catalog(SOURCES))
    design['items'][0]['pass_condition'] += '；所有页面必须有标题。'
    with pytest.raises(ValueError, match='deterministic'):
        contract.validate_design(design, requirements, source_catalog(SOURCES))


def test_source_revision_has_two_calls_per_round_and_never_a_design_call(tmp_path, monkeypatch):
    monkeypatch.setattr(contract, 'case_sources', lambda *_args: copy.deepcopy(SOURCES))
    requirements, approval = outputs()
    rejection = {'approved': False, 'summary': '修正来源目标', 'issues': [{
        'item_ids': ['R_Q1'], 'source_ids': requirements['requirements'][0]['source_ids'],
        'counterexample': '存在应保留但未覆盖的独立目标', 'fix': '补充Q1中的真实来源目标'}]}
    client = Client(config(), [requirements, rejection, requirements, approval])
    suite = contract.generate_contract({'id': '01'}, config(), client, tmp_path)
    assert suite['status'] == 'approved'
    assert [role for role, *_rest in client.calls] == ['generator', 'reviewer', 'generator', 'reviewer']
    assert [system for role, system, _ in client.calls if role == 'generator'] == [contract.EXTRACT] * 2
    retry = client.calls[2][2]
    assert retry['source_catalog'] == source_catalog(SOURCES)
    assert retry['previous_requirements'] == requirements
    assert retry['audit_feedback'] == rejection
    assert retry['revision_instruction'] == contract.REVISION_INSTRUCTION
    assert 'previous_design' not in retry
    assert (tmp_path / '01' / 'requirements-0.json').exists()
    assert (tmp_path / '01' / 'requirements-1.json').exists()


def test_persistent_source_conflict_stops_after_six_logical_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(contract, 'case_sources', lambda *_args: copy.deepcopy(SOURCES))
    requirements = outputs()[0]
    rejection = {'approved': False, 'summary': '源目标重叠仍未消除', 'issues': [{
        'item_ids': ['R_Q1'], 'source_ids': requirements['requirements'][0]['source_ids'],
        'counterexample': '独立目标仍混合在一起', 'fix': '根据来源修订Q1'}]}
    client = Client(config(), [requirements, rejection] * 3)
    result = contract.generate_contract({'id': '01'}, config(), client, tmp_path)
    assert result['status'] == 'quality_failed'
    assert len(client.calls) == 6
    assert not (tmp_path / '01' / 'rubric.json').exists()
    status = json.loads((tmp_path / '01' / 'status.json').read_text())
    assert status['reviews'] == 3 and status['stop_reason'] == 'revision_limit'


def test_fixed_library_issue_stops_without_pretending_requirements_can_repair_it(tmp_path, monkeypatch):
    monkeypatch.setattr(contract, 'case_sources', lambda *_args: copy.deepcopy(SOURCES))
    requirements = outputs()[0]
    rejection = {'approved': False, 'summary': '固定库有实质问题', 'issues': [{
        'item_ids': ['V_LAYOUT'], 'source_ids': [],
        'counterexample': '固定布局政策自身存在问题', 'fix': '仅修Q无法修复固定库'}]}
    client = Client(config(), [requirements, rejection])
    result = contract.generate_contract({'id': '01'}, config(), client, tmp_path)
    assert result['status'] == 'quality_failed'
    assert len(client.calls) == 2
    status = json.loads((tmp_path / '01' / 'status.json').read_text())
    assert status['stop_reason'] == 'fixed_library_issue_not_repairable_by_source_extraction'


def test_missing_source_goal_can_be_audited_without_inventing_existing_item_id():
    requirements = outputs()[0]
    suite = contract.compile_suite(contract.compile_requirements(requirements), contract.FIXED_WEIGHTS)
    issue = {'item_ids': [], 'source_ids': requirements['requirements'][0]['source_ids'],
             'counterexample': '这段来源中的明确目标未被提取', 'fix': '补充对应来源目标'}
    review = {'approved': False, 'issues': [issue]}
    contract.validate_review(review, suite, source_catalog(SOURCES))
    issue['source_ids'] = []
    with pytest.raises(ValueError, match='omitted requirement'):
        contract.validate_review(review, suite, source_catalog(SOURCES))


def test_compiler_template_change_invalidates_previous_approval(prepared, monkeypatch):
    case, cfg, suite = prepared
    changed = copy.deepcopy(contract.REQUIREMENT_TEMPLATES)
    changed['deliverable']['prefix'] += '另一个规则：'
    monkeypatch.setattr(contract, 'REQUIREMENT_TEMPLATES', changed)
    with pytest.raises(ValueError, match='fingerprint'):
        contract.verify_suite(case, suite, cfg)


@pytest.mark.parametrize('operation, second_case_status, expected_exit', [
    ('generate', 'success', None), ('generate', 'error', 1),
    ('judge', 'unknown', 2), ('judge', 'error', 1),
])
def test_parallel_cases_share_clients_and_only_root_writes_summary(
        prepared, tmp_path, monkeypatch, operation, second_case_status, expected_exit):
    first_case, _cfg, suite = prepared
    second_case = {**copy.deepcopy(first_case), 'id': '02'}
    cfg = config('judge' if operation == 'judge' else 'rubric')
    options = cli_files(tmp_path, first_case, cfg)
    manifest = {'schema_version': 1, 'seed': 1, 'cases': [first_case, second_case]}
    manifest['id'] = digest(manifest)
    write_json(tmp_path / 'manifest.json', manifest)
    write_json(tmp_path / 'verification.json', cfg)
    write_json(tmp_path / 'rubrics' / '02' / 'rubric.json', suite)
    barrier = threading.Barrier(2, timeout=5)
    root_thread = threading.get_ident()
    created, received, summary_threads = [], [], []
    lock = threading.Lock()

    def make_client(config, *_args, **_kwargs):
        assert threading.get_ident() == root_thread
        result = Client(config)
        created.append(result)
        return result

    real_write_json = contract.write_json
    def track_summary(path, value):
        if str(path).endswith('run-summary.json'):
            summary_threads.append(threading.get_ident())
        real_write_json(path, value)

    def assess(case, primary, verification):
        with lock:
            received.append((case['id'], primary, verification, threading.get_ident()))
        barrier.wait()  # Both cases must run simultaneously, even at API concurrency=1.
        if case['id'] == '02' and second_case_status == 'error':
            raise ValueError('case-specific failure')
        return 'incomplete' if case['id'] == '02' and second_case_status == 'unknown' else 'complete'

    def generate(case, _config, primary, _output, reviewer_client=None):
        assess(case, primary, reviewer_client)
        return {'status': 'approved', 'items': []}

    def judge(case, _suite, _config, primary, _decks, _sources, _output, verification_client=None):
        status = assess(case, primary, verification_client)
        return {candidate: {'summary': {'status': status, 'score': 100 if status == 'complete' else None}}
                for candidate in ('A', 'B')}

    monkeypatch.setattr(contract, 'Client', make_client)
    monkeypatch.setattr(contract, 'write_json', track_summary)
    monkeypatch.setattr(contract, 'generate_contract', generate)
    monkeypatch.setattr(contract, 'verify_suite', lambda *_args: None)
    monkeypatch.setattr(contract, 'prepare_case', lambda *_args: {})
    import ppt_rubric.contract_judge as judge_module
    monkeypatch.setattr(judge_module, 'evaluate_contract', judge)
    args = [operation, *options, '--workers', '2', '--out', str(tmp_path / 'parallel'),
            '--verification-config', str(tmp_path / 'verification.json')]
    if operation == 'judge':
        args += ['--rubrics', str(tmp_path / 'rubrics')]
    if expected_exit is None:
        contract.main(args)
    else:
        with pytest.raises(SystemExit) as stopped:
            contract.main(args)
        assert stopped.value.code == expected_exit
    assert len(created) == 2 and len(received) == 2
    assert all(primary is created[0] and verification is created[1]
               for _case, primary, verification, _thread in received)
    assert len({thread for *_rest, thread in received}) == 2
    assert summary_threads == [root_thread, root_thread]
    summary = json.loads((tmp_path / 'parallel' / 'run-summary.json').read_text())
    assert set(summary) == {'01', '02'}
    if second_case_status == 'error':
        assert summary['02']['status'] == 'error'
    elif second_case_status == 'unknown':
        assert summary['02']['A']['status'] == 'incomplete'


@pytest.mark.parametrize('workers', ['0', '-1'])
def test_cli_rejects_nonpositive_workers_before_reading_inputs(tmp_path, workers):
    with pytest.raises(SystemExit) as stopped:
        contract.main(['generate', '--config', str(tmp_path / 'not-read.json'),
                       '--out', str(tmp_path / 'out'), '--workers', workers])
    assert stopped.value.code == 2
