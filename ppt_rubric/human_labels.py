"""Import human Markdown separately from generation/judging; never infer a winner."""
from __future__ import annotations

import re
from pathlib import Path

from .common import file_digest
from .data import select_cases, validate_manifest

# The historical Markdown importer keeps its single-record contract. New
# multi-human collections have an explicit schema and separate output set.
from .human_review import import_review_bundle  # noqa: F401


def _field(text, name, multiline=False):
    # The template permits bold labels and either colon style.
    plain = text.replace('**', '')
    matches = list(re.finditer(r'^\s*-\s*' + re.escape(name) + r'\s*[:：][ \t]*(.*)$',
                               plain, re.MULTILINE))
    if len(matches) != 1 or not matches[0].group(1).strip():
        raise ValueError(f'Expected one nonempty human field: {name}')
    match = matches[0]
    if not multiline:
        return match.group(1).strip()
    rest = plain[match.start(1):]
    end = re.search(r'^\s*-\s*不确定的地方[^\n]*[:：]|^##\s', rest, re.MULTILINE)
    return rest[:end.start()].strip() if end else rest.strip()


def import_markdown_labels(manifest, source, split='dev', allow_holdout=False):
    validate_manifest(manifest)
    cases = select_cases(manifest, split=split, allow_holdout=allow_holdout)
    source = Path(source).resolve()
    labels = []
    for case in cases:
        candidates = case['candidates']
        if len(candidates) != 2:
            raise ValueError('Markdown pairwise labels require exactly two candidates')
        paths = sorted((source / case['id']).glob('评测人_*.md'))
        if len(paths) != 1:
            raise ValueError(f"Case {case['id']}: expected one reviewer file; adjudicate multiple reviews explicitly")
        identities = {Path(c['pptx']).parent.name: c['id'] for c in candidates}
        if len(identities) != 2:
            raise ValueError('Candidate directories do not uniquely identify the two models')
        for candidate in candidates:
            original = Path(candidate['pptx'])
            copy = source / case['id'] / original.parent.name / original.name
            if any(file_digest(p) != candidate['sha256'] for p in (original, copy)):
                raise ValueError(f"Case {case['id']}: reviewed PPTX differs from frozen candidate")
        path = paths[0]
        text = path.read_text(encoding='utf-8')
        mapping = {}
        for letter in 'AB':
            value = _field(text, f'候选 {letter} 实际模型')
            models = [name for name in identities
                      if re.fullmatch(re.escape(name) + r'(?:\s*[（(].*[）)])?', value)]
            if len(models) != 1:
                raise ValueError(f"Case {case['id']}: ambiguous candidate identity for {letter}")
            mapping[letter] = identities[models[0]]
        if len(set(mapping.values())) != 2:
            raise ValueError(f"Case {case['id']}: reviewer A and B refer to the same candidate")
        choice = _field(text, '你的选择')
        if choice in {'A 更好', 'B 更好'}:
            winner, status = mapping[choice[0]], 'decided'
        elif choice in {'持平', '基本持平'}:
            winner, status = 'tie', 'decided'
        elif choice == '暂时无法判断':
            winner, status = None, 'undecided'
        else:
            raise ValueError(f"Case {case['id']}: unrecognized or multiple selected human choices")
        labels.append({
            'case_id': case['id'], 'winner': winner, 'label_status': status,
            'reasons': _field(text, '主要理由', multiline=True),
            'reviewer': _field(text, '评测人及日期'),
            'original_choice': choice, 'reviewer_to_manifest': mapping,
            'reasons_candidate_ids': 'reviewer A/B; translate with reviewer_to_manifest',
            'candidate_hashes': {c['id']: c['sha256'] for c in candidates},
            'source_path': str(path), 'source_sha256': file_digest(path),
        })
    return {'schema_version': 1, 'manifest_id': manifest['id'], 'split': split,
            'labels': labels, 'decided': sum(r['label_status'] == 'decided' for r in labels),
            'undecided': [r['case_id'] for r in labels if r['label_status'] == 'undecided'],
            'deferred_cases': [c['id'] for c in manifest['cases'] if c not in cases],
            'policy': 'Explicit choices only; no winner inferred from reasons; labels are comparison-only.'}
