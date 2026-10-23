"""User-selected minimal source audit: exact template and strict output contract."""
import json


class AuditFormatError(ValueError):
    pass


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AuditFormatError(f'Duplicate JSON key: {key}')
        result[key] = value
    return result


def _constant(value):
    raise AuditFormatError(f'Invalid JSON constant: {value}')


def parse_audit_output(text, rubric_ids):
    """Only valid empty issues means approval. Invalid output always raises."""
    if not isinstance(text, str) or not text.strip():
        raise AuditFormatError('Expected nonempty JSON response text')
    try:
        result = json.loads(text, object_pairs_hook=_object, parse_constant=_constant)
    except (ValueError, TypeError) as error:
        raise AuditFormatError(str(error)) from None
    if not isinstance(result, dict) or set(result) != {'issues'} or not isinstance(result['issues'], list):
        raise AuditFormatError('Expected exactly one issues array')
    allowed = set(rubric_ids)
    for index, issue in enumerate(result['issues']):
        if not isinstance(issue, dict) or set(issue) != {'item_ids', 'problem', 'suggestion'}:
            raise AuditFormatError(f'Issue {index}: expected item_ids, problem, suggestion')
        ids = issue['item_ids']
        if not isinstance(ids, list) or any(not isinstance(x, str) or x not in allowed for x in ids) or len(ids) != len(set(ids)):
            raise AuditFormatError(f'Issue {index}: select unique existing rubric IDs; supplied rubric criterion IDs: {sorted(allowed)}; not requirement IDs such as Q01')
        for key in ('problem', 'suggestion'):
            if not isinstance(issue[key], str) or not issue[key].strip():
                raise AuditFormatError(f'Issue {index}: expected nonempty {key}')
    return result


AUDIT_TEMPLATE = '''你是一位PPT评估标准设计与质量审核专家。你的任务是根据用户原始需求，从全局视角审核整套rubric，找出影响评估合理性的问题，并提出具体修改建议。

你审核的是评估标准本身，不是某份PPT的质量。
审核的目标是判断这套rubric是否可用于评估，不预设它一定存在问题。如果整套rubric已符合用户要求、没有需要修改的实质问题，应直接放行，不必为了提出意见而要求修改。

【用户原始需求prompt】
{{用户原始需求全文}}

【用户提供的来源材料】
{{用户提供的附件或其他来源原文；没有则填“无”}}

【用户已明确的评分约定】
{{用户明确指定的权重、分值或其他评分要求；没有则填“无”}}

【待审核rubric】
以下JSON包含全部待审核条目：
- id：该条rubric的编号。
- content：实际用于评估PPT的完整检查问题。
- dimension：该条rubric实际计分所属的维度，取值为fulfillment（需求落实）、correctness（正确性）、integrity（信息诚信）、narrative（叙事）或visual（视觉）。
- score：该条rubric在总分中的实际分值。

{{rubric_json}}

【审核任务】
请结合用户原始需求、来源材料和评分约定，通读整套rubric，重点检查以下问题：

1. 重复审查
不同rubric是否实质检查同一个要求，导致同一个缺陷被重复扣分？
主题相近、引用相同来源，不一定代表重复。请说明具体什么缺陷会让哪些条目同时扣分。

2. 原子性不足
一条rubric是否捆绑了多个可以独立满足或违反的要求，应拆成分别判断的条目？
如果需要拆分，请指出各个独立检查点，并保留原条目中所有合理要求。
同一事实的对象、数值、单位、时间和状态等必要限定，不应机械拆成多个重复计分条目。

3. 分数分配不合理
各条rubric的分值及整套分数分配，是否违背用户明确的评分约定，或明显偏离用户强调的任务重点？
核对维度预算时，按每条实际提供的dimension字段归类并累加score，再与用户明确的维度预算比较。id仅为条目标识，不代表评分维度；不得按R_Q、C_、N_等编号前缀推断归属。同一编号系列可以包含不同维度的条目，同一维度也可以同时包含来源条目和固定库条目。
重复或过度拆分的条目，是否使某个要求获得了不成比例的总分？
用户明确指定的分值和权重应作为审核依据，不能仅因你的个人偏好要求调整。拆分或合并条目时，应说明如何处理原有分值，避免无依据地增加该要求的总权重。

4. 需求遗漏或偏离
用户明确要求的内容是否遗漏？rubric是否加入了用户没有要求、也不是完成该任务所必需的额外限制？
检查问题是否改变了来源原意，或遗漏了影响判断的必要条件？

先整体判断这套rubric是否已符合要求；符合要求就放行，只有发现有具体依据、确实需要修改的实质问题时才提出修改建议。
轻微措辞差异、篇幅长短或个人表达偏好，不作为阻止放行的理由。
审核以content中实际写出的检查条件为准，不要假定存在未展示的补充规则。
修改建议应明确说明涉及哪些条目，以及应删除、合并、拆分、改写或调整分值的具体内容。

【输出要求】
只返回一个合法JSON对象，不要使用Markdown代码块，不要在JSON之外添加说明。

输出结构：
{
  "issues": [
    {
      "item_ids": ["涉及的现有rubric编号"],
      "problem": "具体问题及依据",
      "suggestion": "具体修改建议"
    }
  ]
}

字段规则：
- item_ids只能填写待审核rubric中真实存在的id，直接复制原编号，不要填写需求编号或拟新增的条目编号。
- 如果发现某个用户要求完全遗漏、没有对应条目，item_ids填写空数组[]，并在problem中指出遗漏的原始要求。
- 每个问题单独填写一个对象；同一个问题不要重复报告。
- 放行的JSON约定为 {"issues": []}：issues为空数组，表示未发现阻止使用的实质问题。
- issues非空表示需要修改，不能同时声称放行。不要另外输出approved、status或“放行”等字段或文字。

放行时的完整输出示例：
{"issues": []}

需要修改时的完整输出示例（示例内容不代表本次审核结论）：
{
  "issues": [
    {
      "item_ids": ["R_Q04"],
      "problem": "该条同时要求说明实施步骤和明确责任人。两者可以独立满足：PPT可能给出完整实施步骤，但没有指定责任人。因此，该条捆绑了两个独立检查点。",
      "suggestion": "拆为实施步骤是否明确、责任人是否明确两条rubric，保留两个要求。拆分后两条分值之和保持该条原有总分，再根据用户对两项要求的重视程度分配。"
    }
  ]
}
'''
AUDIT, _ = AUDIT_TEMPLATE.split("【用户原始需求prompt】", 1)
AUDIT = AUDIT.strip()

SCORING_CONVENTIONS = ('总分100分；五维预算：需求落实30、正确性25、信息诚信10、叙事15、视觉20。'
    '固定视觉分值：V_READ为25/3分，V_LABEL和V_RESIDUE各10/3分，'
    'V_LAYOUT和V_ENCODING各2.5分。信息诚信维度共10分；无来源信息边界条目时I_BOUNDARY为10分。'
    '这些是用户指定的通用评分约定。')


def audit_payload(case, sources, suite, images=True):
    """Only original task, attachments, user weights and actual questions/scores."""
    attachments = [source for source in sources if source['id'] != 'prompt']
    values = {
        '{{用户原始需求全文}}': case['prompt'],
        '{{用户提供的附件或其他来源原文；没有则填“无”}}': (
            '\n\n'.join(source['id'] + (f' ({source["source_name"]}, page {source["page"]})'
                                      if 'page_image' in source else '')
                        + '\n' + source['text'] for source in attachments) or '无'),
        '{{用户明确指定的权重、分值或其他评分要求；没有则填“无”}}': SCORING_CONVENTIONS,
        '{{rubric_json}}': json.dumps([{'id': item['id'], 'content': item['judge_prompt'],
                                      'dimension': item['dimension'], 'score': item['score']} for item in suite['items']],
                                    ensure_ascii=False, indent=2),
    }
    # Substitute only template tokens, never placeholder-like strings inside source data.
    import re
    filled = re.sub('|'.join(re.escape(key) for key in values),
                    lambda match: values[match.group()], AUDIT_TEMPLATE)
    result = '【用户原始需求prompt】' + filled.split('【用户原始需求prompt】', 1)[1]
    if any('page_image' in source for source in sources):
        from .materials import MATERIAL_NOTES, TEXT_MATERIAL_NOTES
        result += '\n\n【材料输入说明】\n' + (MATERIAL_NOTES if images else TEXT_MATERIAL_NOTES)
    return result
