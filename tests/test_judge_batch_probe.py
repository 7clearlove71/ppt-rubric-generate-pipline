import json

import pytest

from ppt_rubric.v25.judge_batch import parse_batch_answer


def row(rid, answer='yes'):
    return {'rubric_id': rid, 'evidence': '第2页有证据', 'answer': answer}


def test_restores_requested_order_without_changing_unknown():
    raw = json.dumps({'verdicts': [row('B', 'unknown'), row('A', 'no')]})
    assert [r['answer'] for r in parse_batch_answer(raw, ['A', 'B'])] == ['no', 'unknown']


@pytest.mark.parametrize('rows', [[row('A')], [row('A'), row('A')],
                                 [row('A'), row('C')], [row('A'), row('B', 'maybe')]])
def test_rejects_incomplete_or_ambiguous_batches(rows):
    with pytest.raises(ValueError):
        parse_batch_answer(json.dumps({'verdicts': rows}), ['A', 'B'])


def test_rejects_duplicate_json_keys():
    with pytest.raises(ValueError):
        parse_batch_answer('{"verdicts":[],"verdicts":[]}', ['A'])


def test_rejects_ambiguous_expected_ids():
    with pytest.raises(ValueError):
        parse_batch_answer(json.dumps({'verdicts': [row('A'), row('A')]}), ['A', 'A'])
