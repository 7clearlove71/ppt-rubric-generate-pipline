from copy import deepcopy

import pytest

from ppt_rubric.common import digest
from ppt_rubric.label_reliability import analyze_label_reliability


def fixture():
    cases = [{'id': cid, 'split': split, 'prompt': 'Make a deck',
              'candidates': [{'id': 'A', 'sha256': 'hash-a'}, {'id': 'B', 'sha256': 'hash-b'}]}
             for cid, split in [('01', 'dev'), ('02', 'dev'), ('03', 'dev'), ('04', 'holdout')]]
    manifest = {'cases': cases}
    manifest['id'] = digest(manifest)
    labels = {'manifest_id': manifest['id'], 'labels': [
        {'case_id': cid, 'winner': winner, 'label_status': status,
         'reviewer': 'human-one', 'source_path': f'never-open/{cid}.md',
         'source_sha256': f'source-{cid}', 'candidate_hashes': {'A': 'hash-a', 'B': 'hash-b'},
         'reasons': 'A reads better, but canonical choice remains authoritative.'}
        for cid, winner, status in [('01', 'B', 'decided'), ('02', 'tie', 'decided'),
                                    ('03', None, 'undecided')]]}
    return manifest, labels


def test_counts_canonical_choices_without_changing_labels_or_guessing_undecided():
    manifest, labels = fixture()
    original = deepcopy(labels)
    report = analyze_label_reliability(manifest, labels)
    assert labels == original
    assert report['summary']['effective_decided_cases'] == 2
    assert report['summary']['ties'] == 1
    assert report['summary']['undecided'] == 1
    assert report['summary']['single_observed_record_cases'] == 3
    assert report['summary']['inter_rater_reliability'] is None
    assert report['cases'][0]['canonical_label']['winner'] == 'B'
    assert report['cases'][2]['canonical_label']['winner'] is None


def test_missing_protocol_metadata_is_unknown_not_nonblind():
    manifest, labels = fixture()
    report = analyze_label_reliability(manifest, labels)
    row = report['cases'][0]
    assert row['facts']['protocol_documentation']['blindness']['status'] == 'unknown'
    assert 'blindness' in row['unknowns']
    assert row['facts']['canonical_source']['path'] == 'never-open/01.md'
    assert row['facts']['observed_named_reviewer_count'] == 1


def test_requires_documentation_for_protocol_claims_and_never_opens_references():
    manifest, labels = fixture()
    labels['labels'][0]['reliability'] = {
        'blindness': {'status': 'blinded', 'evidence': []},
        'repeat_review': {'status': 'repeated', 'evidence': ['inaccessible://record']},
        'tie_semantics': 'Equivalent quality for the intended task',
    }
    report = analyze_label_reliability(manifest, labels)
    protocol = report['cases'][0]['facts']['protocol_documentation']
    assert protocol['blindness']['status'] == 'unknown'
    assert protocol['blindness']['claimed_status'] == 'blinded'
    assert protocol['repeat_review']['status'] == 'repeated'
    assert report['summary']['documented_repeated_cases'] == 1
    assert 'evidence_independently_verified' in report['cases'][0]['unknowns']


def test_multiple_reviewers_are_provenance_not_votes_or_extra_cases():
    manifest, labels = fixture()
    labels['labels'][0]['review_records'] = [
        {'reviewer_id': 'one', 'winner': 'A'},
        {'reviewer_id': 'two', 'winner': 'A'},
    ]
    report = analyze_label_reliability(manifest, labels)
    assert report['cases'][0]['canonical_label']['winner'] == 'B'
    assert report['cases'][0]['facts']['observed_named_reviewer_count'] == 2
    assert report['summary']['effective_decided_cases'] == 2
    assert report['summary']['multiple_named_reviewer_cases'] == 1


def test_two_records_by_same_reviewer_do_not_establish_two_independent_people():
    manifest, labels = fixture()
    labels['labels'][0]['review_records'] = [{'reviewer_id': 'same'}, {'reviewer_id': 'same'}]
    report = analyze_label_reliability(manifest, labels)
    assert report['cases'][0]['facts']['observed_review_record_count'] == 2
    assert report['cases'][0]['facts']['observed_named_reviewer_count'] == 1
    assert 'independent_review' in report['cases'][0]['unknowns']


class HeldOutRecord(dict):
    def get(self, key, default=None):
        if key != 'case_id':
            raise AssertionError('Read held-out label content')
        return super().get(key, default)


def test_dev_only_does_not_access_held_out_label_fields():
    manifest, labels = fixture()
    labels['labels'].append(HeldOutRecord(case_id='04'))
    report = analyze_label_reliability(manifest, labels)
    assert [row['case_id'] for row in report['cases']] == ['01', '02', '03']
    with pytest.raises(ValueError, match='allow-holdout'):
        analyze_label_reliability(manifest, labels, split='all')


def test_unlabeled_case_is_preserved_without_fabricated_reviewer():
    manifest, labels = fixture()
    labels['labels'].pop()
    report = analyze_label_reliability(manifest, labels)
    assert report['summary']['unlabeled'] == 1
    assert report['cases'][2]['canonical_label'] is None
    assert 'reviewer_count' in report['cases'][2]['unknowns']


@pytest.mark.parametrize('change, error', [
    (lambda labels: labels.update(manifest_id='wrong'), 'different manifest'),
    (lambda labels: labels['labels'].append(dict(labels['labels'][0])), 'Duplicate'),
    (lambda labels: labels['labels'][0].update(candidate_hashes={}), 'different candidate'),
    (lambda labels: labels['labels'][2].update(winner='A'), 'nondecided'),
    (lambda labels: labels['labels'][0].update(reliability={'blindness': {'status': 'probably'}}), 'Invalid reliability'),
])
def test_rejects_inconsistent_selected_provenance(change, error):
    manifest, labels = fixture()
    change(labels)
    with pytest.raises(ValueError, match=error):
        analyze_label_reliability(manifest, labels)
