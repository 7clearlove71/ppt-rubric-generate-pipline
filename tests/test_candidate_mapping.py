"""Candidate identity exports across batches, failures, and reviewer ID changes."""
import copy
import csv
import json

import pytest

from ppt_rubric.candidate_mapping import candidate_identity, mapping_rows, write_candidate_mapping
from ppt_rubric.common import digest, read_json, write_json


def cases():
    return [{'id': case_id, 'prompt': 'Offline mapping task', 'attachments': [], 'split': 'dev',
             'candidates': [{'id': cid, 'pptx': f'/uploads/{case_id}/{name}/result.pptx',
                             'sha256': digest((case_id, cid))}
                            for cid, name in [('A', '提交甲,版本1'), ('B', '提交乙'), ('C', '提交丙')]]}
            for case_id in ['02', '03']]


def test_mapping_preserves_distinct_submissions_same_names_hashes_and_intervals(tmp_path):
    selected = cases()
    selected[0]['candidates'][1]['display_name'] = '用户指定名称|乙\n修订版'
    selected[0]['candidates'][2]['sha256'] = selected[0]['candidates'][0]['sha256']
    candidate = selected[0]['candidates'][1]
    identity = candidate_identity(selected[0], candidate,
                                  {'identity': {'sha256': candidate['sha256']}, 'slides': [1, 2, 3]})
    results = {'02': {'B': {'candidate_identity': identity, 'summary': {
        'status': 'incomplete', 'score': None, 'score_lower': 70, 'score_upper': 80, 'unresolved_weight': 10}}},
               '03': {'status': 'error'}}
    original = copy.deepcopy(results)
    rows = mapping_rows(selected, results, tmp_path / 'judgments')
    data = write_candidate_mapping(tmp_path, rows, 'manifest-id')
    assert results == original
    assert [r['candidate_key'] for r in rows] == ['02/A', '02/B', '02/C', '03/A', '03/B', '03/C']
    assert [r['score_status'] for r in rows] == ['missing', 'incomplete', 'missing', 'error', 'error', 'error']
    assert rows[1]['score'] is None and rows[1]['unresolved_weight'] == 10
    assert rows[1]['page_count'] == 3 and rows[1]['display_name'] == candidate['display_name']
    assert all(r['input_filename'] == 'result.pptx' for r in rows)
    assert len({r['input_pptx'] for r in rows}) == 6
    assert read_json(tmp_path / 'candidate-mapping.json') == data
    with (tmp_path / 'candidate-mapping.csv').open(encoding='utf-8-sig', newline='') as stream:
        exported = list(csv.DictReader(stream))
    assert len(exported) == 6 and exported[0]['display_name'] == '提交甲,版本1/result.pptx'
    assert all(r['manifest_id'] == 'manifest-id' for r in exported)
    assert exported[1]['display_name'] == candidate['display_name']
    assert exported[1]['score'] == '' and exported[1]['score_lower'] == '70'
    markdown = (tmp_path / 'candidate-mapping.md').read_text()
    assert '用户指定名称\\|乙<br>修订版' in markdown and '不代表名次' in markdown


def test_public_export_has_no_review_fields_even_when_old_rows_are_supplied(tmp_path):
    case = cases()[0]
    rows = mapping_rows([case])
    assert all('human_candidate_ids' not in r for r in rows)
    rows[0].update(human_candidate_ids=[{'reviewer': 'private-reviewer', 'human_candidate_id': 'B'}],
                   reviewer_to_manifest={'A': 'B'}, human_score=99)
    data = write_candidate_mapping(tmp_path, rows)
    assert data['schema_version'] == 2
    for ext in ['json', 'csv', 'md']:
        content = (tmp_path / f'candidate-mapping.{ext}').read_text(encoding='utf-8-sig')
        assert 'human_' not in content and 'private-reviewer' not in content and '人工编号' not in content
    assert set(data['candidates'][0]) == set(mapping_rows([case])[0])


def test_shared_exclusions_and_reduced_maximum_are_visible_in_all_formats(tmp_path):
    from ppt_rubric.v25.comparison_scoring import COMPARISON_POLICY
    case = cases()[0]
    comparison = {'policy': COMPARISON_POLICY, 'score_possible': 90, 'excluded_weight': 10,
                  'excluded_rubric_ids': ['R_Q01', 'V_LAYOUT']}
    reports = {c['id']: {'summary': {'status': 'complete', 'score': 80, 'score_lower': 80,
                                   'score_upper': 80, 'unresolved_weight': 0},
                        'comparison': comparison} for c in case['candidates']}
    rows = mapping_rows([case], {case['id']: reports})
    data = write_candidate_mapping(tmp_path, rows)
    assert all(row['score_possible'] == 90 and row['excluded_weight'] == 10 for row in data['candidates'])
    assert all(row['excluded_rubric_ids'] == ['R_Q01', 'V_LAYOUT'] for row in data['candidates'])
    with (tmp_path / 'candidate-mapping.csv').open(encoding='utf-8-sig', newline='') as stream:
        exported = list(csv.DictReader(stream))
    assert all(row['score_possible'] == '90' and row['excluded_weight'] == '10' for row in exported)
    assert all(json.loads(row['excluded_rubric_ids']) == ['R_Q01', 'V_LAYOUT'] for row in exported)
    markdown = (tmp_path / 'candidate-mapping.md').read_text()
    assert '比较满分' in markdown and '共同剔除条目' in markdown and 'V_LAYOUT' in markdown


@pytest.mark.parametrize('change', ['id', 'hash', 'metadata'])
def test_mapping_rejects_reports_bound_to_another_submission(change):
    case = cases()[0]
    candidate = case['candidates'][0]
    report = {'case_id': '02', 'candidate_id': 'A', 'candidate_hash': candidate['sha256'],
              'candidate_identity': candidate_identity(case, candidate)}
    if change == 'id':
        report['candidate_id'] = 'B'
    elif change == 'hash':
        report['candidate_hash'] = 'another-file'
    else:
        report['candidate_identity']['input_pptx'] = '/wrong/result.pptx'
    with pytest.raises(ValueError, match='differs from manifest'):
        mapping_rows([case], {'02': {'A': report}})


def test_batch_judge_emits_global_mapping_even_when_one_case_fails(tmp_path, monkeypatch):
    from ppt_rubric.v25 import contract_v2 as contract
    from ppt_rubric.v25 import contract_judge as judge
    selected = cases()
    manifest = {'cases': selected}
    manifest['id'] = digest(manifest)
    write_json(tmp_path / 'manifest.json', manifest)
    for case in selected:
        write_json(tmp_path / 'rubrics' / case['id'] / 'rubric.json', {
            'pipeline_mode': 'light', 'source_audit': {'status': 'skipped'}})
    clients = []
    monkeypatch.setattr(contract, 'Client', lambda *a, **kw: clients.append(object()) or clients[-1])
    monkeypatch.setattr(contract, 'verify_suite', lambda *a: None)
    def prepare(case, *args):
        if case['id'] == '03':
            raise ValueError('Offline render failure')
        return {c['id']: {'identity': {'sha256': c['sha256']}, 'slides': [1, 2]}
                for c in case['candidates']}
    monkeypatch.setattr(contract, 'prepare_case', prepare)
    monkeypatch.setattr(judge, 'evaluate_contract', lambda case, *a: {
        c['id']: {'summary': {'status': 'complete', 'score': 90, 'score_lower': 90,
                              'score_upper': 90, 'unresolved_weight': 0},
                  'comparison': {'score_possible': 90, 'excluded_weight': 10,
                                 'excluded_rubric_ids': ['R_Q01'], 'policy': {'version': 'test-policy'}}}
        for c in case['candidates']})
    config = read_json('configs/contract-v25-judge.json')
    config['base_url'] = 'https://offline.invalid/v1'
    config.pop('keychain_service', None)
    write_json(tmp_path / 'config.json', config)
    output = tmp_path / 'judgments'
    with pytest.raises(SystemExit) as error:
        contract.main(['judge', '--manifest', str(tmp_path / 'manifest.json'),
                       '--config', str(tmp_path / 'config.json'), '--rubrics', str(tmp_path / 'rubrics'),
                       '--workers', '2', '--out', str(output)])
    assert error.value.code == 1 and len(clients) == 1
    mapping = read_json(output / 'candidate-mapping.json')
    assert mapping['manifest_id'] == manifest['id']
    assert len(mapping['candidates']) == 6
    assert [r['score_status'] for r in mapping['candidates']] == ['complete'] * 3 + ['error'] * 3
    assert all(r['score_possible'] == 90 and r['excluded_rubric_ids'] == ['R_Q01']
               for r in mapping['candidates'][:3])
    assert all(r['score_possible'] is None for r in mapping['candidates'][3:])
    result = read_json(output / 'run-summary.json')
    assert result['02']['A']['candidate_identity']['candidate_key'] == '02/A'
    assert result['02']['A']['candidate_identity']['page_count'] == 2
    assert result['02']['A']['comparison']['score_possible'] == 90
