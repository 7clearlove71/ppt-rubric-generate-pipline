import copy

import pytest

from ppt_rubric.common import digest, read_json, write_json
from ppt_rubric.contract_report import accounting, interval_preference, summarize, weight_sensitivity
from ppt_rubric.rubric import aggregate


WEIGHTS = {'fulfillment': 30, 'correctness': 25, 'integrity': 15, 'narrative': 15, 'visual': 15}


def interval(lower, upper=None):
    return {'score_lower': lower, 'score_upper': lower if upper is None else upper}


@pytest.mark.parametrize('a,b,expected,bounds', [
    ((53, 53), (50, 50), 'tie', [3, 3]),
    ((47, 47), (50, 50), 'tie', [-3, -3]),
    ((53.0001, 53.0001), (50, 50), 'A', [3.0001, 3.0001]),
    ((46.9999, 46.9999), (50, 50), 'B', [-3.0001, -3.0001]),
    ((47, 53), (50, 50), 'tie', [-3, 3]),
    ((53, 70), (50, 50), None, [3, 20]),
    ((30, 47), (50, 50), None, [-20, -3]),
    ((54, 70), (49, 50), 'A', [4, 21]),
    ((0, 100), (0, 100), None, [-100, 100]),
])
def test_interval_robustness_and_inclusive_tie_boundary(a, b, expected, bounds):
    result = interval_preference(interval(*a), interval(*b))
    assert result['prediction'] == expected
    assert result['difference_interval'] == pytest.approx(bounds)
    assert (result['status'] == 'unresolved') == (expected is None)


@pytest.mark.parametrize('bad', [interval(float('nan')), interval(0, float('inf')),
                                 interval(51, 50), interval(-1, 50), interval(0, 101), interval(True)])
def test_invalid_intervals_cannot_produce_a_comparison(bad):
    with pytest.raises(ValueError, match='interval'):
        interval_preference(bad, interval(50))


@pytest.mark.parametrize('margin', [-1, float('nan'), float('inf'), True])
def test_invalid_tie_margins_are_rejected(margin):
    with pytest.raises(ValueError, match='margin'):
        interval_preference(interval(50), interval(50), margin)


def summary(rates=None, unknown=0):
    rates = rates or dict.fromkeys(WEIGHTS, .5)
    dimensions = {d: {'earned': w * rates[d], 'possible': w, 'unresolved': w * unknown}
                  for d, w in WEIGHTS.items()}
    lower = sum(row['earned'] for row in dimensions.values())
    return {**interval(lower, lower + unknown * 100), 'dimensions': dimensions}


def test_sensitivity_evaluates_all_32_corners_and_not_just_one_dimension_at_a_time():
    a = summary({'fulfillment': 1, 'correctness': 0, 'integrity': .5, 'narrative': .5, 'visual': .5})
    b = summary({'fulfillment': 0, 'correctness': 1, 'integrity': .5, 'narrative': .5, 'visual': .5})
    result = weight_sensitivity(a, b, WEIGHTS)
    assert result['corner_count'] == sum(result['outcomes'].values()) == 32
    assert result['outcomes']['A'] > 0 and result['outcomes']['B'] > 0
    assert not result['stable']
    assert result['factors'] == [.8, 1.2]


def test_sensitivity_boundary_rounding_never_turns_exact_three_points_into_a_win():
    a, b = summary(dict.fromkeys(WEIGHTS, .53)), summary(dict.fromkeys(WEIGHTS, .5))
    result = weight_sensitivity(a, b, WEIGHTS)
    assert result['outcomes'] == {'tie': 32}
    assert result['stable']


def test_sensitivity_identical_unresolved_outcomes_are_not_stable_predictions():
    a, b = summary(dict.fromkeys(WEIGHTS, 0), unknown=1), summary()
    result = weight_sensitivity(a, b, WEIGHTS)
    assert result['outcomes'] == {'unresolved': 32}
    assert result['stable'] is False


def test_sensitivity_rejects_nonfive_dimension_and_inconsistent_budgets():
    with pytest.raises(ValueError, match='five positive'):
        weight_sensitivity(summary(), summary(), {'fulfillment': 50, 'visual': 50})
    wrong = summary()
    wrong['dimensions']['visual']['possible'] = 16
    with pytest.raises(ValueError, match='budgets'):
        weight_sensitivity(wrong, summary(), WEIGHTS)
    wrong = summary()
    wrong['score_lower'] = 49
    with pytest.raises(ValueError, match='reported score interval'):
        weight_sensitivity(wrong, summary(), WEIGHTS)


def attempt(number, invocation='run-one', model='model-one', status='success', tokens=10):
    result = {'request_id': number, 'invocation_id': invocation, 'model': model, 'status': status}
    if tokens is not None:
        result['usage'] = {'total_tokens': tokens}
    return result


def test_accounting_uses_only_canonical_request_journals_and_includes_failed_started_attempts(tmp_path):
    first = [attempt(1), attempt(2, status='failed', tokens=0), attempt(3, status='started', tokens=None)]
    write_json(tmp_path / 'rubrics/primary-api/requests.json', first)
    write_json(tmp_path / 'judgments/verification-api/requests.json', [attempt(1, 'run-two', 'model-two', tokens=20)])
    write_json(tmp_path / 'pre-repair/rubrics/primary-api/requests.json', first)
    write_json(tmp_path / 'judgments/primary-api/usage.json', [{'cached': True, 'total_tokens': 99999}])
    result = accounting(tmp_path)
    assert result['total_unique_requests'] == 4
    assert len(result['journals']) == 2
    assert result['models']['model-one']['requests'] == 3
    assert result['models']['model-one']['reported_tokens'] == 10
    assert result['models']['model-one']['missing_usage'] == 1
    assert result['models']['model-one']['statuses'] == {'success': 1, 'failed': 1, 'started': 1}
    assert result['models']['model-two']['reported_tokens'] == 20
    assert result['cost_estimate'] is None


def test_identical_request_copies_across_live_journals_are_not_counted_twice(tmp_path):
    row = attempt(1)
    for directory in ('rubrics/primary-api', 'judgments/primary-api'):
        write_json(tmp_path / directory / 'requests.json', [row])
    result = accounting(tmp_path)
    assert result['total_unique_requests'] == result['models']['model-one']['requests'] == 1
    assert len(result['excluded_duplicate_attempts']) == 1
    assert sum(j['unique_requests'] for j in result['journals']) == 1


def test_conflicting_copies_require_journal_reconciliation_instead_of_guessing(tmp_path):
    write_json(tmp_path / 'rubrics/primary-api/requests.json', [attempt(1, status='started', tokens=None)])
    write_json(tmp_path / 'judgments/primary-api/requests.json', [attempt(1)])
    with pytest.raises(ValueError, match='Conflicting copies'):
        accounting(tmp_path)


def test_usage_boolean_is_missing_not_one_token_and_attempt_identity_is_required(tmp_path):
    path = tmp_path / 'rubrics/primary-api/requests.json'
    write_json(path, [attempt(1, tokens=True)])
    result = accounting(tmp_path)
    assert result['models']['model-one']['reported_tokens'] == 0
    assert result['models']['model-one']['missing_usage'] == 1
    row = attempt(1)
    del row['invocation_id']
    write_json(path, [row])
    with pytest.raises(ValueError, match='invocation_id'):
        accounting(tmp_path)


def fixture_artifacts(root):
    manifest = {'cases': [{'id': '01', 'prompt': 'Synthetic offline report task', 'split': 'dev',
                          'candidates': [{'id': c, 'sha256': f'hash-{c}'} for c in ('A', 'B')]}]}
    manifest['id'] = digest(manifest)
    suite = {'case_id': '01', 'status': 'approved', 'evaluation_protocol': 'contract_v2',
             'provenance': {'source_hash': 'source-hash', 'prompt_version': 'test-contract-version'}, 'dimension_weights': WEIGHTS,
             'items': [{'id': d, 'dimension': d, 'score': w} for d, w in WEIGHTS.items()]}
    suite['rubric_hash'] = digest(suite)
    write_json(root / 'rubrics/01/rubric.json', suite)
    for cid in ('A', 'B'):
        verdicts = [{'rubric_id': d, 'answer': 'no' if cid == 'B' and d == 'visual' else 'yes',
                     'assessment_status': 'not_independently_verified' if d == 'visual' else 'independent_agreement'}
                    for d in WEIGHTS]
        scores = aggregate(suite, verdicts)
        report = {'case_id': '01', 'candidate_id': cid, 'candidate_hash': f'hash-{cid}',
                  'rubric_hash': suite['rubric_hash'], 'source_hash': 'source-hash', 'config_hash': 'frozen-config',
                  'judge_model': 'primary', 'verification_model': 'verification', 'judge_validator_version': 1,
                  'judge_prompt_fingerprint': 'frozen-prompt', 'evaluation_protocol': 'contract_v2',
                  'primary': {'summary': scores, 'visual_pages': [{'page': 1, 'verdicts': [verdicts[-1]]}]},
                  'summary': scores, 'verdicts': verdicts, 'ownership_conflicts': [],
                  'consensus': {'independently_evaluated_items': [d for d in WEIGHTS if d != 'visual']}}
        write_json(root / f'judgments/01/{cid}.json', report)
    labels = {'manifest_id': manifest['id'], 'labels': [{'case_id': '01', 'winner': 'A',
              'candidate_hashes': {'A': 'hash-A', 'B': 'hash-B'}, 'reasons': 'Synthetic comparison only.'}]}
    return manifest, labels


def test_summary_uses_same_frozen_automation_when_human_choice_changes(tmp_path, monkeypatch):
    manifest, labels = fixture_artifacts(tmp_path)
    untouched = copy.deepcopy(labels)

    def forbid_model(*args, **kwargs):
        raise AssertionError('Offline reporting must not call a model')

    monkeypatch.setattr('ppt_rubric.provider.Client.ask', forbid_model)
    first = summarize(manifest, tmp_path, labels)
    assert labels == untouched
    labels['labels'][0]['winner'] = 'B'
    second = summarize(manifest, tmp_path, labels)
    assert first['complete_score_agreement']['agreement'] == 1
    assert second['complete_score_agreement']['agreement'] == 0
    assert first['robust_interval_agreement']['agreement'] == 1
    assert second['robust_interval_agreement']['agreement'] == 0
    for key in ('candidates', 'weight_sensitivity', 'primary_preference', 'combined_interval_preference'):
        assert first['cases'][0][key] == second['cases'][0][key]
    assert first['independent_content_answers'] == {'independent_agreement': 8}


def source_qa_fixture(root, material=True):
    suite = read_json(root / 'rubrics/01/rubric.json')
    return {'schema_version': 1, 'audit_status': 'complete',
            'target_version': suite['provenance']['prompt_version'], 'target_cases': ['01'],
            'scope': {'candidate_files_read_in_this_audit': False, 'human_labels_read_in_this_audit': False},
            'method': 'Apply full contracts to source-only counterexamples.',
            'limitations': ['Synthetic Codex review; not a new human label.'],
            'summary': {'reviewed_final_v23_cases': 999, 'confirmed_material_findings': 999},
            'cases': [{'case_id': '01', 'rubric_hash': suite['rubric_hash'],
                       'prompt_version': suite['provenance']['prompt_version'],
                       'audit_status': ('reviewed_with_confirmed_material_findings' if material
                                        else 'reviewed_no_confirmed_material_counterexample'),
                       'findings': ([{'id': '01-F01', 'type': 'duplicate_fact_ownership',
                                      'severity': 'material', 'criteria': ['fulfillment', 'correctness'],
                                      'consequence': 'One fact omission can lose two item weights.'}]
                                    if material else [])}]}


def add_unreviewed_manifest_case(manifest, split='dev'):
    case = copy.deepcopy(manifest['cases'][0])
    case.update(id='02', split=split)
    manifest['cases'].append(case)
    manifest['id'] = digest({k: v for k, v in manifest.items() if k != 'id'})


def test_source_semantic_qa_is_missing_not_passed_when_absent(tmp_path):
    manifest, _ = fixture_artifacts(tmp_path)
    result = summarize(manifest, tmp_path)['source_semantic_qa']
    assert result['status'] == 'missing'
    assert result['summary']['reviewed_cases'] == 0
    assert result['summary']['cases_without_confirmed_counterexample'] == []
    assert result['summary']['unreviewed_cases'] == ['01']
    assert result['summary']['conclusion_status'] == 'not_final'
    assert result['cases'][0]['audit_status'] == 'not_reviewed'
    assert result['cases'][0]['rubric_hash_verified'] is False


@pytest.mark.parametrize('material', [True, False])
def test_source_semantic_qa_reports_bound_evidence_without_changing_any_metrics(tmp_path, material):
    manifest, labels = fixture_artifacts(tmp_path)
    baseline = summarize(manifest, tmp_path, labels)
    qa = source_qa_fixture(tmp_path, material)
    write_json(tmp_path / 'final-source-semantic-qa.json', qa)
    before = {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    report = summarize(manifest, tmp_path, labels)
    after = {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    assert before == after
    assert {k: v for k, v in report.items() if k != 'source_semantic_qa'} == {
        k: v for k, v in baseline.items() if k != 'source_semantic_qa'}
    result = report['source_semantic_qa']
    assert result['status'] == 'complete'
    assert result['evidence_kind'] == 'codex_source_semantic_review'
    assert result['source_digest'] == digest(qa)
    assert result['scope'] == qa['scope'] and result['method'] == qa['method']
    assert qa['limitations'][0] in result['limitations']
    assert any('not new human annotation' in note for note in result['limitations'])
    assert result['summary']['reviewed_cases'] == 1
    assert result['summary']['confirmed_material_findings'] == int(material)
    assert result['summary']['cases_with_confirmed_material_findings'] == int(material)
    assert result['summary']['case_ids_with_confirmed_material_findings'] == (['01'] if material else [])
    assert result['summary']['cases_without_confirmed_counterexample'] == ([] if material else ['01'])
    assert result['cases'][0]['rubric_hash'] == qa['cases'][0]['rubric_hash']
    assert result['cases'][0]['rubric_hash_verified'] is True


@pytest.mark.parametrize('all_rows_present', [True, False])
def test_source_semantic_qa_in_progress_is_partial_even_when_every_row_is_reviewed(tmp_path, all_rows_present):
    manifest, _ = fixture_artifacts(tmp_path)
    qa = source_qa_fixture(tmp_path)
    qa['audit_status'] = 'in_progress'
    if not all_rows_present:
        qa['cases'] = []
    write_json(tmp_path / 'final-source-semantic-qa.json', qa)
    result = summarize(manifest, tmp_path)['source_semantic_qa']
    assert result['status'] == 'partial'
    assert result['audit_status'] == 'in_progress'
    assert result['summary']['conclusion_status'] == 'not_final'
    assert result['summary']['reviewed_cases'] == int(all_rows_present)
    assert result['summary']['unreviewed_cases'] == ([] if all_rows_present else ['01'])


def test_source_semantic_qa_subset_never_marks_unreviewed_cases_clean(tmp_path):
    manifest, _ = fixture_artifacts(tmp_path)
    add_unreviewed_manifest_case(manifest)
    write_json(tmp_path / 'final-source-semantic-qa.json', source_qa_fixture(tmp_path, False))
    result = summarize(manifest, tmp_path)
    assert result['total_cases'] == 2 and result['counts']['rubrics_approved'] == 1
    assert result['source_semantic_qa']['status'] == 'partial'
    assert result['source_semantic_qa']['summary']['coverage'] == .5
    assert result['source_semantic_qa']['summary']['unreviewed_cases'] == ['02']
    assert result['source_semantic_qa']['cases'][1]['rubric_hash'] is None


@pytest.mark.parametrize('rubric_exists', [True, False])
def test_source_semantic_qa_pending_without_hash_is_unreviewed_even_before_rubric_exists(tmp_path, rubric_exists):
    artifact_root = tmp_path if rubric_exists else tmp_path / 'fixture'
    manifest, _ = fixture_artifacts(artifact_root)
    qa = source_qa_fixture(artifact_root)
    qa.update(audit_status='in_progress', cases=[{'case_id': '01', 'audit_status': 'pending', 'findings': []}])
    write_json(tmp_path / 'final-source-semantic-qa.json', qa)
    result = summarize(manifest, tmp_path)['source_semantic_qa']
    assert result['status'] == 'partial'
    assert result['summary']['unreviewed_cases'] == ['01']
    assert result['summary']['reviewed_cases'] == 0
    assert result['cases'][0]['audit_status'] == 'pending'
    assert result['cases'][0]['rubric_hash_verified'] is False


def test_source_semantic_qa_pending_explicit_stale_hash_is_still_rejected(tmp_path):
    manifest, _ = fixture_artifacts(tmp_path)
    qa = source_qa_fixture(tmp_path)
    qa['audit_status'] = 'in_progress'
    qa['cases'][0].update(audit_status='pending', rubric_hash='old-rubric', findings=[])
    write_json(tmp_path / 'final-source-semantic-qa.json', qa)
    with pytest.raises(ValueError, match='rubric hash'):
        summarize(manifest, tmp_path)


@pytest.mark.parametrize('mutation,message', [
    (lambda q: q.update(target_cases=['99']), 'nonexistent case'),
    (lambda q: q.update(target_cases=['01', '01']), 'unique'),
    (lambda q: q['cases'].append(copy.deepcopy(q['cases'][0])), 'duplicate case'),
    (lambda q: q['cases'][0]['findings'].append(copy.deepcopy(q['cases'][0]['findings'][0])), 'finding IDs'),
    (lambda q: q['cases'][0].update(case_id='99'), 'declared targets'),
    (lambda q: q['cases'][0].update(rubric_hash='old-rubric'), 'rubric hash'),
    (lambda q: q['cases'][0].update(prompt_version='old-version'), 'version'),
    (lambda q: q.update(target_version='old-version'), 'version'),
    (lambda q: q.update(cases=[]), 'finish every'),
    (lambda q: q['cases'][0].update(audit_status='in_progress'), 'finish every'),
    (lambda q: q['cases'][0].update(audit_status='reviewed_no_confirmed_material_counterexample'), 'contradicts'),
    (lambda q: q['cases'][0].update(findings=[]), 'contradicts'),
])
def test_invalid_or_stale_source_semantic_qa_is_rejected(tmp_path, mutation, message):
    manifest, _ = fixture_artifacts(tmp_path)
    qa = source_qa_fixture(tmp_path)
    mutation(qa)
    write_json(tmp_path / 'final-source-semantic-qa.json', qa)
    with pytest.raises(ValueError, match=message):
        summarize(manifest, tmp_path)


def test_source_semantic_qa_cannot_claim_review_without_current_rubric(tmp_path):
    manifest, _ = fixture_artifacts(tmp_path / 'fixture')
    write_json(tmp_path / 'final-source-semantic-qa.json', source_qa_fixture(tmp_path / 'fixture'))
    with pytest.raises(ValueError, match='no current rubric'):
        summarize(manifest, tmp_path)


def test_source_semantic_qa_from_another_split_never_leaks_findings_or_reads_its_rubric(tmp_path, monkeypatch):
    manifest, _ = fixture_artifacts(tmp_path)
    add_unreviewed_manifest_case(manifest, split='holdout')
    write_json(tmp_path / 'final-source-semantic-qa.json', source_qa_fixture(tmp_path))
    original = read_json

    def no_other_split_rubric(path):
        assert 'rubrics/01' not in str(path)
        return original(path)

    monkeypatch.setattr('ppt_rubric.contract_report.read_json', no_other_split_rubric)
    result = summarize(manifest, tmp_path, split='holdout', allow_holdout=True)['source_semantic_qa']
    assert result['status'] == 'not_applicable'
    assert result['summary']['reviewed_cases'] == result['summary']['confirmed_material_findings'] == 0
    assert result['summary']['unreviewed_cases'] == ['02']
    assert result['cases'][0]['findings'] == []


def test_source_semantic_qa_cannot_mix_splits(tmp_path):
    manifest, _ = fixture_artifacts(tmp_path)
    add_unreviewed_manifest_case(manifest, split='holdout')
    qa = source_qa_fixture(tmp_path)
    qa['target_cases'].append('02')
    write_json(tmp_path / 'final-source-semantic-qa.json', qa)
    with pytest.raises(ValueError, match='cannot mix'):
        summarize(manifest, tmp_path)


@pytest.mark.parametrize('field,value,expected', [
    ('source_hash', 'different-source', 'approved rubric/source'),
    ('rubric_hash', 'different-rubric', 'approved rubric/source'),
    ('candidate_hash', 'other-candidate', 'Candidate hash'),
    ('judge_prompt_fingerprint', 'other-prompt', 'mismatched'),
    ('judge_validator_version', 999, 'mismatched'),
    ('verification_model', 'another-model', 'mismatched'),
])
def test_report_comparability_is_checked_even_without_labels(tmp_path, field, value, expected):
    manifest, _ = fixture_artifacts(tmp_path)
    path = tmp_path / 'judgments/01/A.json'
    report = read_json(path)
    report[field] = value
    write_json(path, report)
    with pytest.raises(ValueError, match=expected):
        summarize(manifest, tmp_path)


def test_nonverified_ownership_conflict_is_not_counted_as_independent_content(tmp_path):
    manifest, _ = fixture_artifacts(tmp_path)
    path = tmp_path / 'judgments/01/A.json'
    report = read_json(path)
    report['consensus']['independently_evaluated_items'].remove('narrative')
    next(v for v in report['verdicts'] if v['rubric_id'] == 'narrative')['assessment_status'] = 'ownership_conflict'
    write_json(path, report)
    result = summarize(manifest, tmp_path)
    assert result['independent_content_answers'] == {'independent_agreement': 7}


def test_robust_interval_result_is_reported_separately_from_complete_score_agreement(tmp_path):
    manifest, labels = fixture_artifacts(tmp_path)
    path = tmp_path / 'judgments/01/A.json'
    report = read_json(path)
    # One uncertain visual criterion leaves A in [85,100], while B's known
    # correctness failure below leaves it at 60. Every completion still favors A.
    report['verdicts'][-1]['answer'] = 'unknown'
    suite = read_json(tmp_path / 'rubrics/01/rubric.json')
    report['summary'] = aggregate(suite, report['verdicts'])
    write_json(path, report)
    path = tmp_path / 'judgments/01/B.json'
    report = read_json(path)
    next(v for v in report['verdicts'] if v['rubric_id'] == 'correctness')['answer'] = 'no'
    report['summary'] = aggregate(suite, report['verdicts'])
    write_json(path, report)
    result = summarize(manifest, tmp_path, labels)
    assert result['complete_score_agreement']['n'] == 0
    assert result['complete_score_agreement']['agreement'] is None
    assert result['robust_interval_agreement']['n'] == 1
    assert result['robust_interval_agreement']['agreement'] == 1
    assert result['cases'][0]['candidates']['A']['combined']['score'] is None


def test_complete_score_cannot_contradict_interval_without_human_labels(tmp_path):
    manifest, _ = fixture_artifacts(tmp_path)
    path = tmp_path / 'judgments/01/A.json'
    report = read_json(path)
    report['summary']['score'] = 0
    write_json(path, report)
    with pytest.raises(ValueError, match='complete score'):
        summarize(manifest, tmp_path)


def test_quality_failure_status_is_not_mislabeled_missing(tmp_path):
    manifest, _ = fixture_artifacts(tmp_path / 'unused')
    write_json(tmp_path / 'rubrics/01/status.json', {'status': 'quality_failed'})
    result = summarize(manifest, tmp_path)
    assert result['cases'][0]['rubric_status'] == 'quality_failed'
    assert result['counts']['rubrics_approved'] == 0


def test_holdout_requires_explicit_selection_and_cannot_be_silently_mixed(tmp_path):
    manifest, _ = fixture_artifacts(tmp_path)
    manifest['cases'][0]['split'] = 'holdout'
    manifest['id'] = digest({k: v for k, v in manifest.items() if k != 'id'})
    with pytest.raises(ValueError, match='allow-holdout'):
        summarize(manifest, tmp_path, split='holdout')
    with pytest.raises(ValueError, match='separately'):
        summarize(manifest, tmp_path, split='all', allow_holdout=True)
    result = summarize(manifest, tmp_path, split='holdout', allow_holdout=True)
    assert result['cases'][0]['rubric_status'] == 'missing'  # Does not reuse dev artifacts.


def test_single_assessor_metric_keeps_primary_and_combined_agreement_separate(tmp_path):
    manifest, labels = fixture_artifacts(tmp_path)
    suite = read_json(tmp_path / 'rubrics/01/rubric.json')
    path = tmp_path / 'judgments/01/A.json'
    report = read_json(path)
    # The independent verification has unresolved content, but the original
    # primary score is complete. Neither view may overwrite the other.
    report['verdicts'][0]['answer'] = 'unknown'
    report['summary'] = aggregate(suite, report['verdicts'])
    write_json(path, report)
    before = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    result = summarize(manifest, tmp_path, labels)
    after = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    assert before == after  # No fabricated alternate reports or artifact rewrites.
    metric = result['single_assessor_agreement']
    assert (metric['n'], metric['matches'], metric['agreement'], metric['decided_labels'], metric['coverage']) == (1, 1, 1, 1, 1)
    assert metric['assessment'] == 'primary_only_not_independently_verified'
    assert metric['cases'][0]['scores'] == {'A': 100, 'B': 85}
    assert metric['cases'][0]['candidate_hashes'] == {'A': 'hash-A', 'B': 'hash-B'}
    assert result['complete_score_agreement']['n'] == 0
    assert result['robust_interval_agreement']['n'] == 0


@pytest.mark.parametrize('a_score,expected', [(53, 'tie'), (47, 'tie'), (53.0001, 'A'), (46.9999, 'B')])
def test_primary_single_assessor_uses_frozen_three_point_tie_rule(tmp_path, a_score, expected):
    manifest, labels = fixture_artifacts(tmp_path)
    for cid, score in (('A', a_score), ('B', 50)):
        path = tmp_path / f'judgments/01/{cid}.json'
        report = read_json(path)
        report['primary']['summary'] = {**summary(dict.fromkeys(WEIGHTS, score / 100)),
                                         'status': 'complete', 'score': score, 'unresolved_weight': 0}
        write_json(path, report)
    result = summarize(manifest, tmp_path, labels)
    assert result['single_assessor_agreement']['cases'][0]['prediction'] == expected
    assert result['single_assessor_agreement']['tie_margin'] == 3


def test_missing_case_is_excluded_and_majority_baseline_uses_only_comparable_subset(tmp_path):
    manifest, labels = fixture_artifacts(tmp_path)
    missing = copy.deepcopy(manifest['cases'][0])
    missing['id'] = '02'
    manifest['cases'].append(missing)
    manifest['id'] = digest({k: v for k, v in manifest.items() if k != 'id'})
    labels['manifest_id'] = manifest['id']
    missing_label = copy.deepcopy(labels['labels'][0])
    missing_label.update(case_id='02', winner='B')
    labels['labels'].append(missing_label)
    result = summarize(manifest, tmp_path, labels)
    metric = result['single_assessor_agreement']
    assert metric['n'] == 1 and metric['decided_labels'] == 2 and metric['coverage'] == .5
    assert metric['skipped'] == [{'case_id': '02', 'reason': 'missing primary candidate assessment'}]
    assert metric['majority_label_baseline']['label_counts'] == {'A': 1}
    assert metric['majority_label_baseline']['n'] == metric['n']


def test_primary_incomplete_score_is_excluded_even_if_interval_preference_is_robust(tmp_path):
    manifest, labels = fixture_artifacts(tmp_path)
    path = tmp_path / 'judgments/01/A.json'
    report = read_json(path)
    report['primary']['summary'] = {'status': 'incomplete', 'score': None, 'score_lower': 95,
                                    'score_upper': 100, 'unresolved_weight': 5}
    write_json(path, report)
    result = summarize(manifest, tmp_path, labels)
    metric = result['single_assessor_agreement']
    assert result['cases'][0]['primary_preference']['prediction'] == 'A'
    assert metric['n'] == 0 and metric['agreement'] is None and metric['coverage'] == 0
    assert metric['decided_labels'] == 1
    assert metric['skipped'] == [{'case_id': '01', 'reason': 'incomplete primary score'}]


def test_undecided_human_label_is_not_a_single_assessor_tie_or_failure(tmp_path):
    manifest, labels = fixture_artifacts(tmp_path)
    labels['labels'][0].update(winner=None, label_status='undecided')
    result = summarize(manifest, tmp_path, labels)
    metric = result['single_assessor_agreement']
    assert metric['n'] == metric['decided_labels'] == 0
    assert metric['coverage'] is None and metric['agreement'] is None
    assert metric['majority_label_baseline']['agreement'] is None


def test_single_assessor_maps_verified_manifest_candidate_names_without_a_b_assumptions(tmp_path):
    manifest, labels = fixture_artifacts(tmp_path)
    mapping = {'A': 'entry-left', 'B': 'entry-right'}
    for candidate in manifest['cases'][0]['candidates']:
        candidate['id'] = mapping[candidate['id']]
    manifest['id'] = digest({k: v for k, v in manifest.items() if k != 'id'})
    labels['manifest_id'] = manifest['id']
    labels['labels'][0]['winner'] = mapping['A']
    labels['labels'][0]['candidate_hashes'] = {mapping[c]: f'hash-{c}' for c in ('A', 'B')}
    for old, new in mapping.items():
        path = tmp_path / f'judgments/01/{old}.json'
        report = read_json(path)
        report['candidate_id'] = new
        path.rename(tmp_path / f'judgments/01/{new}.json')
        write_json(tmp_path / f'judgments/01/{new}.json', report)
    result = summarize(manifest, tmp_path, labels)
    metric = result['single_assessor_agreement']
    assert metric['n'] == metric['matches'] == 1
    assert metric['cases'][0]['prediction'] == 'entry-left'
    assert metric['cases'][0]['scores'] == {'entry-left': 100, 'entry-right': 85}
    assert result['robust_interval_agreement']['matches'] == 1


def test_single_assessor_rejects_human_candidate_hash_mismatch(tmp_path):
    manifest, labels = fixture_artifacts(tmp_path)
    labels['labels'][0]['candidate_hashes']['A'] = 'wrong-hash'
    with pytest.raises(ValueError, match='different candidate files'):
        summarize(manifest, tmp_path, labels)
