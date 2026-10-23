import sys

import pytest

from ppt_rubric import cli
from ppt_rubric.common import digest, read_json, write_json


@pytest.mark.parametrize('existing', [None, {'labels': ['historical']}, {'labels': ['new']}])
def test_legacy_import_output_is_exclusive_or_identical(tmp_path, monkeypatch, existing):
    manifest = {'cases': [{'id': '01', 'split': 'dev', 'prompt': 'Task',
                           'candidates': [{'id': 'A'}, {'id': 'B'}]}]}
    manifest['id'] = digest(manifest)
    manifest_path, output = tmp_path / 'manifest.json', tmp_path / 'labels.json'
    write_json(manifest_path, manifest)
    if existing is not None:
        write_json(output, existing)
    result = {'labels': ['new'], 'decided': 1, 'undecided': [], 'deferred_cases': []}
    # An identical imported set is idempotent; different history is preserved.
    if existing == {'labels': ['new']}:
        write_json(output, result)
    previous = output.read_bytes() if output.exists() else None
    monkeypatch.setattr(cli, 'import_markdown_labels', lambda *args: result)
    monkeypatch.setattr(sys, 'argv', ['ppt-rubric', 'import-labels', '--manifest', str(manifest_path),
                                    '--source', 'unused', '--out', str(output)])
    if existing == {'labels': ['historical']}:
        with pytest.raises(ValueError, match='Refusing to overwrite'):
            cli.main()
        assert output.read_bytes() == previous
    else:
        cli.main()
        assert read_json(output) == result
        if previous is not None:
            assert output.read_bytes() == previous
