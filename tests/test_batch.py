"""Resumable user batches. HTTP and rendering are local fixtures throughout."""
import copy
import json
import os
import signal
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from ppt_rubric import batch
from ppt_rubric.batch_provider import BatchProvider, request_profile, transport_config
from ppt_rubric.batch_state import BatchState, run_lock
from ppt_rubric.common import digest, read_json, write_json
from ppt_rubric.provider import ProviderError
from ppt_rubric.v25 import contract_v2 as contract


@pytest.fixture
def user_batch(tmp_path, monkeypatch):
    source, output = tmp_path / 'inputs', tmp_path / 'run'
    for i in range(3):
        task = source / f'task-{i:03}'
        task.mkdir(parents=True)
        (task / 'prompt.txt').write_text(f'项目{i}：介绍实施目标，投资280万元。', encoding='utf-8')
        for j in range(2 + (i == 1)):
            candidate = task / f'proposal-{j}'
            candidate.mkdir()
            (candidate / 'result.pptx').write_bytes(f'offline PPTX {i}-{j}'.encode())
    configs = []
    for name in ('k3-fast', 'judge-fast', 'auditor-fast'):
        config = read_json(batch.ROOT / f'configs/contract-v25-{name}.json')
        config.update(min_request_interval_seconds=0, max_retries=1, api_concurrency=2)
        path = tmp_path / f'{name}.json'
        write_json(path, config)
        configs.append(path)
    monkeypatch.setenv('BAILIAN_API_KEY', 'offline-batch-key')
    monkeypatch.setenv('MAAS_API_KEY', 'offline-audit-key')
    rendered, sent = [], []
    faults = {'write_fail': False, 'judge_fail': False, 'model_unknown': False,
              'fatal': False, 'delay': 0, 'audit_issues': False}

    def prepare(case, config, cache):
        rendered.append(case['id'])
        directory = Path(cache) / 'offline-render' / case['id']
        directory.mkdir(parents=True, exist_ok=True)
        decks = {}
        for candidate in case['candidates']:
            image = directory / f'{candidate["id"]}.png'
            image.write_bytes(b'offline page ' + candidate['sha256'].encode())
            decks[candidate['id']] = {'identity': {'sha256': candidate['sha256']},
                'slides': [{'page': 1, 'image': str(image), 'rendered_text': 'PDF目标和投资280万元'}]}
        return decks
    monkeypatch.setattr(batch, 'prepare_case', prepare)
    monkeypatch.setattr(batch.judge, 'rendered_text_geometry', lambda *args: [])
    monkeypatch.setattr(batch.judge, 'focus_views', lambda *args: [])

    class Response:
        def __init__(self, raw, status=200, code=None):
            self.status_code, self.ok, self.headers = status, status == 200, {'x-request-id': 'offline-service-id'}
            self.raw, self.code = raw, code
        def json(self):
            if self.code:
                return {'error': {'code': self.code, 'message': 'offline generic error'}}
            return {'choices': [{'message': {'content': self.raw}, 'finish_reason': 'stop'}],
                    'usage': {'total_tokens': 11}}
    class Session:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, **kwargs):
            request = kwargs['json']
            system = request['messages'][0]['content']
            text = request['messages'][1]['content'][0]['text']
            payload = text if system == contract.AUDIT else json.loads(text)
            sent.append((system, payload, request, url))
            if faults['delay']:
                time.sleep(faults['delay'])
            if faults['fatal']:
                return Response('', 400, 'Arrearage')
            if system == contract.EXTRACT:
                result = {'task_summary': '项目汇报', 'source_policy': '只依据给定来源', 'requirements': [
                    {'id': 'Q01', 'kind': 'deliverable', 'text': '介绍实施目标', 'importance': 2,
                     'source_ids': [payload['source_catalog'][0]['id']]},
                    {'id': 'Q02', 'kind': 'fact', 'text': '投资280万元', 'importance': 2,
                     'source_ids': [payload['source_catalog'][0]['id']]}]}
            elif system == contract.WRITE_CRITERION:
                if faults['write_fail'] and payload['requirement']['id'] == 'Q02':
                    faults['write_fail'] = False
                    return Response('', 400, 'InvalidParameter')
                q = payload['requirement']
                result = {'requirement_id': q['id'], 'title': q['text'], 'question': '是否' + q['text'] + '？'}
            elif system == contract.AUDIT:
                result = {'issues': []}
            elif 'criteria' in payload:
                return Response(json.dumps({'verdicts': [{'rubric_id': c['id'], 'answer': 'yes',
                    'evidence': 'Independent fixture ' + c['id']} for c in payload['criteria']]}))
            else:
                if faults['judge_fail'] and payload['criterion']['id'] == 'C_CALC':
                    faults['judge_fail'] = False
                    return Response('', 400, 'UnknownError')
                answer = 'unknown' if faults['model_unknown'] and payload['criterion']['id'] == 'I_BOUNDARY' else 'yes'
                return Response('第1页：离线模拟证据。\\boxed{' + answer + '}')
            return Response(json.dumps(result, ensure_ascii=False))
    monkeypatch.setattr('ppt_rubric.provider.requests.Session', Session)
    args = ['--source', str(source), '--out', str(output), '--mode', 'light',
            '--config', str(configs[0]), '--judge-config', str(configs[1]),
            '--source-audit-config', str(configs[2]), '--max-api-requests', '1000',
            '--workers', '2', '--log-interval', '0.1', '--confirm-bailian-bulk']
    return args, source, output, sent, rendered, faults, configs


def single_task(fixture):
    args, source, *_ = fixture
    for task in sorted(source.iterdir())[1:]:
        shutil.rmtree(task)
    args[args.index('--workers') + 1] = '1'
    return args


def test_batch_end_to_end_multiple_candidates_and_same_command_is_noop(user_batch, capsys):
    args, source, output, sent, rendered, faults, _ = user_batch
    faults['model_unknown'] = True
    assert batch.main(args) == 0
    summary = read_json(output / 'summary.json')
    assert summary['status'] == 'complete' and summary['task_counts'] == {'complete': 3}
    mapping = read_json(output / 'candidate-mapping.json')
    assert len(mapping['candidates']) == 7
    assert {r['case_id'] for r in mapping['candidates']} == {'task-000', 'task-001', 'task-002'}
    assert all(r['score'] == pytest.approx(90) and r['score_possible'] == pytest.approx(90)
               for r in mapping['candidates'])
    assert all('human' not in key for row in mapping['candidates'] for key in row)
    assert summary['accounting']['bailian']['requests'] == len(sent)
    assert summary['accounting']['bailian']['known_total_tokens'] == len(sent) * 11
    assert summary['accounting']['bailian']['missing_usage'] == 0
    generation = [(s, p) for s, p, _, _ in sent if s in {contract.EXTRACT, contract.WRITE_CRITERION}]
    assert all('candidate' not in json.dumps(p) and 'proposal-' not in json.dumps(p) for s, p in generation)
    assert len(rendered) == 3
    count = len(sent)
    shutil.rmtree(output / 'cache' / 'api')
    assert batch.main(args) == 0
    assert len(sent) == count and len(rendered) == 3
    assert len(read_json(output / 'bailian-api/requests.json')) == count
    log = (output / 'progress.log').read_text()
    assert '进度 3/3（100.0%）' in log and '跳过已完成 3题' in log
    assert 'offline-batch-key' not in log
    assert read_json(output / 'progress.json')['complete'] == 3


def test_partial_generation_reuses_successful_calls_without_api_cache(user_batch):
    args = single_task(user_batch)
    _, _, output, sent, _, faults, _ = user_batch
    faults['write_fail'] = True
    assert batch.main(args) == 1
    assert len(sent) == 3
    shutil.rmtree(output / 'cache' / 'api')
    assert batch.main(args) == 0
    extracts = [r for r in sent if r[0] == contract.EXTRACT]
    q1 = [p for s, p, *_ in sent if s == contract.WRITE_CRITERION and p['requirement']['id'] == 'Q01']
    q2 = [p for s, p, *_ in sent if s == contract.WRITE_CRITERION and p['requirement']['id'] == 'Q02']
    assert len(extracts) == len(q1) == 1 and len(q2) == 2
    assert read_json(output / 'summary.json')['accounting']['bailian']['failed'] == 1


def test_scoring_retries_only_http_failure_and_keeps_model_unknown(user_batch):
    args = single_task(user_batch)
    _, _, output, sent, rendered, faults, _ = user_batch
    faults.update(judge_fail=True, model_unknown=True)
    assert batch.main(args) == 2
    before = len(sent)
    old_assessments = [read_json(p) for p in (output / 'judgments/task-000').glob('*-assessment.json')]
    assert sum(r['execution']['unavailable_requests'] for r in old_assessments) == 1
    shutil.rmtree(output / 'cache' / 'api')
    assert batch.main(args) == 0
    assert len(sent) == before + 1 and len(rendered) == 1
    assert sent[-1][1]['criterion']['id'] == 'C_CALC'
    reports = [read_json(p) for p in (output / 'judgments/task-000').glob('*-assessment.json')]
    histories = [r for a in reports for r in a['content']['records'] if r.get('recovery_history')]
    assert histories[0]['recovery_history'][0]['error']['http_status'] == 400
    assert all(any(r['answer'] == 'unknown' for r in a['content']['records']) for a in reports)
    assert read_json(output / 'summary.json')['accounting']['bailian']['failed'] == 1


def test_cumulative_budget_stops_and_later_starts_previously_unstarted_tasks(user_batch):
    args, _, output, sent, _, _, _ = user_batch
    args[args.index('--max-api-requests') + 1] = '4'
    args[args.index('--workers') + 1] = '1'
    assert batch.main(args) == 2
    assert len(sent) == 4
    state = BatchState(output / 'state.sqlite3')
    assert sum(t['status'] == 'pending' for t in state.tasks().values()) == 2
    state.close()
    assert batch.main(args) == 2
    assert len(sent) == 4
    args[args.index('--max-api-requests') + 1] = '1000'
    assert batch.main(args) == 0
    assert read_json(output / 'summary.json')['accounting']['bailian']['requests'] == len(sent)


@pytest.mark.parametrize('change', ['prompt', 'pptx', 'config', 'mode', 'image', 'checkpoint', 'report', 'response'])
def test_changed_inputs_and_saved_results_rejected_before_new_requests(user_batch, change):
    args = single_task(user_batch)
    _, source, output, sent, _, faults, configs = user_batch
    faults['judge_fail'] = True
    assert batch.main(args) == 2
    before = len(sent)
    if change == 'prompt':
        (source / 'task-000/prompt.txt').write_text('changed')
    elif change == 'pptx':
        next(source.rglob('*.pptx')).write_bytes(b'changed')
    elif change == 'config':
        config = read_json(configs[1]); config['request_options'] = {'enable_thinking': False}
        write_json(configs[1], config)
    elif change == 'mode':
        args[args.index('--mode') + 1] = 'full'
        args += ['--audit-max-api-requests', '20']
    elif change == 'image':
        next((output / 'cache/offline-render').rglob('*.png')).write_bytes(b'changed')
    elif change == 'checkpoint':
        path = next((output / 'judgments').rglob('*-assessment.json'))
        row = read_json(path); row['checkpoint_hash'] = 'changed'; write_json(path, row)
    elif change == 'report':
        # Finish first: published complete reports are immutable once skipped.
        assert batch.main(args) == 0
        before = len(sent)
        path = output / 'judgments/task-000/A.json'
        row = read_json(path); row['summary']['score'] = 0; write_json(path, row)
    else:
        with sqlite3.connect(output / 'state.sqlite3') as db:
            db.execute("UPDATE responses SET hash='changed'")
    with pytest.raises(SystemExit):
        batch.main(args)
    assert len(sent) == before


def test_real_process_exit_releases_lock_and_preserves_unknown_inflight_consumption(user_batch):
    args = single_task(user_batch)
    _, _, output, sent, _, _, _ = user_batch
    script = '''
import json, os, sys
from ppt_rubric import batch
import ppt_rubric.provider as provider
class Session:
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def post(self, *args, **kwargs): os._exit(77)
provider.requests.Session = Session
batch.main(json.loads(sys.argv[1]))
'''
    child = subprocess.run([sys.executable, '-c', script, json.dumps(args)], cwd=batch.ROOT,
                           capture_output=True, text=True, timeout=20)
    assert child.returncode == 77, child.stderr
    assert batch.main(args) == 0
    accounting = read_json(output / 'summary.json')['accounting']['bailian']
    assert accounting['requests'] == len(sent) + 1
    assert accounting['interrupted'] == accounting['missing_usage'] == 1
    old = read_json(output / 'bailian-api/requests.json')[0]
    assert old['previous_status'] == 'started' and old['remote_outcome_unknown']
    assert 'usage' not in old


def test_full_audits_and_light_never_reads_audit_config(user_batch):
    args = single_task(user_batch)
    _, _, output, sent, _, _, configs = user_batch
    configs[2].unlink()
    assert batch.main(args) == 0
    assert all(system != contract.AUDIT for system, *_ in sent)


def test_full_mode_uses_separate_audit_budget_and_preserves_audit_status(user_batch):
    args = single_task(user_batch)
    _, _, output, sent, _, _, _ = user_batch
    args[args.index('--mode') + 1] = 'full'
    args += ['--audit-max-api-requests', '10']
    assert batch.main(args) == 0
    audits = [r for r in sent if r[0] == contract.AUDIT]
    assert len(audits) == 1 and audits[0][2]['model'] == 'kimi-k2.6'
    summary = read_json(output / 'summary.json')
    assert summary['accounting']['maas']['requests'] == 1
    assert all(r['source_audit']['status'] == 'passed' for r in summary['tasks']['task-000']['result'].values())


def test_heartbeat_reports_progress_while_requests_are_running(user_batch, capsys):
    args = single_task(user_batch)
    _, _, _, _, _, faults, _ = user_batch
    faults['delay'] = 0.02
    assert batch.main(args) == 0
    lines = capsys.readouterr().out.splitlines()
    assert any('进度 0/1' in line and '运行 1' in line and '当前 task-000' in line for line in lines)
    assert any('候选' in line for line in lines)


def test_exclusive_run_lock_and_foreign_output_are_not_overwritten(user_batch):
    args = single_task(user_batch)
    _, _, output, sent, _, _, _ = user_batch
    output.mkdir()
    (output / 'authorization.json').write_text('{"status":"closed"}')
    with pytest.raises(SystemExit): batch.main(args)
    assert list(output.iterdir()) == [output / 'authorization.json'] and not sent
    (output / 'authorization.json').unlink()
    write_json(output / 'batch.json', {'schema_version': 1, 'initializing': True,
                                      'source': str(user_batch[1])})
    with run_lock(output):
        with pytest.raises(SystemExit): batch.main(args)
    assert not sent


def test_transport_preserves_generation_and_judge_request_profiles(user_batch):
    generator, assessor = [read_json(path) for path in user_batch[-1][:2]]
    generator.update(request_options={'enable_thinking': True}, max_output_tokens=99)
    assessor.update(request_options={'enable_thinking': False}, max_output_tokens=100)
    combined = transport_config(generator, assessor)
    assert request_profile(combined, 'generator') == request_profile(generator, 'generator')
    assert request_profile(combined, 'judge') == request_profile(assessor, 'judge')


def test_budget_is_shared_across_concurrent_tasks_and_stages(user_batch):
    args, _, output, sent, _, faults, _ = user_batch
    args[args.index('--max-api-requests') + 1] = '12'
    faults['delay'] = 0.005
    assert batch.main(args) == 2
    assert len(sent) == 12
    assert read_json(output / 'summary.json')['accounting']['bailian']['requests'] == 12


def test_account_failure_stops_shared_provider_and_retains_receipt(user_batch):
    args, _, output, sent, _, faults, _ = user_batch
    faults['fatal'] = True
    assert batch.main(args) == 1
    assert 1 <= len(sent) <= 2  # At most the configured two in-flight requests.
    ledger = read_json(output / 'bailian-api/requests.json')
    assert all(r['provider_error']['code'] == 'Arrearage' for r in ledger)
    assert all(r['fatal'] for r in ledger)


def test_one_render_failure_does_not_skip_other_tasks(user_batch, monkeypatch):
    args, _, output, _, rendered, _, _ = user_batch
    prepare = batch.prepare_case
    def partly_broken(case, *rest):
        if case['id'] == 'task-001':
            raise ValueError('Offline corrupt PPTX fixture')
        return prepare(case, *rest)
    monkeypatch.setattr(batch, 'prepare_case', partly_broken)
    assert batch.main(args) == 1
    assert read_json(output / 'summary.json')['task_counts'] == {'complete': 2, 'error': 1}
    mapping = read_json(output / 'candidate-mapping.json')['candidates']
    assert len(mapping) == 7
    assert all(r['score'] is None and r['score_status'] == 'error' for r in mapping if r['case_id'] == 'task-001')
    monkeypatch.setattr(batch, 'prepare_case', prepare)
    assert batch.main(args) == 0
    assert len(rendered) == 3


def test_sigterm_saves_progress_and_same_command_resumes(user_batch, monkeypatch):
    args = single_task(user_batch)
    _, _, output, _, _, faults, _ = user_batch
    before = signal.getsignal(signal.SIGTERM)
    import ppt_rubric.provider as provider_module
    post = provider_module.requests.Session.post
    stopped = False
    def post_and_stop(self, *args, **kwargs):
        nonlocal stopped
        response = post(self, *args, **kwargs)
        if not stopped:
            stopped = True
            os.kill(os.getpid(), signal.SIGTERM)
        return response
    monkeypatch.setattr(provider_module.requests.Session, 'post', post_and_stop)
    assert batch.main(args) == 130
    assert signal.getsignal(signal.SIGTERM) == before
    assert batch.main(args) == 0
    with sqlite3.connect(output / 'state.sqlite3') as db:
        assert list(db.execute('SELECT status FROM sessions ORDER BY id')) == [('interrupted',), ('complete',)]


def test_thousand_tasks_are_all_scheduled_with_bounded_workers(user_batch, monkeypatch):
    args, source, output, sent, _, _, _ = user_batch
    for task in source.iterdir():
        shutil.rmtree(task)
    for i in range(1000):
        task = source / f'task-{i:04}'
        task.mkdir()
        (task / 'prompt.txt').write_text(f'Offline task {i}')
        for cid in ('one', 'two', 'three'):
            candidate = task / cid
            candidate.mkdir()
            (candidate / 'result.pptx').write_bytes(cid.encode())
    lock, running, maximum = threading.Lock(), 0, 0
    def simulate(case, output, cache, state, progress, *rest):
        nonlocal running, maximum
        with lock:
            running += 1
            maximum = max(maximum, running)
        state.task(case['id'], 'complete', '离线调度完成', {})
        with lock:
            running -= 1
    monkeypatch.setattr(batch, 'run_case', simulate)
    assert batch.main(args) == 0
    assert not sent and maximum <= 2
    assert read_json(output / 'summary.json')['task_counts'] == {'complete': 1000}
    assert len(read_json(output / 'candidate-mapping.json')['candidates']) == 3000


def test_v25_renderer_resolves_soffice_on_path(tmp_path, monkeypatch):
    from ppt_rubric.v25 import render
    executable = tmp_path / 'bin/soffice'
    executable.parent.mkdir()
    executable.write_text('#!/bin/sh\nexit 99\n')
    executable.chmod(0o755)
    monkeypatch.setenv('PATH', str(executable.parent))
    calls = []
    def stop_before_conversion(command, **kwargs):
        calls.append(command)
        raise RuntimeError('Stop before launching the fixture binary')
    monkeypatch.setattr(render.subprocess, 'run', stop_before_conversion)
    with pytest.raises(RuntimeError, match='Stop before'):
        render.prepare_deck(tmp_path / 'unused.pptx', {'soffice': 'soffice'}, tmp_path / 'cache')
    assert calls[0] == [str(executable), '--version']


def test_interruption_during_resume_keeps_prior_successes_and_http400_evidence(user_batch, monkeypatch):
    args = single_task(user_batch)
    _, _, output, sent, _, faults, _ = user_batch
    faults['judge_fail'] = True
    assert batch.main(args) == 2
    before = len(sent)
    request = batch.judge._request
    def interrupt_before_failed_request(client, system, payload, images, previous=None, **kwargs):
        if previous is not None and previous['status'] == 'unavailable':
            raise SystemExit(77)
        return request(client, system, payload, images, previous, **kwargs)
    monkeypatch.setattr(batch.judge, '_request', interrupt_before_failed_request)
    with pytest.raises(SystemExit) as error:
        batch.main(args)
    assert error.value.code == 77 and len(sent) == before
    checkpoints = [read_json(p) for p in (output / 'judgments/task-000').glob('*-assessment.json')]
    records = [r for c in checkpoints for r in c['content']['records']]
    assert sum(r['status'] == 'unavailable' for r in records) == 1
    assert any(r.get('error', {}).get('http_status') == 400 for r in records)
    assert list((output / 'judgments/task-000/checkpoint-history').glob('*.json'))
    monkeypatch.setattr(batch.judge, '_request', request)
    assert batch.main(args) == 0 and len(sent) == before + 1


def test_successful_response_is_durable_before_call_returns(user_batch, monkeypatch):
    _, _, output, sent, _, _, configs = user_batch
    output.mkdir()
    state = BatchState(output / 'state.sqlite3')
    progress = batch.Progress(output, state, 0, 1)
    config = read_json(configs[0])
    provider = BatchProvider(config, output / 'cache', state, 'bailian', 10, output, progress, threading.Event())
    view = provider.view(config, 'offline', '生成标准')
    payload = {'source_catalog': [{'id': 'source-1', 'text': '项目目标'}]}
    save_response = state.save_response
    monkeypatch.setattr(state, 'save_response', lambda *args: (_ for _ in ()).throw(SystemExit(77)))
    try:
        with pytest.raises(SystemExit):
            view.ask('generator', contract.EXTRACT, payload, validate=lambda x: None)
        assert len(sent) == 1 and state.accounting('bailian')['success'] == 1
        shutil.rmtree(output / 'cache/api')
        monkeypatch.setattr(state, 'save_response', save_response)
        answer = view.ask('generator', contract.EXTRACT, payload, validate=lambda x: None)
        assert answer['requirements'][0]['id'] == 'Q01' and len(sent) == 1
    finally:
        state.close()


@pytest.mark.parametrize('cap', [0, 1, 2])
def test_budget_exhaustion_during_generation_is_resumable_not_fatal(user_batch, cap):
    args = single_task(user_batch)
    _, _, output, sent, _, _, _ = user_batch
    args[args.index('--max-api-requests') + 1] = str(cap)
    assert batch.main(args) == 2 and len(sent) == cap
    args[args.index('--max-api-requests') + 1] = '1000'
    assert batch.main(args) == 0


def test_missing_credentials_stop_once_without_counting_unsent_http_requests(user_batch, monkeypatch):
    args, _, output, sent, _, _, _ = user_batch
    monkeypatch.delenv('BAILIAN_API_KEY')
    assert batch.main(args) == 1 and not sent
    assert read_json(output / 'summary.json')['accounting']['bailian']['requests'] == 0
    monkeypatch.setenv('BAILIAN_API_KEY', 'offline-fixed-key')
    assert batch.main(args) == 0


def test_default_light_batch_ten_tasks_and_judge_thirty():
    args = batch.parser().parse_args(['--source', '/fixture/inputs', '--out', '/fixture/run', '--max-api-requests', '0'])
    assert args.mode == contract.pipeline_mode({}) == 'light' and args.workers == 10
    for suffix in ('', '-fast'):
        cfg = read_json(batch.ROOT / f'configs/contract-v25-judge{suffix}.json')
        assert cfg['api_concurrency'] == cfg['assessment_workers'] == 30
        assert cfg['min_request_interval_seconds'] == 0


def test_judge_reaches_thirty_concurrent_requests_with_shared_generation_client(user_batch, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    _, _, output, _, _, _, configs = user_batch
    generator = read_json(configs[0])
    generator.update(api_concurrency=1, min_request_interval_seconds=20)
    assessor = read_json(batch.ROOT / 'configs/contract-v25-judge-fast.json')
    shared = transport_config(generator, assessor)
    output.mkdir()
    state = BatchState(output / 'state.sqlite3')
    progress = batch.Progress(output, state, 0, 1)
    provider = BatchProvider(shared, output / 'cache', state, 'bailian', 30, output, progress, threading.Event())
    barrier = threading.Barrier(30)
    class Response:
        ok = True
        status_code = 200
        def json(self):
            return {'choices': [{'message': {'content': r'Independent offline evidence.\boxed{yes}'}, 'finish_reason': 'stop'}],
                    'usage': {'total_tokens': 1}}
    def post(self, *args, **kwargs):
        barrier.wait(timeout=5)  # Fails if the generation profile caps or spaces judge calls.
        return Response()
    monkeypatch.setattr('ppt_rubric.provider.requests.Session.post', post)
    def call(index):
        view = provider.view(assessor, 'fixture', '评分')
        return view.ask_text('judge', 'Offline judgment', {'criterion': {'id': f'C{index}'}},
                             validate=batch.judge.parse_boxed_answer)
    try:
        with ThreadPoolExecutor(max_workers=30) as pool:
            results = list(pool.map(call, range(30)))
        assert len(results) == state.accounting('bailian')['success'] == 30
        assert provider._stage_gates['generate']._value == 1
        assert provider.pacing_profile() == (None, 20)  # Outside a stage, retain the shared fallback.
    finally:
        state.close()


@pytest.mark.parametrize('problem', ['missing', 'duplicate'])
def test_visual_batch_format_retry_preserves_bad_raw_and_one_record_per_page(user_batch, monkeypatch, problem):
    args = single_task(user_batch)
    _, _, output, _, _, _, configs = user_batch
    for path in configs[:2]:
        cfg = read_json(path)
        cfg['max_retries'] = 2
        write_json(path, cfg)
    import ppt_rubric.provider as provider_module
    original_post = provider_module.requests.Session.post
    invalid_raw = None
    def malformed_once(self, *args, **kwargs):
        nonlocal invalid_raw
        response = original_post(self, *args, **kwargs)
        payload = json.loads(kwargs['json']['messages'][1]['content'][0]['text'])
        if 'criteria' in payload and invalid_raw is None:
            data = json.loads(response.raw)
            if problem == 'missing': data['verdicts'].pop()
            else: data['verdicts'][-1] = data['verdicts'][0]
            invalid_raw = json.dumps(data)
            response.raw = invalid_raw
        return response
    monkeypatch.setattr(provider_module.requests.Session, 'post', malformed_once)
    assert batch.main(args) == 0
    ledger = read_json(output / 'bailian-api/requests.json')
    visual = [r for r in ledger if r['request_context'].get('rubric_ids')]
    assert len(visual) == 3 and sum(r['status'] == 'success' for r in visual) == 2
    failed = next(r for r in visual if r['status'] == 'failed')
    assert failed['failure_stage'] == 'schema_validation'
    assert read_json(output / 'bailian-api' / failed['invalid_response']) == invalid_raw
    assert all(len(r['request_context']['rubric_ids']) == 5 and r['request_context']['pages'] == [1] for r in visual)
    for path in (output / 'judgments/task-000').glob('*-assessment.json'):
        assessment = read_json(path)
        assert len(assessment['visual_batches']) == 1
        assert len(assessment['visual_pages'][0]['verdicts']) == 5
