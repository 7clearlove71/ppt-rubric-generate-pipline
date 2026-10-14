import copy

import pytest

from ppt_rubric import pipeline
from ppt_rubric.common import read_json


def suite():
    dimensions = ['fulfillment','correctness','integrity','narrative','visual']
    return {'groups':[{'id':f'G{i}','dimension':d,'importance':1,'rationale':'task goal'} for i,d in enumerate(dimensions)],
            'items':[{'id':f'R{i}','group_id':f'G{i}','importance':1,'source_refs':['Q1'] if i==0 else [],
                      'basis':'explicit' if i==0 else 'quality','scope':'whole presentation','evidence_mode':'both',
                      'judge_prompt':f'Check a specific observable task condition number {i}'} for i in range(5)]}


class FakeClient:
    def __init__(self, passes):self.passes=iter(passes);self.revisions=0;self.calls=[]
    def ask(self,role,system,payload,images=(),**kwargs):
        from ppt_rubric import prompts
        self.calls.append((role,system,payload))
        if system==prompts.REQUIREMENTS:
            return {'requirements':[{'id':'Q1','text':'task','source_id':'prompt','quote':'task','importance':3}]}
        if system==prompts.OBSERVE:return {'observations':[],'render_concerns':[]}
        if system==prompts.COUNTEREXAMPLE_REVIEW:return {'approved':True,'issues':[]}
        if system==prompts.REVIEW:
            approved=next(self.passes)
            return {'approved':approved,'issues':[] if approved else [{'severity':'error','reason':'ambiguous','fix':'clarify','item_ids':['R0']}],
                    'coverage':{'Q1':['R0']},'summary':'reviewed'}
        if system==prompts.REVISE:self.revisions+=1
        return copy.deepcopy(suite())


def setup(monkeypatch):
    case={'id':'01','prompt':'task','candidates':[{'id':'A'},{'id':'B'},{'id':'C'}]}
    config={'dimension_weights':dict.fromkeys(['fulfillment','correctness','integrity','narrative','visual'],20),
            'max_revisions':2,'api_concurrency':2,'max_case_text_chars':1000}
    decks={cid:{'identity':{'sha256':cid},'slides':[]} for cid in 'ABC'}
    monkeypatch.setattr(pipeline,'prepare_case',lambda *args:decks)
    return case,config


def test_three_candidates_publish_one_suite_after_repair(tmp_path,monkeypatch):
    case,config=setup(monkeypatch)
    client=FakeClient([False,True])
    result=pipeline.generate(case,config,client,tmp_path,tmp_path)
    assert result['status']=='approved'
    assert client.revisions==1
    assert len(list(tmp_path.rglob('rubric.json')))==1
    assert sum(i['score'] for i in result['items'])==100
    assert set(read_json(tmp_path/'01'/'observations.json'))=={'A','B','C'}
    # Same signed inputs resume the published artifact without further model calls.
    before=len(client.calls)
    assert pipeline.generate(case,config,client,tmp_path,tmp_path)==result
    assert len(client.calls)==before


def test_failed_review_stops_without_publishing(tmp_path,monkeypatch):
    case,config=setup(monkeypatch)
    client=FakeClient([False,False,False])
    result=pipeline.generate(case,config,client,tmp_path,tmp_path)
    assert result['status']=='quality_failed'
    assert client.revisions==2
    assert not (tmp_path/'01'/'rubric.json').exists()
    assert len(read_json(tmp_path/'01'/'reviews.json'))==3


def test_stale_approval_is_not_left_on_failed_regeneration(tmp_path,monkeypatch):
    case,config=setup(monkeypatch)
    pipeline.generate(case,config,FakeClient([True]),tmp_path,tmp_path)
    config['max_revisions']=0
    pipeline.generate(case,config,FakeClient([False]),tmp_path,tmp_path)
    assert not (tmp_path/'01'/'rubric.json').exists()
    assert len(list((tmp_path/'01').glob('superseded-*.json')))==1


def test_structural_error_is_repaired_without_wasting_semantic_review(tmp_path,monkeypatch):
    case,config=setup(monkeypatch)
    draft=suite()
    draft['items'][1]['judge_prompt']='此项的评分要求请参考R0中的具体标准。'
    client=FakeClient([True])
    context={'requirements':{'requirements':[{'id':'Q1'}]},'sources':[]}
    result=pipeline.review_and_repair(case,config,client,tmp_path,context,draft,{'case_id':'01'})
    assert result['status']=='approved'
    assert client.revisions==1
    from ppt_rubric import prompts
    assert sum(system==prompts.REVIEW for _,system,_ in client.calls)==1
    assert read_json(tmp_path/'reviews.json')[0]['review']['skipped'] is True


def test_review_coverage_must_match_actual_item_source_refs():
    result={'approved':True,'issues':[],'coverage':{'Q1':['R1']}}
    pipeline.audit_review(result, {'requirements':[{'id':'Q1'}]}, suite())
    assert result['coverage']=={'Q1':['R0']}
    assert result['reported_coverage']=={'Q1':['R1']}
    # Computing the map cannot manufacture a missing requirement.
    with pytest.raises(ValueError,match='coverage'):
        pipeline.audit_review({'approved':True,'issues':[]},
                              {'requirements':[{'id':'missing'}]},suite())


def test_dedicated_reviser_role_does_not_change_review_roles(tmp_path,monkeypatch):
    from ppt_rubric import prompts
    case,config=setup(monkeypatch)
    client=FakeClient([False,True])
    context={'requirements':{'requirements':[{'id':'Q1'}]},'sources':[]}
    result=pipeline.review_and_repair(case,config,client,tmp_path,context,suite(),{'case_id':'01'},reviser_role='reviser')
    assert result['status']=='approved'
    assert [(role,system) for role,system,_ in client.calls if system==prompts.REVISE]==[('reviser',prompts.REVISE)]
    assert all(role=='reviewer' for role,system,_ in client.calls if system in {prompts.REVIEW,prompts.COUNTEREXAMPLE_REVIEW})


def test_counterexample_veto_blocks_otherwise_approved_suite(tmp_path,monkeypatch):
    case,config=setup(monkeypatch)
    config['max_revisions']=0
    from ppt_rubric import prompts
    class CounterexampleClient(FakeClient):
        def ask(self,role,system,payload,images=(),**kwargs):
            if system==prompts.COUNTEREXAMPLE_REVIEW:
                return {'approved':False,'issues':[{'severity':'error','item_ids':['R0','R1'],
                         'reason':'One concrete defect is charged twice','fix':'Assign a single owner'}]}
            return super().ask(role,system,payload,images,**kwargs)
    result=pipeline.generate(case,config,CounterexampleClient([True]),tmp_path,tmp_path)
    assert result['status']=='quality_failed'
    assert not (tmp_path/'01'/'rubric.json').exists()


@pytest.mark.parametrize('still_fails', [False, True])
def test_source_refinement_is_blind_bounded_and_preserves_failed_pass(tmp_path,monkeypatch,still_fails):
    from ppt_rubric import prompts
    case,config=setup(monkeypatch)
    config.update(max_revisions=0,max_source_refinements=1)
    class RefinementClient:
        refined=False
        refinements=0
        def ask(self,role,system,payload,images=(),**kwargs):
            def req(ids):
                return {'requirements':[{'id':i,'text':'task','source_id':'prompt','quote':'task','importance':3} for i in ids]}
            if system==prompts.REQUIREMENTS:return req(['Q1','Q2'])
            if system==prompts.REFINE_REQUIREMENTS:
                assert set(payload)=={'sources','requirements','refinement_targets'}
                assert not images
                self.refinements+=1;self.refined=True
                return {**req(['F1','F2']),'refinement_map':{'Q1':['F1'],'Q2':['F2']}}
            if system==prompts.OBSERVE:return {'observations':[],'render_concerns':[]}
            if system==prompts.REVIEW:
                return {'approved':True,'issues':[],'coverage':{'F1':['R0'],'F2':['R1']}}
            if system==prompts.COUNTEREXAMPLE_REVIEW:return {'approved':True,'issues':[]}
            r=copy.deepcopy(suite())
            a,b=('F1','F2') if self.refined else ('Q1','Q2')
            r['items'][0]['source_refs']=[a]
            r['items'][1].update(basis='explicit',source_refs=[b] if self.refined and not still_fails else [a,b])
            return r
    client=RefinementClient()
    result=pipeline.generate(case,config,client,tmp_path,tmp_path)
    assert result['status']==('quality_failed' if still_fails else 'approved')
    assert client.refinements==1
    assert read_json(tmp_path/'01/source-pass-0/status.json')['status']=='quality_failed'
    assert read_json(tmp_path/'01/source-pass-0/requirements.json')['requirements'][0]['id']=='Q1'
    assert read_json(tmp_path/'01/requirements.json')['requirements'][0]['id']=='F1'
    assert read_json(tmp_path/'01/source-refinement.json')['targets']==['Q1']


def test_refinement_lineage_cannot_drop_or_move_source_requirements():
    from ppt_rubric.rubric import validate_requirement_refinement
    sources=[{'id':'prompt','text':'deadline 24 hours; audience staff'}]
    old={'requirements':[{'id':'Q1','text':'deadline','source_id':'prompt','quote':'deadline 24 hours','importance':3}]}
    new={'requirements':[{'id':'F1','text':'deadline','source_id':'prompt','quote':'24 hours','importance':3}],
         'refinement_map':{'Q1':['F1']}}
    validate_requirement_refinement(new,old,sources)
    bad=copy.deepcopy(new);bad['refinement_map']={}
    with pytest.raises(ValueError,match='every original'):validate_requirement_refinement(bad,old,sources)
    bad=copy.deepcopy(new);bad['requirements'][0]['quote']='audience staff'
    with pytest.raises(ValueError,match='unrelated source'):validate_requirement_refinement(bad,old,sources)


@pytest.mark.parametrize('page_failure', [False, True])
@pytest.mark.parametrize('page_defect', [False, True])
def test_evaluate_checks_each_page_once_and_propagates_failure(tmp_path, monkeypatch, page_failure, page_defect):
    from ppt_rubric import prompts
    case, config = setup(monkeypatch)
    config['judge_model'] = 'test-model'
    rubric = pipeline.generate(case, config, FakeClient([True]), tmp_path, tmp_path/'generated')
    decks = {cid: {'identity': {'sha256': cid}, 'slides': [
        {'page': n, 'image': f'page-{n}.png', 'text': 'auxiliary', 'rendered_text': 'auxiliary'}
        for n in (1, 2)]} for cid in 'ABC'}
    monkeypatch.setattr(pipeline, 'prepare_case', lambda *args: decks)
    class Judge:
        page_calls = 0
        full_calls = 0
        def ask(self, role, system, payload, images=(), **kwargs):
            if system == prompts.VISUAL_PAGE:
                self.page_calls += 1
                assert len(images) == 1
                assert len(payload['visual_rubrics']) == 1
                if page_failure and payload['page'] == 2:
                    raise TimeoutError('simulated')
                return {'page': payload['page'], 'checks': [{'rubric_id': 'R4',
                        'status': 'fail' if page_defect and payload['page'] == 2 else 'pass', 'evidence': 'Bottom chart labels'}]}
            self.full_calls += 1
            assert len(images) == 2
            checks = payload.get('page_visual_checks', [])
            if checks:
                assert kwargs['suffix']['visual_resolution_contract']['required_pages'] == ([2] if page_defect else [])
            return {'answer': 'no' if checks and page_defect else 'yes', 'pages': [2], 'evidence': 'Checked against criterion',
                    'visual_resolutions': [{'page': 2, 'decision': 'uphold', 'evidence': 'Labels obscured'}] if checks and page_defect else []}
    client = Judge()
    reports = pipeline.evaluate(case, rubric, config, client, tmp_path, tmp_path/'judged')
    assert client.page_calls == 6  # 3 candidates x 2 pages, not multiplied by item count
    assert client.full_calls == (12 if page_failure else 15)
    for report in reports.values():
        assert report['summary']['status'] == ('incomplete' if page_failure else 'complete')
        assert report['summary']['score'] == (None if page_failure else (80 if page_defect else 100))
        assert report['verdicts'][-1]['answer'] == ('unknown' if page_failure else ('no' if page_defect else 'yes'))
        assert report['judge_prompt_fingerprint']
    assert len(read_json(tmp_path/'judged/01/A-visual-pages.json')) == 2
