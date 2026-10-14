import copy
import importlib.util
from pathlib import Path

import pytest
from ppt_rubric.common import digest, file_digest

spec = importlib.util.spec_from_file_location('migration', Path(__file__).resolve().parents[1] / 'scripts/prepare_new_machine.py')
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def test_relocation_preserves_identity_and_rejects_changed_input(tmp_path):
    candidates = []
    for cid in ['A', 'B']:
        path = tmp_path / 'data' / '01' / cid / 'result.pptx'
        path.parent.mkdir(parents=True)
        path.write_bytes(cid.encode())
        candidates.append({'id': cid, 'pptx': '/old/machine/' + str(path.relative_to(tmp_path)),
                           'sha256': file_digest(path)})
    manifest = {'schema_version': 1, 'seed': 42, 'cases': [
        {'id': '01', 'prompt': 'test', 'split': 'dev', 'attachments': [], 'candidates': candidates}]}
    manifest['id'] = digest(manifest)
    before = copy.deepcopy(manifest)
    result = migration.relocate(manifest, tmp_path)
    assert manifest == before
    assert result['id'] != manifest['id']
    assert result['cases'][0]['split'] == 'dev'
    for old, new in zip(candidates, result['cases'][0]['candidates']):
        assert (old['id'], old['sha256']) == (new['id'], new['sha256'])
        assert Path(new['pptx']).is_file()
    Path(result['cases'][0]['candidates'][0]['pptx']).write_bytes(b'changed')
    with pytest.raises(ValueError, match='hash mismatch'):
        migration.relocate(manifest, tmp_path)
