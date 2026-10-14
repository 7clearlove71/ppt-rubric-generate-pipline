from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import threading
import time
import uuid
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

import requests

from .common import digest, read_json, write_json


class ProviderError(RuntimeError):
    pass


def parse_json(content):
    if not isinstance(content, str):
        raise ValueError('Expected text JSON response')
    text = content.strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError('Expected a JSON object')
    return data


class Client:
    def __init__(self, config, cache_root, journal_dir=None):
        self.config = config
        self.cache = Path(cache_root).resolve() / 'api'
        self.semaphore = threading.BoundedSemaphore(config['api_concurrency'])
        self.lock = threading.Lock()
        self.journal_dir = Path(journal_dir) if journal_dir else None
        self.calls = read_json(self.journal_dir/'usage.json') if self.journal_dir and (self.journal_dir/'usage.json').exists() else []
        self.attempts = read_json(self.journal_dir/'requests.json') if self.journal_dir and (self.journal_dir/'requests.json').exists() else []
        self.invocation_id = uuid.uuid4().hex
        self.request_count = 0
        self._key = None
        self.next_request_at = 0.0
        self.rate_limited_until = 0.0
        self.rate_limit_streak = 0
        self.rate_limit_open = False
        self.transport_failures = 0
        self.transport_open = False

    def wait_for_slot(self):
        while True:
            with self.lock:
                if self.rate_limit_open:
                    raise ProviderError('Rate-limit circuit open; resume from cache after service recovery')
                if self.transport_open:
                    raise ProviderError('Transport circuit open after repeated network failures; resume from cache later')
                now = time.monotonic()
                delay = max(self.next_request_at, self.rate_limited_until) - now
                if delay <= 0:
                    self.next_request_at = now + self.config.get('min_request_interval_seconds', 1)
                    return
            time.sleep(min(delay, 1))

    def handle_rate_limit(self, response):
        value = getattr(response, 'headers', {}).get('Retry-After', '')
        try:
            retry_after = float(value)
        except (ValueError, TypeError):
            try:
                retry_after = parsedate_to_datetime(value).timestamp() - time.time()
            except (ValueError, TypeError, AttributeError):
                retry_after = 0
        with self.lock:
            self.rate_limit_streak += 1
            delay = max(retry_after, self.config.get('rate_limit_backoff_seconds',30) * 2 ** (self.rate_limit_streak-1))
            self.rate_limited_until = max(self.rate_limited_until, time.monotonic()+delay)
            # Never retry sooner than a long Retry-After. Stop this run instead
            # of tying up a batch worker for an unbounded provider outage.
            if delay > 60 or self.rate_limit_streak >= self.config.get('max_consecutive_rate_limits',3):
                self.rate_limit_open = True
        if self.rate_limit_open:
            raise ProviderError('Rate-limit circuit open; resume from cache after service recovery')

    def flush_locked(self):
        # Called with self.lock held so parallel completions cannot replace a
        # newer journal with an older snapshot. No payloads or credentials.
        if self.journal_dir:
            write_json(self.journal_dir / 'usage.json', self.calls)
            write_json(self.journal_dir / 'requests.json', self.attempts)

    def key(self):
        with self.lock:
            if self._key:
                return self._key
            if os.environ.get('PPT_RUBRIC_API_KEY'):
                self._key = os.environ['PPT_RUBRIC_API_KEY']
            else:
                # Credentials are bound to the explicitly supported provider host.
                bindings = {'codex.modelarts-maas.api-key': 'api.modelarts-maas.com',
                            'codex.alibaba-bailian.api-key': 'dashscope.aliyuncs.com'}
                expected_host = bindings.get(self.config.get('keychain_service'))
                if not expected_host or urlparse(self.config['base_url']).hostname != expected_host:
                    raise ProviderError('No matching keychain host binding; configure PPT_RUBRIC_API_KEY')
                p = subprocess.run(['security', 'find-generic-password', '-a', self.config['keychain_account'],
                                    '-s', self.config['keychain_service'], '-w'], capture_output=True, text=True)
                if p.returncode:
                    raise ProviderError('Keychain credential unavailable; set PPT_RUBRIC_API_KEY')
                self._key = p.stdout.strip()
            return self._key

    def ask(self, role, system, payload, images=(), suffix=None, validate=None):
        images = list(images)
        if len(images) > self.config['max_images_per_request']:
            raise ProviderError('Image budget exceeded; configure a larger verified limit or chunk explicitly')
        content = [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]
        for label, image in images:
            content.extend([{"type": "text", "text": label}, {"type": "image_url", "image_url": {
                "url": 'data:image/png;base64,' + base64.b64encode(Path(image).read_bytes()).decode()}}])
        if suffix is not None:
            # Keep large shared sources/images ahead of the changing criterion to
            # make provider-side prefix caching possible (not guaranteed).
            content.append({"type": "text", "text": json.dumps(suffix, ensure_ascii=False)})
        request = {"model": self.config[role + '_model'],
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
                   "max_tokens": self.config['max_output_tokens'], "stream": False}
        # MaaS GLM/Kimi support disabling verbose chain-of-thought for structured tasks.
        request.update(self.config.get('request_options', {}))
        role_options = self.config.get('role_request_options', {}).get(role,{})
        if set(role_options) & {'model','messages','stream','max_tokens'}:
            raise ProviderError('Role request options cannot override core request fields')
        request.update(role_options)
        cache_key = digest({"endpoint": self.config['base_url'], "request": request})
        path = self.cache / (cache_key + '.json')
        if path.exists():
            result = read_json(path)
            try:
                if validate:
                    validate(result['parsed'])
            except (ValueError, KeyError, TypeError):
                path.unlink(missing_ok=True)
            else:
                with self.lock:
                    self.calls.append({"role": role, "model": request['model'], "cache_key": cache_key, "cached": True,
                                       "invocation_id":self.invocation_id})
                    self.flush_locked()
                return result['parsed']
        started = time.monotonic()
        validation_feedback = None
        with self.semaphore:
            for attempt in range(self.config['max_retries']):
                record = None
                parsed = None
                phase = 'transport'
                request_started = time.monotonic()
                try:
                    self.wait_for_slot()
                    with self.lock:
                        budget = self.config.get('max_api_requests')
                        if budget is not None and self.request_count >= budget:
                            raise ProviderError('API request budget reached; increase only within the authorized scope')
                        self.request_count += 1
                        record = {'request_id':len(self.attempts)+1, 'invocation_id':self.invocation_id,
                                  'role':role, 'model':request['model'],
                                  'cache_key':cache_key, 'attempt':attempt+1, 'status':'started'}
                        record['validation_feedback'] = validation_feedback is not None
                        self.attempts.append(record)
                        self.flush_locked()
                    request_started = time.monotonic()
                    attempt_request = request
                    if validation_feedback is not None:
                        attempt_request = {**request, 'messages': request['messages'] + [{
                            'role': 'user', 'content': 'The previous output failed the program output contract: '
                            + validation_feedback + '\nReturn the complete corrected JSON using the original evidence. '
                            'This is format/contract feedback, not a suggested score or answer.'}]}
                    with requests.Session() as session:
                        session.trust_env = self.config.get('trust_env_proxy', False)
                        r = session.post(self.config['base_url'].rstrip('/') + '/chat/completions',
                                         headers={"Authorization": 'Bearer ' + self.key(), "Content-Type": "application/json"},
                                         json=attempt_request, timeout=self.config['timeout_seconds'])
                    with self.lock:
                        record['http_status'] = r.status_code
                        self.transport_failures = 0
                    if not r.ok:
                        if r.status_code == 429:
                            self.handle_rate_limit(r)
                        # Do not log headers, request bodies, or arbitrary provider error echoes.
                        if r.status_code not in {408, 409, 429, 500, 502, 503, 504}:
                            raise ProviderError(f'Provider rejected request (HTTP {r.status_code})')
                        raise requests.RequestException(f'HTTP {r.status_code}')
                    with self.lock:
                        self.rate_limit_streak = 0
                    phase = 'response_parse'
                    body = r.json()
                    with self.lock:
                        record['usage'] = body.get('usage', {})
                    choice = body['choices'][0]
                    if choice.get('finish_reason') == 'length':
                        raise ProviderError('Output token limit reached; revise the task/budget instead of repeating the identical request')
                    phase = 'output_parse'
                    parsed = parse_json(choice['message']['content'])
                    if validate:
                        phase = 'schema_validation'
                        validate(parsed)
                    result = {"parsed": parsed, "model": request['model'], "usage": body.get('usage', {}),
                              "elapsed_seconds": round(time.monotonic() - started, 3), "attempts": attempt + 1}
                    write_json(path, result)
                    with self.lock:
                        record['status'] = 'success'
                        self.calls.append({"role": role, "cache_key": cache_key, "cached": False,
                                           "invocation_id":self.invocation_id,
                                           **{k: v for k, v in result.items() if k != 'parsed'}})
                    return parsed
                except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as e:
                    if record is not None:
                        with self.lock:
                            record.update(status='failed', error_type=type(e).__name__)
                            record['failure_stage'] = phase
                            if phase == 'schema_validation' and isinstance(e,ValueError):
                                record['validation_error'] = str(e)[:1000]
                                validation_feedback = str(e)[:1000]
                                if self.journal_dir and parsed is not None:
                                    relative = f'invalid-responses/{self.invocation_id}-{record["request_id"]}.json'
                                    # Only model JSON, never transport headers, request bodies, or reasoning traces.
                                    write_json(self.journal_dir / relative, parsed)
                                    record['invalid_response'] = relative
                            if isinstance(e,(requests.Timeout,requests.ConnectionError)):
                                self.transport_failures += 1
                                self.transport_open = self.transport_failures >= self.config.get('max_consecutive_transport_failures',2)
                    if self.transport_open:
                        raise ProviderError('Transport circuit open after repeated network failures; response usage may be unknown') from None
                    if attempt + 1 == self.config['max_retries']:
                        raise ProviderError(f'Call failed after {attempt + 1} attempts ({type(e).__name__})') from None
                    time.sleep(min(2 ** attempt, 8))
                except ProviderError:
                    if record is not None:
                        with self.lock:
                            record.update(status='failed', error_type='ProviderError')
                    raise
                finally:
                    if record is not None:
                        with self.lock:
                            record['elapsed_seconds'] = round(time.monotonic() - request_started, 3)
                            self.flush_locked()
        raise ProviderError('Unexpected provider state')
