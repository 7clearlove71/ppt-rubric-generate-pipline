"""Synthetic reviewer checks with known defects; never human preference labels."""
from __future__ import annotations

import argparse
import copy
from pathlib import Path

from . import prompts
from .common import load_config, read_json, write_json, digest
from .pipeline import audit_review
from .provider import Client
from .rubric import validate_requirements, validate_rubric


def run_checks(fixtures, config, client, output):
    validate_requirements(fixtures['requirements'], fixtures['sources'])
    results = []
    for test in fixtures['tests']:
        rubric = copy.deepcopy(fixtures['rubric'])
        by_id = {i['id']:i for i in rubric['items']}
        for change in test['changes']:
            by_id[change['item_id']]['judge_prompt'] = change['judge_prompt']
        structural = validate_rubric(rubric, fixtures['requirements'])
        record = {'id':test['id'], 'expected_approved':test['expected_approved'], 'structural_errors':structural}
        if structural:
            record.update(approved=False, detected_target=True, reviewer_called=False)
        else:
            try:
                payload = {key:fixtures[key] for key in ['sources','requirements']}
                payload.update(rubric=rubric, dimension_weights=config['dimension_weights'])
                # Expected answer, case name, and seeded-defect rationale are
                # deliberately withheld from the reviewer.
                review = client.ask('reviewer',prompts.REVIEW,payload,
                                    validate=lambda r:audit_review(r,fixtures['requirements'],rubric))
                if review['approved']:
                    counterexample = client.ask('reviewer',prompts.COUNTEREXAMPLE_REVIEW,payload,
                                                validate=lambda r:audit_review(r,{'requirements':[]},rubric))
                    record['counterexample_review'] = counterexample
                    if not counterexample['approved']:
                        review = {**review,'approved':False,'issues':review['issues']+counterexample['issues']}
                implicated = {i for issue in review['issues'] if issue['severity']=='error' for i in issue['item_ids']}
                record.update(approved=review['approved'],review=review,reviewer_called=True,
                              detected_target=bool(implicated & set(test.get('target_items',[]))) if not test['expected_approved'] else True)
            except Exception as e:
                record.update(approved=None,reviewer_called=True,error_type=type(e).__name__)
        record['passed'] = record['approved']==test['expected_approved'] and record.get('detected_target',False)
        results.append(record)
        write_json(Path(output)/'results.json',results)
        print(f"{test['id']}: {'pass' if record['passed'] else 'FAIL'}",flush=True)
    summary = {'total':len(results),'passed':sum(r['passed'] for r in results),
               'false_accepts':sum(r['approved'] is True and not r['expected_approved'] for r in results),
               'false_rejects':sum(r['approved'] is False and r['expected_approved'] for r in results),
               'errors':sum(r['approved'] is None for r in results),
               'prompt_version':prompts.VERSION,'fixture_hash':digest(fixtures),
               'fixture_version':fixtures.get('fixture_version','1'),
               'reviewer_model':config.get('reviewer_model'),'results':results,
               'scope':'Synthetic known-defect detection only; not human preference agreement.'}
    write_json(Path(output)/'summary.json',summary)
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixtures',default='fixtures/review_regressions_v2.json')
    parser.add_argument('--config',default='configs/default.json')
    parser.add_argument('--out',default='artifacts/reviewer-regression')
    parser.add_argument('--cache',default='.cache')
    parser.add_argument('--max-api-requests',type=int,default=12)
    parser.add_argument('--confirm-bailian-bulk',action='store_true')
    args=parser.parse_args()
    config=load_config(args.config)
    if config.get('keychain_service')=='codex.alibaba-bailian.api-key' and not args.confirm_bailian_bulk:
        parser.error('Reviewer regression is a batch task; Bailian requires explicit user confirmation')
    if args.max_api_requests<1:
        parser.error('max-api-requests must be positive')
    config['max_api_requests']=args.max_api_requests
    client=Client(config,args.cache,journal_dir=args.out)
    summary=run_checks(read_json(args.fixtures),config,client,args.out)
    if summary['passed'] != summary['total']:
        raise SystemExit(1)


if __name__=='__main__':
    main()
