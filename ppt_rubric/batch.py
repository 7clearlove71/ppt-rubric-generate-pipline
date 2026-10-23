"""One-command contract-v2.5 batches; the same command resumes the same run."""
from __future__ import annotations

import argparse
import copy
import json
import signal
import threading
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path

from .batch_provider import BatchProvider, transport_config
from .batch_state import BatchState, run_lock
from .candidate_mapping import mapping_rows, write_candidate_mapping
from .common import digest, file_digest, read_json, write_json
from .data import ingest
from .provider import ProviderError
from .v25 import contract_judge as judge
from .v25 import contract_v2 as contract
from .v25.recovery import assessment_binding, validate_checkpoint
from .v25.render import case_sources, prepare_case
from .v25.source_catalog import source_catalog

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = 1
OPERATIONAL = {'api_concurrency', 'min_request_interval_seconds', 'timeout_seconds', 'role_timeout_seconds',
               'max_api_requests', 'max_total_api_requests', 'prior_api_requests', 'candidate_workers',
               'assessment_workers', 'rate_limit_backoff_seconds', 'max_consecutive_rate_limits',
               'max_consecutive_transport_failures'}


def config_binding(config):
    # Store only a hash of config, never credential values or arbitrary config text.
    return digest({key: value for key, value in config.items() if key not in OPERATIONAL})


def source_code_binding():
    return digest({str(path.relative_to(ROOT)): file_digest(path)
                   for path in sorted((ROOT / 'ppt_rubric').rglob('*.py'))})


class Progress:
    def __init__(self, output, state, total, interval):
        self.output, self.state, self.total, self.interval = Path(output), state, total, interval
        self.lock, self.done = threading.RLock(), threading.Event()
        self.active, self.reused, self.finished_requests, self.failed_requests = {}, 0, 0, 0
        self.started = time.monotonic()
        self.thread = None

    def line(self, text):
        with self.lock:
            line = datetime.now().astimezone().strftime('%H:%M:%S') + ' | ' + text
            with (self.output / 'progress.log').open('a', encoding='utf-8') as stream:
                stream.write(line + '\n')
            print(line, flush=True)

    def current(self, case_id, stage):
        with self.lock:
            self.active[case_id] = stage

    def stage(self, case_id, stage):
        self.state.task(case_id, 'running', stage)
        self.current(case_id, stage)
        self.line(f'{case_id}：{stage}')

    def request(self, reused=False, failed=False):
        with self.lock:
            self.reused += reused
            self.finished_requests += not reused and not failed
            self.failed_requests += failed

    def finish(self, case_id, status):
        with self.lock:
            self.active.pop(case_id, None)
        self.line(f'{case_id}：' + {'complete': '完成', 'incomplete': '有接口缺项，下次续跑补齐',
                                      'error': '失败，原因见 summary.json'}.get(status, status))
        self.snapshot()

    def snapshot(self):
        counts = Counter(self.state.counts())
        with self.lock:
            data = {'schema_version': SCHEMA, 'updated_unix': time.time(), 'total': self.total,
                    'complete': counts['complete'], 'running': counts['running'],
                    'failed': counts['error'], 'incomplete': counts['incomplete'],
                    'pending': counts['pending'] + counts['interrupted'],
                    'elapsed_seconds': round(time.monotonic() - self.started, 1),
                    'active': dict(self.active), 'reused_responses_this_session': self.reused,
                    'requests': {p: self.state.request_count(p) for p in ('bailian', 'maas')}}
            write_json(self.output / 'progress.json', data)
            percent = 100 * data['complete'] / max(1, self.total)
            text = (f'进度 {data["complete"]}/{self.total}（{percent:.1f}%） | 运行 {data["running"]}'
                    f' | 失败 {data["failed"]} | 缺项 {data["incomplete"]} | 待处理 {data["pending"]}'
                    f' | 累计请求 {sum(data["requests"].values())} | 已用 {self.elapsed(data["elapsed_seconds"])}')
            if self.active:
                text += ' | 当前 ' + '；'.join(f'{cid} {stage}' for cid, stage in sorted(self.active.items()))
        self.line(text)
        return data

    @staticmethod
    def elapsed(seconds):
        minutes, seconds = divmod(int(seconds), 60)
        hours, minutes = divmod(minutes, 60)
        return f'{hours:02}:{minutes:02}:{seconds:02}'

    def start(self):
        self.snapshot()
        def heartbeat():
            while not self.done.wait(self.interval):
                self.snapshot()
        self.thread = threading.Thread(target=heartbeat, name='batch-progress', daemon=True)
        self.thread.start()

    def close(self):
        self.done.set()
        if self.thread:
            self.thread.join()


def saved_decks(path):
    record = read_json(path)
    if record.get('hash') != digest({k: v for k, v in record.items() if k != 'hash'}):
        raise ValueError(f'准备稿检查点完整性校验失败：{path}')
    for image, sha in record['assets'].items():
        if not Path(image).is_file() or file_digest(image) != sha:
            raise ValueError(f'已保存的PDF或图片缺失/变化：{image}')
    return record['decks']


def save_decks(path, decks):
    assets = {}
    for deck in decks.values():
        for asset in ([deck['pdf']] if deck.get('pdf') else []) + [s['image'] for s in deck['slides']]:
            assets[asset] = file_digest(asset)
    record = {'decks': decks, 'assets': assets}
    record['hash'] = digest(record)
    write_json(path, record)


def preflight_resume(cases, output, state, config):
    """Check every existing binding before allowing any new HTTP request."""
    state.verify_artifacts()
    state.verify_responses()
    for case in cases:
        suite_path = output / 'rubrics' / case['id'] / 'rubric.json'
        prepared = output / 'prepared' / f'{case["id"]}.json'
        suite = read_json(suite_path) if suite_path.exists() else None
        if suite is not None:
            contract.verify_suite(case, suite, config)
        decks = saved_decks(prepared) if prepared.exists() else None
        for candidate in case['candidates']:
            checkpoint = output / 'judgments' / case['id'] / f'{candidate["id"]}-assessment.json'
            if not checkpoint.exists():
                continue
            if suite is None or decks is None:
                raise ValueError('评分检查点缺少对应rubric或准备稿，请保留原运行目录')
            catalog = source_catalog(case_sources(case, config))
            binding = assessment_binding(decks[candidate['id']], suite, catalog, config, judge.prompt_bundle(), strict=True)
            validate_checkpoint(checkpoint, binding, judge.parse_boxed_answer)


def compact_reports(reports):
    fields = ('case_id', 'candidate_id', 'candidate_hash', 'candidate_identity', 'document_id',
              'summary', 'comparison', 'execution', 'pipeline_mode', 'source_audit')
    return {cid: {key: copy.deepcopy(report[key]) for key in fields if key in report}
            for cid, report in reports.items()}


def publish(output, state, manifest, caps):
    tasks = state.tasks()
    results = {cid: row['result'] for cid, row in tasks.items() if row['result'] is not None}
    counts = Counter(row['status'] for row in tasks.values())
    accounting = {p: {**state.accounting(p), 'request_cap': cap} for p, cap in caps.items()}
    status = ('complete' if counts['complete'] == len(tasks) else
              'failed' if counts['error'] else 'incomplete')
    summary = {'schema_version': SCHEMA, 'updated_unix': time.time(), 'manifest_id': manifest['id'],
               'status': status, 'total_tasks': len(tasks), 'task_counts': dict(counts),
               'tasks': tasks, 'accounting': accounting,
               'comparison_policy': judge.COMPARISON_POLICY,
               'notes': ['比较分数与执行完整性分开报告；接口缺项不表示执行完成。',
                         '仅同题候选可比较；映射顺序不代表排名。']}
    write_json(output / 'summary.json', summary)
    write_json(output / 'run-summary.json', results)
    write_candidate_mapping(output, mapping_rows(manifest['cases'], results, output / 'judgments'), manifest['id'])
    lines = ['# 批量运行结果', '', f'状态：{status}；完成 {counts["complete"]}/{len(tasks)} 题。', '',
             '| 任务 | 执行状态 | 阶段 |', '| --- | --- | --- |']
    for cid, row in tasks.items():
        safe = lambda value: str(value).replace('|', '\\|').replace('\n', ' ')
        lines.append(f'| {safe(cid)} | {row["status"]} | {safe(row["stage"])} |')
    lines += ['', '分数、满分和原始作品名称见 candidate-mapping.md / .csv；逐条理由见 judgments。',
              '完整状态和用量见 summary.json；运行中进度见 progress.json / progress.log。', '']
    (output / 'summary.md').write_text('\n'.join(lines), encoding='utf-8')
    for provider in caps:
        state.export_requests(provider, output / f'{provider}-api' / 'requests.json')
    return summary


def run_case(case, output, cache, state, progress, primary, auditor, generator, assessor, audit_config):
    cid = case['id']
    try:
        progress.stage(cid, '读取来源')
        sources = case_sources(case, generator)
        progress.stage(cid, '生成评分标准')
        suite = contract.generate_contract(case, generator, primary.view(generator, cid, '生成标准'),
            output / 'rubrics', reviewer_client=auditor.view(audit_config, cid, '审核标准') if auditor else None,
            progress=lambda stage: progress.current(cid, stage))
        contract.verify_suite(case, suite, assessor)
        state.artifact(output / 'rubrics' / cid / 'rubric.json')
        progress.stage(cid, '渲染PPT')
        prepared = output / 'prepared' / f'{cid}.json'
        if prepared.exists():
            decks = saved_decks(prepared)
        else:
            decks = prepare_case(case, assessor, cache)
            save_decks(prepared, decks)
        state.artifact(prepared)
        progress.stage(cid, '评分')
        client = primary.view(assessor, cid, '评分')
        client.documents = {judge.document_id(deck): key for key, deck in decks.items()}
        reports = judge.evaluate_contract(case, suite, assessor, client, decks, sources,
                                          output / 'judgments', resume_current=True)
        status = ('error' if any(r['execution']['status'] == 'failed' for r in reports.values()) else
                  'complete' if all(r['execution']['status'] == 'complete' for r in reports.values()) else 'incomplete')
        # Mutable partial checkpoints carry their own hashes. Final reports and
        # completed-task status are committed together; a crash between file
        # writes and this transaction must remain resumable.
        artifacts = [output / 'judgments' / cid / name for candidate in case['candidates']
                     for name in (f'{candidate["id"]}.json', f'{candidate["id"]}-assessment.json')] if status == 'complete' else []
        state.task(cid, status, '完成' if status == 'complete' else '评分有接口缺项',
                   compact_reports(reports), artifacts=artifacts)
    except Exception as error:
        interrupted = isinstance(error, ProviderError) and error.details.get('error_category') in {
            'request_budget', 'batch_stopped'}
        status = 'incomplete' if interrupted else 'error'
        state.task(cid, status, '等待续跑' if interrupted else '任务失败',
                   {'status': 'error', 'error_type': type(error).__name__, 'message': str(error)})
    progress.finish(cid, status)
    return status


def parser():
    result = argparse.ArgumentParser(description='批量评测：一条命令完成全流程，重复同一命令自动断点续跑。')
    result.add_argument('--source', required=True, help='输入批次目录：任务/prompt.txt 与 任务/候选/result.pptx')
    result.add_argument('--out', required=True, help='专用运行目录；重复使用相同目录自动续跑')
    result.add_argument('--mode', choices=contract.PIPELINE_MODES, default='light', help='默认 light，full 启用来源审核')
    result.add_argument('--config', default=str(ROOT / 'configs/contract-v25-k3-fast.json'), help='生成配置')
    result.add_argument('--judge-config', default=str(ROOT / 'configs/contract-v25-judge-fast.json'))
    result.add_argument('--source-audit-config', default=str(ROOT / 'configs/contract-v25-auditor-fast.json'))
    result.add_argument('--max-api-requests', '--bailian-cap', dest='primary_cap', required=True, type=int,
                        help='百炼整批累计请求上限，包含生成、评分、失败、重试及中断在途请求；续跑不清零')
    result.add_argument('--audit-max-api-requests', '--maas-cap', dest='audit_cap', type=int,
                        help='full模式必填：MaaS整批累计审核请求上限')
    result.add_argument('--workers', type=int, default=10, help='并行任务数，默认10；judge默认全局30并发，共享预算与熔断')
    result.add_argument('--log-interval', type=float, default=10, help='每隔多少秒输出进度，默认10秒')
    result.add_argument('--confirm-bailian-bulk', action='store_true')
    return result


def main(argv=None):
    argparser = parser()
    args = argparser.parse_args(argv)
    if args.primary_cap < 0 or args.workers < 1 or not 0.1 <= args.log_interval <= 3600:
        argparser.error('请求上限须非负，workers须为正数，log-interval须在0.1—3600秒之间')
    if args.mode == 'full' and (args.audit_cap is None or args.audit_cap < 0):
        argparser.error('full模式需要非负的 --audit-max-api-requests')
    source, output = Path(args.source).resolve(), Path(args.out).resolve()
    if source == output or output.is_relative_to(source) or source.is_relative_to(output):
        argparser.error('输入批次和运行目录必须分开，不能互相包含')
    if output.exists() and not (output / 'batch.json').is_file() and any(
            p.name != '.batch.lock' and not p.name.startswith('.writing-') for p in output.iterdir()):
        argparser.error('输出目录不是通用batch运行目录；请使用新目录，不会迁移历史运行')
    try:
        generator, assessor = read_json(args.config), read_json(args.judge_config)
        generator['pipeline_mode'] = args.mode
        audit = read_json(args.source_audit_config) if args.mode == 'full' else None
        contract.validate_config(generator, 'generate')
        contract.validate_config(assessor, 'judge')
        if audit:
            contract.validate_config(audit, 'generate')
        shared_config = transport_config(generator, assessor)
        output.mkdir(parents=True, exist_ok=True)
        with run_lock(output):
            return execute(args, source, output, generator, assessor, audit, shared_config)
    except (OSError, ValueError, KeyError, TypeError) as error:
        argparser.error(str(error))


def execute(args, source, output, generator, assessor, audit, shared_config):
    marker = output / 'batch.json'
    if not marker.exists():
        write_json(marker, {'schema_version': SCHEMA, 'initializing': True, 'source': str(source)})
    previous = read_json(marker)
    if previous.get('schema_version') != SCHEMA or previous.get('source') != str(source):
        raise ValueError('运行目录版本或输入目录不匹配，请使用新运行目录')
    manifest = ingest(source, output / 'manifest.json', split='dev')
    cases = manifest['cases']
    if len(cases) > 1 and not args.confirm_bailian_bulk:
        raise ValueError('多任务百炼调用须传 --confirm-bailian-bulk，落实本次已授权范围')
    spec = {'schema_version': SCHEMA, 'source': str(source), 'manifest_id': manifest['id'],
            'protocol': contract.VERSION, 'mode': args.mode,
            'generator_binding': config_binding(generator), 'judge_binding': config_binding(assessor),
            'audit_binding': config_binding(audit) if audit else None, 'code_binding': source_code_binding()}
    if not previous.get('initializing') and previous != spec:
        raise ValueError('输入、模式、评分配置或程序版本变化；拒绝混用旧结果，请使用新运行目录')
    write_json(marker, spec)
    caps = {'bailian': args.primary_cap}
    if audit:
        caps['maas'] = args.audit_cap
    state = BatchState(output / 'state.sqlite3')
    progress = None
    try:
        state.add_tasks(cases)
        preflight_resume(cases, output, state, assessor)
        interrupted = state.recover_interrupted()
        session = state.start_session({'caps': caps, 'workers': args.workers})
        progress = Progress(output, state, len(cases), args.log_interval)
        stop, user_stop = threading.Event(), threading.Event()
        primary = BatchProvider(shared_config, output / 'cache', state, 'bailian', caps['bailian'], output, progress, stop)
        auditor = BatchProvider(audit, output / 'cache', state, 'maas', caps['maas'], output, progress, stop) if audit else None
        progress.line(f'批量任务 {len(cases)}题 / {sum(len(c["candidates"]) for c in cases)}份候选；模式 {args.mode}；重复同一命令即可续跑')
        if interrupted:
            progress.line(f'发现 {interrupted} 条中断在途请求：保留远端结果未知记录，计入累计额度')
        previous_tasks = state.tasks()
        pending = iter(case for case in cases if previous_tasks[case['id']]['status'] != 'complete')
        skipped = sum(row['status'] == 'complete' for row in previous_tasks.values())
        progress.line(f'跳过已完成 {skipped}题；待执行或恢复 {len(cases) - skipped}题')
        publish(output, state, manifest, caps)
        progress.start()
        handlers = {}
        def request_stop(signum, frame):
            stop.set()
            user_stop.set()
            progress.line('收到停止信号：停止启动新请求，等待在途请求保存；下次执行相同命令继续')
        if threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGINT, signal.SIGTERM):
                handlers[signum] = signal.signal(signum, request_stop)
        try:
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                active = set()
                exhausted = False
                while active or not exhausted:
                    while not exhausted and not stop.is_set() and len(active) < args.workers:
                        case = next(pending, None)
                        if case is None:
                            exhausted = True
                            break
                        active.add(pool.submit(run_case, case, output, output / 'cache', state, progress,
                                               primary, auditor, generator, assessor, audit))
                    if not active:
                        break
                    completed, active = wait(active, timeout=0.2, return_when=FIRST_COMPLETED)
                    for future in completed:
                        future.result()
        finally:
            for signum, handler in handlers.items():
                signal.signal(signum, handler)
            progress.close()
        summary = publish(output, state, manifest, caps)
        session_status = 'interrupted' if user_stop.is_set() else summary['status']
        state.end_session(session, session_status)
        progress.snapshot()
        progress.line(f'本次执行结束：{session_status}；结果 summary.md / candidate-mapping.csv；日志 progress.log')
        if user_stop.is_set():
            return 130
        if primary.fatal_error or (auditor and auditor.fatal_error) or summary['status'] == 'failed':
            return 1
        return 0 if summary['status'] == 'complete' else 2
    finally:
        if progress:
            progress.close()
        state.close()


if __name__ == '__main__':
    raise SystemExit(main())
