#!/usr/bin/env python3
"""Create a relocated manifest without altering frozen historical records or calling APIs."""
import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ppt_rubric.common import digest, file_digest, read_json, write_json
from ppt_rubric.data import validate_manifest


def relocate(manifest, root):
    validate_manifest(manifest)
    result = copy.deepcopy(manifest)
    for case in result['cases']:
        for item, key in ([(c, 'pptx') for c in case['candidates']]
                          + [(a, 'path') for a in case.get('attachments', [])]):
            original = Path(item[key])
            # Locate the dataset suffix, without depending on the old username/OS.
            indexes = [i for i, part in enumerate(original.parts) if part == 'data']
            if not indexes:
                raise ValueError('Expected a dataset path below data/')
            relative = Path(*original.parts[indexes[-1]:])
            target = (root / relative).resolve()
            target.relative_to(root.resolve())
            if file_digest(target) != item['sha256']:
                raise ValueError(f'Input hash mismatch: {relative}')
            item[key] = str(target)
    result.pop('id')
    result['id'] = digest(result)
    validate_manifest(result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=ROOT / 'data/manifest.json')
    parser.add_argument('--out', type=Path, default=ROOT / 'data/manifest-local.json')
    args = parser.parse_args()
    if args.out.resolve() == args.manifest.resolve():
        parser.error('Historical manifest must not be overwritten')
    original = read_json(args.manifest)
    relocated = relocate(original, ROOT)
    if args.out.exists() and read_json(args.out) != relocated:
        parser.error('Output already contains a different manifest')
    write_json(args.out, relocated)
    print(json.dumps({'manifest': str(args.out), 'cases': len(relocated['cases']),
                      'historical_id': original['id'], 'local_id': relocated['id'],
                      'api_requests': 0}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
