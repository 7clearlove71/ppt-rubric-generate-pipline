---
name: ppt-rubric-generator
description: 为 PPT 任务构建和审核来源可追溯的加权语义 rubric，运行本项目的构建、评分、回归及人工标签比较。当前入口为 contract；支持恢复历史协议。不用于制作 PPT 或评判生成轨迹。
---

# PPT Rubric Generator

使用项目中的 `ppt_rubric` 包，从包含 `pyproject.toml` 的根目录运行。先读 [README](../../../README.md) 的当前入口和验证状态；设计与局限见 [contract 架构](../../../docs/final-revision-design-20261014.md)。不要把历史通过率当成当前质量。

## 选择协议

新建 rubric 默认采用 `python -m ppt_rubric contract ...` 与 `configs/contract-v2-*.json`。该入口只读 prompt/来源提取 typed requirements，每个要求由程序一对一编译，再由独立模型审核；候选只在 rubric 冻结后进入评分。自动 approved 不是质量验收。

恢复已有实验时沿用该目录的协议、配置、授权和唯一累计账本。不带 `contract` 的 generate/audit/judge/export 仍是 legacy/evidence_v1 路径；旧 approved 不能直接迁入 contract，`audit` 也不是 contract 的复审命令。不要为了继续执行而混用协议产物。

## 不变量

- 每题仅一套对当前和未来候选共同适用的 rubric。人工胜负、理由、生成者、轨迹和其他候选答案不进入构建或评分模型输入。
- 来源目标的存在、准确性及其单位/时点/状态只归一个条目。类型决定维度，judge_prompt由规范字段编译，不能手写另一份相冲突标准。
- 默认五维预算为需求落实30、正确性25、信息诚信15、叙事15、视觉15；不能为追随某例人工胜负改权重、拆条或阈值。
- 允许语义等价、正常近似、合理建议和明确假设；不得把用途或一般检查提醒扩写成固定标题、页数、措辞或版式义务。
- 原图是视觉依据。提取文字和几何相交只辅助定位，不能证明文字可读或发生笔画遮挡。不得将正常作品中的可见缺陷排除为“转换失败”。
- 视觉判据按页观察，任意no则整篇该条no，否则有unknown则unknown，否则yes；每条权重只计一次。相同遮挡不能再扣布局分。精确defect key去重不能证明语义无重复。
- 双模型不互看答案；分歧保留区间，不通过辩论或多次重判逼出胜者。含未决项不报告完整总分；区间稳健偏好须与完整评分分开。

## 当前操作

1. `contract plan` 离线检查来源、范围及调用上界，确认当次授权。生成不需要候选观察。
2. `contract generate --config configs/contract-v2-k3.json --verification-config configs/contract-v2-auditor.json --out <run>/rubrics`。初始提取/审核后最多两轮来源要求修订；固定质量库问题不能靠重新提取来源假装修复。失败原样保留。
3. `contract judge --config configs/contract-v2-judge.json --verification-config configs/contract-v2-verifier.json --rubrics <run>/rubrics --out <run>/judgments`。仅当前版本且哈希验证通过的approved可评分；主评内容/视觉与独立内容评判分别保存。
4. `python -m ppt_rubric.contract_report --run <run> --labels <labels.json> --out <run>/validation-summary.json`。分别报告主评完整覆盖、合并完整覆盖、区间偏好、人工一致率及其分母、语义抽查、失败和用量。人工标签不等同真值，模型共识也不等同真值。
5. `contract export --config configs/contract-v2-k3.json --rubrics <run>/rubrics --out <export.jsonl>`。默认所选用例须全部批准，可用`--cases`明确选择已批准部分；遗漏需在交付说明中列明。schema 2保留来源、作用域与视觉聚合，不能只剩judge_prompt/score后按页重复计分。

`--workers`并行用例共享同一provider Client的限流/预算/熔断，禁止用多个独立进程绕过限流。命令细节及legacy恢复流程见README。

## 验证与凭据

默认只开发集；规则冻结后才显式 `--split holdout --allow-holdout`，留出结果不回流修订。新旧模型/配置/协议的结果不可拼作统一验证。

用 `contract_regression` 跑固定局部视觉探针，预期答案不进模型输入；必须核对具体对象和扣分归属，不能只报分类命中。Codex图像抽查不是新的人类标注。人工可靠性需要真实重复标注，不能用模型模拟评测者补齐。

百炼大批量使用须先得到当次明确授权，若本机存在项目AGENTS.md则遵守其中约定；公开仓库不附带原开发机授权记录，新使用者须确认自己的凭据与调用范围；`--confirm-bailian-bulk`只落实已有授权。本轮不限次数不构成未来授权，配置中缺省上限也不代表可无限调用。授权受限时，恢复前从唯一累计账本扣除全部失败与重试，历史快照不重复计数。

凭据只从环境变量或钥匙串读到内存，禁止打印/写入日志。缺失usage不当零消费，未知价格不编造金额。限流、传输重试和语义修订分别有界；不按结果重复调用。

本机仅使用workspace dependencies解析出的bundled headless LibreOffice；跨机器配置当地运行时与字体。附件适配和容量边界见README，不能静默丢弃材料或页面。
