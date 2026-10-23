"""Fixed visual component checks; expected labels are never in model inputs."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path

from .common import digest, file_digest, read_json, write_json
from .contract_judge import VISUAL_PROMPT, validate_visual_batch
from .contract_v2 import VISUAL_ITEMS, compile_item, validate_config
from .provider import Client


def expected_visual_answers(sample):
    """Labels stay offline; an axis failure is not an overlap/readability failure.

    Existing overlap probes keep their frozen label semantics. New diagnostics
    must explicitly specify the relevant criterion; no unrecognised label is
    silently interpreted as a passing example.
    """
    expected = sample.get('expected_verdicts')
    if expected is None:
        relation = sample.get('ground_truth')
        if relation not in {'overlap', 'separate', 'no_overlap', 'normal'}:
            raise ValueError('Visual regression needs explicit expected_verdicts for this diagnostic')
        expected = {'V_READ': 'no' if relation == 'overlap' else 'yes'}
    valid = {item['id'] for item in VISUAL_ITEMS}
    if (not isinstance(expected, dict) or not expected or not set(expected) <= valid
            or any(answer not in {'yes', 'no', 'unknown'} for answer in expected.values())):
        raise ValueError('Expected visual answers must name real criteria and yes/no/unknown')
    return dict(expected)


def visual_probe(sample, client):
    """Only neutral identity, exact images and the frozen rubric cross the API boundary."""
    image = sample['image_path']
    if file_digest(image) != sample['image']['sha256']:
        raise ValueError('Regression image differs from frozen ground truth')
    doc = 'doc_' + digest({'image_sha256': sample['image']['sha256']})[:24]
    items = [{**i, 'judge_prompt': compile_item(i)} for i in VISUAL_ITEMS]
    criteria = [{key: item[key] for key in ('id', 'judge_prompt', 'scope', 'scope_type', 'evidence_mode')}
                for item in items]
    # Cropped examples include their original page, so scope/materiality is visible.
    original = sample['original_page']['path']
    if file_digest(original) != sample['original_page']['sha256']:
        raise ValueError('Regression original page changed')
    images = [(doc + '/page/1', original)]
    if original != image:
        images.append((doc + '/page/1/local-view', image))
    require_diagnostics = getattr(client, 'config', {}).get('visual_diagnostics', False)
    payload = {'document_id': doc, 'pages': [{'page': 1, 'page_id': doc + '/page/1'}], 'criteria': criteria,
               'require_diagnostics': require_diagnostics,
               'inspection_domains': ['page_edge', 'footer_last_line', 'overlap', 'axis_encoding'],
               'image_supply': {'original_page': True, 'local_view': original != image,
                                'automatic_verdict': False}}
    validate = partial(validate_visual_batch, items=items, pages=[1],
                       require_diagnostics=require_diagnostics)
    result = client.ask('judge', VISUAL_PROMPT, payload, images, validate=validate)
    validate(result)
    return result


def run_checks(dataset, primary, verifier, output):
    records = []
    for sample in dataset['samples']:
        expected = expected_visual_answers(sample)
        row = {'id': sample['id'], 'image_sha256': sample['image']['sha256'],
               'original_page_sha256': sample['original_page']['sha256'],
               'expected_relation': sample.get('ground_truth'), 'expected_verdicts': expected,
               'diagnostic_category': sample.get('diagnostic_category'),
               'assessors': {}}
        # Each provider still has its single shared limiter; no parallel same-provider processes.
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = {name: pool.submit(visual_probe, sample, client)
                    for name, client in [('primary', primary), ('verification', verifier)] if client}
            for name, job in jobs.items():
                try:
                    result = job.result()
                    verdicts = {v['rubric_id']: v for v in result['pages'][0]['verdicts']}
                    matches = {key: verdicts[key]['answer'] == answer for key, answer in expected.items()}
                    row['assessors'][name] = {'status': 'complete', 'response': result,
                        'criterion_matches_expected': matches,
                        'all_expected_answers_match': all(matches.values()),
                        'readability_matches_expected': matches.get('V_READ'),
                        'layout_answer': verdicts['V_LAYOUT']['answer'],
                        'rationale_review_status': 'pending_object_region_and_ownership_review'}
                except Exception as exc:
                    row['assessors'][name] = {'status': 'unavailable', 'error_type': type(exc).__name__}
        records.append(row)
        write_json(Path(output) / 'results.json', {'records': records,
            'scope': dataset.get('scope', 'Selected development diagnostics; no inference to the complete deck or population.'),
            'annotation_provenance': dataset.get('annotation_provenance', 'Frozen diagnostic expectations; not new human reannotation.'),
            'policy': 'Frozen labels withheld; answer matching does not validate object attribution, lost information, severity, or defect ownership.'})
        print(f'visual regression {sample["id"]}: ' + str({k: v.get('criterion_matches_expected') for k, v in row['assessors'].items()}), flush=True)
    return records


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dataset', required=True)
    p.add_argument('--config', required=True)
    p.add_argument('--verification-config')
    p.add_argument('--out', required=True)
    p.add_argument('--cache', default='.cache')
    p.add_argument('--confirm-bailian-bulk', action='store_true')
    args = p.parse_args()
    c = read_json(args.config)
    v = read_json(args.verification_config) if args.verification_config else None
    for cfg in (c, v):
        if cfg:
            validate_config(cfg, 'judge')
            if cfg.get('keychain_service') == 'codex.alibaba-bailian.api-key' and not args.confirm_bailian_bulk:
                p.error('Explicit Bailian authorization is required for this regression')
    primary = Client(c, args.cache, journal_dir=Path(args.out) / 'primary-api')
    verifier = Client(v, args.cache, journal_dir=Path(args.out) / 'verification-api') if v else None
    run_checks(read_json(args.dataset), primary, verifier, args.out)


if __name__ == '__main__':
    main()
