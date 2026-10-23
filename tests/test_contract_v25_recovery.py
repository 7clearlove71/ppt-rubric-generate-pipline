"""Failure-only recovery, checkpoint integrity and unchanged historical evidence."""
import copy
import json
from pathlib import Path

import pytest

from ppt_rubric.common import digest, file_digest, read_json, write_json
from ppt_rubric.provider import ProviderError
from ppt_rubric.v25 import contract_judge as judge
from ppt_rubric.v25.recovery import recovery_history


class Client:
    def __init__(self, config, failures=(), unknowns=(), nos=()):
        self.config = config
        self.failures, self.unknowns, self.nos = set(failures), set(unknowns), set(nos)
        self.calls = []

    def ask_text(self, role, system, payload, images, validate):
        key = ((payload['criteria'][0]['id'] if 'criteria' in payload else payload['criterion']['id']), payload['pages'][0]['page'] if payload['evaluation_scope'] == 'page' else None)
        self.calls.append(key)
        if key in self.failures:
            raise ProviderError('offline HTTP400', {'http_status': 400, 'error_category': 'unclassified_http400',
                                                   'ledger_request_id': len(self.calls),
                                                   'provider_error': {'code': 'Unknown', 'provider_request_id': 'original-service-id'}})
        answer = 'unknown' if key in self.unknowns else 'no' if key in self.nos else 'yes'
        raw = 'Offline evidence. ' + '\\boxed{' + answer + '}'
        if 'criteria' in payload:
            raw = json.dumps({'verdicts': [{'rubric_id': c['id'], 'answer': answer,
                'evidence': 'Independent fixture ' + c['id']} for c in payload['criteria']]})
        validate(raw)
        return raw


@pytest.fixture
def recovery_case(tmp_path, monkeypatch):
    monkeypatch.setattr(judge, 'validate_inputs', lambda *args: None)
    monkeypatch.setattr(judge, 'rendered_text_geometry', lambda deck: [])
    monkeypatch.setattr(judge, 'focus_views', lambda *args: [])
    config = {'judge_model': 'kimi-k3', 'vision_model': 'kimi-k3', 'scoring_method': 'single_k3',
              'max_images_per_request': 60, 'assessment_workers': 2}
    rubric = {'rubric_hash': digest('rubric'), 'items': [
        {'id': 'C1', 'dimension': 'fulfillment', 'score': 50, 'judge_prompt': 'Required conclusion?'},
        {'id': 'C2', 'dimension': 'correctness', 'score': 30, 'judge_prompt': 'Correct number?'},
        {'id': 'V1', 'dimension': 'visual', 'score': 20, 'judge_prompt': 'Readable text?'}]}
    slides = []
    for page in (1, 2):
        image = tmp_path / f'page-{page}.png';image.write_bytes(f'frozen image {page}'.encode())
        slides.append({'page': page, 'image': str(image), 'rendered_text': f'PDF text {page}'})
    deck = {'identity': {'sha256': digest('candidate')}, 'slides': slides}
    case = {'id': 'offline', 'candidates': [{'id': 'A', 'sha256': deck['identity']['sha256']}]}
    return case, rubric, config, {'A': deck}, [{'id': 'prompt', 'text': 'Original task'}]


def evaluate(fixture, client, output, **kwargs):
    case, rubric, config, decks, sources = fixture
    return judge.evaluate_contract(case, rubric, config, client, decks, sources, output, **kwargs)['A']


def test_recovery_only_repeats_failed_calls_and_preserves_unknown_http400(recovery_case, tmp_path):
    config = recovery_case[2]
    old, new = tmp_path / 'old', tmp_path / 'new'
    failures = {('C2', None), ('V1', 2)}
    original = evaluate(recovery_case, Client(config, failures, unknowns={('C1', None)}), old)
    snapshots = {str(p): file_digest(p) for p in old.rglob('*') if p.is_file()}
    recovery = Client({**config, 'assessment_workers': 1, 'max_api_requests': 5})
    result = evaluate(recovery_case, recovery, new, resume_from=old)
    assert set(recovery.calls) == failures and len(recovery.calls) == 2
    assert result['verdicts'][0]['answer'] == 'unknown'  # A successful model unknown is immutable.
    assert result['verdicts'][0]['unknown_origin'] == 'model_insufficient_evidence'
    assert result['execution'] == {'status': 'complete', 'unavailable_requests': 0, 'model_unknown_requests': 1}
    assert result['assessment']['summary']['unresolved_weight'] == 50
    for row in original['assessment']['content']['records'] + original['assessment']['visual_batches']:
        if row['status'] == 'unavailable':
            assert row['error']['http_status'] == 400
    old_unknown = next(v for v in original['verdicts'] if v['rubric_id'] == 'C2')
    assert old_unknown['evidence'] == 'unknown：HTTP400' and old_unknown['unknown_origin'] == 'execution_error'
    recovered = result['assessment']['content']['records'][1]
    assert recovered['recovery_history'][0]['error']['provider_error']['provider_request_id'] == 'original-service-id'
    assert recovered['recovery_history'][0]['error']['http_status'] == 400
    assert snapshots == {str(p): file_digest(p) for p in old.rglob('*') if p.is_file()}
    # This client has no provider cache at all: reused results came from the checkpoint.
    assert not (tmp_path / 'cache').exists()


def test_failed_visual_is_recovered_even_when_another_page_already_makes_item_no(recovery_case, tmp_path):
    cfg = recovery_case[2]
    original = evaluate(recovery_case, Client(cfg, {('V1', 2)}, nos={('V1', 1)}), tmp_path / 'old')
    assert original['summary']['status'] == 'complete' and original['execution']['status'] == 'incomplete'
    recovery = Client(cfg)
    result = evaluate(recovery_case, recovery, tmp_path / 'new', resume_from=tmp_path / 'old')
    assert recovery.calls == [('V1', 2)]
    assert result['verdicts'][-1]['answer'] == 'no' and result['summary']['score'] == 80


@pytest.mark.parametrize('change', ['image', 'source', 'rubric', 'request_options', 'checkpoint', 'missing_image'])
def test_changed_inputs_or_checkpoints_fail_before_any_recovery_call(recovery_case, tmp_path, change):
    cfg = recovery_case[2]
    old = tmp_path / 'old'
    evaluate(recovery_case, Client(cfg, {('C2', None)}), old)
    changed = copy.deepcopy(recovery_case)
    if change == 'image': Path(changed[3]['A']['slides'][0]['image']).write_bytes(b'changed')
    if change == 'missing_image': Path(changed[3]['A']['slides'][0]['image']).unlink()
    if change == 'source': changed[4][0]['text'] = 'New task'
    if change == 'rubric': changed[1]['rubric_hash'] = digest('new rubric')
    if change == 'request_options': changed[2]['request_options'] = {'enable_thinking': True}
    if change == 'checkpoint':
        p = old / 'offline/A-assessment.json';d = read_json(p)
        d['content']['records'][0]['raw'] = r'changed\boxed{no}';write_json(p, d)
    recovery = Client(changed[2])
    with pytest.raises((ValueError, OSError)):
        evaluate(changed, recovery, tmp_path / 'new', resume_from=old)
    assert recovery.calls == []


def test_recovery_preflights_all_candidates_before_submitting_any(recovery_case, tmp_path):
    case, rubric, cfg, decks, sources = recovery_case
    case['candidates'].append({'id': 'B', 'sha256': decks['A']['identity']['sha256']})
    decks['B'] = copy.deepcopy(decks['A'])
    old = tmp_path / 'old'
    evaluate(recovery_case, Client(cfg, {('C2', None)}), old)
    (old / 'offline/B-assessment.json').unlink()
    client = Client(cfg)
    with pytest.raises(FileNotFoundError): evaluate(recovery_case, client, tmp_path / 'new', resume_from=old)
    assert client.calls == []


def test_interrupted_partial_page_reuses_completed_visual_calls(recovery_case, tmp_path):
    case, rubric, cfg, decks, sources = recovery_case
    old = tmp_path / 'old'
    class Interrupted(Client):
        def ask_text(self, role, system, payload, images, validate):
            if payload['evaluation_scope'] == 'page' and payload['pages'][0]['page'] == 2:
                raise KeyboardInterrupt('offline interruption')
            return super().ask_text(role, system, payload, images, validate)
    cfg['assessment_workers'] = 1
    with pytest.raises(KeyboardInterrupt): evaluate(recovery_case, Interrupted(cfg), old)
    client = Client(cfg)
    result = evaluate(recovery_case, client, tmp_path / 'new', resume_from=old)
    assert client.calls == [('V1', 2)]
    assert result['summary']['score'] == 100


def test_repeated_recovery_keeps_all_failed_receipts(recovery_case, tmp_path):
    cfg = recovery_case[2]
    evaluate(recovery_case, Client(cfg, {('C2', None)}), tmp_path / 'one')
    evaluate(recovery_case, Client(cfg, {('C2', None)}), tmp_path / 'two', resume_from=tmp_path / 'one')
    final = evaluate(recovery_case, Client(cfg), tmp_path / 'three', resume_from=tmp_path / 'two')
    record = final['assessment']['content']['records'][1]
    assert len(record['recovery_history']) == 2
    assert all(r['error']['http_status'] == 400 for r in record['recovery_history'])


def test_prior_accounting_is_cumulative_and_rejects_unreconciled_requests(tmp_path):
    run = tmp_path / 'old';(run / 'judgments').mkdir(parents=True)
    ledger = run / 'bailian-api/requests.json'
    write_json(ledger, [{'status': 'success'}, {'status': 'failed'}])
    source, root, history = recovery_history(run)
    assert source == run / 'judgments' and history['prior_api_requests'] == 2
    parent = tmp_path / 'parent'
    write_json(parent / 'primary-api/requests.json', [{'status': 'success'}] * 7)
    parent_history = recovery_history(parent)[2]
    write_json(run / 'recovery.json', {'status': 'closed', **parent_history})
    assert recovery_history(run)[2]['prior_api_requests'] == 9
    saved = read_json(parent / 'primary-api/requests.json')
    write_json(parent / 'primary-api/requests.json', saved + [{'status': 'failed'}])
    with pytest.raises(ValueError, match='lineage changed'): recovery_history(run)
    write_json(parent / 'primary-api/requests.json', saved)
    write_json(ledger, [{'status': 'started'}])
    with pytest.raises(ValueError, match='in-flight'): recovery_history(run)


@pytest.mark.parametrize('runner_layout', [False, True])
def test_both_standard_judge_and_runner_ledger_layouts_are_supported(tmp_path, runner_layout):
    run = tmp_path / 'batch'
    judgments = run / 'judgments';judgments.mkdir(parents=True)
    ledger = run / 'bailian-api/requests.json' if runner_layout else judgments / 'primary-api/requests.json'
    write_json(ledger, [{'status': 'failed'}])
    source, root, history = recovery_history(run if runner_layout else judgments)
    assert source == judgments and root == (run if runner_layout else judgments)
    assert history['prior_api_requests'] == 1
    # Passing the runner's judgments child also finds its sole parent ledger.
    if runner_layout: assert recovery_history(judgments)[1] == run


@pytest.mark.parametrize('marker', ['authorization.json', 'recovery-authorization.json',
                                  'diagnostic-authorization.json', 'run-state.json'])
def test_recovery_refuses_an_active_source_run(tmp_path, marker):
    write_json(tmp_path / 'primary-api/requests.json', [])
    write_json(tmp_path / marker, {'status': 'active'})
    with pytest.raises(ValueError, match='still active'): recovery_history(tmp_path)


def test_recovery_cli_requires_a_new_explicit_cap_before_constructing_client(tmp_path, monkeypatch):
    from ppt_rubric.v25 import contract_v2 as contract
    monkeypatch.setattr(contract, 'Client', lambda *args, **kwargs: pytest.fail('Client constructed'))
    with pytest.raises(SystemExit) as error:
        contract.main(['judge', '--config', 'configs/contract-v25-judge.json', '--out', str(tmp_path),
                       '--resume-from', '/offline/prior-run'])
    assert error.value.code == 2


@pytest.mark.parametrize('cap', [0, 1, 2])
def test_cli_recovery_end_to_end_with_mock_http_and_cumulative_accounting(
        recovery_case, tmp_path, monkeypatch, cap):
    from ppt_rubric.v25 import contract_v2 as contract
    case, rubric, cfg, decks, sources = recovery_case
    cfg.update(read_json(Path(__file__).resolve().parents[1] / 'configs/contract-v25-judge-fast.json'))
    cfg.update(api_concurrency=1, min_request_interval_seconds=0)
    case.update(prompt='Original task', split='dev', attachments=[])
    case['candidates'].append({'id': 'B', 'sha256': digest('candidate-B')})
    decks['B'] = copy.deepcopy(decks['A'])
    decks['B']['identity']['sha256'] = case['candidates'][1]['sha256']
    rubric.update(pipeline_mode='light', source_audit={'status': 'skipped'})
    old, new = tmp_path / 'old', tmp_path / 'recovered'
    original = Client(cfg, failures={('C2', None)}, unknowns={('C1', None)})
    evaluate(recovery_case, original, old)
    write_json(old / 'primary-api/requests.json', [
        {'request_id': i + 1, 'status': 'failed' if key[0] == 'C2' else 'success'}
        for i, key in enumerate(original.calls)])
    write_json(old / 'prepared-decks.json', decks);write_json(old / 'case.json', case)
    write_json(old / 'authorization.json', {'status': 'closed'})
    write_json(tmp_path / 'rubrics/offline/rubric.json', rubric)
    manifest = {'schema_version': 1, 'cases': [case]};manifest['id'] = digest(manifest)
    write_json(tmp_path / 'manifest.json', manifest);write_json(tmp_path / 'config.json', cfg)
    snapshots = {str(p): file_digest(p) for p in old.rglob('*') if p.is_file()}
    monkeypatch.setattr(contract, 'verify_suite', lambda *args: None)
    monkeypatch.setattr(contract, 'case_sources', lambda *args: sources)
    monkeypatch.setenv('BAILIAN_API_KEY', 'offline-key')
    sent = []
    class Response:
        ok = True;status_code = 200
        def json(self):
            return {'choices': [{'message': {'content': r'Offline fixed request.\boxed{yes}'}, 'finish_reason': 'stop'}],
                    'usage': {'total_tokens': 7}}
    class Session:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, *args, **kwargs):
            payload = json.loads(kwargs['json']['messages'][1]['content'][0]['text'])
            sent.append(payload['criterion']['id'])
            return Response()
    monkeypatch.setattr('ppt_rubric.provider.requests.Session', Session)
    args = ['judge', '--config', str(tmp_path / 'config.json'), '--manifest', str(tmp_path / 'manifest.json'),
            '--rubrics', str(tmp_path / 'rubrics'), '--out', str(new), '--resume-from', str(old),
            '--cache', str(tmp_path / 'empty-cache'), '--recovery-request-cap', str(cap)]
    if cap < 2:
        with pytest.raises(SystemExit) as error: contract.main(args)
        assert error.value.code == 2
    else:
        contract.main(args)
    assert sent == ['C2'] * cap
    recovery = read_json(new / 'recovery.json')
    assert recovery['status'] == 'closed' and recovery['prior_api_requests'] == 8
    assert recovery['actual_additional_requests'] == cap
    assert recovery['cumulative_requests'] == recovery['cumulative_request_cap'] == 8 + cap
    assert len(read_json(new / 'primary-api/requests.json')) == cap
    assert snapshots == {str(p): file_digest(p) for p in old.rglob('*') if p.is_file()}
    # A zero-request recovery still writes an empty ledger and can be accounted again.
    assert recovery_history(new)[2]['prior_api_requests'] == 8 + cap


def test_visual_batch_recovery_reuses_all_five_unknown_results_and_only_retries_failed_page(recovery_case, tmp_path):
    case, rubric, cfg, decks, sources = recovery_case
    rubric['items'] = rubric['items'][:2] + [
        {'id': f'V{i}', 'dimension': 'visual', 'score': 4, 'judge_prompt': f'Visual point {i}?'}
        for i in range(1, 6)]
    original = evaluate(recovery_case, Client(cfg, failures={('V1', 2)}, unknowns={('V1', 1)}), tmp_path / 'old')
    first_page = original['assessment']['visual_batches'][0]
    assert first_page['status'] == 'success' and len(first_page['verdicts']) == 5
    assert original['execution']['unavailable_requests'] == 1
    client = Client(cfg)
    result = evaluate(recovery_case, client, tmp_path / 'new', resume_from=tmp_path / 'old')
    assert client.calls == [('V1', 2)]
    assert result['assessment']['visual_batches'][0] == first_page
    assert result['execution'] == {'status': 'complete', 'unavailable_requests': 0, 'model_unknown_requests': 1}
    assert result['comparison']['excluded_rubric_ids'] == [f'V{i}' for i in range(1, 6)]
    assert result['summary']['score'] == result['comparison']['score_possible'] == 80
    repaired = result['assessment']['visual_batches'][1]
    assert repaired['recovery_history'][0]['error']['http_status'] == 400
    assert repaired['rubric_ids'] == [f'V{i}' for i in range(1, 6)]


def test_visual_checkpoint_cannot_change_an_individual_reason_even_when_rehashed(recovery_case, tmp_path):
    from ppt_rubric.v25.recovery import validate_checkpoint
    original = evaluate(recovery_case, Client(recovery_case[2]), tmp_path / 'old')
    path = tmp_path / 'old/offline/A-assessment.json'
    changed = read_json(path)
    changed['visual_batches'][0]['verdicts'][0]['evidence'] = 'Substituted reason'
    changed['checkpoint_hash'] = digest({k: v for k, v in changed.items() if k != 'checkpoint_hash'})
    write_json(path, changed)
    with pytest.raises(ValueError, match='verdicts differ'):
        validate_checkpoint(path, original['assessment']['recovery_binding'], judge.parse_boxed_answer)
