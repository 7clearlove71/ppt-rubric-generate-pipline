"""Safe provider diagnostics and conservative, code-aware failure decisions."""
from __future__ import annotations

import re


ACCOUNT_CODES = {'Arrearage', 'isv.OUT_OF_SERVICE', 'PrepaidBillOverdue',
                 'PostpaidBillOverdue', 'BudgetLimitExceeded', 'insufficient_quota'}
TRANSIENT_CODES = {'InternalError', 'internal_error', 'InternalError.Timeout',
                   'RequestTimeOut', 'ResponseTimeout', 'ServiceUnavailable',
                   'ModelUnavailable', 'SystemError', 'ModelServiceFailed'}


def validate_failure_options(config):
    for name in ('max_total_api_requests', 'prior_api_requests'):
        if name in config and (type(config[name]) is not int or config[name] < 0):
            raise ValueError(f'{name} must be a nonnegative integer')
    if type(config.get('retry_unknown_http400_once', False)) is not bool:
        raise ValueError('retry_unknown_http400_once must be a boolean')


def _token(value, secret=None):
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.:/\[\]-]{1,160}', value):
        return '[omitted]'
    if (secret and secret in value) or re.search(r'sk-[A-Za-z0-9_-]+', value):
        return '[redacted]'
    return value


def response_diagnostic(response, secret=None):
    """Never persist arbitrary error echoes, response headers or request data.

    Only recognizable generic messages survive. Unknown message shapes are
    represented explicitly; their code and service request ID remain useful.
    """
    try:
        body = response.json()
    except (ValueError, TypeError, AttributeError):
        body = {}
    body = body if isinstance(body, dict) else {}
    error = body.get('error', body)
    error = error if isinstance(error, dict) else {}
    headers = {str(k).lower(): v for k, v in (getattr(response, 'headers', {}) or {}).items()}
    request_id = (headers.get('x-request-id') or headers.get('x-dashscope-request-id')
                  or headers.get('request-id') or body.get('request_id') or error.get('request_id'))
    message = error.get('message')
    safe_patterns = (
        r'Access denied, please make sure your account is in good standing\.'
        r'(?: For details, see: https://help\.aliyun\.com/[A-Za-z0-9_/#.\-]+)?',
        r'(?:An internal error has occurred|An internal error has occured|An system error has occured)'
        r', please try again later(?: or contact service support)?\.',
        r'(?:Input or output|Input|Output) data may contain inappropriate content\.?',
        r'(?:Requests throttling triggered|Requests rate limit exceeded|Too many requests|'
        r'Request timed out, please try again later|Failed to request model service)\.?',
        r'Range of (?:input length|max_tokens) should be \[\d+,\s*\d+\]',
    )
    safe_message = (message if isinstance(message, str) and len(message) <= 500
                    and not (secret and secret in message)
                    and any(re.fullmatch(pattern, message) for pattern in safe_patterns)
                    else '[provider message omitted: unrecognized or potentially sensitive content]')
    result = {'code': _token(error.get('code'), secret), 'type': _token(error.get('type'), secret),
              'param': _token(error.get('param'), secret), 'message': safe_message,
              'provider_request_id': _token(request_id, secret)}
    usage = body.get('usage')
    if isinstance(usage, dict):
        known = {k: v for k, v in usage.items() if k in {
            'prompt_tokens', 'completion_tokens', 'total_tokens', 'input_tokens', 'output_tokens'}
                 and type(v) is int and v >= 0}
        if known:
            result['usage'] = known
    return result


def failure_policy(status, diagnostic):
    code = diagnostic.get('code') or diagnostic.get('type') or ''
    if code in ACCOUNT_CODES:
        return 'account_error', False, True
    if status == 401 or (status == 403 and 'inspection' not in code.lower()):
        return 'authentication_error', False, True
    if 'inspection' in code.lower() or code in {'Contain.Forbidden.Label.Error'}:
        return 'content_or_inspection_error', False, False
    if 'invalidparameter' in code.lower() or code.startswith(('Invalid', 'invalid_')):
        return 'invalid_request', False, False
    if code in TRANSIENT_CODES or status in {408, 409, 429, 500, 502, 503, 504}:
        return ('rate_limit' if status == 429 else 'transient_service_error'), True, False
    return ('unclassified_http400' if status == 400 else 'http_error'), False, False
