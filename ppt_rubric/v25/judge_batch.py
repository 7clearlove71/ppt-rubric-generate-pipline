"""Strict multi-criterion response contract for page-local visual judging and probes."""
import json



def compile_batch_prompt(items):
    from .contract_judge import JUDGE_PROMPT

    ids = [item['id'] for item in items]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError('Expected nonempty unique criterion IDs')
    # Keep the original semantic decision rule, replacing only its single-answer format.
    common = JUDGE_PROMPT.split('请先输出可核查的简短理由')[0]
    return common + '''仅当提供的材料不足以核验某条要求时，该条回答unknown。
本次同时检查多条要求。每条独立核验、独立给出具体页码和简短依据，不因其他条目的结论改变本条判断，不合并条目或总评。
只输出一个JSON对象，格式为{"verdicts":[{"rubric_id":"条目ID","evidence":"可核查的简短理由","answer":"yes或no或unknown"}]}。
每个给定ID必须恰好出现一次，不增删ID，每条须有自己的理由。
以下是本次检查的要求：
''' + json.dumps([{'rubric_id': i['id'], 'question': i['judge_prompt']} for i in items], ensure_ascii=False)


def parse_batch_answer(raw, ids):
    if (not isinstance(ids, list) or not ids or any(not isinstance(rid, str) for rid in ids)
            or len(ids) != len(set(ids))):
        raise ValueError('Expected nonempty unique criterion IDs')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key] = value
        return result

    if not isinstance(raw, str) or not raw.strip():
        raise ValueError('Expected nonempty visible response text')
    text = raw.strip()
    if text.startswith('```json\n') and text.endswith('```'):
        text = text[8:-3].strip()
    data = json.loads(text, object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Invalid JSON constant')))
    if not isinstance(data, dict) or set(data) != {'verdicts'} or not isinstance(data['verdicts'], list):
        raise ValueError('Expected verdicts array only')
    rows = data['verdicts']
    if any(not isinstance(row, dict) or set(row) != {'rubric_id', 'answer', 'evidence'}
           or not isinstance(row['rubric_id'], str)
           or row['answer'] not in ('yes', 'no', 'unknown')
           or not isinstance(row['evidence'], str) or not row['evidence'].strip() for row in rows):
        raise ValueError('Invalid per-criterion verdict')
    returned = [row['rubric_id'] for row in rows]
    if len(returned) != len(ids) or set(returned) != set(ids):
        raise ValueError('Missing, duplicate or unexpected criterion IDs')
    by_id = {row['rubric_id']: row for row in rows}
    return [by_id[rid] for rid in ids]
