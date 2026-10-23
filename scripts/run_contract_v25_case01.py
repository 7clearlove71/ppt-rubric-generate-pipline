"""Fresh, bounded single-case run of contract-v2.5; no historical judgments as input."""
import copy
import argparse
import threading
import json
import os
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
from ppt_rubric.common import read_json, write_json, file_digest
from ppt_rubric.candidate_mapping import candidate_identity, mapping_rows, write_candidate_mapping
from ppt_rubric.provider import Client
from ppt_rubric.v25 import contract_v2 as contract
from ppt_rubric.v25.contract_judge import evaluate_contract, visual_item
from ppt_rubric.v25.render import case_sources
import fitz

PARSER = argparse.ArgumentParser(description='Fresh single-case run; shared provider budgets and isolated artifacts.')
PARSER.add_argument('--case', default='01', choices=['01', '02', '03'], help='One development case only')
PARSER.add_argument('--bailian-cap', type=int, default=180, help='Total operational ceiling across generation and scoring')
PARSER.add_argument('--out', required=True, help='New run directory; existing authorization is never reset')
PARSER.add_argument('--mode', choices=contract.PIPELINE_MODES, default='light',
                    help='light: 轻量，跳过审核；full: 全量，最多两轮修改后继续评分')
PARSER.add_argument('--profile', choices=['fast', 'standard'], default='fast')
RUN = None
CAPS = {'bailian': 180, 'maas': 90}


def configure_credential(config, fallback_file):
    """An explicit environment credential wins; never write credentials to disk."""
    name = config.get('api_key_env', 'PPT_RUBRIC_API_KEY')
    if os.environ.get(name, '').strip():
        return
    credential = read_json(fallback_file).get('api_key')
    if not isinstance(credential, str) or not credential.strip():
        raise ValueError('Credential file contains no nonempty API key')
    os.environ[name] = credential.strip()


def accounting():
    result = {}
    for provider in CAPS:
        path = RUN / f'{provider}-api/requests.json'
        rows = read_json(path) if path.exists() else []
        result[provider] = {
            'operational_cap': CAPS[provider], 'used': len(rows),
            'remaining': CAPS[provider] - len(rows),
            'success': sum(r['status'] == 'success' for r in rows),
            'failed': sum(r['status'] == 'failed' for r in rows),
            'started': sum(r['status'] == 'started' for r in rows),
            'known_total_tokens': sum(r.get('usage', {}).get('total_tokens', 0) for r in rows),
            'missing_usage_request_ids': [r['request_id'] for r in rows if not r.get('usage')],
            'unique_ledger': str(path.relative_to(RUN)),
        }
    write_json(RUN / 'shared-budget.json', result)
    return result


class RecordedClient(Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.record_lock = threading.Lock()

    def ask(self, role, system, payload, images=(), suffix=None, validate=None):
        return self.recorded('json', role, system, payload, images, suffix, validate)

    def ask_text(self, role, system, payload, images=(), suffix=None, validate=None):
        return self.recorded('text', role, system, payload, images, suffix, validate)

    def recorded(self, mode, role, system, payload, images=(), suffix=None, validate=None):
        images = list(images)
        with self.record_lock:
            number = len(list((RUN / f'{self.provider}-inputs').glob('[0-9][0-9][0-9].json'))) + 1
            path = RUN / f'{self.provider}-inputs/{number:03d}.json'
            write_json(path, {'role': role, 'model': self.config[role + '_model'],
                             'response_mode': mode, 'system': system, 'payload': payload,
                             'images': [{'label': label, 'path': str(image), 'sha256': file_digest(image)}
                                        for label, image in images], 'suffix': suffix})
        criterion = (payload.get('criterion', {}).get('id', payload.get('requirement', {}).get('id', '-'))
                     if isinstance(payload, dict) else '-')
        print(f'{self.provider} logical call {number}: {role}, criterion={criterion}', flush=True)
        try:
            method = super().ask_text if mode == 'text' else super().ask
            answer = method(role, system, payload, images, suffix, validate)
            write_json(path.with_name(path.stem + '-output.json'), answer)
            return answer
        finally:
            accounting()


def actual_call_plan(suite, decks):
    """Estimate only the single K3 scoring pass, using the published rubric."""
    visual = sum(visual_item(item) for item in suite['items'])
    content = len(suite['items']) - visual
    pages = {cid: len(deck['slides']) for cid, deck in decks.items()}
    calls = {cid: content + (count if visual else 0) for cid, count in pages.items()}
    return {'requirements': len(suite['requirements']['requirements']),
            'content_criteria': content, 'visual_criteria': visual,
            'candidate_pages': pages, 'scoring_method': 'single_k3',
            'judge_logical_calls_by_candidate': calls,
            'judge_logical_calls': sum(calls.values()),
            'pipeline_mode': suite.get('pipeline_mode'), 'source_audit': suite.get('source_audit'),
            'token_estimate': None}


def main(argv=None):
    global RUN
    args = PARSER.parse_args(argv)
    if args.bailian_cap < 1:
        raise ValueError('bailian-cap must be positive')
    CAPS['bailian'] = args.bailian_cap
    RUN = Path(args.out).resolve()
    marker = RUN / 'authorization.json'
    if marker.exists():
        raise RuntimeError('Run already started; use an explicit accounted recovery, never silently reset it')
    case = next(c for c in read_json(ROOT / 'data/manifest-local.json')['cases'] if c['id'] == args.case)
    for candidate in case['candidates']:
        assert file_digest(candidate['pptx']) == candidate['sha256']
    write_json(RUN / 'case.json', case)
    profiles = {}
    for name in (('k3', 'auditor', 'judge') if args.mode == 'full' else ('k3', 'judge')):
        profile_suffix = '-fast' if args.profile == 'fast' else ''
        config = read_json(ROOT / f'configs/contract-v25-{name}{profile_suffix}.json')
        provider = 'bailian' if name in {'k3', 'judge'} else 'maas'
        config['max_api_requests'] = CAPS[provider]
        if name == 'k3':
            config['pipeline_mode'] = args.mode
        contract.validate_config(config, 'judge' if name == 'judge' else 'generate')
        profiles[name] = config
        write_json(RUN / f'{name}-config.json', config)
    frozen = read_json(ROOT / 'artifacts/validation-20261019-contract-v24/frozen-dev-decks.json')
    decks = {c['id']: copy.deepcopy(frozen[f'{args.case}/{c["id"]}']) for c in case['candidates']}
    assets = {}
    for cid, deck in decks.items():
        assert deck['identity']['sha256'] == next(c['sha256'] for c in case['candidates'] if c['id'] == cid)
        assert len(deck['slides']) > 0
        with fitz.open(deck['pdf']) as pdf:
            assert len(pdf) == len(deck['slides'])
            for slide in deck['slides']:
                slide['rendered_text'] = pdf[slide['page'] - 1].get_text()
                assets[slide['image']] = file_digest(slide['image'])
        assets[deck['pdf']] = file_digest(deck['pdf'])
    write_json(RUN / 'prepared-decks.json', decks)
    write_candidate_mapping(RUN, mapping_rows([case], output=RUN / 'judgments'))
    write_json(RUN / 'render-inputs.json', {
        'mode': 'reuse_original_rendered_PDF_and_PNG; fresh_PyMuPDF_text_extraction',
        'historical_judgments_reused': False, 'pymupdf_version': fitz.VersionBind,
        'render_metadata_preserved': True, 'asset_sha256': assets})
    files = (sorted((ROOT / 'ppt_rubric').glob('*.py'))
             + sorted((ROOT / 'ppt_rubric/v25').glob('*.py'))
             + sorted((ROOT / 'scripts').glob('run_contract_v25_case01.py')))
    hashes = {}
    for path in files:
        rel = path.relative_to(ROOT)
        target = RUN / 'frozen-source' / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        hashes[str(rel)] = file_digest(path)
    write_json(RUN / 'source-freeze.json', {'sha256': hashes, 'pipeline_version': contract.VERSION,
                                          'offline_tests_executed_by_runner': False})
    write_json(marker, {'status': 'active', 'case_ids': [args.case],
        'user_request': f'Current explicit request to run case{args.case}; invoked via reusable runner', 'scheduling_profile': args.profile,
        'authorization_basis': 'Explicit current single-case run request and standing single-case trial allowance; no bulk or old balances.',
        'pipeline_mode': args.mode,
        'operational_caps_not_user_specified': CAPS, 'max_revisions': 2 if args.mode == 'full' else 0, 'max_attempts_per_logical_call': 3,
        'models': {'extraction_and_wording': 'kimi-k3', 'candidate_scoring': 'kimi-k3',
                   'independent_source_audit': 'kimi-k2.6' if args.mode == 'full' else None},
        'scoring_method': 'single_k3',
        'human_labels_in_inputs': False, 'holdout': False, 'started_unix': time.time(),
        'cost_estimate': None})
    status = {'status': 'initializing', 'pipeline_mode': args.mode, 'started_unix': time.time(), 'scoring_method': 'single_k3'}
    write_json(RUN / 'execution-status.json', status)
    mapping_results = {}
    try:
        configure_credential(profiles['k3'], '/home/vp2-lyz/.config/codex-credentials/bailian-qwen-vl.json')
        if args.mode == 'full':
            configure_credential(profiles['auditor'], '/home/vp2-lyz/.config/codex-credentials/modelarts-maas-glm.json')
        primary = RecordedClient(profiles['k3'], RUN / 'cache', RUN / 'bailian-api')
        primary.provider = 'bailian'
        auditor = None
        if args.mode == 'full':
            auditor = RecordedClient(profiles['auditor'], RUN / 'cache', RUN / 'maas-api')
            auditor.provider = 'maas'
        status['status'] = 'generating'
        write_json(RUN / 'execution-status.json', status)
        suite = contract.generate_contract(case, profiles['k3'], primary, RUN / 'rubrics', reviewer_client=auditor)
        status.update(generation_status=suite['status'], criterion_count=len(suite.get('items', [])),
                      source_audit=suite.get('source_audit'))
        if suite['status'] not in {'approved', 'ready'}:
            status['status'] = 'generation_failed'
            return 1
        contract.verify_suite(case, suite, profiles['judge'])
        write_json(RUN / 'actual-call-plan.json', actual_call_plan(suite, decks))
        primary.config = profiles['judge']
        # Generation has drained; update the same Client's transport capacity
        # while retaining its cumulative ledger, budget and circuit breakers.
        primary.semaphore = threading.BoundedSemaphore(profiles['judge']['api_concurrency'])
        primary.next_request_at = 0.0
        status['status'] = 'judging'
        write_json(RUN / 'execution-status.json', status)
        result = evaluate_contract(case, suite, profiles['judge'], primary, decks,
            case_sources(case, profiles['judge']), RUN / 'judgments')
        candidates = {c['id']: c for c in case['candidates']}
        for cid, report in result.items():
            report.setdefault('candidate_identity', candidate_identity(case, candidates[cid], decks[cid]))
        mapping_results[case['id']] = result
        execution_failed = any(r.get('execution', {}).get('status') == 'failed' for r in result.values())
        incomplete = any(r['summary']['status'] != 'complete'
                         or r.get('execution', {}).get('status', 'complete') != 'complete' for r in result.values())
        status.update(status='failed' if execution_failed else 'incomplete' if incomplete else 'finished', scoring_method='single_k3',
                      summaries={cid: r['summary'] for cid, r in result.items()},
                      comparisons={cid: r.get('comparison') for cid, r in result.items()})
    except Exception as error:
        status.update(status='failed', error_type=type(error).__name__, message=str(error))
        raise
    finally:
        if case['id'] not in mapping_results:
            mapping_results[case['id']] = {'status': 'error'}
        mapping = write_candidate_mapping(RUN, mapping_rows([case], mapping_results, RUN / 'judgments'))
        status['candidate_mapping_files'] = mapping['files']
        status.update(worker_completed=True, ended_unix=time.time(), accounting=accounting())
        write_json(RUN / 'execution-status.json', status)
        auth = read_json(marker)
        auth.update(status='closed', closed_unix=time.time(), final_status=status['status'], unused_capacity_rolls_over=False)
        write_json(marker, auth)
        print(json.dumps(status, ensure_ascii=False), flush=True)
    return 1 if status['status'] == 'failed' else 2 if status['status'] == 'incomplete' else 0


if __name__ == '__main__':
    raise SystemExit(main())
