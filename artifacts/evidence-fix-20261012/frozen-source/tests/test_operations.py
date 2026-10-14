import json

from ppt_rubric.common import read_json,write_json
from ppt_rubric.operations import plan_run,run_report
from ppt_rubric.provider import Client


def test_offline_plan_counts_all_candidates_and_retries(tmp_path,monkeypatch):
    monkeypatch.setattr('ppt_rubric.operations.file_digest',lambda p:'hash')
    monkeypatch.setattr('ppt_rubric.operations.extract_pptx',lambda p:[{'hidden':False}]*4)
    case={'id':'01','split':'dev','prompt':'task','candidates':[
        {'id':c,'pptx':'unused','sha256':'hash'} for c in 'ABC']}
    config={'max_case_text_chars':1000,'max_images_per_request':60,'max_revisions':2,'max_retries':3}
    plan=plan_run([case],config)
    assert plan['totals']=={'first_pass_logical_calls':8,'max_logical_calls':14,
                           'max_http_attempts':42,'first_pass_image_presentations':12}
    assert plan['cost_estimate'] is None
    refined=plan_run([case],{**config,'max_source_refinements':1})
    assert refined['totals']['first_pass_logical_calls']==8
    assert refined['totals']['max_logical_calls']==28
    assert refined['totals']['max_http_attempts']==84
    monkeypatch.setattr('ppt_rubric.operations.file_digest',lambda p:'changed')
    assert plan_run([case],config)['ready_cases']==0


def test_failed_response_tokens_are_in_journal_and_report(tmp_path,monkeypatch):
    from ppt_rubric.common import load_config
    config=load_config('configs/default.json')
    config['min_request_interval_seconds']=0
    client=Client(config,tmp_path/'cache',tmp_path/'run')
    monkeypatch.setenv('PPT_RUBRIC_API_KEY','private-test-key')
    monkeypatch.setattr('ppt_rubric.provider.time.sleep',lambda n:None)
    bodies=iter(['not JSON','{"ok":true}'])
    class Response:
        ok=True
        status_code=200
        def json(self):
            return {'choices':[{'message':{'content':next(bodies)}}],
                    'usage':{'prompt_tokens':10,'completion_tokens':2,'total_tokens':12}}
    class Session:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,*args,**kwargs):return Response()
    monkeypatch.setattr('ppt_rubric.provider.requests.Session',Session)
    assert client.ask('judge','instruction',{'source':'data'})=={'ok':True}
    assert client.ask('judge','instruction',{'source':'data'})=={'ok':True}
    write_json(tmp_path/'run'/'run-summary.json',{'01':{'status':'quality_failed'}})
    result=run_report(tmp_path/'run')
    counts=result['models'][config['judge_model']]
    assert counts['http_attempts']==2 and counts['failed_attempts']==1
    assert counts['reported_total_tokens']==24
    assert result['logical_cache_hits']==1 and result['human_agreement'] is None
    assert 'private-test-key' not in json.dumps(read_json(tmp_path/'run'/'requests.json'))
    resumed=Client(config,tmp_path/'cache',tmp_path/'run')
    assert resumed.ask('judge','instruction',{'source':'data'})=={'ok':True}
    assert resumed.request_count==0
    assert len(read_json(tmp_path/'run'/'requests.json'))==2
    assert run_report(tmp_path/'run')['models'][config['judge_model']]['reported_total_tokens']==24


def test_report_rejects_stale_approved_file(tmp_path):
    write_json(tmp_path/'run-summary.json',{'01':{'status':'approved','items':5}})
    report=run_report(tmp_path)
    assert report['status_counts']=={'invalid_approval':1}
    assert report['approved_fraction']==0


def test_regression_hides_expected_answer_and_requires_relevant_defect(tmp_path):
    from ppt_rubric.regression import run_checks
    from ppt_rubric.common import load_config
    from ppt_rubric import prompts
    fixtures=read_json('fixtures/review_regressions.json')
    # Use a semantic-only defect: numeric thresholds are now rejected offline.
    fixtures['tests']=[fixtures['tests'][4]]
    class ClientStub:
        def ask(self,role,system,payload,**kwargs):
            assert role=='reviewer' and system==prompts.REVIEW
            assert set(payload)=={'sources','requirements','rubric','dimension_weights'}
            # Rejecting for an unrelated reason must not count as detecting the seeded error.
            return {'approved':False,'issues':[{'severity':'error','item_ids':['R01'],'reason':'unrelated','fix':'unrelated'}]}
    result=run_checks(fixtures,load_config('configs/default.json'),ClientStub(),tmp_path)
    assert result['passed']==0 and result['results'][0]['detected_target'] is False


def test_export_refuses_partial_or_tampered_suites(tmp_path):
    import pytest
    from ppt_rubric.common import digest
    from ppt_rubric.operations import export_suites
    from ppt_rubric.rubric import assign_scores,VALIDATOR_VERSION
    fixtures=read_json('fixtures/review_regressions.json')
    weights=dict(fulfillment=30,correctness=25,integrity=15,narrative=15,visual=15)
    suite=assign_scores(fixtures['rubric'],weights)
    suite.update(case_id='01',status='approved',dimension_weights=weights,
                 provenance={'validator_version':VALIDATOR_VERSION,'source_hash':digest(fixtures['sources'])})
    suite['rubric_hash']=digest(suite)
    write_json(tmp_path/'rubrics'/'01'/'rubric.json',suite)
    write_json(tmp_path/'rubrics'/'01'/'requirements.json',fixtures['requirements'])
    cases=[{'id':i,'prompt':fixtures['sources'][0]['text']} for i in ['01','02']]
    config={'max_case_text_chars':1000}
    destination=tmp_path/'export.jsonl'
    with pytest.raises(ValueError,match='not written'):
        export_suites(cases,config,tmp_path/'rubrics',destination)
    assert not destination.exists()
    result=export_suites(cases,config,tmp_path/'rubrics',destination,True)
    assert result['eligible']==1 and result['omitted'][0]['case_id']=='02'
    exported=json.loads(destination.read_text())
    assert len(exported['rubrics'])==6 and set(exported['rubrics'][0])=={'judge_prompt','score'}
    suite['items'][0]['score']+=1
    suite.pop('rubric_hash');suite['rubric_hash']=digest(suite)
    write_json(tmp_path/'rubrics'/'01'/'rubric.json',suite)
    with pytest.raises(ValueError,match='not written'):
        export_suites(cases[:1],config,tmp_path/'rubrics',destination)
    # A failed new export must preserve the previous complete artifact.
    assert json.loads(destination.read_text())==exported


def test_judge_plan_includes_per_page_checks_once(tmp_path, monkeypatch):
    from ppt_rubric.common import digest
    from ppt_rubric.rubric import assign_scores, VALIDATOR_VERSION
    fixtures = read_json('fixtures/review_regressions_v2.json')
    weights = dict(fulfillment=30, correctness=25, integrity=15, narrative=15, visual=15)
    rubric = assign_scores(fixtures['rubric'], weights)
    rubric.update(case_id='01', status='approved', dimension_weights=weights,
                  provenance={'validator_version': VALIDATOR_VERSION, 'source_hash': digest(fixtures['sources'])})
    rubric['rubric_hash'] = digest(rubric)
    write_json(tmp_path/'01/rubric.json', rubric)
    write_json(tmp_path/'01/requirements.json', fixtures['requirements'])
    monkeypatch.setattr('ppt_rubric.operations.file_digest', lambda p: 'hash')
    monkeypatch.setattr('ppt_rubric.operations.extract_pptx', lambda p: [{'hidden': False}] * 4)
    case = {'id': '01', 'split': 'dev', 'prompt': fixtures['sources'][0]['text'],
            'candidates': [{'id': c, 'pptx': 'unused', 'sha256': 'hash'} for c in 'AB']}
    config = {'max_case_text_chars': 1000, 'max_images_per_request': 60, 'max_retries': 3}
    result = plan_run([case], config, 'judge', tmp_path)
    assert result['ready_cases'] == 1
    assert result['cases'][0]['visual_page_calls'] == 8
    assert result['totals']['first_pass_logical_calls'] == 20  # 12 criterion calls + 8 page calls
    assert result['totals']['max_http_attempts'] == 60
    assert result['totals']['first_pass_image_presentations'] == 56
