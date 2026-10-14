import json
import pytest

from ppt_rubric.common import load_config
from ppt_rubric.provider import Client, ProviderError


def test_schema_invalid_response_is_retried_before_caching(tmp_path, monkeypatch):
    config=load_config('configs/default.json')
    config['min_request_interval_seconds']=0
    client=Client(config,tmp_path,journal_dir=tmp_path/'run')
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
        def post(self,*args,**kwargs):calls.append(kwargs['json']);return Response()
    monkeypatch.setattr('ppt_rubric.provider.requests.Session',Session)
    def validate(obj):
        if obj.get('answer') not in {'yes','no'}:raise ValueError('invalid verdict')
    assert client.ask('judge','judge instruction',{'input':'data'},validate=validate)=={'answer':'yes'}
    assert len(calls)==2
    assert client.ask('judge','judge instruction',{'input':'data'},validate=validate)=={'answer':'yes'}
    assert len(calls)==2
    assert 'test-only-not-a-real-key' not in next((tmp_path/'api').glob('*.json')).read_text()
    assert [r['status'] for r in client.attempts]==['failed','success']
    assert len(calls[0]['messages']) == 2
    assert len(calls[1]['messages']) == 3
    assert 'invalid verdict' in calls[1]['messages'][-1]['content']
    assert calls[1]['messages'][:2] == calls[0]['messages']
    assert (tmp_path/'run'/client.attempts[0]['invalid_response']).exists()
    assert 'test-only-not-a-real-key' not in ''.join(p.read_text() for p in (tmp_path/'run').rglob('*.json'))


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


def test_thinking_reviser_has_its_own_response_timeout(tmp_path,monkeypatch):
    config=load_config('configs/evidence-v1-k3.json');config['min_request_interval_seconds']=0
    client=Client(config,tmp_path)
    monkeypatch.setenv('PPT_RUBRIC_API_KEY','test-key')
    timeouts=[]
    class Response:
        ok=True
        status_code=200
        def json(self):return {'choices':[{'message':{'content':'{"ok":true}'},'finish_reason':'stop'}]}
    class Session:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,*args,**kwargs):
            timeouts.append(kwargs['timeout'])
            assert 'role_timeout_seconds' not in kwargs['json']
            return Response()
    monkeypatch.setattr('ppt_rubric.provider.requests.Session',Session)
    client.ask('reviser','revision',{})
    client.ask('reviewer','review',{})
    assert timeouts==[600,180]


def test_reviser_output_recovery_preserves_other_role_requests_and_cache(tmp_path,monkeypatch):
    config=load_config('configs/evidence-v1-k3.json');config['min_request_interval_seconds']=0
    monkeypatch.setenv('PPT_RUBRIC_API_KEY','test-key')
    requests_sent=[]
    class Response:
        ok=True
        status_code=200
        def json(self):return {'choices':[{'message':{'content':'{"ok":true}'},'finish_reason':'stop'}]}
    class Session:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,*args,**kwargs):requests_sent.append(kwargs['json']);return Response()
    monkeypatch.setattr('ppt_rubric.provider.requests.Session',Session)
    Client(config,tmp_path).ask('reviewer','review',{})
    revised={**config,'role_max_output_tokens':{'reviser':32768}}
    client=Client(revised,tmp_path)
    client.ask('reviewer','review',{})  # Remains an identical cache hit.
    client.ask('reviser','revision',{})
    assert len(requests_sent)==2
    assert [r['max_tokens'] for r in requests_sent]==[config['max_output_tokens'],32768]
    assert all('role_max_output_tokens' not in r for r in requests_sent)
    assert client.request_count==1


@pytest.mark.parametrize('limits',[{'reviser':True},{'reviser':0},{'reviser':1.5},{'missing':32000},[]])
def test_invalid_role_output_limits_rejected_before_model_work(tmp_path,limits):
    config=load_config('configs/default.json');config['role_max_output_tokens']=limits
    path=tmp_path/'config.json';path.write_text(json.dumps(config))
    with pytest.raises(ValueError,match='role_max_output_tokens'):load_config(path)


def test_provider_specific_credentials_are_isolated(tmp_path, monkeypatch):
    monkeypatch.setenv('BAILIAN_API_KEY', 'test-bailian')
    monkeypatch.setenv('MAAS_API_KEY', 'test-maas')
    monkeypatch.setenv('PPT_RUBRIC_API_KEY', 'test-generic')
    config = load_config('configs/default.json')
    for name, expected in [('BAILIAN_API_KEY', 'test-bailian'), ('MAAS_API_KEY', 'test-maas')]:
        assert Client(dict(config, api_key_env=name), tmp_path).key() == expected
    monkeypatch.delenv('BAILIAN_API_KEY')
    with pytest.raises(ProviderError, match='BAILIAN_API_KEY'):
        Client(dict(config, api_key_env='BAILIAN_API_KEY'), tmp_path).key()
