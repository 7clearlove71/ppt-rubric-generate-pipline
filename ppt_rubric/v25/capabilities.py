"""Explicit per-role input capabilities; never infer them from model names."""
from __future__ import annotations

import math


ROLES = {'generator', 'reviewer', 'judge'}


def validate_capabilities(config):
    values = config.get('role_supports_images', {})
    if (not isinstance(values, dict) or set(values) - ROLES
            or any(type(value) is not bool for value in values.values())):
        raise ValueError('role_supports_images must map generator/reviewer/judge to JSON booleans')


def supports_images(config, role):
    validate_capabilities(config)
    return config.get('role_supports_images', {}).get(role, True)


def scoring_method(config):
    return 'single_k3' if supports_images(config, 'judge') else 'single_text'


def evaluation_coverage(rubric, config=None, mode=None):
    if mode is None:
        mode = 'full' if supports_images(config or {}, 'judge') else 'content_only'
    if mode not in {'full', 'content_only'}:
        raise ValueError('Unsupported evaluation coverage mode')
    included = [i for i in rubric['items'] if mode == 'full' or i['dimension'] != 'visual']
    ids = {i['id'] for i in included}
    skipped = [i for i in rubric['items'] if i['id'] not in ids]
    return {'mode': mode, 'reason': 'all_dimensions' if mode == 'full' else 'judge_images_disabled',
            'evaluated_rubric_ids': [i['id'] for i in included],
            'skipped_rubric_ids': [i['id'] for i in skipped],
            'score_possible': math.fsum(i['score'] for i in included),
            'unassessed_weight': math.fsum(i['score'] for i in skipped)}


def scoped_rubric(rubric, coverage):
    if coverage != evaluation_coverage(rubric, mode=coverage.get('mode')):
        raise ValueError('Evaluation coverage differs from the frozen rubric')
    ids = set(coverage['evaluated_rubric_ids'])
    return {**rubric, 'items': [i for i in rubric['items'] if i['id'] in ids]}
