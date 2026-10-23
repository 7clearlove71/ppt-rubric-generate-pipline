"""Exercise the documented directory -> manifest -> offline plan user workflow."""
import sys

import pytest

from ppt_rubric.common import file_digest, read_json
from ppt_rubric.data import ingest, validate_manifest
from ppt_rubric import cli
from ppt_rubric.v25 import contract_v2
from ppt_rubric.v25.render import case_sources


def make_inputs(source, counts=(2, 3)):
    for number, count in enumerate(counts, 1):
        task = source / f'task-{number:03d}'
        task.mkdir(parents=True)
        (task / 'prompt.txt').write_text('制作产业项目汇报，列出投资和下一步规划。', encoding='utf-8')
        attachments = task / 'attachments'
        attachments.mkdir()
        (attachments / 'facts.md').write_text('总投资280万元；第一年培训50人。', encoding='utf-8')
        for candidate in range(count):
            folder = task / f'proposal-{candidate + 1:03d}'
            folder.mkdir()
            # These are hash fixtures, not renderable slides; no API/rendering is performed.
            (folder / 'result.pptx').write_bytes(f'offline-{number}-{candidate}'.encode())


def test_user_batch_ingest_cli_selects_every_task_and_plan_is_offline(tmp_path, monkeypatch):
    source, manifest_path = tmp_path / 'inputs', tmp_path / 'manifest.json'
    make_inputs(source)
    monkeypatch.setattr(sys, 'argv', ['ppt-rubric', 'ingest', '--source', str(source),
                                     '--out', str(manifest_path), '--split', 'dev'])
    cli.main()
    manifest = read_json(manifest_path)
    validate_manifest(manifest)
    assert [c['split'] for c in manifest['cases']] == ['dev', 'dev']
    assert [len(c['candidates']) for c in manifest['cases']] == [2, 3]
    for case in manifest['cases']:
        assert len(case_sources(case, {'max_case_text_chars': 100000})) == 2
        for candidate in case['candidates']:
            assert candidate['sha256'] == file_digest(candidate['pptx'])
    def no_client(*args, **kwargs):
        raise AssertionError('Offline plan must not construct an API Client')
    monkeypatch.setattr(contract_v2, 'Client', no_client)
    contract_v2.main(['plan', '--manifest', str(manifest_path), '--mode', 'light',
                     '--config', 'configs/contract-v25-k3.json', '--out', str(tmp_path / 'plan.json')])
    plan = read_json(tmp_path / 'plan.json')
    assert plan['offline'] is True and plan['pipeline_mode'] == 'light'
    assert [c['case_id'] for c in plan['cases']] == ['task-001', 'task-002']


def test_bulk_candidates_beyond_z_have_safe_unique_ids(tmp_path):
    make_inputs(tmp_path / 'inputs', (30,))
    manifest = ingest(tmp_path / 'inputs', tmp_path / 'manifest.json', split='dev')
    candidates = manifest['cases'][0]['candidates']
    assert [c['id'] for c in candidates] == [chr(65 + i) for i in range(26)] + ['AA', 'AB', 'AC', 'AD']
    assert len({c['pptx'] for c in candidates}) == 30
    validate_manifest(manifest)


def test_research_default_split_and_seed_behavior_is_preserved(tmp_path):
    make_inputs(tmp_path / 'inputs', (2, 2, 2, 2))
    default = ingest(tmp_path / 'inputs', tmp_path / 'default.json')
    explicit = ingest(tmp_path / 'inputs', tmp_path / 'auto.json', split='auto')
    assert default == explicit
    assert sum(c['split'] == 'holdout' for c in default['cases']) == 1


def test_candidate_prompt_cannot_change_the_shared_task(tmp_path):
    source = tmp_path / 'inputs'
    make_inputs(source, (2,))
    (source / 'task-001/proposal-001/prompt.txt').write_text('另一项任务', encoding='utf-8')
    with pytest.raises(ValueError, match='candidate prompts differ'):
        ingest(source, tmp_path / 'manifest.json', split='dev')


def test_unsupported_attachment_is_not_silently_ignored(tmp_path):
    source = tmp_path / 'inputs'
    make_inputs(source, (2,))
    (source / 'task-001/attachments/notes.docx').write_bytes(b'unsupported')
    manifest = ingest(source, tmp_path / 'manifest.json', split='dev')
    with pytest.raises(ValueError, match='Unsupported attachment'):
        case_sources(manifest['cases'][0], {'max_case_text_chars': 100000})
