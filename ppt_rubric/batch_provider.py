"""Shared provider pacing, cumulative budgets and durable logical responses."""
from __future__ import annotations

import copy
import threading
from pathlib import Path

from .common import digest, file_digest, read_json
from .provider import Client, ProviderError


def request_profile(config, role):
    return {'endpoint': config['base_url'], 'model': config[role + '_model'],
            'max_tokens': config.get('role_max_output_tokens', {}).get(role, config['max_output_tokens']),
            'options': {**config.get('request_options', {}), **config.get('role_request_options', {}).get(role, {})}}


def transport_config(generator, judge):
    """One actual Client serves generation and judging without mutating config.

    Role-specific options preserve each original request exactly. Views expose
    the original config to rubric provenance and judgment binding code.
    """
    for key in ('base_url', 'api_key_env', 'keychain_service', 'keychain_account', 'trust_env_proxy'):
        if generator.get(key) != judge.get(key):
            raise ValueError(f'生成和评分必须共用同一供应商凭据及端点：{key}')
    result = copy.deepcopy(generator)
    result['request_options'] = {}
    result['role_request_options'] = {}
    result['role_max_output_tokens'] = {}
    result['role_timeout_seconds'] = {}
    for role, config in [('generator', generator), ('reviser', generator), ('judge', judge), ('vision', judge)]:
        if role + '_model' not in config:
            continue
        profile = request_profile(config, role)
        result[role + '_model'] = profile['model']
        result['role_request_options'][role] = profile['options']
        result['role_max_output_tokens'][role] = profile['max_tokens']
        result['role_timeout_seconds'][role] = config.get('role_timeout_seconds', {}).get(role, config['timeout_seconds'])
    # One global in-flight ceiling, with separate stage gates and pacing. A
    # slow generation profile must not reduce the judge's configured capacity.
    result['api_concurrency'] = max(generator['api_concurrency'], judge['api_concurrency'])
    result['batch_stage_limits'] = {stage: {key: config.get(key, 0) for key in
        ('api_concurrency', 'min_request_interval_seconds')}
        for stage, config in [('generate', generator), ('judge', judge)]}
    result['min_request_interval_seconds'] = max(generator.get('min_request_interval_seconds', 0),
                                                 judge.get('min_request_interval_seconds', 0))
    result['max_images_per_request'] = judge['max_images_per_request']
    return result


class BatchProvider(Client):
    def __init__(self, config, cache, state, provider, cap, output, progress, stop):
        super().__init__(config, cache)
        self.state, self.provider, self.progress, self.stop = state, provider, progress, stop
        self.journal_dir = Path(output) / f'{provider}-api'
        self._local = threading.local()
        self._stage_gates = {stage: threading.BoundedSemaphore(limits['api_concurrency'])
                             for stage, limits in config.get('batch_stage_limits', {}).items()}
        self._flushed_attempts = 0
        self._pending = {}
        previous = state.request_count(provider)
        self.config = {**config, 'prior_api_requests': previous,
                       'max_api_requests': max(0, cap - previous), 'max_total_api_requests': cap}

    def pacing_profile(self):
        stage = getattr(self._local, 'stage', None)
        limits = self.config.get('batch_stage_limits', {}).get(stage)
        if limits is not None:
            return stage, limits['min_request_interval_seconds']
        return super().pacing_profile()

    def view(self, config, case_id, stage):
        return ClientView(self, config, case_id, stage)

    def flush_locked(self):
        # Only new/in-flight receipts are upserted, instead of serializing all
        # prior requests on every transition. The inherited lock protects this.
        if not hasattr(self, '_pending'):
            return
        for record in self.attempts[self._flushed_attempts:]:
            record['logical_key'] = getattr(self._local, 'key', None)
            record['batch_context'] = getattr(self._local, 'context', {})
            self._pending[record['request_id']] = record
        self._flushed_attempts = len(self.attempts)
        records, responses = list(self._pending.values()), []
        for record in records:
            if record['status'] == 'success' and record.get('logical_key'):
                cached = read_json(self.cache / (record['cache_key'] + '.json'))
                responses.append((record['logical_key'], cached['parsed']))
        self.state.save_requests(self.provider, records, responses)
        self._pending = {r['request_id']: r for r in records if r['status'] == 'started'}

    def durable_request(self, view, mode, role, system, payload, images, suffix, validate):
        images = list(images)
        key = digest({'profile': request_profile(view.config, role), 'mode': mode,
                      'system': system, 'payload': payload, 'suffix': suffix,
                      'images': [(label, file_digest(path)) for label, path in images]})
        criterion = payload.get('criterion', payload.get('requirement', {})) if isinstance(payload, dict) else {}
        pages = payload.get('pages', []) if isinstance(payload, dict) else []
        detail = str(criterion.get('id', ''))
        if isinstance(payload, dict) and payload.get('criteria'):
            detail = ','.join(c['id'] for c in payload['criteria'])
        if isinstance(payload, dict) and payload.get('document_id') in getattr(view, 'documents', {}):
            detail = '候选' + view.documents[payload['document_id']] + ' ' + detail
        if len(pages) == 1:
            detail += f' 第{pages[0]["page"]}页'
        self.progress.current(view.case_id, view.stage + (' ' + detail if detail else ''))
        cached = self.state.response(key)
        if cached is not None:
            if validate:
                validate(cached)
            self.progress.request(reused=True)
            return copy.deepcopy(cached)
        if self.stop.is_set():
            raise ProviderError('批量任务已停止启动新请求；再次执行相同命令续跑',
                                {'error_category': 'batch_stopped', 'failure_stage': 'before_request'})
        # Resolve credentials before a receipt is counted as an HTTP attempt.
        # Missing environment configuration must stop the shared batch once,
        # rather than consume the budget separately for every queued task.
        if self.request_count < self.config['max_api_requests']:
            try:
                self.key()
            except (ProviderError, OSError):
                details = {'error_category': 'credentials', 'fatal': True, 'failure_stage': 'before_request'}
                with self.lock:
                    self.fatal_error = details
                self.stop.set()
                raise ProviderError('供应商凭据不可用，请设置配置指定的环境变量后续跑', details) from None
        self._local.key = key
        self._local.stage = view.scheduling_stage
        self._local.context = {'case_id': view.case_id, 'stage': view.stage}
        try:
            gate = self._stage_gates.get(view.scheduling_stage)
            if gate is None:
                result = super()._ask(role, system, payload, images, suffix, validate, response_mode=mode)
            else:
                with gate:
                    if self.stop.is_set():
                        raise ProviderError('批量任务已停止启动新请求',
                                            {'error_category': 'batch_stopped', 'failure_stage': 'before_request'})
                    result = super()._ask(role, system, payload, images, suffix, validate, response_mode=mode)
            # Also persist a pre-existing API cache hit; HTTP successes were
            # already committed with their receipts by flush_locked().
            self.state.save_response(key, result)
            self.progress.request()
            return result
        except ProviderError as error:
            self.progress.request(failed=True)
            if error.details.get('fatal') or error.details.get('error_category') == 'request_budget' or self.fatal_error:
                self.stop.set()
            raise
        finally:
            self._local.key = None
            self._local.stage = None
            self._local.context = {}


class ClientView:
    def __init__(self, provider, config, case_id, stage):
        self.provider, self.config, self.case_id, self.stage = provider, config, case_id, stage
        self.scheduling_stage = {'生成标准': 'generate', '评分': 'judge'}.get(stage, stage)

    def ask(self, role, system, payload, images=(), suffix=None, validate=None):
        return self.provider.durable_request(self, 'json', role, system, payload, images, suffix, validate)

    def ask_text(self, role, system, payload, images=(), suffix=None, validate=None):
        return self.provider.durable_request(self, 'text', role, system, payload, images, suffix, validate)
