"""User-facing candidate identities. Never include these labels in model inputs."""
from __future__ import annotations

import csv
import io
import json
import os
import tempfile
from pathlib import Path

from .common import write_json


IDENTITY_FIELDS = ('candidate_key', 'case_id', 'candidate_id', 'display_name',
                   'input_pptx', 'input_filename', 'input_directory', 'sha256')
MAPPING_FIELDS = (*IDENTITY_FIELDS, 'page_count', 'document_id', 'score_status', 'score',
                  'score_lower', 'score_upper', 'unresolved_weight', 'score_possible',
                  'excluded_weight', 'excluded_rubric_ids', 'comparison_policy', 'judgment_file')
MAPPING_FIELDS = (*MAPPING_FIELDS, 'evaluation_mode', 'unassessed_weight')


def candidate_identity(case, candidate, deck=None):
    """Use the frozen manifest's ID and file hash, not an inferred model name."""
    source = candidate.get('pptx')
    path = Path(source).absolute() if source else None
    name = candidate.get('display_name')
    if name is not None and (not isinstance(name, str) or not name.strip()):
        raise ValueError('Candidate display_name must be a nonempty string')
    if name is None:
        name = '/'.join(filter(None, (path.parent.name, path.name))) if path else candidate['id']
    identity = {'candidate_key': f'{case["id"]}/{candidate["id"]}',
                'case_id': case['id'], 'candidate_id': candidate['id'],
                'display_name': name, 'input_pptx': str(path) if path else None,
                'input_filename': path.name if path else None,
                'input_directory': str(path.parent) if path else None,
                'sha256': candidate['sha256'], 'page_count': None}
    if deck is not None:
        if deck.get('identity', {}).get('sha256') != candidate['sha256']:
            raise ValueError('Candidate mapping deck hash differs from manifest')
        identity['page_count'] = len(deck['slides'])
    return identity


def mapping_rows(cases, results=None, output=None):
    """Accept full judge reports or CLI summaries, preserving manifest order."""
    results = results or {}
    rows = []
    for case in cases:
        case_result = results.get(case['id'], {})
        for candidate in case['candidates']:
            identity = candidate_identity(case, candidate)
            report = case_result.get(candidate['id'], {})
            # Full reports bind identities independently of the display metadata.
            for field, expected in (('case_id', case['id']), ('candidate_id', candidate['id']),
                                    ('candidate_hash', candidate['sha256'])):
                if field in report and report[field] != expected:
                    raise ValueError('Candidate mapping report identity differs from manifest')
            recorded = report.get('candidate_identity')
            if recorded is not None:
                if (not isinstance(recorded, dict)
                        or any(recorded.get(k) != identity[k] for k in IDENTITY_FIELDS)):
                    raise ValueError('Candidate mapping metadata differs from manifest')
                identity['page_count'] = recorded.get('page_count')
            summary = report.get('summary', report)
            comparison = report.get('comparison') or {}
            coverage = report.get('evaluation_coverage') or comparison.get('evaluation_coverage') or {}
            status = summary.get('status') or ('error' if case_result.get('status') == 'error'
                                              else 'missing' if case['id'] in results else 'pending')
            rows.append({**identity, 'document_id': report.get('document_id'),
                         'score_status': status, 'score': summary.get('score'),
                         'score_lower': summary.get('score_lower'), 'score_upper': summary.get('score_upper'),
                         'unresolved_weight': summary.get('unresolved_weight'),
                         'score_possible': comparison.get('score_possible'),
                         'excluded_weight': comparison.get('excluded_weight'),
                         'excluded_rubric_ids': comparison.get('excluded_rubric_ids'),
                         'comparison_policy': comparison.get('policy', {}).get('version'),
                         'evaluation_mode': coverage.get('mode'),
                         'unassessed_weight': coverage.get('unassessed_weight'),
                         'judgment_file': str(Path(output).resolve() / case['id'] / f'{candidate["id"]}.json')
                             if output is not None else None})
    return rows


def _atomic_text(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.writing-', suffix=path.suffix)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8-sig' if path.suffix == '.csv' else 'utf-8', newline='') as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_candidate_mapping(output, rows, manifest_id=None):
    """Write a program-readable map, spreadsheet, and static human-readable table."""
    output = Path(output)
    # An allowlist keeps old development-only annotations out of public exports.
    rows = [{key: row.get(key) for key in MAPPING_FIELDS} for row in rows]
    data = {'schema_version': 2, 'manifest_id': manifest_id, 'candidates': rows,
            'files': {ext: str((output / f'candidate-mapping.{ext}').resolve()) for ext in ('json', 'csv', 'md')},
            'identity_rule': 'Join scores by candidate_key (case_id/candidate_id) within this manifest, and verify SHA-256.',
            'notes': ['Candidate IDs are local to the current case and manifest.',
                      'display_name is a user label or file path label, not a verified generator model.',
                      'Rows follow manifest order, not rank. Incomplete scores retain intervals.',
                      'Current comparison totals omit any criterion with a final unknown in the same-case cohort; '
                      'score_possible is the reduced maximum, without weight redistribution.',
                      'Only compare or rank candidates for the same task/rubric; no cross-case ranking is implied.',
                      'Compare matching evaluation modes only; content_only leaves visual weight 20 unassessed, '
                      'with original content maximum 80 and no normalization.',
                      'Missing or pending rows do not have scores.']}
    fields = ['candidate_key', 'display_name', 'input_pptx', 'sha256', 'page_count',
              'score_status', 'score', 'score_lower', 'score_upper', 'unresolved_weight',
              'score_possible', 'excluded_weight', 'excluded_rubric_ids', 'comparison_policy',
              'judgment_file', 'evaluation_mode', 'unassessed_weight']
    buffer = io.StringIO(newline='')
    writer = csv.DictWriter(buffer, fieldnames=['manifest_id', *fields])
    writer.writeheader()
    for row in rows:
        writer.writerow({'manifest_id': manifest_id, **{
            k: json.dumps(row[k], ensure_ascii=False) if isinstance(row.get(k), list) else row.get(k)
            for k in fields}})
    def cell(value):
        return ('' if value is None else str(value)).replace('|', '\\|').replace('\r', ' ').replace('\n', '<br>')
    headers = ['用例/候选ID', '名称（用户标签或来源目录/文件）', '输入PPTX', 'SHA-256', '页数',
               '评分状态', '比较分数', '分数下界', '分数上界', '未决权重',
               '比较满分', '剔除权重', '共同剔除条目', '比较计分版本', '评分结果文件', '评测范围', '未评权重']
    lines = ['# 候选身份映射', '',
             'manifest_id：' + cell(manifest_id), '',
             '按“用例ID/候选ID”关联分数和原始文件；A/B是本批次候选编号，名称来自输入目录或用户标签。', '',
             '表格按manifest顺序排列，不代表名次。未完整评分保留区间；跨用例分数不构成同题排序。', '',
             '当前比较分数对同题所有候选共同剔除最终unknown条目；其余原分值不变，以“比较满分”为分母。原始判断仍保留。', '',
             'content_only表示仅内容评测，视觉未评，原内容满分80；full为全维评测。两种范围不可直接混比或折算成相同百分制。', '',
             '| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join(['---'] * len(headers)) + ' |']
    for row in rows:
        values = [row.get(k) for k in fields]
        lines.append('| ' + ' | '.join(cell(v) for v in values) + ' |')
    write_json(output / 'candidate-mapping.json', data)
    _atomic_text(output / 'candidate-mapping.csv', buffer.getvalue())
    _atomic_text(output / 'candidate-mapping.md', '\n'.join(lines) + '\n')
    return data
