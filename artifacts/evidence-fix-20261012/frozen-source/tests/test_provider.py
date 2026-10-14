import json
import pytest

from ppt_rubric.common import load_config
from ppt_rubric.provider import Client, ProviderError


def test_schema_invalid_response_is_retried_before_caching(tmp_path, monkeypatch):
    config=load_config('configs/default.json')
    config['min_request_interval_seconds']=0
    client=Client(config,tmp_path)
    monkeypatch.setenv('PPT_RUBRIC_API_KEY','test-only-not-a-real-key')
    monkeypatch.setattr('ppt_rubric.provider.time.sleep',lambda n:None)
    bodies=iter([{'wrong':'shape'},{'answer':'yes'}])
    calls=[]
    class Response:
        ok=True
        status_code=200
        def json(self):return {'choices':[{'message':{'content':json.dumps(next(bodies))},'finish_reason':'stop'}]}
    class Session:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,*args,**kwargs):calls.append(1);return Response()
    monkeypatch.setattr('ppt_rubric.provider.requests.Session',Session)
    def validate(obj):
        if obj.get('answer') not in {'yes','no'}:raise ValueError('invalid verdict')
    assert client.ask('judge','judge instruction',{'input':'data'},validate=validate)=={'answer':'yes'}
    assert len(calls)==2
    assert client.ask('judge','judge instruction',{'input':'data'},validate=validate)=={'answer':'yes'}
    assert len(calls)==2
    assert 'test-only-not-a-real-key' not in next((tmp_path/'api').glob('*.json')).read_text()
    assert [r['status'] for r in client.attempts]==['failed','success']


def test_request_budget_blocks_before_any_network_call(tmp_path,monkeypatch):
    config=load_config('configs/default.json');config['max_api_requests']=0
    client=Client(config,tmp_path)
    def forbidden_session():raise AssertionError('Network must not be touched after budget exhaustion')
    monkeypatch.setattr('ppt_rubric.provider.requests.Session',forbidden_session)
    with pytest.raises(ProviderError,match='budget reached'):
        client.ask('judge','test',{'input':'data'})
    assert client.request_count==0


def test_repeated_rate_limit_opens_circuit_and_stops_queued_calls(tmp_path,monkeypatch):
    config=load_config('configs/default.json')
    config.update(min_request_interval_seconds=0,rate_limit_backoff_seconds=0,max_consecutive_rate_limits=2)
    client=Client(config,tmp_path,journal_dir=tmp_path/'run')
    monkeypatch.setenv('PPT_RUBRIC_API_KEY','test-key')
    monkeypatch.setattr('ppt_rubric.provider.time.sleep',lambda n:None)
    calls=[]
    class Response:
        ok=False
        status_code=429
        headers={}
    class Session:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,*args,**kwargs):calls.append(1);return Response()
    monkeypatch.setattr('ppt_rubric.provider.requests.Session',Session)
    with pytest.raises(ProviderError,match='circuit open'):client.ask('judge','test',{'input':1})
    with pytest.raises(ProviderError,match='circuit open'):client.ask('judge','test',{'input':2})
    assert len(calls)==2
    assert all(r['status']=='failed' for r in client.attempts)


def test_long_retry_after_stops_instead_of_retrying_early(tmp_path):
    client=Client(load_config('configs/default.json'),tmp_path)
    class Response:headers={'Retry-After':'600'}
    with pytest.raises(ProviderError,match='circuit open'):client.handle_rate_limit(Response())
    assert client.rate_limit_open


def test_output_truncation_is_not_retried_identically(tmp_path,monkeypatch):
    config=load_config('configs/default.json')
    client=Client(config,tmp_path)
    monkeypatch.setenv('PPT_RUBRIC_API_KEY','test-key')
    calls=[]
    class Response:
        ok=True
        status_code=200
        def json(self):return {'choices':[{'finish_reason':'length'}],'usage':{'total_tokens':16000}}
    class Session:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,*args,**kwargs):calls.append(1);return Response()
    monkeypatch.setattr('ppt_rubric.provider.requests.Session',Session)
    with pytest.raises(ProviderError,match='Output token limit'):client.ask('judge','instruction',{})
    assert len(calls)==1 and client.attempts[0]['usage']['total_tokens']==16000


def test_timeout_circuit_stops_remaining_work(tmp_path,monkeypatch):
    import requests
    config=load_config('configs/default.json');config['min_request_interval_seconds']=0
    client=Client(config,tmp_path)
    monkeypatch.setenv('PPT_RUBRIC_API_KEY','test-key')
    monkeypatch.setattr('ppt_rubric.provider.time.sleep',lambda n:None)
    calls=[]
    class Session:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,*args,**kwargs):calls.append(1);raise requests.ReadTimeout('private data must not appear in journal')
    monkeypatch.setattr('ppt_rubric.provider.requests.Session',Session)
    with pytest.raises(ProviderError,match='Transport circuit open'):client.ask('judge','instruction',{})
    with pytest.raises(ProviderError,match='Transport circuit open'):client.ask('judge','another instruction',{})
    assert len(calls)==2
    assert 'private data' not in json.dumps(client.attempts)
