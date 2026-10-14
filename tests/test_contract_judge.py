import copy
from threading import Barrier

import pytest

from ppt_rubric.common import digest, read_json
from ppt_rubric.contract_judge import (
    CONTENT_PROMPT, VISUAL_PROMPT, aggregate_visual, combine_assessments,
    document_id, evaluate_contract, page_catalog, validate_verdicts,
    validate_visual_batch, validate_inputs,
)
from ppt_rubric.contract_v2 import compile_item, VALIDATOR_VERSION
from ppt_rubric.source_catalog import source_catalog


SOURCES = [{'id': 'prompt', 'text': 'Present the supplied facts accurately.\nState uncertainty.'}]


def rubric(case_id='01'):
    result = {'status': 'approved', 'evaluation_protocol': 'contract_v2', 'case_id': case_id,
            'provenance': {'source_hash': digest(SOURCES), 'case_id': case_id, 'validator_version': VALIDATOR_VERSION},
            'source_catalog_hash': digest(source_catalog(SOURCES)),
            'dimension_weights': {'fulfillment': 40, 'correctness': 30, 'integrity': 0, 'narrative': 0, 'visual': 30},
            'items': [
        {'id': 'C1', 'dimension': 'fulfillment', 'score': 40, 'judge_prompt': 'The requested conclusion exists.',
         'scope': 'whole document', 'scope_type': 'document', 'evidence_mode': 'text'},
        {'id': 'C2', 'dimension': 'correctness', 'score': 30, 'judge_prompt': 'The conclusion is accurate.',
         'scope': 'whole document', 'scope_type': 'document', 'evidence_mode': 'both'},
        {'id': 'V_READ', 'dimension': 'visual', 'score': 20, 'judge_prompt': 'Objects and labels are readable.',
         'scope': 'each page', 'scope_type': 'page', 'evidence_mode': 'visual'},
        {'id': 'V_LAYOUT', 'dimension': 'visual', 'score': 10, 'judge_prompt': 'Readable objects have useful hierarchy.',
         'scope': 'each page', 'scope_type': 'page', 'evidence_mode': 'visual'}]}
    for item in result['items']:
        item.update(objective=item['judge_prompt'], applicability='The relevant content is applicable.',
                    pass_condition=item['judge_prompt'], exclusions='Do not count an unrelated defect.', source_ids=[])
        item['judge_prompt'] = compile_item(item)
    result['signature'] = digest(result['provenance'])
    result['rubric_hash'] = digest(result)
    return result


def decks(count=4):
    return {cid: {'identity': {'sha256': digest('bytes-' + cid)}, 'slides': [
        {'page': p, 'image': f'/test/{cid}-{p}.png', 'text': f'{cid} content {p}', 'rendered_text': f'{cid} render {p}'}
        for p in range(1, count + 1)]} for cid in ('A', 'B')}


def case_manifest(*candidate_ids, case_id='01'):
    return {'id': case_id, 'candidates': [{'id': cid, 'sha256': digest('bytes-' + cid)}
                                        for cid in candidate_ids or ('A', 'B')]}


def verdict(rid, answer='yes', pages=None, key='actual-defect'):
    return {'rubric_id': rid, 'answer': answer, 'pages': pages if pages is not None else [1],
            'evidence': 'Examined the supplied original image and criterion.',
            'defect': {'key': key, 'description': 'A localized criterion violation.'} if answer == 'no' else None}


class Client:
    def __init__(self, answers=None, fail_content=False, fail_pages=(), visual_fail_page=None, model='model', duplicate=False):
        self.config = {'judge_model': model, 'max_images_per_request': 6}
        self.calls = []
        self.answers = answers or {}
        self.fail_content = fail_content
        self.fail_pages = set(fail_pages)
        self.visual_fail_page = visual_fail_page
        self.duplicate = duplicate

    def ask(self, role, system, payload, images, **kwargs):
        self.calls.append({'role': role, 'system': system, 'payload': copy.deepcopy(payload), 'images': list(images)})
        if system == CONTENT_PROMPT:
            if self.fail_content:
                raise TimeoutError('failed test request')
            return {'verdicts': [verdict(item['id'], self.answers.get(item['id'], 'yes'),
                                key='same-object' if self.duplicate else item['id'] + '-object')
                                 for item in payload['criteria']]}
        assert system == VISUAL_PROMPT
        if self.fail_pages & {p['page'] for p in payload['pages']}:
            raise TimeoutError('failed visual batch')
        return {'pages': [{'page': p['page'], 'verdicts': [verdict(item['id'],
            'no' if p['page'] == self.visual_fail_page and item['id'] == 'V_READ' else self.answers.get(item['id'], 'yes'),
            pages=[p['page']], key=f"page-{p['page']}-physical-label") for item in payload['criteria']]}
            for p in payload['pages']]}


def config():
    return {'judge_model': 'primary', 'max_images_per_request': 6, 'max_visual_focus_images': 3}


def test_contract_requires_complete_criterion_coverage_and_real_page_localization():
    items = rubric()['items'][:2]
    validate_verdicts({'verdicts': [verdict('C1'), verdict('C2')]}, items, {1, 2})
    with pytest.raises(ValueError, match='exactly once'):
        validate_verdicts({'verdicts': [verdict('C1'), verdict('C1')]}, items, {1, 2})
    with pytest.raises(ValueError, match='original page'):
        validate_verdicts({'verdicts': [verdict('C1', pages=[99]), verdict('C2')]}, items, {1, 2})
    with pytest.raises(ValueError, match='supporting pages'):
        validate_verdicts({'verdicts': [verdict('C1', pages=[]), verdict('C2')]}, items, {1, 2})


def test_document_absence_requires_explicit_complete_search_but_not_fake_page():
    row = verdict('C1', 'no', pages=[])
    with pytest.raises(ValueError, match='absence_checked'):
        validate_verdicts({'verdicts': [row]}, rubric()['items'][:1], {1, 2})
    row['absence_checked'] = True
    row['evidence'] = 'Searched every original page; the requested conclusion is absent.'
    validate_verdicts({'verdicts': [row]}, rubric()['items'][:1], {1, 2})


def test_optional_source_ids_resolve_supplied_text_without_transcription():
    catalog = source_catalog(SOURCES)
    row = verdict('C1')
    row['source_ids'] = [catalog[0]['id']]
    validate_verdicts({'verdicts': [row]}, rubric()['items'][:1], {1}, catalog)
    row['source_ids'] = ['invented-id']
    with pytest.raises(ValueError):
        validate_verdicts({'verdicts': [row]}, rubric()['items'][:1], {1}, catalog)


def test_page_local_contract_cannot_cite_or_skip_another_page():
    items = rubric()['items'][2:]
    response = {'pages': [{'page': 1, 'verdicts': [verdict('V_READ'), verdict('V_LAYOUT')]}]}
    validate_visual_batch(response, items, [1])
    with pytest.raises(ValueError, match='every supplied original page'):
        validate_visual_batch(response, items, [1, 2])
    response['pages'][0]['verdicts'][0]['pages'] = [2]
    with pytest.raises(ValueError):
        validate_visual_batch(response, items, [1])


def test_visual_local_failure_survives_other_unknown_page():
    item = rubric()['items'][2]
    pages = [{'page': 1, 'verdicts': [verdict('V_READ', 'no', [1])]},
             {'page': 2, 'verdicts': [verdict('V_READ', 'unknown', [2])]}]
    result = aggregate_visual([item], pages)[0]
    assert result['answer'] == 'no'
    assert result['pages'] == [1]
    assert result['page_verdicts'][1]['answer'] == 'unknown'


def test_document_namespaces_are_label_independent_and_no_cross_candidate_payload(tmp_path):
    ds = decks()
    client = Client()
    reports = evaluate_contract(case_manifest(), rubric(), config(), client, ds, SOURCES, tmp_path)
    assert all(r['summary']['score'] == 100 for r in reports.values())
    for call in client.calls:
        payload = call['payload']
        assert 'candidates' not in payload and 'candidate_id' not in payload and 'verdicts' not in payload
        assert 'score' not in str(payload['criteria'])
        assert all(page['page_id'].startswith(payload['document_id'] + '/') for page in payload['pages'])
        if call['system'] == CONTENT_PROMPT:
            own = 'A' if payload['document_id'] == document_id(ds['A']) else 'B'
            other = 'B' if own == 'A' else 'A'
            assert all(page['extracted_text'].startswith(own) for page in payload['pages'])
            assert all(f'/{other}-' not in path for _, path in call['images'])
        else:
            assert len(payload['pages']) <= 3
    relabeled = {'candidate-renamed': ds['A']}
    assert page_catalog(relabeled['candidate-renamed']) == page_catalog(ds['A'])
    stored = read_json(tmp_path / '01' / 'A.json')
    assert stored['evaluation_protocol'] == 'contract_v2'
    assert stored['candidate_hash'] == ds['A']['identity']['sha256']
    assert stored['summary'] == reports['A']['summary']


def test_failed_auxiliary_geometry_never_blocks_real_image_judgment(tmp_path, monkeypatch):
    def fail_geometry(deck):
        raise RuntimeError('optional geometry unavailable')
    monkeypatch.setattr('ppt_rubric.contract_judge.rendered_text_geometry', fail_geometry)
    result = evaluate_contract(case_manifest('A'), rubric(), config(), Client(), {'A': decks()['A']}, SOURCES, tmp_path)['A']
    assert result['summary']['score'] == 100
    assert result['primary']['auxiliary_errors'][0]['stage'] == 'geometry'


def test_content_and_visual_infrastructure_failures_are_isolated(tmp_path):
    first = Client(fail_content=True)
    result = evaluate_contract(case_manifest('A'), rubric(), config(), first, {'A': decks()['A']}, SOURCES, tmp_path)['A']
    rows = {row['rubric_id']: row for row in result['verdicts']}
    assert rows['C1']['answer'] == 'unknown'
    assert rows['V_READ']['answer'] == 'yes'
    assert result['summary']['score_lower'] == 30
    assert result['summary']['score_upper'] == 100
    assert result['summary']['score'] is None
    second = Client(fail_pages={4}, visual_fail_page=1)
    result = evaluate_contract(case_manifest('A', case_id='02'), rubric('02'), config(), second, {'A': decks()['A']}, SOURCES, tmp_path)['A']
    rows = {row['rubric_id']: row for row in result['verdicts']}
    assert rows['C1']['answer'] == 'yes'
    assert rows['V_READ']['answer'] == 'no'
    assert rows['V_LAYOUT']['answer'] == 'unknown'
    assert result['primary']['visual_pages'][-1]['verdicts'][0]['answer'] == 'unknown'


def test_second_model_is_blind_and_disagreement_remains_interval(tmp_path):
    primary, secondary = Client(model='first'), Client(answers={'C2': 'no'}, model='second')
    result = evaluate_contract(case_manifest('A'), rubric(), config(), primary, {'A': decks()['A']}, SOURCES,
                               tmp_path, verification_client=secondary)['A']
    assert primary.calls == secondary.calls
    assert result['primary']['summary']['score'] == 100
    assert result['verification']['summary']['score'] == 70
    assert result['summary']['score'] is None
    assert (result['summary']['score_lower'], result['summary']['score_upper']) == (70, 100)
    rows = {row['rubric_id']: row for row in result['verdicts']}
    assert rows['C2']['assessment_status'] == 'independent_disagreement'
    assert rows['C1']['assessment_status'] == 'independent_agreement'
    assert len(primary.calls) == len(secondary.calls) == 3  # No debate/repair call.


def test_shared_defect_key_flags_conflict_without_changing_raw_answers(tmp_path):
    client = Client(answers={'C1': 'no', 'C2': 'no'}, duplicate=True)
    result = evaluate_contract(case_manifest('A'), rubric(), config(), client, {'A': decks()['A']}, SOURCES, tmp_path)['A']
    assert result['primary']['summary']['score'] == 30
    assert [v['answer'] for v in result['primary']['verdicts'][:2]] == ['no', 'no']
    assert [v['answer'] for v in result['verdicts'][:2]] == ['unknown', 'unknown']
    assert result['summary']['unresolved_weight'] == 70
    assert result['ownership_conflicts'] == [{'assessor': 'primary', 'defect_key': 'same-object', 'rubric_ids': ['C1', 'C2']}]


def test_content_only_verification_does_not_claim_visual_consensus(tmp_path):
    primary, secondary = Client(), Client()
    result = evaluate_contract(case_manifest('A'), rubric(), {**config(), 'verification_content_only': True},
        primary, {'A': decks()['A']}, SOURCES, tmp_path, verification_client=secondary)['A']
    assert len(secondary.calls) == 1 and secondary.calls[0]['system'] == CONTENT_PROMPT
    assert result['summary']['score'] == 100
    assert result['verification']['summary']['score'] is None
    assert result['consensus']['independently_evaluated_items'] == ['C1', 'C2']
    rows = {row['rubric_id']: row for row in result['verdicts']}
    assert rows['V_READ']['assessment_status'] == 'not_independently_verified'
    assert rows['V_READ']['verification_answer'] is None


def test_source_mismatch_rejected_before_api_calls(tmp_path):
    client = Client()
    with pytest.raises(ValueError, match='source hash'):
        evaluate_contract(case_manifest(), rubric(), config(), client, decks(),
                          [{'id': 'prompt', 'text': 'Different task'}], tmp_path)
    assert client.calls == []


@pytest.mark.parametrize('batch_size,expected', [(1, [1, 1, 1, 1]), (2, [2, 2]), (3, [3, 1])])
def test_visual_batch_size_is_the_configured_number(tmp_path, batch_size, expected):
    client = Client()
    evaluate_contract(case_manifest('A'), rubric(), {**config(), 'visual_batch_size': batch_size},
                      client, {'A': decks()['A']}, SOURCES, tmp_path)
    assert [len(c['payload']['pages']) for c in client.calls if c['system'] == VISUAL_PROMPT] == expected


@pytest.mark.parametrize('batch_size', [0, 4, True, 1.5, '2'])
def test_invalid_visual_batch_size_fails_before_api_calls(tmp_path, batch_size):
    client = Client()
    with pytest.raises(ValueError, match='visual_batch_size'):
        evaluate_contract(case_manifest('A'), rubric(), {**config(), 'visual_batch_size': batch_size},
                          client, {'A': decks()['A']}, SOURCES, tmp_path)
    assert client.calls == []


def test_visual_crops_fit_the_remaining_provider_image_capacity(tmp_path, monkeypatch):
    limits = []

    def many_views(deck, geometry, settings):
        limits.append(settings['max_visual_focus_images'])
        page = deck['slides'][0]['page']
        # Even a crop producer returning too many images cannot exceed capacity.
        return [{'id': f'crop_{i}', 'page': page, 'rect': [0, 0, 10, 10], 'image': f'/test/crop_{i}.png'}
                for i in range(6)]

    monkeypatch.setattr('ppt_rubric.contract_judge.focus_views', many_views)
    client = Client()
    client.config['max_images_per_request'] = 3
    evaluate_contract(case_manifest('A'), rubric(), {**config(), 'visual_batch_size': 2},
                      client, {'A': decks(3)['A']}, SOURCES, tmp_path)
    calls = [call for call in client.calls if call['system'] == VISUAL_PROMPT]
    assert limits == [1, 2]
    assert [len(call['images']) for call in calls] == [3, 3]
    assert all(label.startswith(call['payload']['document_id']) for call in calls for label, _ in call['images'])


def test_independent_clients_execute_concurrently_without_sharing_responses(tmp_path):
    started_together = Barrier(2)

    class ParallelClient(Client):
        def ask(self, role, system, payload, images, **kwargs):
            if system == CONTENT_PROMPT:
                started_together.wait(timeout=2)
            return super().ask(role, system, payload, images, **kwargs)

    primary, secondary = ParallelClient(), ParallelClient(model='independent')
    result = evaluate_contract(case_manifest('A'), rubric(), {**config(), 'verification_content_only': True},
                               primary, {'A': decks()['A']}, SOURCES, tmp_path, secondary)['A']
    assert result['summary']['score'] == 100
    assert primary.calls[0]['payload'] == secondary.calls[0]['payload']
    assert all('primary' not in call['payload'] and 'verification' not in call['payload'] for call in secondary.calls)


@pytest.mark.parametrize('field,value,expected', [
    ('status', 'draft', 'approved'),
    ('case_id', 'another-case', 'case binding'),
    ('source_catalog_hash', 'wrong-hash', 'catalog'),
    ('signature', 'wrong-signature', 'signature'),
])
def test_approved_rubric_metadata_is_checked_even_if_outer_hash_is_recomputed(tmp_path, field, value, expected):
    suite = rubric()
    suite[field] = value
    suite['rubric_hash'] = digest({k: v for k, v in suite.items() if k != 'rubric_hash'})
    client = Client()
    with pytest.raises(ValueError, match=expected):
        evaluate_contract(case_manifest(), suite, config(), client, decks(), SOURCES, tmp_path)
    assert client.calls == []


@pytest.mark.parametrize('mutation,expected', [
    ('changed_score', 'budgets'), ('changed_prompt', 'canonical'), ('missing_field', 'canonical'),
    ('duplicate_id', 'unique criteria'), ('unknown_source', 'source'),
])
def test_criterion_integrity_cannot_be_bypassed_with_a_recomputed_hash(tmp_path, mutation, expected):
    suite = rubric()
    if mutation == 'changed_score':
        suite['items'][0]['score'] = 39
    elif mutation == 'changed_prompt':
        suite['items'][0]['judge_prompt'] += '\nExtra criterion added after approval.'
    elif mutation == 'missing_field':
        del suite['items'][0]['pass_condition']
    elif mutation == 'duplicate_id':
        suite['items'][1]['id'] = suite['items'][0]['id']
    elif mutation == 'unknown_source':
        suite['items'][0]['source_ids'] = ['not-a-source']
    suite['rubric_hash'] = digest({k: v for k, v in suite.items() if k != 'rubric_hash'})
    client = Client()
    with pytest.raises(ValueError, match=expected):
        evaluate_contract(case_manifest(), suite, config(), client, decks(), SOURCES, tmp_path)
    assert client.calls == []


def test_rubric_content_hash_and_audit_version_are_not_optional(tmp_path):
    client = Client()
    altered = rubric()
    altered['rubric_hash'] = 'not-the-content-hash'
    with pytest.raises(ValueError, match='changed after approval'):
        evaluate_contract(case_manifest(), altered, config(), client, decks(), SOURCES, tmp_path)
    obsolete = rubric()
    obsolete['provenance']['validator_version'] = -1
    obsolete['signature'] = digest(obsolete['provenance'])
    obsolete['rubric_hash'] = digest({k: v for k, v in obsolete.items() if k != 'rubric_hash'})
    with pytest.raises(ValueError, match='current contract audit'):
        evaluate_contract(case_manifest(), obsolete, config(), client, decks(), SOURCES, tmp_path)
    assert client.calls == []


def test_deck_candidate_hash_bindings_and_candidate_coverage_are_exact(tmp_path):
    client = Client()
    swapped = decks()
    swapped['A'], swapped['B'] = swapped['B'], swapped['A']
    with pytest.raises(ValueError, match='candidate hash'):
        evaluate_contract(case_manifest(), rubric(), config(), client, swapped, SOURCES, tmp_path)
    with pytest.raises(ValueError, match='exactly the case candidates'):
        evaluate_contract(case_manifest(), rubric(), config(), client, {'A': decks()['A']}, SOURCES, tmp_path)
    assert client.calls == []


def test_same_client_cannot_be_used_as_its_own_independent_verification(tmp_path):
    client = Client()
    with pytest.raises(ValueError, match='separate independently configured client'):
        evaluate_contract(case_manifest(), rubric(), config(), client, decks(), SOURCES, tmp_path, client)
    assert client.calls == []
