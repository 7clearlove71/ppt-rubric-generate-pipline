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
    payload = {'document_id': doc, 'pages': [{'page': 1, 'page_id': doc + '/page/1'}], 'criteria': criteria}
    validate = partial(validate_visual_batch, items=items, pages=[1])
    result = client.ask('judge', VISUAL_PROMPT, payload, images, validate=validate)
    validate(result)
    return result


def run_checks(dataset, primary, verifier, output):
    records = []
    for sample in dataset['samples']:
        row = {'id': sample['id'], 'image_sha256': sample['image']['sha256'],
               'expected_relation': sample['ground_truth'], 'assessors': {}}
        # Each provider still has its single shared limiter; no parallel same-provider processes.
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = {name: pool.submit(visual_probe, sample, client)
                    for name, client in [('primary', primary), ('verification', verifier)] if client}
            for name, job in jobs.items():
                try:
                    result = job.result()
                    verdicts = {v['rubric_id']: v for v in result['pages'][0]['verdicts']}
                    expected = 'no' if sample['ground_truth'] == 'overlap' else 'yes'
                    row['assessors'][name] = {'status': 'complete', 'response': result,
                        'readability_matches_expected': verdicts['V_READ']['answer'] == expected,
                        'layout_answer': verdicts['V_LAYOUT']['answer']}
                except Exception as exc:
                    row['assessors'][name] = {'status': 'unavailable', 'error_type': type(exc).__name__}
        records.append(row)
        write_json(Path(output) / 'results.json', {'records': records,
            'scope': 'Four selected development locations; Codex image observations, not fresh human reannotation.',
            'policy': 'Frozen labels withheld; expected relation comparison does not validate every rationale or the whole deck.'})
        print(f'visual regression {sample["id"]}: ' + str({k: v.get('readability_matches_expected') for k, v in row['assessors'].items()}), flush=True)
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
