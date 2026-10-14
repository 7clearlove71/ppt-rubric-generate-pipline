"""Contracts distilled from the 2026-10-12 failures; no human labels in fixtures."""
import copy
from pathlib import Path

import pytest

from ppt_rubric import prompts
from ppt_rubric.common import read_json
from ppt_rubric.evidence_protocol import (
    arithmetic_check, compile_ownership_boundaries, ownership_errors, prepare_evidence_verdict,
    validate_consistency, evaluate_evidence, judgment_evidence,
    supplied_quote,
    validate_page,
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


def test_rendered_geometry_preserves_positions_but_makes_no_visibility_verdict(tmp_path):
    import fitz
    from ppt_rubric.render import rendered_text_geometry
    doc=fitz.open();page=doc.new_page(width=300,height=200)
    page.insert_text((20,40),'42',fontsize=20,color=(1,0,0))
    page.insert_text((20,90),'unit',fontsize=20)
    path=tmp_path/'slide.pdf';doc.save(path);doc.close()
    geometry=rendered_text_geometry({'pdf':str(path),'slides':[{'page':1}]})
    spans=geometry[0]['spans']
    assert [s['text'] for s in spans]==['42','unit']
    assert spans[0]['bbox'][3] < spans[1]['bbox'][1]
    assert spans[0]['color']=='#ff0000'
    assert all('failure_mode' not in s for s in spans)


@pytest.mark.parametrize('present,answer,decision',[(False,'yes','reject'),(True,'no','confirm'),(None,'unknown','uncertain')])
def test_explicit_defect_boolean_has_no_confirm_means_correct_ambiguity(present,answer,decision):
    r=suite();modes={m['id']:m for m in r['failure_modes']}
    ledger=[{**observation('reasoning_validity'),'id':'p1_e1','page':1}]
    raw={'answer':answer,'pages':[1],'evidence':'Checked against the slide', 'evidence_ids':['p1_e1'],
         'resolutions':[{'evidence_id':'p1_e1','defect_present':present,'reason':'A localized finding'}]}
    result=prepare_evidence_verdict(raw,2,r['items'][1],ledger,modes,SOURCES)
    assert result['resolutions'][0]['decision']==decision
    assert result['failure_ids']==(['p1_e1'] if present else [])
    raw['resolutions'][0]['decision']='confirm' if decision!='confirm' else 'reject'
    with pytest.raises(ValueError,match='contradicts'):
        prepare_evidence_verdict(raw,2,r['items'][1],ledger,modes,SOURCES)


def test_focus_regions_exclude_separate_text_and_keep_original_image_budget(tmp_path):
    import fitz
    from ppt_rubric.render import rendered_text_geometry
    from ppt_rubric.visual_regions import focus_images
    doc=fitz.open();page=doc.new_page(width=300,height=200)
    page.insert_text((30,45),'top',fontsize=20)
    page.insert_text((30,100),'unit',fontsize=20,color=(1,0,0))
    page.insert_text((36,104),'overlap',fontsize=20,color=(0,1,0))
    path=tmp_path/'page.pdf';doc.save(path);doc.close()
    deck={'pdf':str(path),'slides':[{'page':1,'image':'original.png'}]}
    geometry=rendered_text_geometry(deck)
    regions=geometry[0]['text_overlap_candidates']
    assert len(regions)==1 and set(regions[0]['texts'])=={'unit','overlap'}
    assert focus_images(deck,geometry,{'max_images_per_request':1})==[]
    images=focus_images(deck,geometry,{'max_images_per_request':2})
    assert len(images)==1 and 'Slide 1 local enlarged view' in images[0][0]
    assert Path(images[0][1]).exists()
    assert focus_images(deck,geometry,{'max_images_per_request':2})==images
    assert deck['slides'][0]['image']=='original.png'


def test_neutral_inspection_never_receives_expected_text_ledger_or_rubric(monkeypatch):
    from ppt_rubric.visual_regions import collect_visual_inspections
    views=[{'id':f'v{i}', 'page':i, 'rect':[1,2,3,4], 'label':'expected text must stay out',
            'image':f'crop{i}.png'} for i in range(1,4)]
    monkeypatch.setattr('ppt_rubric.visual_regions.focus_views', lambda *a:views)
    class Neutral:
        calls=[]
        def ask(self, role, system, payload, images, validate):
            self.calls.append(images)
            assert payload=={} and images==[('Image',f'crop{len(self.calls)}.png')]
            assert system==prompts.VISUAL_INSPECTION
            if len(self.calls)==2: raise TimeoutError('Unavailable supplemental view')
            result={'text_seen':['unit','caption'],'overlapping':True,
                    'overlap_objects':['unit and caption intersect'],'description':'Green over gold'}
            validate(result)
            return result
    client=Neutral()
    result=collect_visual_inspections({},[],client,{'max_visual_inspections':2})
    assert len(client.calls)==2 and [r['status'] for r in result]==['observed','unavailable']
    assert result[0]['page']==1 and result[1]['error_type']=='TimeoutError'
    assert all('failure_mode' not in r and 'answer' not in r for r in result)


def test_inspection_must_be_resolved_but_cannot_automatically_set_score():
    r=suite();modes={m['id']:m for m in r['failure_modes']}
    ledger=[{**observation(),'id':'p1_e1','page':1}]
    inspections=[{'id':'crop1','status':'observed','page':1,
                  'rect':[1,2,3,4], 'observation':{'overlapping':True,'description':'Caption overlaps unit'}}, {'id':'crop2','status':'unavailable'}]
    raw={'answer':'yes','pages':[1],'evidence':'Criterion allows decorative intersections',
         'evidence_ids':['p1_e1'],'resolutions':[]}
    check=lambda v:prepare_evidence_verdict(v,2,r['items'][4],ledger,modes,SOURCES,inspections)
    with pytest.raises(ValueError,match='inspection exactly once'):check(raw)
    raw['inspection_resolutions']=[{'inspection_id':'crop1','relation_confirmed':True,
        'defect_present':False,'reason':'Image shows only decorative strokes, no text hidden'}]
    assert check(raw)['answer']=='yes'  # Observer is not an automatic no.
    raw['inspection_resolutions'][0]['defect_present']=None
    with pytest.raises(ValueError,match='unresolved'):check(raw)
    raw['inspection_resolutions'][0]['defect_present']=True
    with pytest.raises(ValueError,match='registered defect'):check(raw)
    raw['answer']='no'
    assert check(raw)['failure_ids']==['crop1']  # Explicit judge confirmation compiles its own ID.
    raw['inspection_resolutions'][0]['relation_confirmed']=False
    with pytest.raises(ValueError,match='spatial relation'):check(raw)
    raw['inspection_resolutions'][0]['relation_confirmed']=True
    raw['additional_observations']=[{**observation('visual_readability'),'kind':'visual','page':1,
                                   'quote':'Actual text overlaps caption'}]
    result=check(raw)
    assert len(result['failure_ids'])==2 and result['registered_evidence'][0]['page']==1


def test_visual_inspection_integration_is_shared_and_confined_to_readability(monkeypatch,tmp_path):
    from ppt_rubric.common import read_json
    calls=[]
    def inspect(deck, geometry, client, config):
        calls.append(deck['identity']['sha256'])
        return [{'id':'crop1','status':'observed','page':1,'image':'internal.png',
                 'observation':{'overlapping':True}}]
    monkeypatch.setattr('ppt_rubric.evidence_protocol.collect_visual_inspections',inspect)
    class Integrated(Client):
        def ask(self,role,system,payload,images=(),**kwargs):
            result=super().ask(role,system,payload,images,**kwargs)
            if system==prompts.EVIDENCE_JUDGE:
                assert len(images)==2  # All original pages still included.
                if payload['owned_failure_modes'][0]['owner']=='R4':
                    assert 'image' not in payload['visual_inspections'][0]
                    result['inspection_resolutions']=[{'inspection_id':'crop1','relation_confirmed':False,
                        'defect_present':False,'reason':'Visible image disproves alleged intersection'}]
                else:assert 'visual_inspections' not in payload
            return result
    reports=evaluate_evidence({'id':'01'},suite(),{'api_concurrency':1,'judge_model':'test'},
                              Integrated(),decks(),SOURCES,tmp_path)
    assert calls==['A','B']
    assert all(r['summary']['score']==100 for r in reports.values())
    assert read_json(tmp_path/'01/A-evidence.json')['visual_inspections'][0]['image']=='internal.png'
    assert reports['A']['verdicts'][4]['inspection_resolutions'][0]['defect_present'] is False


def test_real_crop_ids_are_rejected_with_actionable_empty_inspection_feedback():
    raw = read_json(FIXTURES/'case01-crops-as-inspections.json')
    before = copy.deepcopy(raw)
    r = suite(); modes = {m['id']: m for m in r['failure_modes']}
    with pytest.raises(ValueError, match=r'Set inspection_resolutions=\[\]') as error:
        prepare_evidence_verdict(raw, 13, r['items'][4], [], modes, SOURCES)
    assert 'Crop IDs and text_overlap_candidates are not independent inspections' in str(error.value)
    assert raw == before  # Reject the response; never strip fields to manufacture success.


def test_non_readability_visual_payload_explicitly_has_no_independent_inspections(monkeypatch,tmp_path):
    r = suite()
    item = copy.deepcopy(r['items'][4]); item['id'] = 'R5'
    r['items'].append(item)
    for mode in r['failure_modes']:
        if mode['id'] in {'visual_hierarchy', 'chart_label_integrity'}:
            mode['owner'] = 'R5'
    monkeypatch.setattr('ppt_rubric.evidence_protocol.collect_visual_inspections',
                        lambda *args: [{'id':'crop1','status':'observed','page':1,
                                        'observation':{'overlapping':True}}])
    seen = []
    class ExplicitContract(Client):
        def ask(self,role,system,payload,images=(),**kwargs):
            result = super().ask(role,system,payload,images,**kwargs)
            if system == prompts.EVIDENCE_JUDGE:
                owner = payload['owned_failure_modes'][0]['owner']
                if owner == 'R4':
                    assert payload['required_inspection_ids'] == ['crop1']
                    result['inspection_resolutions'] = [{'inspection_id':'crop1',
                        'relation_confirmed':True,'defect_present':False,'reason':'Criterion threshold not met'}]
                elif owner == 'R5':
                    seen.append(owner)
                    assert payload['visual_inspections'] == []
                    assert payload['required_inspection_ids'] == []
                    assert 'inspection_resolutions=[]' in payload['inspection_resolution_contract']
                    assert len(images) == 2  # Both original slides remain visible.
            return result
    reports = evaluate_evidence({'id':'01'}, r, {'api_concurrency':1,'judge_model':'test'},
                                ExplicitContract(), decks(), SOURCES, tmp_path)
    assert seen == ['R5','R5']
    assert all(v['answer'] == 'yes' for report in reports.values() for v in report['verdicts'])


def test_review_gets_omitted_page_heading_without_other_owned_defects(tmp_path):
    class Context(Client):
        reviewed = 0
        def ask(self,role,system,payload,images=(),**kwargs):
            result = super().ask(role,system,payload,images,**kwargs)
            if system == prompts.EVIDENCE_PAGE and payload['page'] == 1:
                result['observations'] += [
                    {**observation(), 'quote':'对标项目累计临床植入与注册验证例数',
                     'statement':'Heading identifies external comparator projects'},
                    {**observation('narrative_gap'), 'quote':'Different unrelated defect'}]
            if system == prompts.CONSISTENCY_REVIEW and payload['owned_failure_modes'][0]['owner'] == 'R2':
                self.reviewed += 1
                for candidate in payload['candidates'].values():
                    assert candidate['verdict']['evidence_ids'] == ['p1_e1']
                    refs = {e['id']:e for e in candidate['evidence']}
                    assert refs['p1_e2']['quote'] == '对标项目累计临床植入与注册验证例数'
                    assert 'p1_e3' not in refs  # Other-owner defects are not new premises.
                    assert 'p2_e1' not in refs  # Keep the context localized.
            return result
    client = Context()
    reports = evaluate_evidence({'id':'01'}, suite(), {'api_concurrency':1,'judge_model':'test'},
                                client, decks(), SOURCES, tmp_path)
    assert client.reviewed == 1
    assert all(report['summary']['score'] == 100 for report in reports.values())


def test_visual_judge_cannot_inherit_wrong_object_claim_but_rejudge_keeps_new_evidence():
    from ppt_rubric.evidence_protocol import criterion_ledger
    r=suite();modes={m['id']:m for m in r['failure_modes']}
    wrong={**observation('visual_readability'), 'id':'old','page':1,'kind':'visual',
           'quote':'4.2 covers the unit', 'failure_mode_origin':'model_observation'}
    actual={**wrong,'id':'direct','quote':'Green caption intersects gold unit',
            'failure_mode_origin':'direct_judge_observation'}
    fact={**observation(),'id':'fact','page':1}
    ledger=[wrong,fact,actual];before=copy.deepcopy(ledger)
    selected=criterion_ledger(ledger,r['items'][4],modes)
    assert [x['id'] for x in selected]==['fact','direct']
    assert ledger==before  # Historical diagnosis is preserved verbatim.
    assert criterion_ledger(ledger,r['items'][1],modes)==ledger


def test_real_crop_citations_compile_explicit_model_decisions_without_changing_answer():
    data=read_json(FIXTURES/'case09-region-citations.json')
    raw=data['raw'];before=copy.deepcopy(raw)
    r=suite();modes={m['id']:m for m in r['failure_modes']}
    result=prepare_evidence_verdict(raw,8,r['items'][4],data['regions'],modes,SOURCES,data['inspections'])
    assert raw==before and result['answer']==raw['answer']=='no'
    assert {'p1_overlap_fe92d10a01','p2_overlap_54471eb5c8'} <= set(result['failure_ids'])
    assert all('4.2' not in e['quote'] for e in result['registered_evidence'][:2])
    assert 'p4_overlap_41b880fea8' in result['evidence_ids']
    assert 'p4_overlap_41b880fea8' not in result['failure_ids']  # Geometry is not an automatic defect.
    forged=copy.deepcopy(raw);forged['evidence_ids'].append('invented_region')
    with pytest.raises(ValueError,match='registered evidence'):
        prepare_evidence_verdict(forged,8,r['items'][4],data['regions'],modes,SOURCES,data['inspections'])
    forged=copy.deepcopy(raw);forged['answer']='yes'
    with pytest.raises(ValueError,match='requires no'):
        prepare_evidence_verdict(forged,8,r['items'][4],data['regions'],modes,SOURCES,data['inspections'])


def test_rejudge_cannot_contradict_its_inspection_resolution():
    data=read_json(FIXTURES/'case09-region-citations.json')
    r=suite();modes={m['id']:m for m in r['failure_modes']}
    result=prepare_evidence_verdict(data['raw'],8,r['items'][4],data['regions'],modes,SOURCES,data['inspections'])
    ledger={e['id']:e for e in data['regions']}
    ledger.update({e['id']:e for e in result['registered_evidence']})
    raw={**result,'additional_observations':[]}
    raw['inspection_resolutions']=copy.deepcopy(result['inspection_resolutions'])
    raw['inspection_resolutions'][0]['defect_present']=False
    with pytest.raises(ValueError,match='contradicts'):
        prepare_evidence_verdict(raw,8,r['items'][4],list(ledger.values()),modes,SOURCES,data['inspections'])


def test_supplied_geometry_references_replay_without_rewriting_real_verdict():
    data=read_json(FIXTURES/'case02-provided-geometry-refs.json')
    before=copy.deepcopy(data)
    result=prepare_evidence_verdict(data['raw'],data['page_count'],data['item'],
        data['ledger'],data['modes'],data['sources'],visual_geometry=data['geometry'])
    assert data==before
    assert result['answer']==data['raw']['answer']=='no'
    assert result['evidence']==data['raw']['evidence']
    assert result['inspection_resolutions']==[]
    original_ids={row['id'] for row in data['ledger']}
    missing=set(data['raw']['evidence_ids'])-original_ids
    assert len(missing)==12
    neutral=[row for row in result['registered_evidence'] if row['id'] in missing]
    assert {row['id'] for row in neutral}==missing
    assert all(row['failure_mode'] is None for row in neutral)
    assert not missing.intersection(result['failure_ids'])
    # Structural replay does not endorse the model's ownership or visual claims.
    forged=copy.deepcopy(data['raw']);forged['evidence_ids'].append('invented_region')
    with pytest.raises(ValueError,match='registered evidence'):
        prepare_evidence_verdict(forged,data['page_count'],data['item'],
            data['ledger'],data['modes'],data['sources'],visual_geometry=data['geometry'])
    with pytest.raises(ValueError,match='registered evidence'):
        prepare_evidence_verdict(data['raw'],data['page_count'],data['item'],
            data['ledger'],data['modes'],data['sources'])


def test_geometry_text_span_is_neutral_and_cannot_confirm_itself():
    r=suite();modes={m['id']:m for m in r['failure_modes']}
    geometry=[{'page':1,'spans':[{'id':'p1_t1','text':'A title','bbox':[1,2,30,40]}],
               'text_overlap_candidates':[]}]
    raw={'answer':'yes','pages':[1],'evidence':'The title is readable.',
         'evidence_ids':['p1_t1'],'resolutions':[]}
    result=prepare_evidence_verdict(raw,2,r['items'][4],[],modes,SOURCES,visual_geometry=geometry)
    assert result['failure_ids']==[] and result['registered_evidence'][0]['failure_mode'] is None
    assert result['registered_evidence'][0]['quote']=='A title'
    raw['resolutions']=[{'evidence_id':'p1_t1','defect_present':True,'reason':'The box exists.'}]
    raw['answer']='no'
    with pytest.raises(ValueError,match='New defects belong in additional_observations'):
        prepare_evidence_verdict(raw,2,r['items'][4],[],modes,SOURCES,visual_geometry=geometry)


def test_missing_observation_arrays_remain_invalid_with_actionable_field_path():
    r=suite();modes={m['id']:m for m in r['failure_modes']}
    row={**observation('visual_readability'),'page':1}
    del row['source_refs']
    raw={'answer':'no','pages':[],'evidence':'Unreadable text.','evidence_ids':[],
         'resolutions':[],'additional_observations':[row], 'source_refs':[]}
    with pytest.raises(ValueError,match=r'additional_observations\[0\].*inside that observation'):
        prepare_evidence_verdict(raw,2,r['items'][4],[],modes,SOURCES)


def test_real_consistency_quote_typography_does_not_rewrite_model_decisions():
    data=read_json(FIXTURES/'case02-citation-typography.json')
    before=copy.deepcopy(data)
    ids={cid:{row['id'] for row in candidate['evidence']} for cid,candidate in data['candidates'].items()}
    assert data['raw']['issues'][0]['rule_quote'] not in data['criterion']
    validate_consistency(data['raw'],ids,data['candidates'],data['criterion'])
    assert data==before
    forged=copy.deepcopy(data['raw'])
    forged['issues'][0]['rule_quote']=forged['issues'][0]['rule_quote'].replace('不强制要求','强制要求')
    with pytest.raises(ValueError,match='actual criterion'):
        validate_consistency(forged,ids,data['candidates'],data['criterion'])
    forged=copy.deepcopy(data['raw'])
    forged['issues'][0]['verdict_quotes']['A']=forged['issues'][0]['verdict_quotes']['A'].replace('三类风险','四类风险')
    with pytest.raises(ValueError,match='not supplied reasoning'):
        validate_consistency(forged,ids,data['candidates'],data['criterion'])


@pytest.mark.parametrize('quote,text,expected',[
    ('a "quoted" term','a “quoted” term',True),
    ("a 'term'",'a ‘term’',True),
    ('A  title\nwraps','A title wraps',True),
    ('存疑的地方标出来，别下定论','存疑的地方标出来,别下定论',True),
    ('0，45','0.45',False),
    ('nowhere','now here',False),
    ('no defect','a defect',False),
    ('not required','required',False),
    ('120万元','1200万元',False),
    (' \n ','anything',False),
])
def test_citation_normalization_preserves_meaningful_text(quote,text,expected):
    assert supplied_quote(quote,text) is expected


def test_real_noncontiguous_source_quote_stays_invalid_with_all_paths():
    data=read_json(FIXTURES/'case03-noncontiguous-source-quotes.json')
    before=copy.deepcopy(data)
    with pytest.raises(ValueError) as error:
        validate_page(data['raw'],data['raw']['page'],data['sources'],data['modes'])
    assert all(path in str(error.value) for path in data['invalid_paths'][:2])
    assert data['invalid_paths'][2] not in str(error.value)  # Only comma typography differs.
    assert 'Split noncontiguous rows' in str(error.value)
    assert data==before  # No fabricated repair or discarded observation.


def test_split_source_fragments_are_valid_without_changing_visible_observation():
    r=suite();modes={m['id']:m for m in r['failure_modes']}
    sources=[{'id':'prompt','text':'Line A: 80\nLine B: 95\nLine C: 70'}]
    row=observation(None);row['source_refs']=[{'source_id':'prompt','quote':'Line A: 80\nLine C: 70'}]
    raw={'page':1,'complete':True,'observations':[row]}
    with pytest.raises(ValueError,match=r'observations\[0\].source_refs\[0\]'):
        validate_page(raw,1,sources,modes)
    row['source_refs']=[{'source_id':'prompt','quote':part} for part in ['Line A: 80','Line C: 70']]
    validate_page(raw,1,sources,modes)
    row['source_refs'][1]['quote']='Line C: 700'
    with pytest.raises(ValueError,match=r'observations\[0\].source_refs\[1\]'):
        validate_page(raw,1,sources,modes)


def test_real_source_comma_typography_preserves_original_observations():
    data=read_json(FIXTURES/'case05-source-comma-typography.json')
    before=copy.deepcopy(data)
    validate_page(data['raw'],data['raw']['page'],data['sources'],data['modes'])
    assert data==before
    assert data['raw']['observations'][0]['source_refs'][0]['quote'] not in data['sources'][0]['text']
    forged=copy.deepcopy(data['raw'])
    forged['observations'][0]['source_refs'][0]['quote']='存疑的地方标出来，下定论'
    with pytest.raises(ValueError,match=r'observations\[0\].source_refs\[0\]'):
        validate_page(forged,forged['page'],data['sources'],data['modes'])
