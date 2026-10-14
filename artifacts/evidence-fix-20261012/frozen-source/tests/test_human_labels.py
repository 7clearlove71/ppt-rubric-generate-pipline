from pathlib import Path

import pytest

from ppt_rubric.common import digest, file_digest
from ppt_rubric.human_labels import import_markdown_labels


def fixture(tmp_path, choice='A 更好'):
    source = tmp_path / 'reviews'
    cases = []
    for cid, split in [('01', 'dev'), ('02', 'holdout')]:
        candidates = []
        for aid, model in [('A', 'model-one'), ('B', 'model-two')]:
            p = source / cid / model / 'result.pptx'
            p.parent.mkdir(parents=True)
            p.write_bytes(model.encode())
            candidates.append({'id': aid, 'pptx': str(p), 'sha256': file_digest(p)})
        cases.append({'id':cid, 'split':split, 'prompt':'Make a deck', 'candidates':candidates})
    text = f'''# Review
- 评测人及日期：tester, 2026-10-10
- 候选 A 实际模型：model-two（result.pptx，2 页）
- 候选 B 实际模型：model-one
## 整体选择
- **你的选择：** {choice}
- **主要理由：** A has clearer evidence.
Second line of reasoning.
- **不确定的地方（如有）：** Needs discussion.
'''
    (source / '01/评测人_tester.md').write_text(text)
    # Any accidental read of held-out content fails UTF-8 decoding.
    (source / '02/评测人_tester.md').write_bytes(b'\xff')
    manifest = {'cases':cases}
    manifest['id'] = digest(manifest)
    return manifest, source


def test_import_maps_actual_identity_and_does_not_read_holdout(tmp_path):
    manifest, source = fixture(tmp_path)
    result = import_markdown_labels(manifest, source)
    label = result['labels'][0]
    assert label['winner'] == 'B'
    assert label['reviewer_to_manifest'] == {'A':'B', 'B':'A'}
    assert 'Second line' in label['reasons'] and 'Needs discussion' not in label['reasons']
    assert result['deferred_cases'] == ['02']
    with pytest.raises(ValueError, match='allow-holdout'):
        import_markdown_labels(manifest, source, split='all')


def test_undecided_is_not_inferred_from_reason(tmp_path):
    manifest, source = fixture(tmp_path, '暂时无法判断')
    result = import_markdown_labels(manifest, source)
    assert result['labels'][0]['winner'] is None
    assert result['labels'][0]['label_status'] == 'undecided'
    assert result['decided'] == 0
    assert result['undecided'] == ['01']


def test_import_rejects_changed_pptx(tmp_path):
    manifest, source = fixture(tmp_path)
    Path(manifest['cases'][0]['candidates'][0]['pptx']).write_bytes(b'changed')
    with pytest.raises(ValueError, match='differs from frozen'):
        import_markdown_labels(manifest, source)


@pytest.mark.parametrize('choice', ['A 更好 / B 更好', '大概A', ''])
def test_import_does_not_guess_ambiguous_choice(tmp_path, choice):
    manifest, source = fixture(tmp_path, choice)
    with pytest.raises(ValueError):
        import_markdown_labels(manifest, source)


def test_import_requires_explicit_adjudication_for_multiple_reviewers(tmp_path):
    manifest, source = fixture(tmp_path)
    (source / '01/评测人_second.md').write_text('Second opinion')
    with pytest.raises(ValueError, match='adjudicate'):
        import_markdown_labels(manifest, source)
