# 独立计分实验与真人复评入口

2026-10-19。对应改进计划阶段五、六。默认 `assign_scores`、`aggregate`、五维预算和3分平局阈值均未替换；本页新增工具不调用模型，不读取人工胜负来挑权重。

## 固定答案的计分对照

`ppt_rubric.scoring_experiment` 使用同一份冻结 rubric 和同一批 yes/no/unknown，先用原 `aggregate` 重放基线，再计算单独标为 `experimental_only` 的替代政策。它不改写历史文件，输出文件必须不存在。

```bash
.venv/bin/python -m ppt_rubric.scoring_experiment \
  --rubric artifacts/final-revision-20261014/rubrics/10/rubric.json \
  --judgment artifacts/final-revision-20261014/judgments/10/A.json \
  --view consensus --out artifacts/scoring-comparison-new/10-A.json
```

五维预算仍为30/25/15/15/15。实验只在正确性维度内预设来源事实15分、通用推理10分，不按来源事实数量重新分配通用推理预算。来源事实组没有条目时，15分以 `unused_group_credit` 明示为空组得分，不重新分配给推理；这是需要独立验证的政策假设。

旧版06只有 `C_REASON` 一个正确性条目，故它独占25分；10的正确性条目重要性总和17、`C_REASON` 重要性2，故它仅占 `25 × 2 / 17 = 2.941176…` 分。替代政策让两例的通用推理都占10分。15/10划分并未经过真人锚点验证，不表示优于旧政策，更不能据固定六个人工标签选出“最优”比例。

宽泛条目的诊断记录与计分单元分离：显式等价条目可以用 `--aliases` 映射到同一计分单元；程序不因来源ID相同或文字相似自动认定等价。相同 `harm_id` 只能有一个 `owner_unit_id`，所有者必须存在实际诊断；非所有者不重复扣该损害。精确ID去重只保证申报身份的一致性，语义上的同一损害仍须另外核查。

有限等级为 `full`、`local_material`、`key_unusable`、`unknown`；损失比例分别为0、0.5、1、未决。局部实质缺陷必须解释核心任务为何仍可使用；关键目标不可用必须解释具体哪个要求或决策受到阻断。每条诊断须提供非空 `anchor_ref`；真实锚点的充分性仍需独立审核。程序对单元取最大损失，不按页数、缺陷数量或重复出现次数累加。unknown保留上下界，不自动折算为0.5。

二元重放只比较聚合政策，**不会**把真实作品的no自动认定为已经核实的关键损害等级。`graded_impact` 只有显式传入独立诊断文件时才计算；本次真实作品均未填损害等级。独立诊断文件是一组以下结构的记录：

```json
[
  {
    "rubric_id": "V_READ",
    "severity": "local_material",
    "anchor_ref": "独立证据记录ID及任务影响锚点；此处是格式示例",
    "harm_id": "明确同一实际损害的稳定标识",
    "owner_unit_id": "V_READ"
  }
]
```

复制同一损害、拆分诊断、等价目标拆分及更换分页必须保持区间；加入无关事实不能稀释通用推理预算；修复已知单一损害必须按声明权重返还得分。CLI会运行这些结构稳定性检查；这些检查不证明损害识别正确。

本次离线结果在 `artifacts/improvement-20261019/scoring/`，覆盖历史14份PPT的主评和合并两个视图，共28份对照。原始基线分数均按浮点容差核对，历史判断未变，未读取人工标签、未运行留出。`reproduce.py` 可输出到另一个新目录；分级计分与评分锚点仍未通过真人验证。

## 静态盲评包

`ppt_rubric.human_review prepare` 接收既有 `prepare` 的JSON产物，复制原始PNG/PDF字节到每位评测者的匿名文件夹，附原任务、附件和空白JSON表单。页面顺序完整可查，不依赖JavaScript或点击放大。每名评测者的候选顺序按显式种子独立随机化；映射只留在权限受限的 `organizer` 目录。

```bash
.venv/bin/python -m ppt_rubric.human_review prepare \
  --manifest data/manifest-local.json --prepared artifacts/prepared-dev-local.json \
  --reviewers reviewer-1,reviewer-2 --seed 20261019 \
  --out artifacts/human-review-new
```

只向每位评测者分发其 `reviewers/<评测者编号>/` 文件夹，勿分发 `organizer/collection.json`。文件名隐藏生成者，图中自带的身份线索、意外泄盲和实际阅读情况仍须真人登记，程序不声称已证明盲法成立。

每个表单先填写 `evidence_records`：`id`、匿名 `candidate`、`page`、`observation`、`basis`、`impact`、`certainty`（confirmed/uncertain/preference）；再填理由及 `reason_evidence_ids`、整体选择、是否完整查看、带时区的ISO 8601提交时间及实际可靠性元数据。

整体选择有四类：

| choice | preferred_candidate | 比较标签 |
|---|---|---|
| better | 明确的candidate_1或candidate_2 | 按该记录的私有映射转换 |
| quality_equivalent | null | tie |
| tradeoff_unresolved | null | undecided |
| insufficient_evidence | null | undecided |

这张映射表在生成表单前已固定并登记哈希，导入器拒绝临时改为更匹配自动分数的映射。

## 显式多评测者与裁决导入

每案例至少导入两位不同评测者的真实提交。每条原始记录保留来源文件哈希、匿名映射、原选择、证据和理由；重复评测用不同 `record_id`、实际 `round_id` 和提交时间另存。导入器校验候选PPTX、登记PDF/每页PNG，以及每位评测者实际文件夹内的静态文件哈希，拒绝缺页、渲染变更和来源哈希冲突。

即使两人一致，也不会自动投票或取首份当作canonical。未有明确裁决时，原始选择照常导入，canonical保持 `unlabeled`；不会被旧比较器误当作已决定标签。

需要裁决时，由第三位真人另存 `adjudication.json`，沿用同样的字段，增加 `reviewed_record_ids` 完整列出所有原始记录ID。组织者在对应case的 `adjudication` 填入 `source_path`、该裁决者使用的 `packet_path` 和 `anonymous_to_manifest`；可额外填写 `source_sha256` 锁定原始文件。不得删除分歧、复用原评测者充当第三方、漏掉一份原始意见，或用模型模拟真人。

```bash
.venv/bin/python -m ppt_rubric.human_review import \
  --manifest data/manifest-local.json \
  --source artifacts/human-review-new/organizer/collection.json \
  --out artifacts/human-labels-new.json
```

需要确认“人和模型看同一个渲染”时，额外提供 `--model-render-hashes <独立登记的case到render-bundle哈希JSON>`。该登记应在自动评分时从实际渲染输入冻结，不能拿人工包自己声明的哈希冒充独立登记。没有该证据时 `same_render` 仍为unknown，尽管程序已经核对评测者之间的文件一致。

历史 `import-labels` 命令及 `import_markdown_labels` 的单Markdown格式保持兼容；同案多Markdown仍会拒绝，避免偷偷选一人。旧CLI现在只允许新建输出或完全相同内容的幂等重跑，不能覆盖不同的历史标签。新schema-2标签另存；`label_reliability`单列原始意见分歧和裁决是否存在，不从理由推导胜者、不估计未经设计支持的可靠性系数。

本次已准备 `artifacts/improvement-20261019/human-review/` 的7例、14候选、130页静态包及两组**未分配真人**的槽位。真实评测者已安排0人、提交0份、裁决0份；空白表单会被导入器拒绝。历史渲染保留原有渲染器身份，只迁移文件路径；不能把这些旧报告没有逐图哈希的情况说成已独立证明人机渲染相同。下一步需要两位真实评测者实施复评，必要时第三人裁决后才能报告新人工一致率。
