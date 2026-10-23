"""Single-K3 orchestration, direct scoring, and retired-workflow guards."""
import copy
import json

import pytest

from ppt_rubric.common import digest, read_json
from ppt_rubric.v25 import contract_judge as judge


class K3Client:
    def __init__(self, config, answers=None):
        self.config = config
        self.answers = answers or {}
        self.calls = []

    def ask_text(self, role, system, payload, images, validate):
        self.calls.append((role, system, copy.deepcopy(payload), list(images)))
        if 'criteria' in payload:
            page = payload['pages'][0]['page']
            raw = json.dumps({'verdicts': [{'rubric_id': c['id'], 'answer': self.answers.get((c['id'], page), 'yes'),
                'evidence': 'Independent fixture evidence for ' + c['id']} for c in payload['criteria']]})
            validate(raw)
            return raw
        rid = payload['criterion']['id']
        page = payload['pages'][0]['page'] if payload['evaluation_scope'] == 'page' else None
        answer = self.answers.get((rid, page), 'yes')
        raw = f'Visible fixture evidence for {rid}.\\boxed{{{answer}}}'
        validate(raw)
        return raw


@pytest.fixture
def isolated_evaluation(monkeypatch):
    # Contract validation has separate end-to-end coverage. Here a small suite
    # makes call counts and unresolved weight easy to inspect independently.
    monkeypatch.setattr(judge, 'validate_inputs', lambda *args: None)
    monkeypatch.setattr(judge, 'rendered_text_geometry', lambda deck: [])
    monkeypatch.setattr(judge, 'focus_views', lambda *args: [])
    config = {'judge_model': 'kimi-k3', 'vision_model': 'kimi-k3',
              'scoring_method': 'single_k3'}
    rubric = {'rubric_hash': digest('rubric'), 'items': [
        {'id': 'C1', 'dimension': 'fulfillment', 'score': 50,
         'judge_prompt': 'Is the required conclusion present?'},
        {'id': 'C2', 'dimension': 'correctness', 'score': 30,
         'judge_prompt': 'Is the reported number correct?'},
        {'id': 'V1', 'dimension': 'visual', 'score': 20,
         'judge_prompt': 'Is the text readable?'}]}
    deck = {'identity': {'sha256': digest('candidate')}, 'slides': [
        {'page': page, 'image': f'/fixture/{page}.png', 'rendered_text': f'PDF page {page}'}
        for page in (1, 2)]}
    case = {'id': 'single', 'candidates': [{'id': 'A', 'sha256': deck['identity']['sha256']}]}
    return case, rubric, config, {'A': deck}, [{'id': 'prompt', 'text': 'User task'}]


def test_single_k3_excludes_unknown_and_keeps_original_assessment(isolated_evaluation, tmp_path):
    case, rubric, config, decks, sources = isolated_evaluation
    client = K3Client(config, {('C2', None): 'unknown', ('V1', 1): 'no', ('V1', 2): 'unknown'})
    report = judge.evaluate_contract(case, rubric, config, client, decks, sources, tmp_path)['A']

    assert len(client.calls) == 4  # C + P, with no second content pass.
    assert [row['answer'] for row in report['verdicts']] == ['yes', 'unknown', 'no']
    assert report['summary']['score_lower'] == 50
    assert report['summary']['score_upper'] == 50
    assert report['summary']['unresolved_weight'] == 0
    assert report['summary']['score'] == 50
    assert report['comparison']['score_possible'] == 70
    assert report['comparison']['excluded_rubric_ids'] == ['C2']
    # V1 remains no after page aggregation, despite the unknown on page 2.
    assert report['assessment']['summary']['score_upper'] == 80
    assert report['assessment']['summary']['unresolved_weight'] == 30
    assert report['verdicts'] == report['assessment']['verdicts']
    assert report['scoring_method'] == judge.SCORING_METHOD == 'single_k3'
    assert report['judge_validator_version'] == judge.VALIDATOR_VERSION == 10
    assert not {'primary', 'verification', 'consensus', 'verification_model'} & set(report)
    assert all('verification_answer' not in row and 'primary_answer' not in row
               for row in report['verdicts'])
    checkpoint = tmp_path / case['id'] / 'A-assessment.json'
    assert json.loads(checkpoint.read_text()) == report['assessment']
    assert not (checkpoint.parent / 'A-primary.json').exists()
    assert report['judge_prompt_fingerprint'] == digest([judge.PIPELINE_VERSION, judge.prompt_bundle()])
    former_bundle = judge.prompt_bundle()
    former_bundle.pop('scoring_method')
    assert report['judge_prompt_fingerprint'] != digest([judge.PIPELINE_VERSION, former_bundle])


def test_cohort_excludes_content_and_visual_unknown_for_every_candidate(isolated_evaluation, tmp_path):
    case, rubric, config, decks, sources = isolated_evaluation
    for cid, count in [('B', 3), ('C', 1)]:
        deck = copy.deepcopy(decks['A'])
        deck['identity']['sha256'] = digest(cid)
        deck['slides'] = [{'page': p, 'image': f'/fixture/{cid}/{p}.png', 'rendered_text': 'PDF'}
                          for p in range(1, count + 1)]
        decks[cid] = deck
        case['candidates'].append({'id': cid, 'sha256': digest(cid)})
    class CohortClient(K3Client):
        def ask_text(self, role, system, payload, images, validate):
            # A has already finished when B starts with candidate_workers=1.
            if payload['document_id'] == judge.document_id(decks['B']):
                assert not (tmp_path / case['id'] / 'A.json').exists()
            self.answers = ({('C2', None): 'unknown'} if payload['document_id'] == judge.document_id(decks['A'])
                            else {('V1', 3): 'unknown'} if payload['document_id'] == judge.document_id(decks['B'])
                            else {('C2', None): 'no', ('V1', 1): 'no'})
            return super().ask_text(role, system, payload, images, validate)
    client = CohortClient(config)
    reports = judge.evaluate_contract(case, rubric, config, client, decks, sources, tmp_path)
    assert len(client.calls) == 3 * 2 + 6  # Original C + P calls only.
    assert all(r['summary']['score'] == 50 for r in reports.values())
    assert all(r['comparison']['excluded_rubric_ids'] == ['C2', 'V1'] for r in reports.values())
    assert all(r['comparison']['score_possible'] == 50 for r in reports.values())
    assert reports['C']['assessment']['summary']['score'] == 50
    assert reports['C']['verdicts'][1]['answer'] == reports['C']['verdicts'][2]['answer'] == 'no'
    for cid, report in reports.items():
        assert read_json(tmp_path / case['id'] / f'{cid}.json') == report
    mapping = read_json(tmp_path / case['id'] / 'candidate-mapping.json')['candidates']
    assert all(row['score'] == 50 and row['score_possible'] == 50 for row in mapping)
    assert all(row['excluded_rubric_ids'] == ['C2', 'V1'] for row in mapping)


def test_execution_unknown_is_excluded_without_hiding_failed_request(isolated_evaluation, tmp_path):
    case, rubric, config, decks, sources = isolated_evaluation
    class UnavailableClient(K3Client):
        def ask_text(self, role, system, payload, images, validate):
            if payload.get('criterion', {}).get('id') == 'C2':
                raise RuntimeError('Offline unavailable request')
            return super().ask_text(role, system, payload, images, validate)
    report = judge.evaluate_contract(case, rubric, config, UnavailableClient(config), decks, sources, tmp_path)['A']
    assert report['comparison']['excluded_rubric_ids'] == ['C2']
    assert report['summary']['score'] == report['comparison']['score_possible'] == 70
    assert report['assessment']['summary']['unresolved_weight'] == 30
    assert report['execution']['status'] == 'incomplete'
    assert report['execution']['unavailable_requests'] == 1
    assert report['verdicts'][1]['answer'] == 'unknown'


def test_candidate_mapping_is_exported_without_sending_identity_labels_to_judge(isolated_evaluation, tmp_path):
    case, rubric, config, decks, sources = isolated_evaluation
    case['candidates'][0].update(pptx='/submissions/producer-secret/result.pptx', display_name='Secret producer XYZ')
    client = K3Client(config)
    report = judge.evaluate_contract(case, rubric, config, client, decks, sources, tmp_path)['A']
    mapping = read_json(tmp_path / case['id'] / 'candidate-mapping.json')['candidates'][0]
    assert mapping['candidate_key'] == 'single/A'
    assert mapping['display_name'] == 'Secret producer XYZ' and mapping['page_count'] == 2
    assert mapping['score'] == 100 and mapping['sha256'] == case['candidates'][0]['sha256']
    assert report['candidate_identity']['input_pptx'] == case['candidates'][0]['pptx']
    transmitted = json.dumps(client.calls)
    assert 'producer-secret' not in transmitted and 'Secret producer XYZ' not in transmitted


@pytest.mark.parametrize('same_client', [False, True])
def test_verification_client_is_rejected_before_validation_or_provider_work(
        isolated_evaluation, tmp_path, monkeypatch, same_client):
    case, rubric, config, decks, sources = isolated_evaluation
    client = K3Client(config)
    second = client if same_client else K3Client(config)
    monkeypatch.setattr(judge, 'validate_inputs', lambda *args: pytest.fail('validation was reached'))
    with pytest.raises(ValueError, match='verification_client is no longer supported'):
        judge.evaluate_contract(case, rubric, config, client, decks, sources, tmp_path,
                                verification_client=second)
    assert client.calls == second.calls == []
    assert not (tmp_path / case['id']).exists()


@pytest.mark.parametrize('wrong_role', ['judge_model', 'vision_model'])
def test_actual_client_profile_must_use_k3(isolated_evaluation, tmp_path, wrong_role):
    case, rubric, config, decks, sources = isolated_evaluation
    client = K3Client({**config, wrong_role: 'kimi-k2.6'})
    with pytest.raises(ValueError, match='requires kimi-k3'):
        judge.evaluate_contract(case, rubric, config, client, decks, sources, tmp_path)
    assert client.calls == []


def test_retired_scoring_method_rejected_before_provider_work(isolated_evaluation, tmp_path):
    case, rubric, config, decks, sources = isolated_evaluation
    config['scoring_method'] = 'independent_consensus'
    client = K3Client(config)
    with pytest.raises(ValueError, match='scoring_method=single_k3'):
        judge.evaluate_contract(case, rubric, config, client, decks, sources, tmp_path)
    assert client.calls == []


def test_old_dual_report_is_preserved_without_relabeling(isolated_evaluation, tmp_path):
    case, rubric, config, decks, sources = isolated_evaluation
    output = tmp_path / case['id']
    output.mkdir()
    legacy = {'primary': {'answer': 'yes'}, 'verification': {'answer': 'no'},
              'consensus': {'answer': 'unknown'}, 'judge_validator_version': 8}
    (output / 'A.json').write_text(json.dumps(legacy))
    report = judge.evaluate_contract(case, rubric, config, K3Client(config), decks, sources, tmp_path)['A']
    preserved = list(output.glob('A-superseded-*.json'))
    assert len(preserved) == 1
    assert json.loads(preserved[0].read_text()) == legacy
    assert json.loads((output / 'A.json').read_text()) == report
    assert report['scoring_method'] == 'single_k3'


@pytest.mark.parametrize('flag', ['--verification-config', '--source-audit-config'])
def test_judge_cli_rejects_auditor_flags_before_creating_client(tmp_path, monkeypatch, capsys, flag, plan_manifest):
    from pathlib import Path
    from ppt_rubric.v25 import contract_v2 as contract

    root = Path(__file__).resolve().parents[1]
    monkeypatch.setattr(contract, 'Client', lambda *args, **kwargs: pytest.fail('API client constructed'))
    with pytest.raises(SystemExit) as error:
        contract.main(['judge', '--manifest', str(plan_manifest),
                       '--config', str(root / 'configs/contract-v25-judge.json'),
                       flag, str(root / 'configs/contract-v25-auditor.json'),
                       '--cases', '01', '--rubrics', str(tmp_path / 'rubrics'),
                       '--out', str(tmp_path / 'out')])
    assert error.value.code == 2
    assert 'candidate scoring uses one K3 model' in capsys.readouterr().err


@pytest.mark.parametrize('profile', ['old_verifier', 'wrong_model'])
def test_judge_cli_rejects_retired_and_non_k3_profiles_before_creating_client(
        tmp_path, monkeypatch, capsys, profile, plan_manifest):
    from pathlib import Path
    from ppt_rubric.v25 import contract_v2 as contract

    root = Path(__file__).resolve().parents[1]
    config_path = root / 'configs/contract-v25-verifier.json'
    expected = 'Independent candidate verification has been removed'
    if profile == 'wrong_model':
        config = json.loads((root / 'configs/contract-v25-judge.json').read_text())
        config['judge_model'] = 'kimi-k2.6'
        config_path = tmp_path / 'wrong-model.json'
        config_path.write_text(json.dumps(config))
        expected = 'both require kimi-k3'
    monkeypatch.setattr(contract, 'Client', lambda *args, **kwargs: pytest.fail('API client constructed'))
    with pytest.raises(SystemExit) as error:
        contract.main(['judge', '--manifest', str(plan_manifest),
                       '--config', str(config_path), '--cases', '01',
                       '--rubrics', str(tmp_path / 'rubrics'), '--out', str(tmp_path / 'out')])
    assert error.value.code == 2
    assert expected in capsys.readouterr().err


def test_offline_plan_uses_single_model_call_formula(tmp_path, monkeypatch, plan_manifest):
    from pathlib import Path
    from ppt_rubric.v25 import contract_v2 as contract

    root = Path(__file__).resolve().parents[1]
    monkeypatch.setattr(contract, 'Client', lambda *args, **kwargs: pytest.fail('API client constructed'))
    output = tmp_path / 'plan.json'
    contract.main(['plan', '--manifest', str(plan_manifest),
                   '--config', str(root / 'configs/contract-v25-k3.json'),
                   '--source-audit-config', str(root / 'configs/contract-v25-auditor.json'),
                   '--cases', '01', '--out', str(output)])
    plan = json.loads(output.read_text())
    assert plan['offline'] is True
    assert plan['scoring_method'] == 'single_k3'
    assert plan['judge_models'] == {'content': 'kimi-k3', 'visual': 'kimi-k3'}
    assert plan['judge_logical_calls_per_document_formula'] == 'C + P (if V > 0; otherwise C)'
    assert plan['fixed_content_criteria'] == len(contract.QUALITY_ITEMS)
    assert plan['fixed_visual_criteria'] == len(contract.VISUAL_ITEMS)
    assert not any(key.startswith('verification_') for key in plan)


def test_all_five_visual_criteria_share_page_images_and_independent_results(
        isolated_evaluation, tmp_path, monkeypatch):
    case, rubric, config, decks, sources = isolated_evaluation
    visual_ids = ['V_READ', 'V_LABEL', 'V_RESIDUE', 'V_LAYOUT', 'V_ENCODING']
    rubric['items'] = rubric['items'][:2] + [
        {'id': rid, 'dimension': 'visual', 'score': 4, 'judge_prompt': 'Check ' + rid}
        for rid in visual_ids]
    views = []
    def crops(deck, geometry, cfg):
        page = deck['slides'][0]['page']
        views.append(page)
        return [{'page': page, 'id': 'zoom', 'rect': [0, 0, 100, 100], 'image': f'/fixture/crop-{page}.png'}]
    monkeypatch.setattr(judge, 'focus_views', crops)
    client = K3Client(config, {('V_READ', 1): 'no', ('V_READ', 2): 'unknown',
                               ('V_LABEL', 2): 'unknown'})
    report = judge.evaluate_contract(case, rubric, config, client, decks, sources, tmp_path)['A']
    content = [c for c in client.calls if c[2]['evaluation_scope'] == 'document']
    visual = [c for c in client.calls if c[2]['evaluation_scope'] == 'page']
    assert len(content) == len(visual) == 2  # C + P, rather than C + 5P.
    assert sorted(views) == [1, 2]
    for _, system, payload, images in visual:
        page = payload['pages'][0]['page']
        assert [c['id'] for c in payload['criteria']] == visual_ids
        assert 'criterion' not in payload
        assert [path for _, path in images] == [f'/fixture/{page}.png', f'/fixture/crop-{page}.png']
        assert all(rid in system for rid in visual_ids)
    answers = {v['rubric_id']: v['answer'] for v in report['verdicts']}
    assert answers == {'C1': 'yes', 'C2': 'yes', 'V_READ': 'no', 'V_LABEL': 'unknown',
                       'V_RESIDUE': 'yes', 'V_LAYOUT': 'yes', 'V_ENCODING': 'yes'}
    assert report['comparison']['excluded_rubric_ids'] == ['V_LABEL']
    assert report['summary']['score'] == 92 and report['comparison']['score_possible'] == 96
    assert report['execution']['model_unknown_requests'] == 1
    for page in report['assessment']['visual_pages']:
        assert [v['evidence'] for v in page['verdicts']] == ['Independent fixture evidence for ' + rid for rid in visual_ids]
    assert len(report['assessment']['visual_batches']) == 2


@pytest.mark.parametrize('malformation', ['missing', 'duplicate', 'extra', 'empty_reason', 'invalid_answer'])
def test_invalid_visual_batch_never_publishes_partial_answers(isolated_evaluation, tmp_path, malformation):
    case, rubric, config, decks, sources = isolated_evaluation
    rubric['items'][-1]['score'] = 10
    rubric['items'] += [{'id': 'V2', 'dimension': 'visual', 'score': 10, 'judge_prompt': 'Another visual point?'}]
    class BadBatch(K3Client):
        def ask_text(self, role, system, payload, images, validate):
            if 'criteria' not in payload:
                return super().ask_text(role, system, payload, images, validate)
            rows = [{'rubric_id': c['id'], 'answer': 'yes', 'evidence': 'Independent evidence'} for c in payload['criteria']]
            if malformation == 'missing': rows.pop()
            if malformation == 'duplicate': rows[-1] = rows[0]
            if malformation == 'extra': rows.append({'rubric_id': 'unexpected', 'answer': 'no', 'evidence': 'Evidence'})
            if malformation == 'empty_reason': rows[-1]['evidence'] = ' '
            if malformation == 'invalid_answer': rows[-1]['answer'] = 'maybe'
            # Even alternate transports that skip validate cannot publish partial results.
            return json.dumps({'verdicts': rows})
    report = judge.evaluate_contract(case, rubric, config, BadBatch(config), decks, sources, tmp_path)['A']
    assert report['execution']['unavailable_requests'] == 2  # Requests, not ten criterion failures.
    assert report['execution']['status'] == 'incomplete'
    assert all(v['answer'] == 'unknown' and v['unknown_origin'] == 'execution_error'
               for page in report['assessment']['visual_pages'] for v in page['verdicts'])
    assert all(r['status'] == 'unavailable' and 'raw' in r for r in report['assessment']['visual_batches'])
