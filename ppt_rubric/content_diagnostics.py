"""Same-model text diagnostic ablation; expected outcomes stay outside requests.

This is a development mechanism check, not whole-deck or human agreement
evaluation. Images and text-only results must never be pooled as one experiment.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from .common import digest, read_json, write_json
from .content_evidence import assess_content
from .contract_judge import CONTENT_PROMPT, _criteria, _request, validate_verdicts
from .provider import Client
from .source_catalog import source_catalog


def run_diagnostics(dataset, client, config, output):
    """Compare identical source/criterion/text under the two fixed methods."""
    from functools import partial
    output = Path(output)
    results = []
    input_hash = digest(dataset)
    for sample in dataset['samples']:
        # Never forward sample IDs, mechanism labels, comparison rules or the
        # alternate method's judgments to either request.
        catalog = source_catalog([{'id': 'prompt', 'text': sample['source_text']}])
        item = sample['criterion']
        if item.get('evidence_mode') != 'text':
            raise ValueError('Text diagnostics require an explicitly text-only criterion')
        doc = 'doc_' + digest({'pages': sample['pages'], 'source': sample['source_text']})[:24]
        pages = [{'page': p['page'], 'page_id': f"{doc}/page/{p['page']}",
                  'extracted_text': p['text'], 'rendered_text': p['text']} for p in sample['pages']]
        payload = {'document_id': doc, 'source_catalog': catalog, 'pages': pages,
                   'criteria': _criteria([item]), 'evidence_channels': ['text']}
        validate = partial(validate_verdicts, items=[item], allowed_pages={p['page'] for p in pages}, catalog=catalog)
        baseline = _request(client, CONTENT_PROMPT, payload, [], validate)
        deck = {'slides': [{'page': p['page'], 'text': p['text']} for p in sample['pages']]}
        evidence = assess_content(deck, [item], catalog, client,
            {**config, 'content_evidence_channel': 'text'}, doc, pages, _criteria, _request)
        row = {'id': sample['id'], 'mechanism': sample['mechanism'], 'expected': sample['expected'],
               'expectation_basis': sample['expectation_basis'], 'input_hash': digest(payload),
               'monolithic': baseline, 'evidence_tasks': evidence, 'rationale_review': 'pending'}
        for method in ('monolithic', 'evidence_tasks'):
            result = row[method]
            verdicts = result.get('raw', {}).get('verdicts', []) if method == 'monolithic' else result.get('verdicts', [])
            # Rejected raw responses remain inspectable evidence, not valid
            # answers. Partial extraction/task runs also cannot certify a
            # complete diagnostic comparison, even if a retained answer fits.
            answer = verdicts[0]['answer'] if result.get('status') == 'success' and len(verdicts) == 1 else None
            row[method + '_answer'] = answer
            row[method + '_matches'] = answer == sample['expected'] if answer is not None else None
        results.append(row)
        write_json(output / 'results.json', {'dataset_hash': input_hash, 'records': results,
            'scope': 'Authored development text mechanisms; no original-page visibility or human agreement claim.',
            'expected_labels_in_requests': False, 'same_model': config['judge_model'],
            'policy': 'Fixed criteria and text in both methods; method responses never shared across arms.'})
        print(f"diagnostic {sample['id']}: old={row['monolithic_answer']} evidence={row['evidence_tasks_answer']}", flush=True)
    summary = {'dataset_hash': input_hash, 'samples': len(results), 'paired_examples': True,
               'semantic_rationales_reviewed': False, 'production_acceptance': False,
               'human_agreement': None, 'holdout_run': False}
    for method in ('monolithic', 'evidence_tasks'):
        summary[method] = {'matches': sum(r[method + '_matches'] is True for r in results),
            'unknown': sum(r[method + '_answer'] == 'unknown' for r in results),
            'unavailable': sum(r[method + '_answer'] is None for r in results),
            'false_positive_normal': [r['id'] for r in results if r['expected'] == 'yes' and r[method + '_answer'] == 'no'],
            'missed_known_defects': [r['id'] for r in results if r['expected'] == 'no' and r[method + '_answer'] == 'yes']}
    write_json(output / 'summary.json', summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', required=True)
    parser.add_argument('--config', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--cache', default='.cache')
    parser.add_argument('--confirm-bailian-bulk', action='store_true',
                        help='Only records already obtained explicit authorization for this diagnostic batch')
    args = parser.parse_args(argv)
    config = read_json(args.config)
    from urllib.parse import urlparse
    if ('dashscope' in (urlparse(config['base_url']).hostname or '').split('.')
            and not args.confirm_bailian_bulk):
        parser.error('Explicit Bailian bulk authorization is required for the diagnostic batch')
    journal = Path(args.out) / 'api'
    # max_api_requests is an operation cap, so a restart subtracts this unique
    # journal rather than resetting the cap for the next invocation.
    prior = len(read_json(journal / 'requests.json')) if (journal / 'requests.json').exists() else 0
    if config.get('max_api_requests') is not None:
        config = {**config, 'max_api_requests': max(0, config['max_api_requests'] - prior)}
    client = Client(config, args.cache, journal_dir=journal)
    run_diagnostics(read_json(args.dataset), client, config, args.out)


if __name__ == '__main__':
    main()
