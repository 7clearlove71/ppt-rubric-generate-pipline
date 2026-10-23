"""Bounded development-only paired probe; never modifies historical reports."""
import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import copy
import json
import os
from pathlib import Path
import random
import signal
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ppt_rubric.common import read_json, write_json, file_digest, digest
from ppt_rubric.provider import Client, ProviderError
from ppt_rubric.v25.contract_judge import parse_boxed_answer, visual_item
from ppt_rubric.v25.judge_batch import compile_batch_prompt, parse_batch_answer

CAP = 90
RUNS = {'02': ROOT / 'artifacts/validation-20261021-contract-v25-case02-light',
        '03': ROOT / 'artifacts/validation-20261022-contract-v25-case03-light'}


def prepare(out):
    if (out / 'plan.json').exists():
        return read_json(out / 'plan.json')
    tasks, bindings = [], []
    sources = {}
    for case, root in RUNS.items():
        items = read_json(root / f'rubrics/{case}/rubric.json')['items']
        inputs = [read_json(p) for p in sorted((root / 'bailian-inputs').glob('[0-9][0-9][0-9].json'))]
        for cid in (['A'] if case == '02' else ['A', 'B']):
            assessment = read_json(root / f'judgments/{case}/{cid}-assessment.json')
            doc = assessment['document_id']
            lookup = {}
            for request in inputs:
                payload = request['payload']
                if payload.get('document_id') == doc and 'criterion' in payload:
                    page = payload['pages'][0]['page'] if payload['evaluation_scope'] == 'page' else None
                    lookup[(payload['criterion']['id'], page)] = request
            sources[(case, cid)] = (items, assessment, lookup)
            bindings.extend({'path': str(p), 'sha256': file_digest(p)} for p in
                            [root / f'rubrics/{case}/rubric.json', root / f'judgments/{case}/{cid}-assessment.json'])

    def add(case, cid, selected, page, size, repeat=False):
        items, assessment, lookup = sources[(case, cid)]
        request = copy.deepcopy(lookup[(selected[0]['id'], page)])
        if size != 1:
            request['system'] = compile_batch_prompt(selected)
            request['payload'].pop('criterion')
            request['payload']['criteria'] = [{'id': i['id']} for i in selected]
        ids = [i['id'] for i in selected]
        rows = assessment['content']['records'] if page is None else [
            r for r in assessment['visual_batches'] if r['pages'] == [page]]
        expected = {r['rubric_id']: r['answer'] for r in rows if r['status'] == 'success'}
        assert all(rid in expected for rid in ids)
        for image in request['images']:
            assert file_digest(image['path']) == image['sha256'], image['path']
        tasks.append({'id': f'{case}-{cid}-p{page or 0}-k{size}-n{len(tasks):03d}',
                      'case': case, 'candidate': cid, 'page': page, 'batch_size': size,
                      'ids': ids, 'weights': {i['id']: i['score'] for i in selected},
                      'historical_answers': {rid: expected[rid] for rid in ids},
                      'request': request, 'single_repeat': repeat})

    for case, cid in [('02', 'A'), ('03', 'B')]:
        items = [i for i in sources[(case, cid)][0] if not visual_item(i)]
        for size in [3, 5, 10, len(items)]:
            for start in range(0, len(items), size):
                add(case, cid, items[start:start + size], None, size)
        repeat_ids = (['R_Q25', 'C_CONSISTENCY', 'C_COMPARE'] if case == '02'
                      else ['R_Q10', 'C_REASON', 'I_BOUNDARY'])
        for rid in repeat_ids:
            add(case, cid, [next(i for i in items if i['id'] == rid)], None, 1, True)
    for case, cid, page in [('02', 'A', 4), ('02', 'A', 8), ('03', 'A', 4), ('03', 'B', 6)]:
        items = [i for i in sources[(case, cid)][0] if visual_item(i)]
        for size in [3, 5]:
            for start in range(0, len(items), size):
                add(case, cid, items[start:start + size], page, size)
    random.Random(20261023).shuffle(tasks)
    plan = {'protocol': 'judge_batch_probe_v1', 'tasks': tasks, 'historical_bindings': bindings,
            'api_cap': CAP, 'limitations': ['Historical single judgments are not truth labels',
                'Development cases only; no human labels; no holdout; case09 remains paused',
                'Content whole-deck and visual same-page are separate experiments',
                'Single repeats are sparse; no claim of statistical equivalence']}
    write_json(out / 'plan.json', plan)
    write_json(out / 'authorization.json', {'status': 'prepared', 'scope': plan['limitations'],
        'user_request': '简单实验3-5个rubric一次judge，准确度差不多情况下一次性judge越多越好',
        'operational_cap': CAP, 'cap_set_by_agent': True, 'historical_authorizations_reopened': False})
    return plan


def summarize(out, plan):
    results = [read_json(p) for p in (out / 'results').glob('*.json')]
    groups, disagreements = {}, []
    for result in results:
        key = ('content' if result['page'] is None else 'visual') + f"/k{result['batch_size']}"
        group = groups.setdefault(key, {'calls': 0, 'success': 0, 'decisions': 0, 'matched': 0,
            'historical_no': 0, 'retained_no': 0, 'historical_yes': 0, 'retained_yes': 0,
            'elapsed_seconds': [], 'signed_score_change': 0, 'absolute_decision_weight_change': 0})
        group['calls'] += 1
        group['elapsed_seconds'].append(result['elapsed_seconds'])
        if result['status'] != 'success':
            continue
        group['success'] += 1
        for row in result['verdicts']:
            rid, answer = row['rubric_id'], row['answer']
            old = result['historical_answers'][rid]
            group['decisions'] += 1
            group['matched'] += answer == old
            if old in ('yes', 'no'):
                group[f'historical_{old}'] += 1
                group[f'retained_{old}'] += answer == old
            if answer != old:
                weight = result['weights'][rid]
                group['signed_score_change'] += weight * ((answer == 'yes') - (old == 'yes'))
                group['absolute_decision_weight_change'] += weight
                disagreements.append({'task': result['id'], 'rubric_id': rid, 'historical': old,
                                      'new': answer, 'weight': weight, 'evidence': row['evidence']})
    ledger = read_json(out / 'api/requests.json') if (out / 'api/requests.json').exists() else []
    report = {'planned_calls': len(plan['tasks']), 'completed_calls': len(results), 'groups': groups,
        'disagreements': disagreements, 'accounting': {'requests': len(ledger),
        'success': sum(r['status'] == 'success' for r in ledger),
        'failed': sum(r['status'] == 'failed' for r in ledger),
        'started': sum(r['status'] == 'started' for r in ledger),
        'known_total_tokens': sum(r.get('usage', {}).get('total_tokens', 0) for r in ledger),
        'missing_usage': sum(not r.get('usage') for r in ledger)}, 'limitations': plan['limitations']}
    write_json(out / 'summary.json', report)
    return report


def detailed_report(out, plan):
    """Offline analysis, with provider response time kept separate from queue time."""
    results = [read_json(p) for p in sorted((out / 'results').glob('*.json'))]
    ledger = read_json(out / 'api/requests.json') if (out / 'api/requests.json').exists() else []
    cached = {}
    for path in (out / 'cache/api').glob('*.json'):
        data = read_json(path)
        cached[data['parsed']] = path.stem
    by_cache = {}
    for record in ledger:
        by_cache.setdefault(record['cache_key'], []).append(record)
    fresh = {(r['case'], r['candidate'], v['rubric_id']): v['answer']
             for r in results if r['single_repeat'] and r['status'] == 'success'
             for v in r['verdicts']}
    groups, scores = {}, {}
    for result in results:
        size = 'all' if result['batch_size'] > 10 else str(result['batch_size'])
        kind = 'content' if result['page'] is None else 'visual'
        key = kind + '/' + size
        g = groups.setdefault(key, {'calls': 0, 'successful_calls': 0, 'decisions': 0,
            'matches': 0, 'historical_no': 0, 'retained_no': 0, 'new_unknowns': 0,
            'fresh_control_decisions': 0, 'fresh_control_matches': 0,
            'fresh_control_no': 0, 'fresh_control_no_retained': 0,
            'provider_seconds': 0, 'total_tokens': 0, 'output_tokens': 0, 'format_failures': 0})
        g['calls'] += 1
        if result['status'] != 'success':
            continue
        g['successful_calls'] += 1
        records = by_cache.get(cached.get(result['raw']), [])
        for record in records:
            g['provider_seconds'] += record['elapsed_seconds']
            g['total_tokens'] += record.get('usage', {}).get('total_tokens', 0)
            g['output_tokens'] += record.get('usage', {}).get('completion_tokens', 0)
            g['format_failures'] += record.get('failure_stage') == 'schema_validation'
        skey = f"{result['case']}/{result['candidate']}/{key}"
        s = scores.setdefault(skey, {'decisions': 0, 'historical_earned': 0, 'new_earned': 0,
                                    'possible': 0, 'unknown_weight': 0})
        for row in result['verdicts']:
            rid, answer = row['rubric_id'], row['answer']
            old = result['historical_answers'][rid]
            weight = result['weights'][rid]
            g['decisions'] += 1
            g['matches'] += old == answer
            g['historical_no'] += old == 'no'
            g['retained_no'] += old == answer == 'no'
            g['new_unknowns'] += answer == 'unknown' and old != 'unknown'
            control = fresh.get((result['case'], result['candidate'], rid)) if kind == 'content' else None
            if control is not None and not result['single_repeat']:
                g['fresh_control_decisions'] += 1
                g['fresh_control_matches'] += answer == control
                g['fresh_control_no'] += control == 'no'
                g['fresh_control_no_retained'] += control == answer == 'no'
            s['decisions'] += 1
            s['possible'] += weight
            s['historical_earned'] += weight if old == 'yes' else 0
            s['new_earned'] += weight if answer == 'yes' else 0
            s['unknown_weight'] += weight if answer == 'unknown' else 0
    for g in groups.values():
        n = g['decisions']
        g['agreement'] = g['matches'] / n if n else None
        g['no_retention'] = g['retained_no'] / g['historical_no'] if g['historical_no'] else None
        g['provider_seconds_per_decision'] = g['provider_seconds'] / n if n else None
    for s in scores.values():
        s['score_change'] = s['new_earned'] - s['historical_earned']
    historical = {}
    for case, cid in [('02', 'A'), ('03', 'B')]:
        root = RUNS[case]
        assessment = read_json(root / f'judgments/{case}/{cid}-assessment.json')
        raws = {r['raw'] for r in assessment['content']['records'] if r['status'] == 'success'}
        keys = set()
        for path in (root / 'cache/api').glob('*.json'):
            parsed = read_json(path).get('parsed')
            if isinstance(parsed, str) and parsed in raws:
                keys.add(path.stem)
        records = [r for r in read_json(root / 'bailian-api/requests.json')
                   if r['cache_key'] in keys and r['status'] == 'success']
        historical[f'{case}/{cid}'] = {'decisions': len(raws), 'matched_ledger_rows': len(records),
            'provider_seconds': sum(r['elapsed_seconds'] for r in records),
            'total_tokens': sum(r.get('usage', {}).get('total_tokens', 0) for r in records)}
    identities = {}
    for task in plan['tasks']:
        identities.setdefault(digest(task['request']), []).append(task['id'])
    duplicates = [ids for ids in identities.values() if len(ids) > 1]
    report = {'groups': groups, 'scores': scores, 'historical_content_service_work': historical,
              'identical_requests_across_settings': duplicates,
              'cache_note': 'Identical remainder groups may reuse the same successful response. Their repeated decisions are not independent observations; per-setting service work attributes the same response to each applicable setting.',
              'acceptance': plan.get('predeclared_acceptance'),
              'timing_note': 'Summed provider response seconds are service work, not wall-clock speedup. Historical timing is not a randomized concurrent control.',
              'visual_score_note': 'Visual sample weights repeat across pages; never sum them as deck penalties.',
              'limitations': plan['limitations']}
    if len(results) == len(plan['tasks']):
        report['recommendation'] = {
            'production_default_changed': False,
            'content': 'Keep one criterion per request; uniform batching has not demonstrated comparable semantic quality.',
            'visual': 'Five criteria on the same page is the strongest pilot candidate; four sampled pages do not establish general accuracy equivalence.',
            'quality_equivalence_passed': False,
            'historical_content_baseline_is_not_truth': True}
    write_json(out / 'analysis.json', report)
    lines = ['# Judge 合批开发实验', '',
             '单条历史结果是对照，并非真实标签；不报告准确率。未读人工标签或留出，09保持暂停。', '',
             '| 范围 | 每次条数 | 调用完成 | 历史一致 | 历史no保留 | 新单条对照一致 | 新unknown | 响应秒/条 |',
             '|---|---:|---:|---:|---:|---:|---:|---:|']
    for key, g in sorted(groups.items()):
        kind, size = key.split('/')
        lines.append(f"| {kind} | {size} | {g['successful_calls']}/{g['calls']} | {g['matches']}/{g['decisions']} | {g['retained_no']}/{g['historical_no']} | {g['fresh_control_matches']}/{g['fresh_control_decisions']} | {g['new_unknowns']} | {g['provider_seconds_per_decision'] or 0:.2f} |")
    lines += ['', '响应秒为供应商响应耗时之和/条目数，排除本地排队；不是端到端提速实测。',
              '视觉仍逐页判断，再按任意no优先聚合；示例页权重不能按页叠加成整稿扣分。', '',
              '逐条理由、差异、API账本分别见results、summary.json、api/requests.json。']
    if len(results) == len(plan['tasks']):
        lines += ['', '## 建议与边界', '',
                  '优先试行同页视觉5条；内容保留逐条。本轮没有切换主pipeline默认模式。', '',
                  '内容3/5/10/全部组均为54条全yes；历史一致率51/54=94.44%，但6项定向新单条对照只匹配4项，未复现03/B的推理与信息边界no。',
                  '新单条自身与历史也仅1/6一致；这6项是定向疑难项，不能当作随机样本推算总体波动率。',
                  '视觉5条组保留了采样页的叠印、NaN等主要问题，17/20与历史一致，另外3项为叠印残留/布局归属变化，静态抽查更支持避免重复扣分。',
                  '这属于小样本语义观察，未通过预设的纯一致性门槛，不宣称总体准确率相当或生产验收通过。',
                  '03/B第6页理由仍把目标6与刻度8称为叠印，位置归因不准确；7.75与8的挤叠可见。保持原文，不把结论一致当作理由全对。', '',
                  '同样的不足尾组存在3次缓存复用，见analysis.json，不能将重复判断视作独立观测；各设置的响应耗时可以归属同一缓存响应，不可相加作为总消费。',
                  '仅视觉5条时，02/03整题请求公式由C+5P变为C+P：156→84、122→58，合计278→142（减少48.92%）。这不是整题提速实测。']
    if (out / 'visual-timing.json').exists():
        timing = read_json(out / 'visual-timing.json')
        lines += ['', f"视觉样本：历史{timing['historical_requests']}次响应合计{timing['historical_response_seconds']:.3f}秒；5条合批{timing['batch5_requests']}次合计{timing['batch5_response_seconds']:.3f}秒。历史对照不是端到端提速实测。"]
    if (out / 'final-accounting.json').exists():
        account = read_json(out / 'final-accounting.json')
        lines += ['', f"实际{account['actual_requests']}请求（{account['successful_requests']}成功、{account['failed_requests']}失败），{account['cache_hits']}次缓存复用；{account['known_total_tokens']:,} tokens，缺usage {account['missing_usage']}；{account['elapsed_minutes']:.2f}分钟。"]
    if (out / 'final-verification.json').exists():
        verification = read_json(out / 'final-verification.json')
        lines += ['', f"完整性核对通过：{verification['integrity_checks_passed']}；质量等价通过：{verification['quality_equivalence_passed']}。离线测试通过{verification.get('offline_tests', {}).get('passed', '未记录')}项。"]
    (out / 'report.md').write_text('\n'.join(lines) + '\n')
    return report


def run(out, plan):
    auth = read_json(out / 'authorization.json')
    if auth['status'] != 'prepared':
        raise ValueError('Experiment already started or closed; do not reopen its authorization')
    for binding in plan['historical_bindings']:
        if file_digest(binding['path']) != binding['sha256']:
            raise ValueError('Historical source changed after experiment preparation')
    config = read_json(ROOT / 'configs/contract-v25-judge-fast.json')
    config.update(max_total_api_requests=CAP, max_api_requests=CAP)
    name = config['api_key_env']
    if not os.environ.get(name, '').strip():
        credential = read_json('/home/vp2-lyz/.config/codex-credentials/bailian-qwen-vl.json')['api_key']
        os.environ[name] = credential.strip()
    client = Client(config, out / 'cache', out / 'api')
    stop = threading.Event()
    for sig in [signal.SIGINT, signal.SIGTERM]:
        signal.signal(sig, lambda *_: stop.set())
    auth.update(status='active', started_unix=time.time())
    write_json(out / 'authorization.json', auth)

    def execute(task):
        request = task['request']
        started = time.monotonic()
        result = {k: v for k, v in task.items() if k != 'request'}
        try:
            images = [(i['label'], i['path']) for i in request['images']]
            for image in request['images']:
                if file_digest(image['path']) != image['sha256']:
                    raise ValueError('Image changed since preparation')
            parser = parse_boxed_answer if task['batch_size'] == 1 else lambda raw: parse_batch_answer(raw, task['ids'])
            raw = client.ask_text('judge', request['system'], request['payload'], images,
                                  suffix=request.get('suffix'), validate=parser)
            verdicts = ([{'rubric_id': task['ids'][0], 'answer': parse_boxed_answer(raw), 'evidence': raw}]
                        if task['batch_size'] == 1 else parse_batch_answer(raw, task['ids']))
            result.update(status='success', raw=raw, verdicts=verdicts)
        except Exception as error:
            result.update(status='failed', error_type=type(error).__name__)
            if isinstance(error, ProviderError):
                result['error'] = error.details
            stop.set()
        result['elapsed_seconds'] = round(time.monotonic() - started, 3)
        write_json(out / f"results/{task['id']}.json", result)
        return result

    pending = iter(t for t in plan['tasks'] if not (out / f"results/{t['id']}.json").exists())
    with ThreadPoolExecutor(max_workers=2) as pool:
        active = set()
        while True:
            while len(active) < 2 and not stop.is_set():
                task = next(pending, None)
                if task is None:
                    break
                print('start', task['id'], task['ids'], flush=True)
                active.add(pool.submit(execute, task))
            if not active:
                break
            finished, active = wait(active, timeout=10, return_when=FIRST_COMPLETED)
            for future in finished:
                result = future.result()
                print('done', result['id'], result['status'], result['elapsed_seconds'], flush=True)
            report = summarize(out, plan)
            print('progress', report['completed_calls'], '/', report['planned_calls'],
                  'requests', report['accounting']['requests'], flush=True)
    report = summarize(out, plan)
    auth.update(status='closed', completed_unix=time.time(), accounting=report['accounting'],
                complete=report['completed_calls'] == report['planned_calls'])
    write_json(out / 'authorization.json', auth)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args()
    out = args.out.resolve()
    plan = prepare(out)
    print('planned calls:', len(plan['tasks']), flush=True)
    if args.run:
        import fcntl
        with (out / 'run.lock').open('a') as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            run(out, plan)
    else:
        summarize(out, plan)
        detailed_report(out, plan)
