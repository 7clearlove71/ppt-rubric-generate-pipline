# PPT Rubric Pipeline

**首次运行：先看 [安装与快速开始](docs/quickstart.md)**。公开仓库包含源码、测试、全部研究文档和离线示例；原始评测数据及实验日志未公开，历史 `data/`、`artifacts/` 路径是本地记录，不随 clone 提供。详见 [发布范围](docs/publication-scope.md)。

首次了解本项目，先看 **[方案概览：调用流程、完整评审 prompt 与真实用例](docs/pipeline-overview.md)**，了解每次模型调用的输入输出、内容与视觉的处理批次，以及评分标准的生成和计分方式。

当前开发主线是 **contract-v2.3：从PPT任务的原始需求与材料提取目标，程序确定性编译一题一套rubric，再独立审核**。构建过程不读取候选PPT、生成轨迹或人工胜负；候选成品只进入后续评分。一套最终标准适用于同题所有已有及未来候选。

**验证状态：2026-10-14本轮已关闭，本版未通过生产质量验收。** 当前为 `2026-10-14.contract-v2.3`、rubric validator 3，333项离线测试通过。7/7自动批准，但来源QA在4例确认5项实质问题；130/130页完成判断，主评14/14份、合并12/14份有完整分数。主评人工一致率66.7%（4/6，覆盖6/6）；合并完整分数一致率75%（3/4，覆盖4/6）；区间比较60%（3/5，覆盖5/6）。定向成品QA另记录17项判定或支撑问题，不能用一致率代替语义质量。详见[最终结果与限制](docs/final-revision-results-20261014.md)及机器报告（本地实验记录：`artifacts/final-revision-20261014/validation-summary.json`）；旧版33.3%或50%不与本版直接作提升比较。

新入口是 `python3 -m ppt_rubric contract ...`，也可直接运行 `python3 -m ppt_rubric.contract_v2 ...`。没有 `contract` 的 `generate`、`judge`、`export` 等命令继续执行历史兼容管线，`configs/default.json` 也仍属于旧版；它们不会自动切换到contract。历史实现、结果和旧命令保留在本文后半部分。

## 当前构建与评分方法

1. 原文建立稳定的来源片段ID，保留连续原文及位置。模型选择ID，程序回填来源，不要求反复抄写引文。
2. 提取带类型的独立来源目标Q：事实、独立交付物、表达要求、明确的信息边界。用途和一般检查提醒保留为上下文，不自动变成额外扣分义务。
3. 每个Q确定性生成一条 `R_<Q.id>`，保留来源ID与重要性；事实“存在且准确”只归正确性。固定库补充推理、信息诚信、内容组织和视觉检查。没有自由DESIGN扩写或候选驱动合成。
4. 独立审核对照原始来源检查遗漏、越权、相邻目标重叠和成立的反例。拒绝时最多修订来源要求两轮；无法靠来源修订解决的固定库问题停止。初次提取与审核为2次逻辑调用，最多6次；失败重试另按请求账本累计。
5. 只发布通过审核且指纹完整的 `rubric.json`。旧批准不能直接升级，当前编译模板、来源、模型请求设置或验证规则不匹配时，原产物按哈希归档，再走正常构建与审核。

五维总预算固定为需求落实30、正确性25、信息诚信15、叙事表达15、视觉15；组内按来源目标重要性分配。该预算是研究协议，不是已经证明最优的人工偏好权重。

当前评分配置由百炼K3评单份匿名PPT的内容和局部视觉，MaaS Kimi-K2.6独立复核内容；两者不互看答案，也不做跨候选改判。视觉逐页判断后，每条标准在整份PPT只计一次权重。主评完整分数、独立判定分歧区间、在所有未决取值下仍不变的偏好，以及人工比较分别报告。模型相互认可不是事实真值，原人工标签也不被当作必然正确的优化目标。

设计与预注册见 [最后一轮设计](docs/final-revision-design-20261014.md)，本次确定性编译的具体原因见 [v2.3来源独立抽查](docs/contract-v23-source-only-audit.md)，历史根因见 [构建审计](docs/final-revision-history-audit.md)，人工标签局限见 [人工审计](docs/final-revision-human-audit.md)。

## 当前入口与产物

从项目根目录、在具备依赖的Python环境中运行。默认选择7个开发用例：01、02、03、05、06、09、10；可用 `--cases 01,02` 明确选择子集。留出04、07、08保持隔离，显式开放仍须遵守冻结后的验证约定。

以下示例把同一轮的生成与评分分别放在 `rubrics/` 和 `judgments/`，供离线报告统一读取。付费命令须在已授权范围内执行；`--confirm-bailian-bulk`只落实已有授权，不产生授权。2026-10-14本轮授权已关闭，实际百炼106次、MaaS35次；这次不限次数的授权不延伸到未来任务，历史授权也各自关闭。

```bash
# 离线构建计划：不读取凭据、不渲染、不调用模型
python3 -m ppt_rubric contract plan \
  --config configs/contract-v2-k3.json \
  --verification-config configs/contract-v2-auditor.json \
  --out artifacts/contract-run/plan.json

# 来源提取、确定性编译、独立审核
python3 -m ppt_rubric contract generate \
  --config configs/contract-v2-k3.json \
  --verification-config configs/contract-v2-auditor.json \
  --out artifacts/contract-run/rubrics --workers 2 --confirm-bailian-bulk

# 对当前版本已批准rubric执行成品评分
python3 -m ppt_rubric contract judge \
  --config configs/contract-v2-judge.json \
  --verification-config configs/contract-v2-verifier.json \
  --rubrics artifacts/contract-run/rubrics \
  --out artifacts/contract-run/judgments --workers 2 --confirm-bailian-bulk

# 离线报告：读取上述目录及真实账本，不调用模型
python3 -m ppt_rubric.contract_report \
  --run artifacts/contract-run --out artifacts/contract-run/report.json
# 需要人工比较时，追加 --labels artifacts/human-labels-dev.json
# 人工标签仅进入该离线比较，不回传提取器、审核器或judge

# 仅导出所选用例中当前版本有效、已批准的schema 2 rubric
python3 -m ppt_rubric contract export \
  --config configs/contract-v2-k3.json \
  --rubrics artifacts/contract-run/rubrics \
  --out artifacts/contract-run/rubrics.jsonl

python3 -m ppt_rubric contract --help
python3 -m ppt_rubric.contract_report --help
```

`--workers`为正整数，默认1，作用是并行处理不同用例。所有用例共享同一个主Client及同一个独立审核/复核Client，共用各自的并发限制、请求间隔、预算、日志和熔断；当前配置每端API并发仍为1。`--workers 2`可以让一个用例等待MaaS时，另一个用例使用K3，不会为每个用例创建独立限流器。共享 `run-summary.json` 只由主线程写入，每例产物位于独立子目录。不要另外启动多个进程绕过共享限流。

生成有质量失败或调用错误时返回退出码1；评分仍有unknown时返回2，不得将进程结束当成全部评分完成。报告可离线检查已有进度，但正在运行时的部分结果不是最终验收。实际消费以各阶段 `primary-api/requests.json` 与 `verification-api/requests.json` 为准；历史快照不重复计数，缺失usage不按零消费。若配置 `max_api_requests`，它限制该Client本次启动的请求数，跨恢复授权核账仍须计入旧日志。

**schema 2导出保留评分语义。** 每题一行，包含 `rubric_hash`、`source_catalog`、`scoring_policy`，每条含 `id`、`judge_prompt`、`score`、`scope`、`scope_type`、`aggregation`。下游必须同时读取作用域与聚合规则，不能只拿两字段就声称复现当前协议：yes获得该条全部权重，no为0，unknown保留分数区间；视觉条目按“任一页no则整条no，否则有unknown则unknown，否则全部yes”聚合，不按页重复加分或平均分配权重。

导出会验证批准状态、当前版本、来源指纹、规范编译结果和评分预算。所选用例存在缺失、失败或过期产物时不静默省略；需要导出已验证子集，应显式用 `--cases` 选择，并在交付时说明覆盖范围。当前schema 2与旧版仅 `judge_prompt + score` 的输出需要分别处理。

## 运行环境（共用）

Python 3.9+，`requests`、`PyMuPDF`，以及 headless LibreOffice。`pytest` 用于测试。

```bash
python3 -m pip install -e '.[test]'
python3 -m ppt_rubric --help
```

公开配置使用 PATH 中的 `soffice`，字体默认继承系统环境。也可将 `soffice` 改为本机绝对路径、在 `font_dirs` 中配置字体目录。在 Codex 原开发机使用 workspace dependencies 解析出的 bundled 版本；发布前本机配置已备份至不上传的 `configs/local/`。

## 数据与人工参照（共用）

- 人工原始记录：`data/10_top_sample_20261010/<用例编号>/评测人_<姓名或标识>.md`，共10份；01—05为 `评测人_zxm.md`，06—10为 `评测人_ldl.md`。
- 新旧样本目录的60个 `prompt.txt`、`result.pptx`、`trajectory.jsonl` 文件已逐一核对 SHA-256，内容一致。`data/manifest.json` 继续引用 `data/10_top_sample`，保留已有候选 A/B 映射、文件指纹和开发/留出划分，无需重新导入。
- 两个样本目录中的旧 Codex 代评文件 `人工评测结果.txt` 和 `human_evaluation.json` 已删除，不作为人工标签使用。
- `import-labels` 已实现 Markdown 到比较标签 JSON 的转换，按实际候选身份与哈希校验 A/B 映射，保留原文及映射；`compare --labels` 读取转换后的 JSON。开发集02、09需交换 A/B。09明确“暂时无法判断”，保留未定，不从理由推断胜负、不当作持平。当前开发集有6个已决定标签，留出正文未读取。
- 2026-10-10历史版的自动生成→完整judge→人工比较方案及预算见 [验证协议](docs/validation-protocol-20261010.md)；当前contract验证设计见 [最后一轮设计](docs/final-revision-design-20261014.md)。比较报告同时提供覆盖率、未完成项和置信区间，部分用例匹配不能冒充整体通过。
- 人工胜负和理由不进入生成器或 judge 输入。开发阶段只分析开发集；留出集04、07、08的评测正文保持隔离，规则冻结后再用于最终验证。

## 当前适用边界

- 尚未完成人工一致率校准，也未在数千例规模上压测。
- 附件适配支持 UTF-8 文本、Markdown、CSV、JSON 和纯文本 PDF。PDF若含图片或矢量图形会明确拒绝，避免只读文字而丢失图表证据。扫描 PDF、图像、Office 附件需要后续增加视觉/解析适配；遇到不支持的格式会失败，不会静默忽略。附件文件哈希也纳入来源指纹。
- 当前contract内容判断查看单份PPT的全部原始页面；视觉判断按少量原始页分批，并可附同页局部图。超过供应商图片或上下文容量仍会明确失败，不静默截断；大文档的内容分块还需要独立设计。
- LibreOffice 与 PowerPoint 可能存在渲染差异。字体发现仅为转换子进程配置，缓存记录字体环境指纹。字体替代仍可能改变字形和换行，应抽查代表性页面。
- 只评静态可见内容，不评动画、转场和轨迹，不自动联网核查候选自行添加的外部事实。无法核验时保留不确定性。
- 生产审核不会为了满足人工排名而逐例改权重。调整规则必须版本化，并保持留出集隔离。

项目 skill 位于 `.agents/skills/ppt-rubric-generator/SKILL.md`，已与当前contract入口及历史恢复边界对齐。人工评测和 rubric 生成设计参考 [PresentBench 评测方法与偏好指南](docs/presentbench-evaluation-guide.md)，其中按五个维度概括评测内容、判断方法与计分规则。渲染等技术对齐及差异详见 [PresentBench 对齐记录](docs/presentbench-alignment.md)。

## 历史实现与验证记录

以下保留旧管线的设计、实测数字和可复核证据。历史的“批准”“完整评分”及一致率只适用于各自版本，不构成contract-v2.3的结果。

### 2026-10-10—14历史实测

为一个 PPT 需求生成一套通过自动审核的语义 rubric。输入包含 prompt、两份或更多候选 PPTX，可带来源材料；候选用于发现遗漏，最终标准必须来自需求、材料或合理的通用质量原则。轨迹暂不参与评分。

2026-10-13—14的历史开发版尚未达到可靠评分或高人工一致率要求。该轮开发集验证已结束：7/7 rubric批准、8/14份PPT完整评分；可比较4例中匹配人工2例，**一致率50%（2/4），明确人工标签覆盖4/6**。03、09复核未决，10因页面证据失败未进入逐条实判。模型为百炼K3生成/审核、MaaS Kimi-K2.6评分；该轮最终prompt `2026-10-13.12`、rubric validator 10、judge validator 13，154项离线测试通过。本轮是逐步修复的开发验证，不是最终validator13的全量独立重测。详见[历史轮次实测报告](docs/validation-results-20261013.md)及[工程修复记录](docs/validation-20261013-engineering.md)。

20份PPTX共179页已渲染，匿名人工标注页面已生成。人工评测记录位于 `data/10_top_sample_20261010/01`—`10` 下的 `评测人_*.md`。开发期间保留每个版本的通过、失败和误判记录；自动审核通过不代表人工质量验收通过。历史2026-10-10版本曾实现7/7生成通过、14/14完整评分，6个明确人工标签中匹配2个（33.3%），09人工未定；这不是现行协议的质量结果。历史报告见[2026-10-10验证](docs/validation-results-20261010.md)，开发过程见 `docs/development-validation.md`。留出集未运行。

2026-10-12 修订后完成单例03实测（prompt `2026-10-12.1`、rubric validator 8、judge validator 4）：A=76.43、B=93.57，自动仍选B、人工持平，分歧未解决。逐页检查抓到了B第3页重叠，但重复扣分、推论覆盖和判定一致性仍有问题；不能宣称全开发集一致率改善。实际百炼5次、MaaS69次，实测已结束。详见 [实测结果](docs/pilot-results-20261012.md) 和 [修订记录](docs/quality-revision-20261012.md)。

### evidence_v1与更早的构建方式

2026-10-12 新增实验协议 `evidence_v1`，首轮使用当时的MaaS配置（现保留为 `configs/evidence-v1-glm-experimental.json`）：先冻结缺陷归属，再逐页登记证据、程序复算、逐条判定，最后做匿名跨候选判定一致性检查。该轮冻结版本为 prompt `2026-10-12.2`、rubric validator 9、judge validator 5；93项离线测试通过。同日完成7个开发用例真实生成实测：0/7批准（5例调用输出校验/解析失败、2例质量失败），没有进入成品评分，已决定人工标签的完整评分覆盖率为0/6，人工一致率不可计算，不能写成0%或沿用旧版33.3%。实际MaaS请求50次、服务报告1,034,328 token，百炼0次。默认配置仍走 legacy 路径；旧 rubric 须复审，不能直接迁入新协议。设计、逐例实测结果和运行步骤见 [证据协议说明](docs/evidence-protocol-20261012.md)。

随后按用户要求改用百炼K3生成/观察/审核，配置为 `configs/evidence-v1-k3.json`，评分仍用MaaS Kimi-K2.6，协议与代码保持冻结。本轮生成1/7通过，仅09获批；09完成逐页证据、逐条判定和有界一致性复核后，双方各9/17条unknown，47.5分权重未解决，不能报告完整总分或胜负。人工一致率仍不可计算，明确人工标签覆盖0/6。百炼实际47次、MaaS119次，均在本轮120/900授权内，现已结束。抽查发现错误计算关系导致正确百分比被误判、视觉重叠漏检、一致性复核疑似过度拒绝等问题。详见 [实测结果与诊断](docs/evidence-protocol-20261012.md)；机器汇总位于 `artifacts/validation-20261012-evidence-k3-v1/validation-summary.json`。

前一轮修复版为 prompt `2026-10-13.10`、rubric validator 10、judge validator 7。K3负责生成/审核，MaaS Kimi-K2.6负责评分。128项离线测试通过；01真实修订/审核通过，修订超时已按角色处理。09单图观察准确识别“同比/亿”叠印，无重叠负例通过；推理组件排除正确计算与跨页披露误罚。最终视觉API响应因图片引用接口被拒，修复后原样离线重放通过，`.10`提示措辞尚未再在线调用；视觉理由仍有程度夸大。此前06复审和09/R04复核也已通过，但没有最新整批完整分数或人工一致率。详见 [本次继续修复与实测](docs/evidence-continuation-20261013.md) 和 [此前修复记录](docs/evidence-repair-20261012.md)。历史33.3%不代表现行质量。

2026-10-13批次目录为 `artifacts/validation-20261013-evidence-v1/`，授权已关闭，实际百炼56/120次、MaaS607/900次，未用额度不滚存。本轮修复视觉图片与观察ID契约、页面几何引用登记、引号和逗号排版兼容、来源引用错误反馈，并支持按角色设置输出上限及超时。01、02、05、06有完整总分，但仍发现视觉漏判、重复扣分与理由错误；全复核通过的05、06均未匹配人工。03、09有界复核后保留unknown；10有两页被供应商403拒绝，另一页输出校验耗尽重试。生成批准、完整评分、复核通过和人工一致率分别统计，不能用其中一项代替其他项。

1. 固定用例清单、文件哈希、匿名候选编号以及开发/留出划分。
2. PPTX 转 PDF，并将全部页面渲染为 150 DPI PNG；提取按页文字作为辅助证据。
3. 仅根据 prompt 和材料提取要求，建立基础 rubric。
4. 视觉模型逐份检查匿名候选，提出可泛化的遗漏点。
5. 生成器综合所有候选观察，产出唯一一套 rubric。
6. 程序检查结构、来源引用、覆盖和评分预算；独立模型检查语义质量，再尝试构造重复计分或越权要求的具体反例。结构不合法时先修订，跳过无效的模型审核。
7. 每次构建自动修订最多两轮。默认保持一次构建；实验配置 `max_source_refinements: 1` 可在持续的来源归属冲突后，仅根据原始材料细化一次复合要求，再重建并审核，原失败记录保存在 `source-pass-0/`。要求细化须保留完整来源映射，不接收人工答案。通过后发布 `rubric.json`；否则标记失败并继续其他用例。候选里的文字重叠等视觉缺陷作为评测证据，不自动当作转换失败而排除差作品。

生产生成不需要人工参与。模型自检提供质量筛选，不等于人类一致率证明。人工只参与开发期标注及验收。

开发实验还可用 `source_refinement_reviser_role: "reviser"`、`reviser_model: "kimi-k3"` 和 `role_request_options.reviser.enable_thinking: true`，只对来源细化后的修订启用独立思考角色。需要同时配置 `max_source_refinements: 1`；默认不启用该兜底，也不因此放宽审核或增加授权预算。已运行的完整配置保存在 `artifacts/validation-20261010-v5/generation-config.json`，属于开发实验配置，尚非质量验收通过的生产推荐。

### 历史配置与授权记录

另提供 `configs/maas-text-review.json` 作为 GLM 文字审核实验配置；它与默认审核器均出现过真实用例的误判，尚未选定生产审核配置。

旧版 `configs/default.json` 默认使用 ModelArts MaaS：GLM-5.2 负责文字生成，Kimi-K2.6 负责视觉观察、语义审核及成品 judge。密钥只从环境变量 `PPT_RUBRIC_API_KEY` 或 macOS 钥匙串读取，不写日志或交付文件。其他 API 域名必须使用显式环境变量，程序不会把 MaaS 钥匙串凭据发送给其他域名。

另提供 `configs/bailian-k3.json`：阿里百炼北京共享兼容接口，使用 `kimi-k3` 和 `enable_thinking=false`。独立钥匙串 service 为 `codex.alibaba-bailian.api-key`，account 为 `codex`，凭据绑定 `dashscope.aliyuncs.com`，与 MaaS 完全分开。已测试文本连通性，实际多模态测试结果记录在 `artifacts/bailian-k3-vision-check.json`。生产时可另行配置业务空间专属域名及对应凭据绑定。

**百炼 key 大批量使用必须先得到用户明确确认。** 2026-09-30的30次限定校验已结束、额度已用尽。2026-10-10已获新一轮开发验证授权：7个开发用例的百炼生成/审核合计最多120次请求（含重试和恢复），MaaS完整评分管理上限300次，不运行留出集。本轮已结束：跨v1—v6百炼实际76/120次、MaaS284/300次；授权与联合预算在 `artifacts/validation-20261010-v1/`，最终报告在 `artifacts/validation-20261010-v6/`。未用额度不构成后续批量生产授权。旧版百炼 CLI 默认只允许单用例、最多12次实际请求（含重试）；只有在用户已经明确批准当前批量范围后才能使用 `--confirm-bailian-bulk`。不得将批量拆成多次单用例来绕过确认。详见项目 `AGENTS.md`。

配置默认仅对 API 子进程请求禁用应用层代理；这不代表绕过系统 VPN/TUN。API 消费受实际服务计费规则约束。`usage.json` 保存服务返回的 token 用量，不猜测价格。

`requests.json` 逐次记录实际请求状态、HTTP 状态与服务返回的 token 用量，包含失败重试；缺失用量明确标记，不能当作免费。同一输出目录恢复运行时累加日志，不覆盖此前的消费记录；请求上限仍按本次命令执行计算，因此跨命令的授权总量需先扣除历史消耗。发生 429 时当前进程的工作线程共享退避时间，连续限流触发熔断。服务要求等待超过60秒时停止本轮请求，不提前重试。重复网络超时也会熔断；输出被token上限截断时不会原样重复请求。恢复时复用成功缓存。多个独立进程不共享限流器，批量任务应使用一个进程的 `--workers`。

### Legacy兼容命令与数据工具

以下未带 `contract` 的模型命令运行旧管线，保留用于历史复现。新的来源合同编译请使用本文开头的入口。

在项目根目录执行：

```bash
# 原始样例导入；现有清单仍使用此目录，人工结果位置见下文
python3 -m ppt_rubric ingest --source data/10_top_sample

# 生成所有用例的匿名评测页面和 PDF/PPTX 副本
python3 -m ppt_rubric human-review

# 默认仅开发集，全自动生成与审核；共享 API 并发上限
python3 -m ppt_rubric generate --workers 2

# 单个开发用例
python3 -m ppt_rubric generate --cases 01

# 离线检查文件和容量，不读取凭据、不渲染、不调用模型
python3 -m ppt_rubric plan --operation generate --cases 01,02
python3 -m ppt_rubric plan --operation judge --cases 01 --rubrics artifacts/rubrics

# 运行后检查批准状态、失败队列和服务返回的用量
python3 -m ppt_rubric report --run artifacts/rubrics

# 已知缺陷回归：合成样例，不使用人工偏好或现有候选
python3 -m ppt_rubric.regression --max-api-requests 12

# 导出下游需要的 judge_prompt + score；一行一个题目、一套 rubric
python3 -m ppt_rubric export --cases 01,02 --rubrics artifacts/rubrics --out artifacts/rubrics.jsonl
# 若明确需要只导出已通过部分，可加 --skip-unapproved；遗漏用例会写入旁路报告

# 使用阿里百炼 K3 生成；MaaS 配置仍可用于独立成品校验
python3 -m ppt_rubric generate --cases 01 --config configs/bailian-k3.json --out artifacts/rubrics-k3

# 在校验规则升级后复审已有结果，不重新读取候选或生成基础 rubric
python3 -m ppt_rubric audit --cases 01 --rubrics artifacts/rubrics-k3 --out artifacts/audited-rubrics

# 用已批准 rubric 逐条独立评判候选成品
python3 -m ppt_rubric judge --cases 01

# 小规模接口冒烟测试；未判完不会给最终总分
python3 -m ppt_rubric judge --cases 01 --limit 2 --out artifacts/smoke-judgments

# 导入人工 Markdown；默认只读取开发集，核对实际候选身份及文件哈希
python3 -m ppt_rubric import-labels --source data/10_top_sample_20261010 --out artifacts/human-labels-dev.json

# 比较结构化人工标签（也支持页面导出的 JSON）
python3 -m ppt_rubric compare --labels artifacts/human-labels-dev.json

# 规则和权重冻结后，显式开放留出集
python3 -m ppt_rubric generate --split holdout --allow-holdout
python3 -m ppt_rubric judge --split holdout --allow-holdout
python3 -m ppt_rubric compare --labels /absolute/path/human_labels.json --split holdout

python3 -m pytest -q
```

7 个开发用例：01、02、03、05、06、09、10。3 个留出用例：04、07、08。划分使用固定 seed=20260930，发生在模型评测及人工标签读取之前；早期数据盘点读取过所有 prompt 的开头和页数。留出集可统一渲染并人工标注，但不用于调整生成规则。

### Legacy运行与输入约定

运行缓存按来源、提示词、模型、配置和图片内容寻址；成功调用可复用，失败调用不当作成功缓存。输入和 rubric 版本不一致时拒绝评分。judge输出若结构化答案与理由末尾明确的“本条判yes/no/unknown”结论矛盾，则拒绝并按预算重试；同样复核旧缓存。此有限检查不等于语义正确性证明。输出每例独立、使用原子 JSON 写入。不同进程请使用不同输出目录；当前不提供跨进程任务锁。

导入目录采用 `用例目录/候选目录/result.pptx`。prompt 可以放在每个候选的 `prompt.txt`，也可以在用例根目录共享一个 `prompt.txt`；两者同时存在须一致。可选来源材料放在 `用例目录/attachments/`，导入时记录哈希，变化后拒绝沿用已冻结清单。候选数量可以大于2；轨迹不读取、不评分。

冷缓存且两个候选时，生成首轮约7次逻辑调用（提取、基础 rubric、两次候选观察、综合、两种审核）；最多两轮修订时上限13次，每次又受总尝试次数与运行预算限制。`plan` 输出该上界，不假定缓存命中，也不把图片数量误当作计费 token。失败可能提前终止，只有普通审核通过才做反例检查。若启用一次来源细化，冷缓存生成逻辑调用上限相应增至26次（两个候选、每次两轮修订），`plan` 已包含该上界。百炼单用例默认12次总请求上限可能先于修订预算耗尽，程序会停止而不会自行提高授权额度。

完整 judge 按“候选数×rubric条数 + 候选总页数”调用（存在视觉条目时）；新增部分每页一次、同次检查所有选中视觉条目，最终每条仍查看整份PPT。逐页发现的问题必须被确认或用具体证据反驳；逐页调用失败使视觉条目为 unknown。前缀排列有利于服务缓存，但不保证服务实际命中。此路径成本明显高于生成，不建议在几千条生产生成时默认同时运行完整 judge。先用小规模开发数据测量质量和用量。

### Legacy权重与输出

默认总分 100：需求落实 30、正确性 25、信息诚信 15、叙事表达 15、视觉 15。这是待人工验证的起点，不是 PresentBench 官方权重。

权重先分配到维度，再分配到评分组，最后分到组内条目。同一个要求拆成多条时共享原组预算，避免拆条自动增重。模型审核负责识别把同一要求换组重复加权的情况。

每条 rubric 提供 `judge_prompt` 和 `score`，另带 `id`、`dimension`、`group_id`、`source_refs`、`basis`、`scope`、`evidence_mode`。最终 `rubric.json` 含所有条目、审核状态、配置与数据指纹。中间草稿、基础 rubric、审核记录保留供排查，不是多套生产输出。

已加入针对显式来源的跨维度重复检查：同一个要求不能同时在需求落实与正确性中重复计分。关键事实采用“存在且准确”的单条标准。校验器版本变化后，旧版本的 approved 也不能直接继续评分，须用 `audit` 重新审核。

每条 judge_prompt 必须能单独判断；程序拒绝引用其他 rubric 编号或仅内部可见需求编号的条目。对中文常见表述中的数量硬门槛（如“至少两项”“不超过20页”）检查是否存在来源依据；这是有限的程序检查，不能代替语义审核。来源相同的有限结构性去重仍须经过模型审核，不能替代语义重复检查。

Judge 每条返回 yes / no / unknown、证据及页码。只有完全满足条件才 yes；部分满足为 no。基础设施错误或无法核验为 unknown，此时总分为 null，报告上下界。不得把 unknown 当作 no，也不从分母里删除以抬高分数。

CLI 正常完成返回0；生成/审核失败返回1；judge存在未完成条目返回2。当前协议的 `approved` 意味着通过两种模型审核和程序检查，不是正确性的证明。默认回归文件 `fixtures/review_regressions_v3.json` 包含有效标准及人为植入的错误（v1/v2保留；v3新增正常舍入、错误数学反例、计划语境及缺失证据边界，新增样例尚未在线验证）；回归报告区分误放、误杀和调用错误，预期答案不会发给审核模型。这组样例也是开发数据，不能用其通过率宣称泛化能力。

对两个候选采用成对一致率，默认分差不超过 3 分判为持平；阈值应在看留出结果前冻结。报告同时提供平局数量、逐例分歧和 Wilson 95% 区间。三份以上候选的 rubric 生成与逐份评分已支持；多候选人工排名一致率尚未实现。
