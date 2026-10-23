"""The test suite is offline: fail closed if a test reaches real HTTP transport."""
import pytest
import requests


@pytest.fixture
def plan_manifest(tmp_path):
    """Source-only CLI input independent of private data and real PPTX files."""
    from ppt_rubric.common import digest, write_json

    manifest = {'schema_version': 1, 'seed': 20260930, 'cases': [{
        'id': '01', 'split': 'dev',
        'prompt': '制作项目周报PPT，说明本周进展及下一步计划。',
        'attachments': [],
        'candidates': [{'id': cid, 'pptx': str(tmp_path / cid / 'result.pptx'),
                        'sha256': digest(cid)} for cid in ('A', 'B')],
    }]}
    manifest['id'] = digest(manifest)
    path = tmp_path / 'manifest.json'
    write_json(path, manifest)
    return path


@pytest.fixture(autouse=True)
def forbid_real_http(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Offline tests must never send a real HTTP request')
    monkeypatch.setattr(requests.sessions.Session, 'send', forbidden)
