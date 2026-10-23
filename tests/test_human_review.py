from copy import deepcopy
from pathlib import Path

import fitz
import pytest

from ppt_rubric.common import digest, file_digest, read_json, write_json
from ppt_rubric.human_labels import import_review_bundle
from ppt_rubric.human_review import prepare_review_packets
from ppt_rubric.label_reliability import analyze_label_reliability


def fixture(tmp_path):
    candidates, prepared = [], {}
    for cid in ('A', 'B'):
        folder = tmp_path / ('real-generator-' + cid)
        folder.mkdir()
        pptx = folder / 'output.pptx'
        pptx.write_bytes(cid.encode())
        candidate_hash = file_digest(pptx)
        candidates.append({'id': cid, 'pptx': str(pptx), 'sha256': candidate_hash})
        pdf, png = folder / 'slides.pdf', folder / 'page-001.png'
        with fitz.open() as document:
            page = document.new_page()
            page.insert_text((20, 20), 'Actual static page ' + cid)
            page.get_pixmap().save(png)
            document.save(pdf)
        prepared['01/' + cid] = {'identity': {'sha256': candidate_hash, 'renderer': 'test renderer', 'dpi': 72},
                                  'pdf': str(pdf), 'slides': [{'page': 1, 'image': str(png)}]}
    manifest = {'cases': [{'id': '01', 'split': 'dev', 'prompt': 'Real task source', 'candidates': candidates},
                           {'id': '04', 'split': 'holdout', 'prompt': 'Do not read me',
                            'candidates': candidates}]}
    manifest['id'] = digest(manifest)
    out = tmp_path / 'human-packets'
    result = prepare_review_packets(manifest, prepared, out, ['human1', 'human2'], 817)
    source = Path(result['collection_path'])
    collection = read_json(source)
    return manifest, source, collection, prepared


def complete(entry, choice='better', preferred='candidate_1', person=None):
    """Synthetic parser fixture only, never production human annotation."""
    record = read_json(entry['source_path'])
    if person:
        record.update(reviewer_id=person, record_id='01-' + person + '-round1')
    record.update(submitted_at='2026-10-19T12:00:00+08:00', viewed_all_pages=True,
                  choice=choice, preferred_candidate=preferred, reasons='Evidence E1 supports my explicit choice.',
                  reason_evidence_ids=['E1'],
                  evidence_records=[{'id': 'E1', 'candidate': 'candidate_1', 'page': 1,
                                     'observation': 'Specified visible object', 'basis': 'Task objective',
                                     'impact': 'Specified task impact', 'certainty': 'confirmed'}],
                  reliability={'blindness': {'status': 'blinded', 'evidence': ['Self-declared no identity access']},
                               'independent_review': {'status': 'independent', 'evidence': ['Separate submission']},
                               'repeat_review': {'status': 'not_repeated', 'evidence': ['First round only']}})
    write_json(entry['source_path'], record)
    return record


def completed_fixture(tmp_path):
    manifest, source, collection, prepared = fixture(tmp_path)
    for entry in collection['cases'][0]['reviews']:
        complete(entry)
    return manifest, source, collection, prepared


def adjudicate(source, collection, choice='better', preferred='candidate_2'):
    case = collection['cases'][0]
    entry = deepcopy(case['reviews'][0])
    entry['source_path'] = str(source.parent / 'adjudication.json')
    write_json(entry['source_path'], read_json(case['reviews'][0]['source_path']))
    record = complete(entry, choice, preferred, 'third-human')
    record['reviewed_record_ids'] = [read_json(review['source_path'])['record_id'] for review in case['reviews']]
    write_json(entry['source_path'], record)
    case['adjudication'] = entry
    write_json(source, collection)
    return entry, record


def test_prepare_makes_blinded_static_packets_and_preserves_exact_render_bytes(tmp_path):
    manifest, source, collection, _ = fixture(tmp_path)
    assert len(collection['cases']) == 1  # Held-out material was not required or read.
    assert (source.stat().st_mode & 0o777) == 0o600
    case = collection['cases'][0]
    for entry in case['reviews']:
        packet = Path(entry['packet_path'])
        public = (packet / 'packet.json').read_text()
        assert 'real-generator' not in public
        assert 'anonymous_to_manifest' not in public
        assert 'automatic' not in public
        assert (packet / 'task.txt').read_text() == manifest['cases'][0]['prompt']
        assert (packet / 'candidate_1/page-001.png').is_file()
        assert read_json(packet / 'review.json')['choice'] is None
    with pytest.raises(ValueError, match='completed explicitly human'):
        import_review_bundle(manifest, source)


def test_two_submitted_reviews_preserve_choices_without_inferred_canonical_vote(tmp_path):
    manifest, source, collection, _ = completed_fixture(tmp_path)
    # Explicitly align the two raw preferences despite independently randomized orders.
    for entry in collection['cases'][0]['reviews']:
        alias = next(alias for alias, cid in entry['anonymous_to_manifest'].items() if cid == 'A')
        complete(entry, preferred=alias)
    labels = import_review_bundle(manifest, source)
    label = labels['labels'][0]
    assert labels['decided'] == 0
    assert labels['awaiting_adjudication'] == ['01']
    assert label['winner'] is None and label['label_status'] == 'unlabeled'
    assert [row['winner'] for row in label['review_records']] == ['A', 'A']
    assert all(row['source_sha256'] for row in label['review_records'])
    assert label['same_render_among_reviewers_verified']
    assert label['reliability']['same_render']['status'] == 'unknown'
    report = analyze_label_reliability(manifest, labels)
    assert report['summary']['multiple_named_reviewer_cases'] == 1
    assert report['summary']['effective_decided_cases'] == 0


def test_explicit_third_party_adjudication_is_separate_from_original_disagreement(tmp_path):
    manifest, source, collection, _ = completed_fixture(tmp_path)
    reviews = collection['cases'][0]['reviews']
    for entry, winner in zip(reviews, ('A', 'B')):
        complete(entry, preferred=next(alias for alias, cid in entry['anonymous_to_manifest'].items() if cid == winner))
    adjudication, _ = adjudicate(source, collection)
    labels = import_review_bundle(manifest, source)
    label = labels['labels'][0]
    assert label['winner'] == adjudication['anonymous_to_manifest']['candidate_2']
    assert label['adjudication']['reviewer_id'] == 'third-human'
    assert len(label['review_records']) == 2
    assert {review['winner'] for review in label['review_records']} == {'A', 'B'}
    reliability = analyze_label_reliability(manifest, labels)
    assert reliability['summary']['raw_rating_disagreement_cases'] == 1
    assert reliability['cases'][0]['facts']['explicit_adjudication_present']


@pytest.mark.parametrize('choice,status,winner', [
    ('quality_equivalent', 'decided', 'tie'),
    ('tradeoff_unresolved', 'undecided', None),
    ('insufficient_evidence', 'undecided', None),
])
def test_distinct_tie_semantics_are_frozen_before_import(tmp_path, choice, status, winner):
    manifest, source, collection, _ = completed_fixture(tmp_path)
    adjudicate(source, collection, choice, preferred=None)
    label = import_review_bundle(manifest, source)['labels'][0]
    assert (label['winner'], label['label_status']) == (winner, status)
    collection['tie_policy']['tradeoff_unresolved'] = 'tie'
    write_json(source, collection)
    with pytest.raises(ValueError, match='frozen tie policy'):
        import_review_bundle(manifest, source)


def test_same_render_with_model_requires_independent_expected_hash_registry(tmp_path):
    manifest, source, collection, _ = completed_fixture(tmp_path)
    expected = {'01': collection['cases'][0]['render_bundle']['sha256']}
    labels = import_review_bundle(manifest, source, expected_render_hashes=expected)
    assert labels['labels'][0]['reliability']['same_render']['status'] == 'matched'
    with pytest.raises(ValueError, match='automated render bundle'):
        import_review_bundle(manifest, source, expected_render_hashes={'01': 'different'})


@pytest.mark.parametrize('which', ['original_page', 'human_page', 'pdf', 'pptx'])
def test_original_and_distributed_render_tampering_is_rejected(tmp_path, which):
    manifest, source, collection, prepared = completed_fixture(tmp_path)
    candidates = manifest['cases'][0]['candidates']
    entry = collection['cases'][0]['reviews'][0]
    path = {'original_page': prepared['01/A']['slides'][0]['image'],
            'human_page': str(Path(entry['packet_path']) / 'candidate_1/page-001.png'),
            'pdf': prepared['01/A']['pdf'], 'pptx': candidates[0]['pptx']}[which]
    Path(path).write_bytes(b'tampered')
    with pytest.raises(ValueError, match='hash mismatch|differs from'):
        import_review_bundle(manifest, source)


def test_duplicate_reviewers_records_and_self_adjudication_are_rejected(tmp_path):
    manifest, source, collection, _ = completed_fixture(tmp_path)
    reviews = collection['cases'][0]['reviews']
    original = read_json(reviews[1]['source_path'])
    first = read_json(reviews[0]['source_path'])
    write_json(reviews[1]['source_path'], dict(original, reviewer_id=first['reviewer_id']))
    with pytest.raises(ValueError, match='distinct human reviewers'):
        import_review_bundle(manifest, source)
    write_json(reviews[1]['source_path'], original)
    entry, record = adjudicate(source, collection)
    write_json(entry['source_path'], dict(record, reviewer_id=first['reviewer_id']))
    with pytest.raises(ValueError, match='Third-party'):
        import_review_bundle(manifest, source)


def test_missing_adjudication_original_coverage_and_changed_source_hash_are_rejected(tmp_path):
    manifest, source, collection, _ = completed_fixture(tmp_path)
    entry, record = adjudicate(source, collection)
    record['reviewed_record_ids'].pop()
    write_json(entry['source_path'], record)
    with pytest.raises(ValueError, match='every original'):
        import_review_bundle(manifest, source)
    collection['cases'][0]['adjudication'] = None
    collection['cases'][0]['reviews'][0]['source_sha256'] = 'stale-expected-hash'
    write_json(source, collection)
    with pytest.raises(ValueError, match='source hash mismatch'):
        import_review_bundle(manifest, source)


def test_bundle_duplicates_never_silently_overwrite_and_heldout_requires_explicit_allow(tmp_path):
    manifest, source, collection, prepared = completed_fixture(tmp_path)
    collection['cases'].append(deepcopy(collection['cases'][0]))
    write_json(source, collection)
    with pytest.raises(ValueError, match='Duplicate selected human case'):
        import_review_bundle(manifest, source)
    with pytest.raises(ValueError, match='allow-holdout'):
        import_review_bundle(manifest, source, split='all')
    with pytest.raises(FileExistsError):
        prepare_review_packets(manifest, prepared, source.parent.parent, ['human1', 'human2'], 817)
