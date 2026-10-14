import copy
import json
from pathlib import Path

import pytest

from ppt_rubric import prompts
from ppt_rubric.common import digest, read_json, write_json
from ppt_rubric.evidence_protocol import (ownership_errors, arithmetic_check, validate_page,
    collect_evidence, validate_evidence_verdict, validate_consistency, evaluate_evidence)
from ppt_rubric.rubric import assign_scores, VALIDATOR_VERSION


SOURCES = [{'id': 'prompt', 'text': 'task'}]


def suite():
    dims = ['fulfillment', 'correctness', 'integrity', 'narrative', 'visual']
    r = {'evaluation_protocol': 'evidence_v1',
         'groups': [{'id': f'G{i}', 'dimension': d, 'importance': 1, 'rationale': 'task'} for i, d in enumerate(dims)],
         'items': [{'id': f'R{i}', 'group_id': f'G{i}', 'importance': 1, 'source_refs': ['Q1'] if i == 0 else [],
                    'basis': 'explicit' if i == 0 else 'quality', 'scope': 'whole deck', 'evidence_mode': 'both',
                    'judge_prompt': f'Observable condition number {i}. '} for i in range(5)], 'failure_modes': []}
    for mid, owner in [('task_missing', 0), ('reasoning_validity', 1), ('unsupported_fact', 2),
                       ('narrative_gap', 3), ('visual_readability', 4), ('chart_label_integrity', 4), ('visual_hierarchy', 4)]:
        definition = f'Definition for {mid}. '
        exclusions = f'Exclude defects outside {mid}. '
        r['failure_modes'].append({'id': mid, 'owner': f'R{owner}', 'definition': definition, 'exclusions': exclusions})
        r['items'][owner]['judge_prompt'] += definition + exclusions
    assign_scores(r, dict.fromkeys(dims, 20))
    r.update(status='approved', case_id='01', dimension_weights=dict.fromkeys(dims, 20),
             provenance={'validator_version': VALIDATOR_VERSION, 'source_hash': digest(SOURCES)})
    r['rubric_hash'] = digest(r)
    return r


def observation(mode=None, calculations=None):
    return {'quote': 'visible claim', 'location': 'center', 'statement': 'Visible source-grounded observation',
            'kind': 'fact', 'failure_mode': mode, 'source_refs': [{'source_id': 'prompt', 'quote': 'task'}],
            'quantities': [], 'calculations': calculations or []}


def decks():
    return {c: {'identity': {'sha256': c}, 'slides': [
        {'page': p, 'image': f'{c}-{p}.png'} for p in (1, 2)]} for c in 'AB'}


def test_ownership_rejects_ambiguous_missing_and_unexportable_boundaries():
    r = suite(); assert ownership_errors(r) == []
    duplicate = copy.deepcopy(r); duplicate['failure_modes'].append(duplicate['failure_modes'][0])
    assert ownership_errors(duplicate)
    missing = copy.deepcopy(r); missing['failure_modes'].pop(1)
    assert ownership_errors(missing)
    broken = copy.deepcopy(r); broken['items'][4]['judge_prompt'] = 'Generic readable condition'
    assert any('verbatim' in e for e in ownership_errors(broken))
    bad_owner = copy.deepcopy(r); bad_owner['failure_modes'][0]['owner'] = ['R0','R1']
    assert ownership_errors(bad_owner)


@pytest.mark.parametrize('operation,left,right,reported,expected', [
    ('divide','120','6.6','18.2',True),
    ('multiply','0.1','0.2','0.02',True),
    ('excess_over_baseline_percent','129','29','29',True),
    ('excess_over_baseline_percent','129','29','37.41',False),
    ('divide','1','0','0',None),
])
def test_arithmetic_recomputes_relations_not_just_multiplication(operation,left,right,reported,expected):
    spec = dict(operation=operation,left=left,right=right,reported=reported,meaning='explicit baseline',
                left_unit='1',right_unit='%' if operation=='excess_over_baseline_percent' else '1',reported_unit='1',
                left_quote=left,right_quote=right+'%' if operation=='excess_over_baseline_percent' else right,
                reported_quote=reported,relation_quote=(f'{left}比基准高{right}% 得{reported}'
                    if operation=='excess_over_baseline_percent' else f'{left} 与 {right} 计算为 {reported}'))
    check = arithmetic_check(spec, spec['relation_quote'])
    assert check['matches_reported_precision'] is expected


def test_evidence_source_grounding_and_duplicate_defect_anchor():
    modes = {m['id']: m for m in suite()['failure_modes']}
    page = {'page': 2, 'complete': True, 'observations': [observation('chart_label_integrity')]}
    validate_page(page, 2, SOURCES, modes)
    page['observations'].append(observation('visual_readability'))
    with pytest.raises(ValueError, match='anchor'): validate_page(page, 2, SOURCES, modes)
    page['observations'] = [observation()]
    page['observations'][0]['source_refs'][0]['quote'] = 'invented source'
    with pytest.raises(ValueError, match='verbatim'): validate_page(page, 2, SOURCES, modes)


def test_untyped_calculation_is_retained_but_never_reassigns_defect_ownership():
    calc = dict(operation='excess_over_baseline_percent', left='129', right='29', reported='37.41', meaning='29% above baseline')
    class Client:
        def ask(self, *args, **kwargs):
            return {'page': 1, 'complete': True, 'observations': [observation(calculations=[calc])]}
    d = decks()['A']; d['slides'] = d['slides'][:1]
    result = collect_evidence(d, suite(), SOURCES, Client())
    assert result['complete']
    r = result['ledger'][0]
    assert r['failure_mode'] is None and r['failure_mode_origin'] == 'model_observation'
    assert r['calculations'] == [calc]
    assert r['arithmetic_checks'][0]['status'] == 'unverified'
    assert r['arithmetic_checks'][0]['matches_reported_precision'] is None


def test_cannot_charge_another_criterion_for_the_same_defect():
    rubric = suite(); modes = {m['id']:m for m in rubric['failure_modes']}
    ledger = [{**observation('chart_label_integrity'), 'id':'p1_e1','page':1}]
    result = {'answer':'no','pages':[1],'evidence':'NaN chart label', 'evidence_ids':['p1_e1'],
              'failure_ids':['p1_e1'], 'resolutions':[{'evidence_id':'p1_e1','decision':'confirm','reason':'Label missing'}]}
    validate_evidence_verdict(result, 2, rubric['items'][4], ledger, modes)
    with pytest.raises(ValueError, match='owned'):
        validate_evidence_verdict(result, 2, rubric['items'][0], ledger, modes)
    result['answer'] = 'yes'
    with pytest.raises(ValueError, match='cannot be ignored'):
        validate_evidence_verdict(result, 2, rubric['items'][4], ledger, modes)


class Client:
    def __init__(self, conflict=False, persistent=False, page_error=False):
        self.conflict, self.persistent, self.page_error = conflict, persistent, page_error
        self.calls=[]; self.review_count={}
    def ask(self, role, system, payload, images=(), **kwargs):
        self.calls.append((system,payload,len(images)))
        if system == prompts.EVIDENCE_PAGE:
            assert len(images) == 1
            if self.page_error: raise TimeoutError('simulated')
            cid = Path(images[0][1]).name[0]
            mode = 'unsupported_fact' if self.conflict and cid == 'A' and payload['page'] == 1 else None
            return {'page':payload['page'],'complete':True,'observations':[observation(mode)]}
        if system == prompts.EVIDENCE_JUDGE:
            refs = payload['required_resolution_ids']
            confirm = bool(payload.get('consistency_feedback'))
            return {'answer':'no' if confirm else 'yes','pages':[1], 'evidence':'Localized independent assessment',
                    'evidence_ids':['p1_e1'], 'failure_ids':refs if confirm else [],
                    'resolutions':[{'evidence_id':i,'decision':'confirm' if confirm else 'reject','reason':'Verified exception'} for i in refs],
                    'score':999}  # Must never be forwarded to consistency reviewer.
        assert system == prompts.CONSISTENCY_REVIEW and role == 'reviewer'
        assert not images
        assert set(payload) == {'sources','criterion','scope','owned_failure_modes','candidates'}
        assert all('score' not in c['verdict'] for c in payload['candidates'].values())
        rid = payload['owned_failure_modes'][0]['owner']
        self.review_count[rid] = self.review_count.get(rid,0)+1
        fail = self.conflict and rid == 'R2' and (self.persistent or self.review_count[rid] == 1)
        verdict = payload['candidates']['A']['verdict']
        return {'consistent':not fail, 'issues':[{'reason':'An evidenced exception changes the outcome',
                'rule_quote':payload['criterion'], 'evidence_refs':{'A':['p1_e1']},
                'observed_answers':{'A':verdict['answer']}, 'verdict_quotes':{'A':verdict['evidence']},
                'expected_answers':{'A':'no' if verdict['answer']=='yes' else 'yes'}}] if fail else []}


@pytest.mark.parametrize('conflict,persistent,page_error', [(False,False,False),(True,False,False),(True,True,False),(False,False,True)])
def test_full_protocol_blind_bounded_and_fail_closed(tmp_path,conflict,persistent,page_error):
    client = Client(conflict,persistent,page_error)
    reports = evaluate_evidence({'id':'01'},suite(),{'api_concurrency':1,'judge_model':'test'},client,decks(),SOURCES,tmp_path)
    if page_error:
        assert len(client.calls) == 4
    elif conflict:
        assert len(client.calls) == 21  # One extra rejudgment and one re-audit, never an infinite loop.
    else:
        assert len(client.calls) == 19
    if page_error:
        assert all(r['summary']['score'] is None for r in reports.values())
    elif persistent:
        assert reports['A']['summary']['score'] is None
        assert reports['B']['summary']['score'] == 100
    elif conflict:
        assert reports['A']['summary']['score'] == 80
        assert reports['B']['summary']['score'] == 100  # Not forced equal to A.
        assert read_json(tmp_path/'01/A-initial.json')['R2']['answer'] == 'yes'
        assert reports['A']['verdicts'][2]['answer'] == 'no'
    else:
        assert all(r['summary']['score'] == 100 for r in reports.values())
    assert (tmp_path/'01/consistency.json').exists()


def test_consistency_cannot_cite_unprovided_evidence_or_hide_conflicts():
    with pytest.raises(ValueError):
        validate_consistency({'consistent':False,'issues':[]}, {'A':{'p1_e1'}})
    with pytest.raises(ValueError):
        validate_consistency({'consistent':False,'issues':[{'reason':'claim','evidence_refs':{'A':['missing']}}]}, {'A':{'p1_e1'}})


def test_plan_accounts_for_evidence_consistency_and_one_repair(tmp_path,monkeypatch):
    from ppt_rubric.operations import plan_run
    r=suite();write_json(tmp_path/'01/rubric.json',r)
    write_json(tmp_path/'01/requirements.json',{'requirements':[{'id':'Q1','quote':'task'}]})
    monkeypatch.setattr('ppt_rubric.operations.file_digest',lambda p:'hash')
    monkeypatch.setattr('ppt_rubric.operations.extract_pptx',lambda p:[{'hidden':False}]*2)
    case={'id':'01','split':'dev','prompt':'task','candidates':[{'id':c,'pptx':'unused','sha256':'hash'} for c in 'AB']}
    config={'evaluation_protocol':'evidence_v1','max_case_text_chars':1000,'max_images_per_request':60,'max_retries':3}
    result=plan_run([case],config,'judge',tmp_path)
    assert result['ready_cases']==1, result
    assert result['cases'][0]['visual_inspection_calls_upper_bound']==4
    assert result['totals']['first_pass_logical_calls']==23
    assert result['totals']['max_logical_calls']==38
    assert result['totals']['max_http_attempts']==114
    config['max_visual_inspections']=0
    assert plan_run([case],config,'judge',tmp_path)['totals']['max_http_attempts']==102


def test_missing_ownership_requires_real_repair_before_semantic_approval(tmp_path):
    from ppt_rubric.pipeline import review_and_repair
    r=suite();r.pop('failure_modes')
    class Repair:
        calls=[]
        def ask(self, role, system, payload, **kwargs):
            self.calls.append(system)
            assert prompts.OWNERSHIP_POLICY in system
            if system.startswith(prompts.REVISE): return suite()
            if system.startswith(prompts.REVIEW): return {'approved':True,'issues':[],'coverage':{'Q1':['R0']}}
            return {'approved':True,'issues':[]}
    client=Repair()
    config={'evaluation_protocol':'evidence_v1','max_revisions':1,'dimension_weights':suite()['dimension_weights']}
    context={'sources':SOURCES,'requirements':{'requirements':[{'id':'Q1','quote':'task'}]}}
    result=review_and_repair({'id':'01'},config,client,tmp_path,context,r,{'validator_version':VALIDATOR_VERSION})
    assert result['status']=='approved'
    assert client.calls[0].startswith(prompts.REVISE)
    assert read_json(tmp_path/'reviews.json')[0]['review']['skipped']


def test_pipeline_dispatch_and_protocol_mismatch(tmp_path, monkeypatch):
    from ppt_rubric import pipeline
    monkeypatch.setattr(pipeline, 'prepare_case', lambda *a: decks())
    monkeypatch.setattr(pipeline, 'case_sources', lambda *a: SOURCES)
    cfg = {'evaluation_protocol':'evidence_v1','api_concurrency':1,'judge_model':'test'}
    r = pipeline.evaluate({'id':'01'}, suite(), cfg, Client(), tmp_path, tmp_path)
    assert r['A']['evaluation_protocol'] == 'evidence_v1'
    assert r['A']['summary']['score'] == 100
    assert all('score' not in v for v in r['A']['verdicts'])
    with pytest.raises(ValueError, match='protocol differ'):
        pipeline.evaluate({'id':'01'}, suite(), {**cfg,'evaluation_protocol':'legacy'}, Client(), tmp_path, tmp_path)


def test_failed_new_run_never_leaves_previous_complete_scores_visible(tmp_path):
    write_json(tmp_path/'01/A.json', {'summary': {'status':'complete','score':100}})
    reports = evaluate_evidence({'id':'01'}, suite(), {'api_concurrency':1,'judge_model':'test'},
                               Client(page_error=True), decks(), SOURCES, tmp_path)
    assert reports['A']['summary']['score'] is None
    assert read_json(tmp_path/'01/A.json')['summary']['score'] is None
    archived = list((tmp_path/'01').glob('A-superseded-*.json'))
    assert len(archived) == 1 and read_json(archived[0])['summary']['score'] == 100


def test_mathematical_flag_requires_explicit_resolution():
    rubric = suite(); modes = {m['id']:m for m in rubric['failure_modes']}
    ledger = [{**observation('reasoning_validity'),'page':1,'id':'p1_e1'}]
    result = {'answer':'yes','pages':[1],'evidence':'Looks fine','evidence_ids':['p1_e1'],
              'failure_ids':[], 'resolutions':[]}
    with pytest.raises(ValueError, match='Resolve'):
        validate_evidence_verdict(result, 1, rubric['items'][1], ledger, modes)
    result['resolutions'] = [{'evidence_id':'p1_e1','decision':'reject','reason':'Operand was transcribed incorrectly; quoted value differs'}]
    validate_evidence_verdict(result, 1, rubric['items'][1], ledger, modes)


def test_bad_failure_mode_identifier_is_a_repairable_error_not_a_crash():
    r = suite(); r['failure_modes'][0]['id'] = []
    assert ownership_errors(r)
