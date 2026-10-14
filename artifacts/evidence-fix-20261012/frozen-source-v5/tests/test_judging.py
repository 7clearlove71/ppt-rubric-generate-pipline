import pytest

from ppt_rubric.judging import validate_judge_verdict


def test_explicit_contradiction_is_rejected_without_rewriting_answer():
    verdict = {'answer': 'yes', 'pages': [1], 'evidence': '存在虚构具体背景信息，本条判no。'}
    with pytest.raises(ValueError, match='contradicts'):
        validate_judge_verdict(verdict, 2)
    assert verdict['answer'] == 'yes'
    with pytest.raises(ValueError, match='contradicts'):
        validate_judge_verdict({'answer': 'no', 'pages': [1], 'evidence': '最终判定为yes。'}, 2)


def test_quoted_text_and_negations_are_not_guessed_verdicts():
    for evidence in ['不存在文字重叠，满足本条。', '页面原文为“本条判no”。',
                     '局部不满足，但本条允许此例外。', '内容齐全，本条判yes。']:
        validate_judge_verdict({'answer': 'yes', 'pages': [1], 'evidence': evidence}, 2)


def test_judge_evidence_and_localization_remain_required():
    for evidence, pages in [('   ', [1]), (['not text'], [1]), ('present', []), ('present', [True]), ('present', [3])]:
        with pytest.raises(ValueError):
            validate_judge_verdict({'answer': 'yes', 'pages': pages, 'evidence': evidence}, 2)
    validate_judge_verdict({'answer': 'unknown', 'pages': [], 'evidence': '图像不可读'}, 2)


def test_old_contradictory_cache_is_rejudged_within_request_budget(tmp_path, monkeypatch):
    import json
    from functools import partial
    from ppt_rubric.common import load_config
    from ppt_rubric.provider import Client
    config = load_config('configs/default.json')
    config.update(max_api_requests=2, min_request_interval_seconds=0)
    client = Client(config, tmp_path)
    monkeypatch.setenv('PPT_RUBRIC_API_KEY', 'test-only-key')
    answers = iter(['yes', 'no'])
    calls = []
    class Response:
        ok = True
        status_code = 200
        def json(self):
            return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(
                {'answer': next(answers), 'pages': [1], 'evidence': '本条判no。'})}}]}
    class Session:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, *args, **kwargs):
            calls.append(1)
            return Response()
    monkeypatch.setattr('ppt_rubric.provider.requests.Session', Session)
    assert client.ask('judge', 'instruction', {})['answer'] == 'yes'
    check = partial(validate_judge_verdict, page_count=1)
    assert client.ask('judge', 'instruction', {}, validate=check)['answer'] == 'no'
    assert client.ask('judge', 'instruction', {}, validate=check)['answer'] == 'no'
    assert len(calls) == client.request_count == 2


def test_visual_page_coverage_and_original_number_are_required():
    from ppt_rubric.judging import validate_visual_page
    good = {'page': 3, 'checks': [{'rubric_id': 'V1', 'status': 'fail', 'evidence': 'Chart labels overlap in lower half'}]}
    validate_visual_page(good, 3, ['V1'])
    for bad in [dict(good, page=1), dict(good, checks=[]), dict(good, checks=good['checks'] * 2)]:
        with pytest.raises(ValueError):
            validate_visual_page(bad, 3, ['V1'])


def test_visual_concerns_cannot_be_ignored_but_can_be_disputed():
    concerns = [{'page': 2, 'status': 'fail', 'evidence': 'Overlap'},
                {'page': 3, 'status': 'unknown', 'evidence': 'Unreadable'}]
    result = {'answer': 'yes', 'pages': [2, 3], 'evidence': 'Checked'}
    with pytest.raises(ValueError, match='resolve'):
        validate_judge_verdict(result, 3, concerns)
    result['visual_resolutions'] = [
        {'page': 2, 'decision': 'uphold', 'evidence': 'Bottom chart labels obscured'},
        {'page': 3, 'decision': 'dispute', 'evidence': 'Header and body are legible in supplied image'}]
    with pytest.raises(ValueError, match='upheld'):
        validate_judge_verdict(result, 3, concerns)
    result['answer'] = 'no'
    validate_judge_verdict(result, 3, concerns)
    result['visual_resolutions'][0].update(decision='dispute', evidence='Boxes touch but all characters are readable')
    result['answer'] = 'yes'
    validate_judge_verdict(result, 3, concerns)
    result['visual_resolutions'][1]['decision'] = 'uphold'
    with pytest.raises(ValueError, match='upheld'):
        validate_judge_verdict(result, 3, concerns)
    result['answer'] = 'unknown'
    validate_judge_verdict(result, 3, concerns)
