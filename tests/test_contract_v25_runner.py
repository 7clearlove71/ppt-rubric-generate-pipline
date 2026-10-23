"""Exercise the reusable case01 runner offline, including role handoff/accounting."""
import copy
import importlib.util
from pathlib import Path

import pytest

from ppt_rubric.common import file_digest, read_json, write_json


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def runner():
    spec = importlib.util.spec_from_file_location(
        'contract_v25_case01_runner', ROOT / 'scripts/run_contract_v25_case01.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('case_id,page_counts', [('01', (13, 13)), ('02', (10, 8)), ('03', (8, 8))])
@pytest.mark.parametrize('credential_failure', [False, True])
@pytest.mark.parametrize('mode,generation_status', [('full', 'approved'), ('full', 'ready'), ('light', 'ready')])
@pytest.mark.parametrize('execution_status', ['complete', 'incomplete', 'failed'])
def test_case01_runner_uses_one_candidate_judge_and_preserves_source_auditor(
        runner, tmp_path, monkeypatch, credential_failure, case_id, page_counts, mode, generation_status, execution_status):
    root, output = tmp_path / 'workspace', tmp_path / 'run'
    monkeypatch.setattr(runner, 'ROOT', root)
    monkeypatch.setenv('BAILIAN_API_KEY', 'offline-key')
    monkeypatch.setenv('MAAS_API_KEY', 'offline-key')
    if credential_failure:
        monkeypatch.delenv('BAILIAN_API_KEY')
        monkeypatch.delenv('MAAS_API_KEY')
    case = {'id': case_id, 'prompt': 'Offline task', 'attachments': [], 'candidates': []}
    decks = {}
    for cid, page_count in zip(('A', 'B'), page_counts):
        pptx = root / f'{cid}.pptx'
        pptx.parent.mkdir(parents=True, exist_ok=True)
        pptx.write_bytes(cid.encode())
        sha = file_digest(pptx)
        case['candidates'].append({'id': cid, 'pptx': str(pptx), 'sha256': sha})
        pdf = root / f'{cid}.pdf'
        pdf.write_bytes(b'Offline PDF fixture; parsing is stubbed below.')
        slides = []
        for page in range(1, page_count + 1):
            image = root / f'{cid}-{page}.png'
            image.write_bytes(b'Offline image fixture; no provider receives it.')
            slides.append({'page': page, 'image': str(image), 'rendered_text': 'stale'})
        decks[f'{case_id}/{cid}'] = {'identity': {'sha256': sha}, 'pdf': str(pdf), 'slides': slides}
    write_json(root / 'data/manifest-local.json', {'cases': [case]})
    write_json(root / 'artifacts/validation-20261019-contract-v24/frozen-dev-decks.json', decks)
    for name in ('k3', 'auditor', 'judge'):
        config = read_json(ROOT / f'configs/contract-v25-{name}.json')
        write_json(root / f'configs/contract-v25-{name}.json', config)

    real_read = runner.read_json

    def offline_read(path):
        if str(path).startswith('/home/vp2-lyz/.config/codex-credentials/'):
            if mode == 'light':
                assert 'modelarts' not in str(path), 'Light mode must never read MaaS credentials'
            if credential_failure:
                raise OSError('Offline credential-read failure fixture')
            return {'api_key': 'offline-key'}
        return real_read(path)

    monkeypatch.setattr(runner, 'read_json', offline_read)

    class PDF:
        def __init__(self, path):
            self.path = Path(path)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def __len__(self):
            return page_counts[0 if self.path.stem == 'A' else 1]

        def __getitem__(self, index):
            class Page:
                def get_text(self):
                    return f'Fresh PDF text page {index + 1}'
            return Page()

    monkeypatch.setattr(runner.fitz, 'open', lambda path: PDF(path))
    clients = []

    class Client:
        def __init__(self, config, cache, ledger):
            self.config = copy.deepcopy(config)
            self.provider = None
            clients.append(self)

    monkeypatch.setattr(runner, 'RecordedClient', Client)
    suite = {'status': generation_status, 'pipeline_mode': mode,
             'source_audit': {'status': ('skipped' if mode == 'light' else
                                         'passed' if generation_status == 'approved' else 'not_passed')}, 'requirements': {'requirements': [{}, {}]}, 'items': [
        {'dimension': 'fulfillment'}, {'dimension': 'correctness'},
        {'dimension': 'visual'}, {'dimension': 'visual'}, {'dimension': 'visual'}]}
    audited = []

    def generate(case, config, primary, output, reviewer_client):
        assert primary.config['generator_model'] == 'kimi-k3'
        assert config['pipeline_mode'] == mode
        if mode == 'full':
            assert reviewer_client.config['reviewer_model'] == 'kimi-k2.6'
        else:
            assert reviewer_client is None
        audited.append(reviewer_client)
        return suite

    monkeypatch.setattr(runner.contract, 'generate_contract', generate)
    monkeypatch.setattr(runner.contract, 'verify_suite', lambda *args: None)
    scoring_calls = []

    # Deliberately omit any verification parameter: the runner must not pass one.
    def evaluate(case, suite, config, primary, decks, sources, output):
        assert len(clients) == (2 if mode == 'full' else 1)
        assert primary is clients[0] and primary.config['judge_model'] == 'kimi-k3'
        assert audited == ([clients[1]] if mode == 'full' else [None])
        if mode == 'full':
            assert clients[1].config['request_options']['thinking']['type'] == 'enabled'
        assert all(slide['rendered_text'].startswith('Fresh PDF text')
                   for deck in decks.values() for slide in deck['slides'])
        scoring_calls.append(primary)
        return {cid: {'summary': {'status': 'complete', 'score': 100},
                      'execution': {'status': execution_status},
                      'comparison': {'score_possible': 100, 'excluded_weight': 0,
                                     'excluded_rubric_ids': [], 'policy': {'version': 'fixture-shared'}}}
                for cid in decks}

    monkeypatch.setattr(runner, 'evaluate_contract', evaluate)
    if credential_failure:
        with pytest.raises(OSError, match='Offline credential-read failure'):
            runner.main(['--mode', mode, '--case', case_id, '--out', str(output), '--profile', 'standard'])
        auth = read_json(output / 'authorization.json')
        status = read_json(output / 'execution-status.json')
        assert auth['status'] == 'closed' and auth['final_status'] == 'failed'
        assert status['worker_completed'] is True and status['status'] == 'failed'
        assert status['accounting']['bailian']['used'] == status['accounting']['maas']['used'] == 0
        assert not clients and not scoring_calls
        return
    exit_code = runner.main(['--mode', mode, '--case', case_id, '--out', str(output), '--profile', 'standard'])
    assert exit_code == {'complete': 0, 'incomplete': 2, 'failed': 1}[execution_status]

    assert len(scoring_calls) == 1
    assert not (output / 'verifier-config.json').exists()
    auth = read_json(output / 'authorization.json')
    assert auth['status'] == 'closed' and auth['scoring_method'] == 'single_k3'
    assert auth['models']['independent_source_audit'] == ('kimi-k2.6' if mode == 'full' else None)
    assert auth['pipeline_mode'] == mode
    assert auth['unused_capacity_rolls_over'] is False
    plan = read_json(output / 'actual-call-plan.json')
    assert plan['judge_logical_calls_by_candidate'] == {cid: 2 + count for cid, count in zip(('A', 'B'), page_counts)}
    assert plan['judge_logical_calls'] == 4 + sum(page_counts)
    assert plan['visual_criteria'] == 3
    status = read_json(output / 'execution-status.json')
    assert status['status'] == {'complete': 'finished', 'incomplete': 'incomplete', 'failed': 'failed'}[execution_status]
    assert status['scoring_method'] == 'single_k3'
    assert all(c['score_possible'] == 100 and c['excluded_rubric_ids'] == []
               for c in status['comparisons'].values())
    assert status['accounting']['bailian']['used'] == status['accounting']['maas']['used'] == 0
    assert read_json(output / 'source-freeze.json')['offline_tests_executed_by_runner'] is False

    # Recovery must remain explicit and must not reset or reopen the closed ledger.
    with pytest.raises(RuntimeError, match='never silently reset'):
        runner.main(['--mode', mode, '--case', case_id, '--out', str(output), '--profile', 'standard'])
    assert len(scoring_calls) == 1
    assert read_json(output / 'authorization.json') == auth


def test_explicit_environment_key_does_not_read_fallback_file(runner, monkeypatch):
    monkeypatch.setenv('BAILIAN_API_KEY', 'new-offline-key')
    monkeypatch.setattr(runner, 'read_json', lambda path: pytest.fail('Old credential file was accessed'))
    runner.configure_credential({'api_key_env': 'BAILIAN_API_KEY'}, '/offline/old-key.json')
    assert runner.os.environ['BAILIAN_API_KEY'] == 'new-offline-key'


def test_absent_environment_key_uses_file_only_in_memory(runner, monkeypatch):
    monkeypatch.delenv('BAILIAN_API_KEY', raising=False)
    monkeypatch.setattr(runner, 'read_json', lambda path: {'api_key': 'file-offline-key'})
    monkeypatch.setattr(runner, 'write_json', lambda *args: pytest.fail('Credential must not be written'))
    runner.configure_credential({'api_key_env': 'BAILIAN_API_KEY'}, '/offline/key.json')
    assert runner.os.environ['BAILIAN_API_KEY'] == 'file-offline-key'


def test_runner_call_plan_can_disable_all_visual_calls(runner):
    suite = {'requirements': {'requirements': [{}]}, 'items': [
        {'dimension': 'fulfillment'}, {'dimension': 'correctness'},
        {'dimension': 'visual'}, {'dimension': 'visual'}]}
    decks = {'A': {'slides': [1, 2]}, 'B': {'slides': [1, 2, 3]}}
    plan = runner.actual_call_plan(suite, decks, {'role_supports_images': {'judge': False}})
    assert plan['judge_logical_calls_by_candidate'] == {'A': 2, 'B': 2}
    assert plan['visual_criteria'] == 0 and plan['scoring_method'] == 'single_text'
    assert plan['evaluation_mode'] == 'content_only'
