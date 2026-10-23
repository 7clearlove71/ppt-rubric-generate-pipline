import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from ppt_rubric.common import load_config, read_json
from ppt_rubric.provider import Client, ProviderError
from ppt_rubric.provider_errors import failure_policy, response_diagnostic


class Response:
    def __init__(self, status, code=None, message='unrecognized private echo', headers=None):
        self.status_code, self.ok = status, status == 200
        self.code, self.message = code, message
        self.headers = headers or {'X-Request-Id': 'service-request-123'}

    def json(self):
        if self.ok:
            return {'choices': [{'message': {'content': r'Offline evidence.\boxed{yes}'}, 'finish_reason': 'stop'}],
                    'usage': {'total_tokens': 7}}
        return {'error': {'code': self.code, 'type': self.code, 'param': None, 'message': self.message}}


def client_and_responses(tmp_path, monkeypatch, responses, **options):
    config = load_config('configs/default.json')
    config.update(min_request_interval_seconds=0, rate_limit_backoff_seconds=0, **options)
    client = Client(config, tmp_path / 'cache', tmp_path / 'journal')
    monkeypatch.setenv('PPT_RUBRIC_API_KEY', 'private-offline-secret')
    monkeypatch.setattr('ppt_rubric.provider.time.sleep', lambda _: None)
    sent = []
    responses = iter(responses)

    class Session:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, *args, **kwargs):
            sent.append(kwargs['json'])
            return next(responses)

    monkeypatch.setattr('ppt_rubric.provider.requests.Session', Session)
    return client, sent


@pytest.mark.parametrize('status,code,category,retry,fatal', [
    (400, 'Arrearage', 'account_error', False, True),
    (429, 'BudgetLimitExceeded', 'account_error', False, True),
    (401, 'InvalidApiKey', 'authentication_error', False, True),
    (403, 'AccessDenied', 'authentication_error', False, True),
    (400, 'InvalidParameter', 'invalid_request', False, False),
    (400, 'InternalError.Algo.InvalidParameter', 'invalid_request', False, False),
    (400, 'DataInspectionFailed', 'content_or_inspection_error', False, False),
    (400, 'InternalError', 'transient_service_error', True, False),
    (503, 'ServiceUnavailable', 'transient_service_error', True, False),
    (429, 'Throttling', 'rate_limit', True, False),
    (400, 'UnrecognizedCode', 'unclassified_http400', False, False),
])
def test_code_aware_failure_policy(status, code, category, retry, fatal):
    assert failure_policy(status, {'code': code}) == (category, retry, fatal)


def test_http400_is_preserved_without_retries_and_receipt_links_criterion(tmp_path, monkeypatch):
    client, sent = client_and_responses(tmp_path, monkeypatch, [Response(400, 'InvalidParameter')])
    with pytest.raises(ProviderError) as error:
        client.ask_text('judge', 'offline', {'document_id': 'doc_ab12', 'criterion': {'id': 'V_LAYOUT'},
                                           'pages': [{'page': 3, 'private': 'source must not be logged'}]})
    assert len(sent) == 1
    receipt = error.value.details
    assert receipt['http_status'] == 400 and receipt['ledger_request_id'] == 1
    assert receipt['provider_error']['provider_request_id'] == 'service-request-123'
    row = read_json(tmp_path / 'journal/requests.json')[0]
    assert row['request_context'] == {'document_id': 'doc_ab12', 'rubric_id': 'V_LAYOUT', 'pages': [3]}
    assert row['failure_stage'] == 'http_response' and 'usage' not in row
    assert 'source must not be logged' not in json.dumps(row)


def test_account_error_opens_shared_circuit_for_queued_calls(tmp_path, monkeypatch):
    client, sent = client_and_responses(tmp_path, monkeypatch, [Response(400, 'Arrearage')], api_concurrency=1)
    def run(i):
        try: client.ask_text('judge', 'offline', {'i': i})
        except ProviderError as error: return error.details
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(run, range(6)))
    assert len(sent) == len(client.attempts) == 1
    assert all(r['fatal'] and r['http_status'] == 400 for r in results)
    assert sum(r.get('circuit_open', False) for r in results) == 5


@pytest.mark.parametrize('code', ['InvalidParameter', 'DataInspectionFailed', 'Arrearage'])
def test_diagnostic_flag_never_retries_known_permanent_errors(tmp_path, monkeypatch, code):
    client, sent = client_and_responses(tmp_path, monkeypatch, [Response(400, code)], retry_unknown_http400_once=True)
    with pytest.raises(ProviderError): client.ask_text('judge', 'offline', {})
    assert len(sent) == 1


@pytest.mark.parametrize('diagnostic,attempts', [(False, 1), (True, 2)])
def test_unknown400_diagnostic_is_bounded_and_identical(tmp_path, monkeypatch, diagnostic, attempts):
    client, sent = client_and_responses(tmp_path, monkeypatch, [Response(400, 'Unknown')] * 3,
                                      retry_unknown_http400_once=diagnostic)
    with pytest.raises(ProviderError) as error: client.ask_text('judge', 'offline', {'data': 'original evidence'})
    assert len(sent) == attempts
    assert all(request == sent[0] for request in sent)
    assert error.value.details['http_status'] == 400


def test_transient_http400_retries_and_keeps_failed_row(tmp_path, monkeypatch):
    client, sent = client_and_responses(tmp_path, monkeypatch, [Response(400, 'InternalError'), Response(200)])
    assert client.ask_text('judge', 'offline', {}) == r'Offline evidence.\boxed{yes}'
    assert len(sent) == 2 and sent[0] == sent[1]
    rows = read_json(tmp_path / 'journal/requests.json')
    assert [r['status'] for r in rows] == ['failed', 'success']
    assert rows[0]['http_status'] == 400 and rows[0]['provider_error']['code'] == 'InternalError'


def test_error_echoes_secrets_and_unrelated_headers_are_never_logged(tmp_path, monkeypatch):
    response = Response(400, 'InvalidParameter', 'messages: private-offline-secret source text data:image/png;base64,xxx',
                        {'X-Request-Id': 'safe-service-id', 'Authorization': 'Bearer private-offline-secret'})
    client, _ = client_and_responses(tmp_path, monkeypatch, [response])
    with pytest.raises(ProviderError) as error: client.ask_text('judge', 'source text', {})
    logged = json.dumps(client.attempts) + json.dumps(error.value.details)
    for forbidden in ['private-offline-secret', 'source text', 'base64', 'Authorization']:
        assert forbidden not in logged
    assert 'safe-service-id' in logged and 'omitted' in logged
    benign = response_diagnostic(Response(400, 'Arrearage', 'Access denied, please make sure your account is in good standing.'))
    assert benign['message'].startswith('Access denied')


def test_cumulative_cap_survives_client_restart_and_prior_accounting(tmp_path, monkeypatch):
    client, sent = client_and_responses(tmp_path, monkeypatch, [Response(400, 'InvalidParameter')],
                                      max_total_api_requests=6, prior_api_requests=5)
    with pytest.raises(ProviderError): client.ask_text('judge', 'offline', {})
    resumed = Client(client.config, tmp_path / 'cache', tmp_path / 'journal')
    with pytest.raises(ProviderError, match='Cumulative'): resumed.ask_text('judge', 'offline', {'new': True})
    assert len(sent) == 1 and len(resumed.attempts) == 1


def test_reported_usage_on_http_failure_is_preserved_and_filtered(tmp_path, monkeypatch):
    response = Response(400, 'InvalidParameter')
    response.json = lambda: {'error': {'code': 'InvalidParameter'},
                            'usage': {'total_tokens': 12, 'prompt_tokens': 10,
                                      'private': 'source text', 'completion_tokens': -1}}
    client, _ = client_and_responses(tmp_path, monkeypatch, [response])
    with pytest.raises(ProviderError): client.ask_text('judge', 'offline', {})
    assert client.attempts[0]['usage'] == {'total_tokens': 12, 'prompt_tokens': 10}
    assert 'source text' not in json.dumps(client.attempts)


@pytest.mark.parametrize('options', [{'retry_unknown_http400_once': 1}, {'max_total_api_requests': -1},
                                   {'prior_api_requests': True}])
def test_invalid_failure_options_rejected_before_transport(tmp_path, options):
    config = load_config('configs/default.json');config.update(options)
    with pytest.raises(ValueError): Client(config, tmp_path)
