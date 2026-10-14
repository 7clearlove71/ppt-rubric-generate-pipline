"""Close this bounded experiment using request journals and complete comparison output."""
import json
from pathlib import Path
from collections import Counter
root=Path(__file__).resolve().parent
roots=[root.parent/f'validation-20261010-v{i}' for i in range(1,7)]
def read(p):return json.loads(p.read_text())
def write(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
budget={'status':'completed','authorization_closed':True,'unused_requests_are_not_new_production_authorization':True,'versions':[str(p) for p in roots]}
usage={}
for provider,stage,limit in [('bailian','rubrics',120),('maas','judgments',300)]:
 rows=[];by_version={}
 for r in roots:
  f=r/stage/'requests.json';a=read(f) if f.exists() else []
  by_version[r.name]=len(a);rows.extend(a)
 assert len(rows)<=limit
 assert not any(x.get('status')=='started' for x in rows), 'Do not close with active requests'
 budget.update({provider+'_limit':limit,provider+'_used':len(rows),provider+'_remaining':limit-len(rows)})
 usage[provider]={'actual_requests':len(rows),'by_version':by_version,'request_statuses':dict(Counter(x['status'] for x in rows)),'reported_usage':{k:sum(x.get('usage',{}).get(k,0) for x in rows) for k in ['prompt_tokens','completion_tokens','total_tokens']},'requests_without_usage':sum(not x.get('usage') for x in rows),'usage_caveat':'Only reported tokens are summed; missing usage is unknown, not zero cost. Unit prices not verified.'}
write(roots[0]/'shared-budget.json',budget);write(root/'usage-summary.json',usage)
auth=read(roots[0]/'authorization.json');auth.update(status='completed',final_bailian_requests=budget['bailian_used'],final_maas_requests=budget['maas_used']);write(roots[0]/'authorization.json',auth)
a=read(root/'agreement.json');g=read(root/'generation-acceptance.json');g['human_alignment']='target_not_met' if a['agreement'] is None or a['agreement']<0.9 or not a['all_decided_cases_evaluated'] else 'development_target_met_holdout_pending';write(root/'generation-acceptance.json',g)
summary={'generation_approved':g['approved'],'generation_total':g['total'],'complete_comparable_cases':a['n'],'decided_human_labels':a['decided_labels'],'matches':a['matches'],'agreement':a['agreement'],'evaluation_coverage':a['evaluation_coverage'],'wilson_95_interval':a['wilson_95_interval'],'working_target':0.9,'working_target_is_not_final_user_accepted_threshold':True,'acceptance':g['human_alignment'],'holdout_evaluated':False,'raw_verdicts_manually_adjusted':False,'budget':budget}
write(root/'validation-summary.json',summary)
rows=[]
for case in a['cases']:
 scores=case['scores'];rows.append(f"| {case['case_id']} | {scores['A']:.2f} | {scores['B']:.2f} | {case['predicted']} | {case['human']} | {'一致' if case['match'] else '不一致'} |")
for case in a['skipped']:
 cid=case['case_id'];scores={}
 for aid in ['A','B']:
  f=root/'judgments'/cid/(aid+'.json');scores[aid]=read(f)['summary']['score'] if f.exists() else None
 prediction = ('tie' if abs(scores['A']-scores['B'])<=3 else ('A' if scores['A']>scores['B'] else 'B')) if all(v is not None for v in scores.values()) else '未完成'
 rows.append(f"| {cid} | {scores['A']} | {scores['B']} | {prediction} | 未定 | {case['reason']} |")
text=f'''# 2026-10-10 开发集端到端验证结果

本轮完成自动生成、自动审核、逐条评分与人工偏好比较。生成配置为v5（prompt 2026-10-10.4、rubric validator 6），最终评分采用v6（judge validator 2），评分提示词及全部rubric内容不变。生成通过 {g['approved']}/{g['total']}；已确定人工标签覆盖 {a['n']}/{a['decided_labels']}，原始胜负匹配 {a['matches']}/{a['n']}。当前结论：**{"未达标" if g['human_alignment']=="target_not_met" else g['human_alignment']}**，不能据此宣布pipeline达到质量验收。

## 逐例结果

编号与A/B均采用冻结manifest映射，02、09的人工编号已按真实候选身份转换；tie表示分差不超过3分。分数为模型原始返回值聚合，没有人工修正。

| 用例 | A分数 | B分数 | 自动偏好 | 人工偏好 | 比较 |
| --- | ---: | ---: | --- | --- | --- |
{chr(10).join(rows)}

09人工选择为“暂时无法判断”，不计入胜负一致率分母。留出04、07、08未运行、未读取人工正文。90%是预先记录的工作目标，尚非用户确认的最终数值门槛。小样本Wilson 95%区间为 {a['wilson_95_interval']}；开发集经过迭代，不是盲测泛化成绩。

## 生成与评分中发现的问题

- 生成阶段：初版仅5/7通过，02、05因来源归属重复而失败。改进通用修订规则后05通过；02通过一次有界的来源要求细化与专用思考修订后通过。正常流程不触发该兜底；默认仍关闭。所有失败草稿保留。
- 03的自动审核遗漏了错误示例：rubric把120÷6.6约等于18.2当成精度错误，实际这是正确的一位小数近似。自动审核通过不能替代数学正确性。
- v3/v5的03/B R13结构化答案yes与理由“本条判no”冲突。v6加入有限的末尾显式结论检查，拒绝矛盾缓存并在共享预算内让模型重新作答；旧版本原始报告保留，没有人工改答案。03/B第3页渲染有明显重叠，R18却判无遮挡。这是评分器可靠性问题。
- 02两个候选均得100分、预测平局，与人工B不一致，诊断见v5的case02-review-findings.json。
- 05与人工同为平局，但自动给出双100，未识别人工指出的实质缺陷；胜负相同不代表评价理由可靠。
- 06的标准对数据引用的存在和一致性约束较强，对推论有效性覆盖较弱。评分器接受A的90%与86%跨分母比较，又遗漏P04与P11的完成状态差异，最终偏好与人工相反。
- 人工理由也有需要保留意见的地方：01把未来栏的AI标定、多中心目标称为已完成；06的90%整改目标不自动等于声称达到95%达标线。保留人工原文与明确选择，不用修改标签提高一致率。

上述是诊断结论，未据此手工修改任何模型分数。详见v1的case01-review-findings.json、v3的case03/case06/case10-review-findings.json，以及v6的verdict-consistency-audit.json。

## 预算与可复核记录

百炼实际 {budget['bailian_used']}/120 次；MaaS实际 {budget['maas_used']}/300 次，均包含重试与各版本消耗。已结束本轮，剩余额度不视为后续批量生产授权。服务报告的token与缺失用量请求数见usage-summary.json，价格未核实，不给出金额。

v1—v6保留配置、来源/代码指纹、冻结代码、生成状态、请求日志和原始评分。v5严格导出7套共113条标准，v6沿用这些已批准标准；6例条目与v3相同，02用最新16条标准完整评分。v6复核缓存并仅对被拒绝的结果重新请求，原始报告未覆盖改写。

## 下一步

显式答案矛盾的拒绝/重试已实现并完成真实缓存复核，但一致率仍未改善。后续先修复通用质量机制，再开展新的有界实验：视觉判据按页检查并用局部图证据复核；加强跨页状态、分母与因果推断检查；为审核器补充有效舍入与伪推论的反例。保留本轮基线，不凭人工排序调整个例权重或平局阈值。开发集稳定后冻结配置，另行验证留出集；当前不进入生产验收。
'''
(root.parent.parent/'docs'/'validation-results-20261010.md').write_text(text)
print(json.dumps(summary,ensure_ascii=False,indent=2))
