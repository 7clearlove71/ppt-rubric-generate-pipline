---
name: ppt-rubric-generator
description: 为 PPT 任务构建和审核来源可追溯的加权语义 rubric，运行本项目的构建、评分、回归及人工标签比较。当前入口为 contract-v2.5；支持恢复历史协议。不用于制作 PPT 或评判生成轨迹。
---

# PPT Rubric Generator

2026-10-23 按阶段能力：当前版本 `2026-10-23.contract-v2.5.2`、包0.2.1、rubric/judge validator均12。`role_supports_images`分别配置generator/reviewer/judge，JSON布尔值、缺省均true，不猜模型能力。generator=false时提取/修订及rubric编写只传材料文字；reviewer=false时full审核只传文字（独立审核Client按自己的配置）。judge=false允许配置非K3文本模型，记录single_text，仅内容评分：保留四维原分值80，视觉5条20分显式未评，无图片请求或视觉调用，不伪造视觉unknown；完整rubric仍100。assessment/report的evaluation_coverage、comparison的scope_possible/unassessed_weight、候选映射evaluation_mode同步范围；unknown共同剔除只作用于实评内容，全部剔除仍not_comparable，禁止把内容分数归一化或与完整图文分数混比。材料任一页无文本时对应纯文本阶段失败；候选全无文本时纯文本judge失败，部分空页保留并声明文本缺失不证明原稿缺失；合计文字超限失败。能力绑定生成/请求/缓存/恢复指纹，改变能力或升级须新目录，历史授权不重开、09不恢复。默认true保持K3图文C＋P；纯文本仅C。详见docs/model-capabilities.md。本轮只离线模拟验证，不证明模型阅读质量。此节覆盖下文2.5.1版本及统一必传图片的旧约定。


2026-10-23 附件增强：当前实现为 `2026-10-23.contract-v2.5.1`，包版本0.2.0，rubric/judge validator均11；命令入口和configs文件名保留v2.5/v25。PDF（含图表/扫描）和PPTX材料统一为页级图片＋PDF辅助文字；扫描空文本页仍有可引用ID。提取/重新提取、full审核和judge读取全部材料；逐条编写只读取当前需求、原始prompt及引用页，不传候选或全量无关材料。材料背景不自动成为逐条必需义务。默认材料总页数40、source_dpi120、每请求60图由材料与候选共享，超限明确失败；隐藏页/加密材料拒绝，备注/嵌入文件/动画过程不读。原文件、缓存PDF和图片指纹参与复用/恢复校验；升级使用新运行目录，不迁移历史检查点。batch统一<run>/cache，分阶段沿用同一--cache与来源渲染设置。旧文本附件继续兼容。详见docs/reference-materials.md与docs/input-format.md；本轮仅代码、文档和合成离线测试，未验证真实附件模型阅读质量或在线吞吐，不恢复09或历史授权。此节覆盖下文旧附件限制与validator说明。


2026-10-23 当前默认：`light` 轻量模式；批量任务并发 `--workers 10`；K3 judge 全局最多 30 个在途请求，条目/页面调度默认 30。内容仍为一条 rubric 一次调用，视觉改为同页全部 5 条一次调用，共用该页原图和局部放大图，严格按 rubric ID 返回独立理由及 `yes/no/unknown`。视觉请求数由 `5×P` 变为 `P`，每稿评分调用为 `C＋P`（无视觉项时为 `C`）；不含失败重试。逐页 any-no 聚合、五维30/25/10/15/20、每条权重只计一次及同题 unknown 共同剔除均保持。judge validator 升为10，rubric validator仍为10；旧检查点不能混入新协议，历史记录不迁移或重评。本次仅离线实现与验证，未新增付费API调用。

2026-10-22 通用用户批次优先用`python -m ppt_rubric batch --source <批次> --out <专用目录> --mode light --max-api-requests <百炼整批累计上限> --confirm-bailian-bulk`，full另需`--audit-max-api-requests`。自动导入整个批次、构建、渲染、评分及汇总；输入和输出完整约定见docs/batch.md。重复相同命令自动续跑：跳过完成题、启动未开始题，生成及评分成功响应从SQLite/严格检查点复用，即使cache/api已删除也不重评；模型unknown成功结果继续复用。增量账本、共享跨阶段限流/累计预算、输入/代码/参数/资产绑定和失败历史保持。旧检查点在batch续跑前归档，重建时保留既有逐请求记录，防止续跑中再中断丢失成功记录/HTTP400证据。每10秒直观输出进度，根目录提供summary.json/.md、三种候选映射、progress.json/.log和state.sqlite3。SIGINT/SIGTERM有界停止新请求并保存，在途未知消费保守计入；强制终止不能保证供应商恰好一次，已保存成功结果不重评。该模式不导入或重开历史运行/授权，原judge --resume-from仍保持新目录规则。模型、prompt、validator、权重与unknown政策不变，仅离线模拟验证。

2026-10-22 HTTP鲁棒性：后续保留`unknown：HTTP400`及失败账本，ProviderError传递脱敏服务错误码/Request ID和请求关联信息；账号/鉴权共享熔断，已知可恢复错误有界重试，未知400默认不重试（可显式开启最多一次原样诊断）。runner环境key优先，不回写磁盘。judge可用`--resume-from`＋`--recovery-request-cap`在新空目录只恢复接口失败/未完成项；成功判断（包括模型unknown）复用，来源/图片/PDF/请求/检查点先校验，旧授权不重开、累计消费并校验恢复链，原日志及recovery_history保留。旧无恢复绑定的历史记录不得自动迁移。比较计分与原assessment区间不在此修复中改写；execution单列，缺项仍返回2，致命失败1。仅离线实现，未新增API调用，详见docs/http-failures-and-recovery.md。

2026-10-22 当前2.5同题比较按用户要求共同剔除unknown条目：候选全部评完后，任一候选某条最终rubric为unknown，该条在所有候选总分中忽略，包含模型材料不足与接口失败。视觉先按原any-no规则聚合，只有整条unknown触发剔除。其余条目原分值不变，不重新分配或归一化；比较满分为剩余权重之和，3分持平阈值保持原分值尺度。全部剔除为not_comparable，无分数/区间/胜负。原始assessment及unknown区间、原文、执行错误保留；顶层summary用于共同子集比较，comparison保留独立版本exclude_unknown_across_candidates_v1、完整候选集合、保留/剔除ID、触发候选、剔除权重与剩余满分。批量judge、runner、离线汇总、rubric导出及三种映射同步；映射schema2添加score_possible/excluded_weight/excluded_rubric_ids/comparison_policy。离线核对共同子集与原始分数，历史无comparison报告不自动重算或迁移。rubric validator10/judge9、prompt与构建权重不变。643项离线测试通过，无新增API调用。本节覆盖下文“unknown必然导致比较总分为空”的旧说明；原始assessment及历史结果仍保留区间。

2026-10-22 用户输入与候选映射：用户流程不依赖人工评测；candidate-mapping.json升级schema 2，JSON/CSV/MD均不含人工编号、评测者或人工得分，即使开发汇总显式传labels也不附加这些映射字段。人工比较工具及历史记录独立保留。当前2.5 judge批量根目录、单例runner运行根目录及离线contract_report输出目录自动生成三种映射，保留candidate_key、名称、输入文件、SHA-256、页数、分数/区间与结果路径；名称不传模型。用户输入规范以docs/input-format.md和docs/quickstart.md为准：批次/任务/prompt.txt、任务/候选/result.pptx、可选attachments；自己的完整批次用ingest --split dev。默认auto研究划分不变；超过26候选使用AA/AB等安全ID。同名/同哈希提交不合并，映射顺序非名次；多于两候选当前仅逐份分数/区间，不生成严格总排序。validator、prompt、计分和历史结果不改写。

2026-10-21 当前流程支持**轻量模式 `light`（默认）**和**全量模式 `full`**。两者的区别仅为 rubric 审核：轻量模式跳过审核，首版 rubric 直接进入单 K3 judge；全量模式初审后最多修改两轮（最多三次审核），仍未通过也继续 judge，结果明确标记“审核未通过”并保存原因。审核接口/格式错误或修改失败时使用最近一版完整、通过程序校验的 rubric，保留错误记录；没有任何完整 rubric 时仍报告生成失败。

最新用户模板已逐字接入，审核输入为原始需求、来源、评分约定及全部条目的 `id/content/dimension/score`，按实际 `dimension` 核对预算，禁止按编号猜维度。严格 JSON 解析保持不变：只有合法 `{"issues":[]}` 代表审核通过，继续评分不等于审核通过。`source_audit.status` 分别为 `skipped/passed/not_passed`，模式、原因、原始问题与审核历史随 rubric、评分报告、汇总及导出一起保存。rubric validator **10**、judge validator **9**；历史产物不改写，旧批准不能直接迁入当前流程。五维30/25/10/15/20、single_k3和judge提示词不变。本次562项离线测试通过，未调用付费API或重跑02。

本节覆盖下文历史validator、强制审核与审核失败拦截说明。新生成默认light，可用`--mode full`启用审核；judge/export继承生成模式。旧产物不能手改validator或审核状态迁移。

使用项目中的 `ppt_rubric` 包，从包含 `pyproject.toml` 的根目录运行。先读 [README](../../../README.md) 的当前入口和验证状态；设计与局限见 [contract 架构](../../../docs/final-revision-design-20261014.md)。不要把历史通过率当成当前质量。

## 选择协议

用户要求的当前2.5以冻结2.3为基线，入口 `python -m ppt_rubric contract-v2.5 ...`，配置 `configs/contract-v25-*.json`，详见 `docs/contract-v25-20261020.md`。最新流程：K3提取原子需求 → K3逐条生成单点rubric → Kimi-K2.6独立来源审核 → 单K3内容及视觉评分 → 直接计分。已删除候选独立内容复核与双模型合并；旧正式模型分工中的“内容复核”不再适用于当前2.5，来源审核仍thinking enabled。

每个requirement/rubric只检查一个可独立满足或违反的点，复合来源义务必须拆开并全部覆盖。禁止分行子问题或一个问号打包多个目标；单一事实的对象、数值、单位、时期和状态属于必要限定，存在与准确不拆重复条目。提取、生成、来源审核及修订都明确原子性；程序只做保守结构检查，不能声称自动证明语义原子性。固定库也拆为8内容＋5视觉，各拆分组importance合计保持原项，2026-10-21按用户要求将I_BOUNDARY所在信息诚信预算减5、V_READ增加5：预算30/25/10/15/20，V_READ importance为5/3，其余视觉项分数不变。来源信息边界目标仍按importance分享信息诚信预算，无该类目标时I_BOUNDARY为10分。

实际judge system为PB公共外壳＋一个问题，不追加提取摘要/source_policy、评分范围/例外或response_rules。原始任务、来源、原图和渲染后PDF文字作为待评数据，明确PDF文本层来源及局限，不传PPTX直接提取文字。输出为自由文字理由＋末尾一个boxed yes/no，材料不足允许unknown；Client.ask_text不先json.loads，完整保留原文。沿用PB正则/规范化及首个有效答案优先、否则最后一个的选择，额外识别unknown，不从正文猜测或挑选答案；无效boxed有界重试。历史第三段首个yes末尾no的冲突仍须保留说明，不能据离线解析改历史unknown或分数。

当前rubric validator 8、judge validator 9、scoring_method=single_k3。旧validator 7及更早批准须正常重新构建与独立来源审核，不能手改编号复用。生成/审核仍结构化接口，文本和旧JSON缓存隔离、共享Client预算/限流。每条内容看完整稿，页码留在自然语言理由；视觉每次一页全部5条，共用原图和局部图，严格JSON按ID独立返回理由与结论，按页聚合且权重只计一次。报告直接保存assessment/summary/verdicts，不再输出primary/verification/consensus；unknown仍保留区间。历史产物不迁移或改写。

2.5生成使用 `--source-audit-config configs/contract-v25-auditor.json`（原 `--verification-config`只作为generate/plan别名）；judge只传 `--config configs/contract-v25-judge.json`，传来源审核或verifier配置会在创建Client前拒绝。离线plan按每轮N＋2构建、C＋V×P评分估算，不再另加C次内容复核；拆分后的N/C/V须重新计算，不声称总调用或耗时必然下降。此次仅代码及离线验证，无新API调用或质量结论。普通contract仍为2.4，历史运行按冻结协议恢复。

新建当前 rubric 默认采用 `python -m ppt_rubric contract-v2.5 ...` 与 `configs/contract-v25-*.json`。构建只读prompt/来源，不读取候选；候选只在rubric冻结后进入单K3评分。自动approved不是质量验收。普通contract入口与contract-v24配置只用于明确指定的2.4工作。

恢复已有实验时沿用该目录的协议、配置、授权和唯一累计账本。不带 `contract` 的 generate/audit/judge/export 仍是 legacy/evidence_v1 路径；旧 approved 不能直接迁入 contract，`audit` 也不是 contract 的复审命令。不要为了继续执行而混用协议产物。

## 不变量

- 每题仅一套对当前和未来候选共同适用的 rubric。人工胜负、理由、生成者、轨迹和其他候选答案不进入构建或评分模型输入。
- 来源目标的存在、准确性及其单位/时点/状态只归一个条目。类型决定维度；2.5由K3生成单条问题并经来源审核，judge_prompt仅包含实际问题。必要边界须在问题本身表达，内部范围/排除元数据不传给judge，不能据其假定模型会遵守隐藏例外。旧2.3/2.4仍按各自确定性编译规则恢复。
- 当前用户指定五维预算为需求落实30、正确性25、信息诚信10、叙事15、视觉20；不能为追随某例人工胜负改权重、拆条或阈值。
- 允许语义等价、正常近似、合理建议和明确假设；不得把用途或一般检查提醒扩写成固定标题、页数、措辞或版式义务。
- 原图是视觉依据。提取文字和几何相交只辅助定位，不能证明文字可读或发生笔画遮挡。不得将正常作品中的可见缺陷排除为“转换失败”。
- 视觉判据按页观察，任意no则整篇该条no，否则有unknown则unknown，否则yes；每条权重只计一次。相同遮挡不能再扣布局分。精确defect key去重不能证明语义无重复。
- 当前2.5仅K3评分；原始assessment保留unknown区间，同题比较summary按本节共同剔除unknown，显示剩余满分，不掩盖原始执行失败。历史双模型协议及无comparison报告继续保留原始区间，含未决项不报告历史完整总分。

## 历史contract（2.4）操作

当前2.5使用上文入口和单K3配置，完整命令见2.5文档；以下用于恢复2.4。

1. `contract plan` 离线检查来源、范围及调用上界，确认当次授权。生成不需要候选观察。
2. `contract generate --config configs/contract-v2-k3.json --verification-config configs/contract-v2-auditor.json --out <run>/rubrics`。初始提取/审核后最多两轮来源要求修订；固定质量库问题不能靠重新提取来源假装修复。失败原样保留。
3. `contract judge --config configs/contract-v2-judge.json --verification-config configs/contract-v2-verifier.json --rubrics <run>/rubrics --out <run>/judgments`。仅当前版本且哈希验证通过的approved可评分；主评内容/视觉与独立内容评判分别保存。
4. `python -m ppt_rubric.contract_report --run <run> --labels <labels.json> --out <run>/validation-summary.json`。分别报告主评完整覆盖、合并完整覆盖、区间偏好、人工一致率及其分母、语义抽查、失败和用量。人工标签不等同真值，模型共识也不等同真值。
5. `contract export --config configs/contract-v2-k3.json --rubrics <run>/rubrics --out <export.jsonl>`。默认所选用例须全部批准，可用`--cases`明确选择已批准部分；遗漏需在交付说明中列明。schema 2保留来源、作用域与视觉聚合，不能只剩judge_prompt/score后按页重复计分。

`--workers`并行用例共享同一provider Client的限流/预算/熔断，禁止用多个独立进程绕过限流。命令细节及legacy恢复流程见README。

## 验证与凭据

默认只开发集；规则冻结后才显式 `--split holdout --allow-holdout`，留出结果不回流修订。新旧模型/配置/协议的结果不可拼作统一验证。

用 `contract_regression` 跑固定局部视觉探针，预期答案不进模型输入；必须核对具体对象和扣分归属，不能只报分类命中。Codex图像抽查不是新的人类标注。人工可靠性需要真实重复标注，不能用模型模拟评测者补齐。

百炼大批量使用须先得到当次明确授权，若本机存在项目AGENTS.md则遵守其中约定；公开仓库保留已关闭的历史授权记录，新使用者仍须确认自己的凭据与本次调用范围；`--confirm-bailian-bulk`只落实已有授权。本轮不限次数不构成未来授权，配置中缺省上限也不代表可无限调用。授权受限时，恢复前从唯一累计账本扣除全部失败与重试，历史快照不重复计数。

凭据只从环境变量或钥匙串读到内存，禁止打印/写入日志。缺失usage不当零消费，未知价格不编造金额。限流、传输重试和语义修订分别有界；不按结果重复调用。

本机仅使用workspace dependencies解析出的bundled headless LibreOffice；跨机器配置当地运行时与字体。附件适配和容量边界见README，不能静默丢弃材料或页面。

## case01重复运行的耗时与加速（2026-10-20）

下次用户要求完整重跑case01，使用 `scripts/run_contract_v25_case01.py --out <新目录> --profile fast`。当前只加载k3/auditor/judge三份profile，MaaS只审核来源，候选仅K3评分；每provider共用一个Client，百炼跨生成和评分共享预算/限流。candidate_workers与assessment_workers默认2，百炼最多2在途、开始间隔20秒；吞吐尚未在线验证，保留429退避/熔断。不要使用冻结历史run.py替代新入口。

先用实际批准rubric与页数计算N＋2构建及C＋V×P评分；提取前只给公式。固定库拆分改变了条目数，旧101次/39分钟估计仅为先前双模型协议历史值，不能用于新流程。仅查看结果不调用API；旧validator7及更早批准已过期，当前需重新构建审核，不能靠重用历史rubric绕过。已关闭轮次不重开、旧余额不滚存。性能历史见 `docs/case01-performance-20261020.md`，当前变化见2.5文档。本次无API调用。

2026-10-21 runner支持显式`--case 02`（默认01）；自动验证各稿实际页数，不固定13页。`--bailian-cap`为跨阶段操作上限，不代表新授权；本次只可运行用户明确要求的02。

2026-10-21来源审核ID接口修复：`issues[].item_ids`只接受rubric真实ID（如R_Q01），不接受需求ID Q01或拟新增拆分ID；无效ID反馈列出真实允许列表，不自动映射或改写模型答案。rubric validator 8；旧7及更早批准须重新审核。

02权重单例已关闭（2026-10-21）：rubric validator8/judge9；15→23→31需求、44条rubric；A 77.166667—78.166667（1分HTTP400未决），B100，区间偏好B。实际百炼240/MaaS6、报告2,187,159 tokens另1usage缺失，115.76分钟；同因叠印在R_Q15/V_READ/V_RESIDUE重复计罚，理由及原分均保留，不能称生产验收。新单例须按实际N和页数重估调用与预算，不能照搬01的180操作上限或继承关闭余额。完整记录artifacts/validation-20261021-contract-v25-case02-weights/。
