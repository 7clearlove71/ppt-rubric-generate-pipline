#!/usr/bin/env python3
"""Verify the published historical snapshot without model calls."""
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
inventory = json.loads((root / 'docs/migration-inventory.json').read_text())
failures = []
for item in inventory['files']:
    path = root / item['path']
    if not path.is_file():
        failures.append(item['path'])
        continue
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    if path.stat().st_size != item['bytes'] or h.hexdigest() != item['sha256']:
        failures.append(item['path'])
print(json.dumps({'checked': len(inventory['files']), 'mismatches': failures}, ensure_ascii=False))
raise SystemExit(bool(failures))
