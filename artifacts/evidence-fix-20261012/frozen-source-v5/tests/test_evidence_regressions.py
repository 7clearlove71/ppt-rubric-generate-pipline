"""Contracts distilled from the 2026-10-12 failures; no human labels in fixtures."""
import copy
from pathlib import Path

import pytest

from ppt_rubric import prompts
from ppt_rubric.common import read_json
from ppt_rubric.evidence_protocol import (
    arithmetic_check, compile_ownership_boundaries, ownership_errors, prepare_evidence_verdict,
    validate_consistency, evaluate_evidence, judgment_evidence,
)
from ppt_rubric.rubric import validate_rubric, normalize_single_source_duplicates
from test_evidence_protocol import suite, observation, decks, SOURCES, Client

FIXTURES = Path(__file__).resolve().parents[1] / 'fixtures/evidence-regressions'


def test_real_case06_compiles_without_fabricating_approval_or_rewriting_original():
    data = read_json(FIXTURES/'case06-final-draft.json')
    original = data['rubric']; before = copy.deepcopy(original)
    assert any('verbatim' in e for e in validate_rubric(original, data['requirements']))
    compiled = compile_ownership_boundaries(original)
    assert validate_rubric(compiled, data['requirements']) == []
    assert original == before and compiled.get('status') != 'approved'
    assert compile_ownership_boundaries(compiled) == compiled
    for a,b in zip(original['items'],compiled['items']):
        assert b['judge_prompt'].startswith(a['judge_prompt'])
    mode = compiled['failure_modes'][0]
    mode['exclusions'] = 'Updated boundary belonging to this same owner.'
    updated = compile_ownership_boundaries(compiled)
    assert 'Updated boundary belonging to this same owner.' in next(i for i in updated['items'] if i['id']==mode['owner'])['judge_prompt']
    assert ownership_errors(updated) == []
    mode['owner'] = 'missing'
    assert any('existing owner' in e for e in ownership_errors(compile_ownership_boundaries(compiled)))


def test_duplicate_normalization_cannot_delete_a_failure_mode_owner():
    rubric = suite()
    duplicate = copy.deepcopy(rubric['items'][0]); duplicate['id']='R5'
    rubric['items'].append(duplicate)
    rubric['failure_modes'].append({'id':'other','owner':'R5','definition':'other condition','exclusions':'separate meaning'})
    normalized, _ = normalize_single_source_duplicates(rubric)
    assert 'R5' in {i['id'] for i in normalized['items']}


def calculation(op, left, right, reported, lu, ru, ou):
    def q(v,u): return v+('' if u=='1' else u)
    spec = dict(operation=op, left=left, right=right, reported=reported,
                left_unit=lu,right_unit=ru,reported_unit=ou,meaning='Explicit displayed relation',
                left_quote=q(left,lu),right_quote=q(right,ru),reported_quote=q(reported,ou))
    spec['relation_quote'] = f"{spec['left_quote']} 与 {spec['right_quote']} 计算为 {spec['reported_quote']}"
    return spec


@pytest.mark.parametrize('args,expected',[
    (('ratio_percent','90','320','28.1','人','人','%'), True),
    (('growth_percent','410','320','28.1','人','人','%'), True),
    (('percent_of','4.2','14','5880','亿元','%','万元'), True),
    (('ratio_percent','1','3','33','1','1','%'), True),
    (('percent_of','4.2','14','5800','亿元','%','万元'), False),
])
def test_realistic_quantities_and_rounding(args,expected):
    spec=calculation(*args)
    assert arithmetic_check(spec,spec['relation_quote'])['matches_reported_precision'] is expected


@pytest.mark.parametrize('problem',['wrong_unit','trimmed_percent','missing_anchor','wrong_relation','zero_denominator'])
def test_extraction_uncertainty_cannot_become_a_defect(problem):
    spec=calculation('ratio_percent','90','320','28.1','人','人','%')
    if problem=='wrong_unit': spec['reported_unit']='1'
    if problem=='trimmed_percent':
        spec['reported_unit']='1'; spec['reported_quote']='28.1'
    if problem=='missing_anchor': spec['left_quote']='900人'
    if problem=='wrong_relation': spec['operation']='excess_over_baseline_percent'
    if problem=='zero_denominator':
        spec=calculation('ratio_percent','90','0','28.1','人','人','%')
    assert arithmetic_check(spec,spec['relation_quote'])['status']=='unverified'


def test_bad_real09_formula_never_reenters_judge_as_verified_math():
    data=read_json(FIXTURES/'case09-wrong-extractions.json')
    assert data['observations']
    for row in data['observations']:
        clean=copy.deepcopy(row)
        clean['arithmetic_checks']=[arithmetic_check(c,row['quote']) for c in row['calculations']]
        assert all(c['status']=='unverified' for c in clean['arithmetic_checks'])
        assert judgment_evidence(clean)['calculations']==[]
        assert row['calculations']  # Diagnostic raw data survives.


def test_missed_visual_defect_can_be_registered_without_stealing_another_owner():
    r=suite(); modes={m['id']:m for m in r['failure_modes']}
    row={**observation('visual_readability'),'page':1,'quote':'金色亿字与绿色同比文字叠印',
         'location':'右上数字单位与同比行','statement':'单位与同比重叠，影响清晰阅读','kind':'visual'}
    raw={'answer':'no','pages':[],'evidence':'右上角单位与同比重叠。','evidence_ids':[],
         'resolutions':[],'additional_observations':[row]}
    v=prepare_evidence_verdict(raw,2,r['items'][4],[],modes,SOURCES)
    assert len(v['failure_ids'])==1 and v['failure_ids']==v['evidence_ids']
    assert v['registered_evidence'][0]['failure_mode']=='visual_readability'
    assert raw['resolutions']==[]  # Client validation is side-effect free.
    neutral={**row,'id':'p1_e1','failure_mode':None}
    assert prepare_evidence_verdict(raw,2,r['items'][4],[neutral],modes,SOURCES)['failure_ids']==['p1_e1']
    stolen={**neutral,'failure_mode':'reasoning_validity'}
    with pytest.raises(ValueError,match='reassign'):
        prepare_evidence_verdict(raw,2,r['items'][4],[stolen],modes,SOURCES)
    with pytest.raises(ValueError,match='belong'):
        prepare_evidence_verdict(raw,2,r['items'][1],[],modes,SOURCES)


@pytest.mark.parametrize('failure',['page','initial','review'])
def test_infrastructure_failures_do_not_propagate_to_other_candidate(tmp_path,failure):
    class Failing(Client):
        def ask(self,role,system,payload,images=(),**kw):
            is_a = images and Path(images[0][1]).name.startswith('A')
            if ((failure=='page' and system==prompts.EVIDENCE_PAGE and is_a)
                or (failure=='initial' and system==prompts.EVIDENCE_JUDGE and is_a)
                or (failure=='review' and system==prompts.CONSISTENCY_REVIEW)):
                raise TimeoutError('deliberate infrastructure outage')
            return super().ask(role,system,payload,images,**kw)
    reports=evaluate_evidence({'id':'01'},suite(),{'api_concurrency':1,'judge_model':'test'},Failing(),decks(),SOURCES,tmp_path)
    assert reports['B']['summary']['score']==100
    assert reports['A']['summary']['score']==(100 if failure=='review' else None)
    assert reports['B']['consistency_summary']['status']=='incomplete'
    assert reports['B']['consistency_summary']['unavailable' if failure=='review' else 'skipped']==5


def test_real_equivalent_yes_verdicts_cannot_be_falsely_described_as_opposite():
    data=read_json(FIXTURES/'case09-equivalent-verdicts.json')
    assert {v['answer'] for v in data['verdicts'].values()}=={'yes'}
    candidates={c:{'verdict':v} for c,v in data['verdicts'].items()}
    ids={c:set(v['evidence_ids']) for c,v in data['verdicts'].items()}
    validate_consistency({'consistent':True,'issues':[],'notes':['两种解释均为结论先行。']},ids,candidates,'rule')
    issue={'reason':'Equivalent rationale has another wording','rule_quote':'rule',
           'evidence_refs':{c:list(x)[:1] for c,x in ids.items()},
           'observed_answers':dict.fromkeys(candidates,'yes'),
           'verdict_quotes':{c:v['evidence'][:30] for c,v in data['verdicts'].items()},
           'expected_answers':{'A':'yes'}}
    with pytest.raises(ValueError,match='advisory'):
        validate_consistency({'consistent':False,'issues':[issue]},ids,candidates,'rule')
    issue['expected_answers']={'A':'no'}; issue['observed_answers']['B']='no'
    with pytest.raises(ValueError,match='misstates'):
        validate_consistency({'consistent':False,'issues':[issue]},ids,candidates,'rule')


def test_direct_visual_record_survives_bounded_rejudge_and_review(tmp_path):
    class Direct(Client):
        def ask(self,role,system,payload,images=(),**kw):
            visual=any(m['id']=='visual_readability' for m in payload.get('owned_failure_modes',[]))
            is_a=images and Path(images[0][1]).name.startswith('A')
            if system==prompts.EVIDENCE_JUDGE and visual and is_a:
                result={'answer':'no','pages':[1],'evidence':'Observed overlapping labels',
                        'evidence_ids':['p1_e1'],'resolutions':[]}
                if payload.get('consistency_feedback'):
                    records=[r for r in payload['ledger'] if r.get('failure_mode_origin')=='direct_judge_observation']
                    assert len(records)==1
                    result.update(answer='yes',evidence='Close spacing, readable without an overlap')
                    result['resolutions']=[{'evidence_id':records[0]['id'],'decision':'reject','reason':'Image confirms close spacing only'}]
                else:
                    result['additional_observations']=[{**observation('visual_readability'),'page':1,'quote':'overlapping labels'}]
                return result
            if system==prompts.CONSISTENCY_REVIEW and visual:
                v=payload['candidates']['A']['verdict']; rows=payload['candidates']['A']['evidence']
                direct=[r for r in rows if r.get('failure_mode_origin')=='direct_judge_observation']
                assert len(direct)==1
                if v['answer']=='no':
                    return {'consistent':False,'issues':[{'reason':'Possible close spacing misread','rule_quote':payload['criterion'],
                        'evidence_refs':{'A':[direct[0]['id']]},'observed_answers':{'A':'no'},
                        'verdict_quotes':{'A':v['evidence']},'expected_answers':{'A':'yes'}}]}
                return {'consistent':True,'issues':[]}
            return super().ask(role,system,payload,images,**kw)
    reports=evaluate_evidence({'id':'01'},suite(),{'api_concurrency':1,'judge_model':'test'},Direct(),decks(),SOURCES,tmp_path)
    v=reports['A']['verdicts'][4]
    assert v['answer']=='yes' and len(v['registered_evidence'])==1
    assert reports['A']['consistency_summary']['status']=='passed'


def test_profile_mismatch_fails_before_models_or_rendering(tmp_path):
    from ppt_rubric.common import load_config, validate_operation_profile
    from ppt_rubric.operations import plan_run
    from ppt_rubric.pipeline import generate, evaluate
    root=Path(__file__).resolve().parents[1]
    config=load_config(root/'configs/evidence-v1.json')
    assert config['generator_model']=='kimi-k3'
    assert config['max_source_refinements']==1
    assert config['role_request_options']['reviser']['enable_thinking'] is True
    judging=load_config(root/'configs/evidence-v1-judge.json')
    validate_operation_profile(config,'generate');validate_operation_profile(judging,'judge')
    for call in (lambda:plan_run([],config,'judge'),
                 lambda:generate({},judging,None,tmp_path,tmp_path),
                 lambda:evaluate({},suite(),config,None,tmp_path,tmp_path)):
        with pytest.raises(ValueError,match='profile'):call()


def test_two_direct_judges_cannot_charge_same_neutral_anchor_twice():
    from ppt_rubric.evidence_protocol import duplicate_confirmations
    base={**observation(),'page':1,'id':'p1_e1'}
    verdicts={rid:{'failure_ids':['p1_e1'],'registered_evidence':[{**base,'failure_mode':mode}]}
              for rid,mode in [('R1','reasoning_validity'),('R2','unsupported_fact')]}
    conflicts=duplicate_confirmations(verdicts,[base])
    assert conflicts[0]['rubric_ids']==['R1','R2']
    verdicts['R2']['failure_ids']=[]
    assert duplicate_confirmations(verdicts,[base])==[]


def test_real_resolution_id_alias_is_losslessly_normalized_before_new_evidence():
    r=suite();modes={m['id']:m for m in r['failure_modes']}
    ledger=[{**observation('visual_readability'),'id':'p1_e1','page':1}]
    raw={'answer':'no','pages':[1],'evidence':'Confirmed overlap','evidence_ids':['p1_e1'],
         'resolutions':[{'id':'p1_e1','decision':'confirm','reason':'Confirmed in image'}]}
    result=prepare_evidence_verdict(raw,2,r['items'][4],ledger,modes,SOURCES)
    assert result['resolutions'][0]['evidence_id']=='p1_e1'
    assert result['failure_ids']==['p1_e1']
    assert 'evidence_id' not in raw['resolutions'][0]
    raw['resolutions'][0]['evidence_id']='another'
    with pytest.raises(ValueError,match='alias'):
        prepare_evidence_verdict(raw,2,r['items'][4],ledger,modes,SOURCES)
    raw['resolutions']=[]
    raw['additional_observations']=[{**observation('visual_readability'),'page':2,'quote':'new overlap'}]
    with pytest.raises(ValueError) as error:
        prepare_evidence_verdict(raw,2,r['items'][4],ledger,modes,SOURCES)
    assert 'p1_e1' in str(error.value) and '_direct_' not in str(error.value)
