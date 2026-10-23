# 来源协议 v2.4：规范目标、条件与审核账本

版本 `2026-10-19.contract-v2.4`，rubric validator 4，发布与导出 schema 3。历史 v2.3/schema 2 产物保留原样；当前入口拒绝把它们直接当作新协议的批准产物。默认五维预算、二元判定和 unknown 区间语义沿用原政策；本次不校准人工胜负。

来源目录仍为原文精确片段，ID、原始字符偏移和文字不变。`source_coverage` 必须对每个片段明确登记 required、context 或 mixed，并解释不评分的背景。多个独立目标可以合法引用同一片段。

`source_targets` 是规范目标账本。每个目标有唯一 ID、kind、object、attribute、value、unit、time、state、mandatory、condition、relation、source_ids 和 scoring_owner。无适用单位、时间或状态时显式填 `not_applicable`，不允许对象、属性或内容为空。数值及其单位、时点和待考状态属于同一事实目标；同对象、属性和时期的 fact 不能靠改变值、单位、状态或关系建立第二个评分所有者。其他 kind 按完整目标内容、条件和关系区分，例如同属“PPT结构/模块交付”的四线对比和1号线深挖是合法独立交付。跨 kind 的语义重复仍由独立审核判断，结构键不作裁决。规范键政策计入批准 fingerprint，防止政策变化后复用旧批准。

每个 Q 的 `target_ids` 恰好拥有一个目标，`reference_target_ids` 只提供上下文。例如模块可以引用空载能耗事实，披露办法可以引用待考来历，模块可以引用全篇公文文体；这些引用不再次拥有事实数值、状态或文体责任。Q 的 text 是摘要，不生成额外通过义务；通过条件只由规范字段编译。

`normalize_source_graph(raw, catalog)` 返回原始JSON的深拷贝和派生日志，只补齐三类由规范 T 可计算的图元数据：Q.source_ids 取其所拥有 T 的 source_ids；Q.reference_target_ids 与关系组件取有序并集；source_coverage.target_ids 从 T.source_ids 反向索引。原始覆盖边额外、缺失或顺序变化均保留 before/after 日志，不靠模型重复维护相同图来决定是否重试。原始JSON、provider缓存与规范 T 不变；逐轮分别保存 raw-requirements、requirements 和 source-graph-derivations。

派生前仍检查原始引用ID，非法或重复的来源/目标引用不能被覆盖后悄悄丢弃。程序不修改 Q 的 kind、importance、text、target_ids，不修改任何 T 字段、条件或覆盖片段的 role/rationale。若某片段被标成 context 却由规范 T 引用，派生后仍保留该语义冲突并拒绝；不得自动改成 required 或 mixed。审核收到 canonical 与全部派生日志，仍须独立核查规范 T 的真实来源支持和 role/rationale，不能以图一致替代语义审核。派生政策也计入批准 fingerprint。

条件支持三种形式：

- `always`：mandatory=true，条件文本和条件来源为空，缺失须检查。
- `if_present`：mandatory=false，来源允许可选内容，出现时核对准确性，未出现自然满足。
- `source_condition`：mandatory=false，来源指定的触发条件成立时须完成；条件本身必须有真实来源引用，未成立时自然满足。

条件的来源必须是目标来源的一部分。结构验证只能证明引用存在，不能证明原文真正支持该条件，这仍是独立审核的责任。

关系类型为 none、before、sequence、depends_on。before 按先后引用两个组件目标，sequence 至少两个；关系组件必须登记为非所有权引用。编译条件只检查已呈现组件之间的关系，组件缺失由组件所有者负责。模块内部事件顺序不能替代模块间的明确先后要求。

批准审核必须保存三类检查。`coverage_checks` 对每个来源片段独立盘点 `source_goals`，区分评分目标和背景，把每个评分目标映射到唯一目标；发现原文要求却没有归属时不能批准。`target_checks` 逐目标核对来源、规范字段及唯一评分所有者，争议、重叠或无来源均阻止批准。`counterexample_checks` 对每个目标构造只改变一个属性的具体假想PPT，实际应用全部判据及例外，列出 expected 与 actual 失败条目和共同修复；条件目标还须检查不触发的正常对照，关系目标还须检查组件互换。相同修复触发多项处罚的审核不能批准。

`required_audit_checks(suite)` 程序生成完整接口清单：每个来源片段所需的 scored/context 目标槽位、每个 T、每个 `(target_id, operation)` 组合。清单只列覆盖义务，不提供审核答案。15个目标含2个条件目标和1个关系目标时，需要15次属性变异、2次条件不触发和1次关系互换，共18项；后三项不能替代对应目标的属性变异。mixed片段必须在 `source_goals` 中分别列出背景和评分目标，不能仅在外层 rationale 提到背景。如果独立审核认为原role无来源依据，须提交具体issue并拒绝，不能为满足接口编造背景。

`build_audit_payload` 将规范T、覆盖和全部判据统一放在 rubric；requirements只保留task_summary、source_policy和Q列表，删除重复T/覆盖与固定库副本。审核仍收到原始来源、图派生日志、派生政策和必检清单。`audit_interface_fingerprint()` 标识审核prompt与接口政策，二者也计入整体批准fingerprint。接口覆盖错误保留原始响应，不自动补写遗漏检查或把未审内容视为通过。

有成立 issue 时允许提前拒绝，不要求模型为尚未检查的部分编造完整记录。批准则必须完整；批准审核嵌入 rubric，计入哈希，并在评分前重新校验。导出保留目标、覆盖、审核及每个条目的所有权与引用关系。

这套结构不能自动识别所有同义改写、模块与全篇的语义重叠或同一原文片段内部的遗漏。最小反例本身也可能写错。离线门禁验证字段、覆盖和责任记录的一致性，不等于证明模型的语义结论真实；模型来源QA也不是真人评测。五类历史漏洞的结构机制和正常对照见 `tests/test_source_contract_v24.py`，在线结果与失败须单独报告，不能以测试通过替代新协议验收。
