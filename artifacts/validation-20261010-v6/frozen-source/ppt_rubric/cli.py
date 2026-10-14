from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .common import load_config, read_json, write_json
from .data import export_human_review, ingest, select_cases, validate_manifest
from .evaluation import agreement
from .human_labels import import_markdown_labels
from .pipeline import evaluate, generate, review_and_repair, prompt_fingerprint
from .provider import Client
from .render import prepare_case, case_sources
from .rubric import VALIDATOR_VERSION, validate_requirements
from . import prompts
from .common import digest
from .operations import plan_run, run_report, export_suites


def main():
    parser = argparse.ArgumentParser(description='One automatically audited PPT rubric per task')
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('ingest')
    p.add_argument('--source', required=True)
    p.add_argument('--out', default='data/manifest.json')
    p.add_argument('--seed', type=int, default=20260930)
    for name in ['prepare', 'human-review', 'generate', 'audit', 'judge', 'plan', 'export']:
        p = sub.add_parser(name)
        p.add_argument('--manifest', default='data/manifest.json')
        p.add_argument('--config', default='configs/default.json')
        p.add_argument('--cache', default='.cache')
        p.add_argument('--out', default={'prepare':'artifacts/prepared.json', 'human-review':'artifacts/human-review',
                                        'generate':'artifacts/rubrics', 'audit':'artifacts/audited-rubrics', 'judge':'artifacts/judgments',
                                        'plan':'artifacts/plan.json', 'export':'artifacts/rubrics.jsonl'}[name])
        if name in {'generate', 'audit', 'judge', 'plan', 'export'}:
            p.add_argument('--split', choices=['dev', 'holdout', 'all'], default='dev')
            p.add_argument('--cases', help='Comma-separated case ids')
            p.add_argument('--allow-holdout', action='store_true')
            p.add_argument('--workers', type=int, default=1)
            p.add_argument('--max-api-requests', type=int, help='Optional lower per-run request budget including retries')
            p.add_argument('--confirm-bailian-bulk', action='store_true',
                           help='Use only after explicit user approval for this bulk Bailian run')
        if name in {'audit','judge','plan','export'}:
            p.add_argument('--rubrics', default='artifacts/rubrics')
        if name == 'plan':
            p.add_argument('--operation', choices=['generate','audit','judge'], default='generate')
        if name == 'export':
            p.add_argument('--skip-unapproved',action='store_true',help='Export approved subset, with explicit omission report')
        if name == 'judge':
            p.add_argument('--limit', type=int, help='Smoke test only; partial runs never receive a final score')
    p = sub.add_parser('import-labels')
    p.add_argument('--manifest', default='data/manifest.json')
    p.add_argument('--source', required=True)
    p.add_argument('--split', choices=['dev','holdout','all'], default='dev')
    p.add_argument('--allow-holdout', action='store_true')
    p.add_argument('--out', default='artifacts/human-labels-dev.json')
    p = sub.add_parser('compare')
    p.add_argument('--manifest', default='data/manifest.json')
    p.add_argument('--labels', required=True)
    p.add_argument('--judgments', default='artifacts/judgments')
    p.add_argument('--config', default='configs/default.json')
    p.add_argument('--split', choices=['dev','holdout','all'], default='dev')
    p.add_argument('--out', default='artifacts/agreement.json')
    p = sub.add_parser('report')
    p.add_argument('--run', required=True)
    p.add_argument('--out', default='artifacts/operational-report.json')
    args = parser.parse_args()
    if args.command == 'report':
        result = run_report(args.run)
        write_json(args.out, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == 'ingest':
        m = ingest(args.source, args.out, args.seed)
        print(json.dumps({'manifest_id':m['id'], 'cases':len(m['cases'])}))
        return
    m = read_json(args.manifest)
    validate_manifest(m)
    if args.command == 'import-labels':
        result = import_markdown_labels(m, args.source, args.split, args.allow_holdout)
        write_json(args.out, result)
        print(json.dumps({'imported':len(result['labels']), 'decided':result['decided'],
                          'undecided':result['undecided'], 'deferred_cases':result['deferred_cases'],
                          'out':args.out}, ensure_ascii=False))
        return
    c = load_config(args.config)
    if args.command == 'compare':
        result = agreement(m, read_json(args.labels), args.judgments, c['tie_margin'], args.split)
        write_json(args.out, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command in {'prepare','human-review'}:
        prepared = {}
        for case in m['cases']:
            decks = prepare_case(case, c, args.cache)
            prepared.update({(case['id'], cid): d for cid, d in decks.items()})
            print(f"prepared {case['id']}", flush=True)
        if args.command == 'human-review':
            print(export_human_review(m, prepared, args.out))
        else:
            write_json(args.out, {f'{case}/{cid}': d for (case,cid),d in prepared.items()})
        return
    if args.workers < 1 or (args.command == 'judge' and args.limit is not None and args.limit < 1):
        parser.error('workers/limit must be positive')
    cases = select_cases(m, args.split, args.cases, args.allow_holdout)
    if args.command == 'export':
        print(json.dumps(export_suites(cases,c,args.rubrics,args.out,args.skip_unapproved),ensure_ascii=False,indent=2))
        return
    if args.command == 'plan':
        result = plan_run(cases,c,args.operation,args.rubrics)
        write_json(args.out,result)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return
    if c.get('keychain_service') == 'codex.alibaba-bailian.api-key':
        if len(cases) > 1 and not args.confirm_bailian_bulk:
            parser.error('Bulk Bailian calls require explicit user approval; do not split runs to evade this requirement')
        if args.confirm_bailian_bulk:
            c.pop('max_api_requests', None)
        else:
            c['max_api_requests'] = min(c.get('max_api_requests', 12), 12)
    if args.max_api_requests is not None:
        if args.max_api_requests < 1:
            parser.error('max-api-requests must be positive')
        c['max_api_requests'] = min(c.get('max_api_requests', args.max_api_requests), args.max_api_requests)
    client = Client(c, args.cache, journal_dir=args.out)
    summaries = {}
    def work(case):
        if args.command == 'generate':
            r = generate(case, c, client, args.cache, args.out)
            return {'status': r['status'], 'items': len(r.get('items', []))}
        if args.command == 'audit':
            directory = Path(args.rubrics) / case['id']
            original = read_json(directory / 'rubric.json')
            requirements = read_json(directory / 'requirements.json')
            sources = case_sources(case, c)
            validate_requirements(requirements, sources)
            if original['provenance']['source_hash'] != digest(sources):
                raise ValueError('Cannot audit rubric against different sources')
            context = {'sources':sources, 'requirements':requirements, 'dimension_weights':c['dimension_weights']}
            meta = {**original['provenance'], 'prompt_version':prompts.VERSION, 'validator_version':VALIDATOR_VERSION,
                    'prompt_fingerprint':prompt_fingerprint(),
                    'config_hash':digest({k:v for k,v in c.items() if k!='max_api_requests'}),
                    'audit_origin':original['rubric_hash']}
            draft = {'groups':[{k:v for k,v in g.items() if k!='score'} for g in original['groups']],
                     'items':[{k:v for k,v in i.items() if k not in {'score','dimension'}} for i in original['items']]}
            destination = Path(args.out) / case['id']
            write_json(destination / 'requirements.json', requirements)
            r = review_and_repair(case,c,client,destination,context,draft,meta)
            return {'status':r['status'], 'items':len(r.get('items',[]))}
        r = evaluate(case, read_json(Path(args.rubrics) / case['id'] / 'rubric.json'), c, client, args.cache, args.out, args.limit)
        return {cid: report['summary'] for cid, report in r.items()}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(work, case): case for case in cases}
        for future in as_completed(futures):
            case = futures[future]
            try:
                summaries[case['id']] = future.result()
            except Exception as e:
                # Exception messages in these modules never contain authentication headers.
                summaries[case['id']] = {'status':'error', 'error_type':type(e).__name__, 'message':str(e)}
                write_json(Path(args.out) / case['id'] / 'status.json', summaries[case['id']])
            print(json.dumps({case['id']: summaries[case['id']]}, ensure_ascii=False), flush=True)
            write_json(Path(args.out) / 'run-summary.json', summaries)
    write_json(Path(args.out) / 'usage.json', client.calls)
    report = run_report(args.out)
    write_json(Path(args.out) / 'report.json', report)
    if any(s.get('status') in {'error','quality_failed','render_review_failed'} for s in summaries.values()):
        raise SystemExit(1)
    if report['status_counts'].get('incomplete'):
        raise SystemExit(2)


if __name__ == '__main__':
    main()
