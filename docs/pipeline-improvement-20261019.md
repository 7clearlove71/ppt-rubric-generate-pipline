# contract-v2.4 改进与验证入口

本次实现依据[2026-10-19改进计划](badcase-analysis-and-improvement-plan-20261019.md)。开发候选为 `2026-10-19.contract-v2.4`、rubric validator 4、导出 schema 3；旧 `2026-10-14.contract-v2.3` 和历史分数保持原样，旧批准不能升级后直接评分。工程测试通过不等同来源语义或成品判断通过验收。

## 正式模型分工

用户再次明确要求沿用之前验证的模型，而非根据可用凭据或API文档示例替换：

| 环节 | 正式模型 | 配置 |
|---|---|---|
| 来源目标提取、有限修订 | 百炼 `kimi-k3`，thinking enabled | `configs/contract-v24-k3.json` |
| 独立来源审核 | MaaS `kimi-k2.6`，thinking enabled | `configs/contract-v24-auditor.json` |
| 内容逐页取证、分任务主评 | 百炼 `kimi-k3`，thinking enabled | `configs/contract-v24-judge.json` |
| 视觉逐页判断 | 百炼 `kimi-k3`，thinking enabled | 同主评配置 |
| 独立内容取证、复核 | MaaS `kimi-k2.6`，thinking disabled | `configs/contract-v24-verifier.json` |

新增 `model_routing=contract-kimi-v1` 在命令和公开生成/评分入口校验模型、端点及独立配置；缺少角色或出现GLM/Qwen替代直接拒绝，不自动回退。角色校验不代表服务端一定提供该模型，接口失败仍须原样报告。旧配置继续保留用于历史研究。

早期 GLM 文本与Qwen视觉请求仅存为旁路探针。用户纠正后已终止相关进程；原始失败、被中断请求和未知用量保留，不能计为K3/K2.6正式验证。API文档中使用Qwen的示例不构成选择该模型的依据。

## 来源目标与审核

来源目录现在有逐片段覆盖账本；每个规范目标包含对象、属性、值、单位、时间、状态、来源支持的条件、独立关系与唯一评分所有者。Q只拥有一个规范目标，其他目标通过非所有权引用提供上下文。编译器从规范字段确定性生成判据，不再从摘要自由扩写。

事实的同一对象/属性/时间保持单一责任；独立交付、表达等目标按完整内容区分，不会仅因位于同一PPT模块容器就被合并。Q来源引用、关系组件引用及来源覆盖的反向链接由规范T确定性派生，原始JSON与派生日志分开保留；非法引用、条件或角色冲突仍拒绝，不修改目标语义来换取通过。

独立审核必须登记原文目标盘点、覆盖、唯一归属和实际应用完整判据的最小反例；条件未触发、交换已呈现组件等正常对照也要核对。详见[来源协议](source-contract-v24-20261019.md)。结构检查不能识别全部同义重复或语义遗漏，不能据此宣称5类来源问题均已通过在线语义验收。

评分和导出现在共用完整发布门禁。直接调用 `evaluate_contract` 也会检查 schema、来源账本、当前编译结果及嵌入的正式批准记录；重算哈希不能绕过未批准审核、改写标准或schema降级。

## 内容取证与分任务核验

新 `content_evidence.py` 先以默认每批3页记录可核查主张与取证范围，再分别核验来源目标、信息边界、推理、组织。记录只含简短证据摘要、对象/单位/起点/状态/假设和可核查计算结果，不要求输出思维过程。

主评与复核各自从同一原始来源和页面独立取证。复核不会收到主评的主张清单、结论或缺陷。相同API请求配置不能依靠缓存复用冒充独立判定；模型共识仍不是事实真值。

yes需要可定位支持；推理核验覆盖已提取的全部推理主张，并登记前提、单位和时间起点检查。条件豁免只允许来源规范条件或固定库实际允许的无适用现象，须引用完整的授权条件；任意提示词子串、完整排除段落、重要性偏好均不能替代必选目标证据。判断页码须包含所引用观察的实际页面。

提取缺页时不能认证全篇yes。接口失败或结构错误保留原始记录并形成unknown；纯文本取证不能认证需要原图的条目。两个评判分歧、未知或重复所有权继续保留区间，不按人工偏好选择答案。提取自身仍可能漏掉主张或误读，程序登记覆盖不能证明语义完整。

## 视觉覆盖、评分与真人复评

新增四边、页脚/表格末行及轴编码候选，按页面与风险类别选择局部图，记录尚未裁取的区域。PDF文字几何尽可能保留页外对象；某些PDF已不含被裁掉对象，因此仍保留固定页边观察带。几何只触发观察，不能自动判no。

视觉判断增加实际对象、区域、信息损失、范围、任务角色及修复记录，明确类别轴和连续轴的区别。仍沿用整篇二元聚合，一处和多处缺陷的诊断范围不会临时改变默认扣分。视觉语义效果须由K3正式验证，其他模型单例不替代这个结论。

默认五维预算、原二元聚合和3分持平阈值均未变。独立计分政策对照、稳定性检查，以及同图静态匿名包、多真人提交/显式裁决入口详见[评分与真人复评](scoring-and-human-validation-20261019.md)。两个空白评测槽位不等于已安排真人，不能报告新的人工一致率。

## 命令与付费范围

```bash
# 离线检查来源、角色及调用上界
.venv/bin/python -m ppt_rubric contract plan \
  --manifest data/manifest-local.json \
  --config configs/contract-v24-k3.json \
  --verification-config configs/contract-v24-auditor.json \
  --out artifacts/new-contract-v24/plan.json

# 正式生成：批量使用须先获得当次明确授权
.venv/bin/python -m ppt_rubric contract generate \
  --manifest data/manifest-local.json \
  --config configs/contract-v24-k3.json \
  --verification-config configs/contract-v24-auditor.json \
  --out artifacts/new-contract-v24/rubrics --confirm-bailian-bulk

# 仅对当前版本已批准标准评分
.venv/bin/python -m ppt_rubric contract judge \
  --manifest data/manifest-local.json \
  --config configs/contract-v24-judge.json \
  --verification-config configs/contract-v24-verifier.json \
  --rubrics artifacts/new-contract-v24/rubrics \
  --out artifacts/new-contract-v24/judgments --confirm-bailian-bulk
```

凭据由配置指定的环境变量注入，不写入项目或日志。本机凭据文件名不决定模型分工。正式批量授权必须另列样本、逻辑调用、实际请求含重试、token规模及可核实费用；未知价格如实标未知，不能沿用历史关闭轮次的余额。恢复时从当轮唯一账本扣除已发请求，不按启动次数重置。

固定文本机制对照在 `tests/fixtures/content-diagnostics-v24.json`；真实分两页的矛盾对照另在 `content-pagination-v24.json`。这些都是作者已见的开发样本，不是独立留出；比较标签只在离线汇总使用，不传入模型。`content_diagnostics` 支持已经获明确授权的百炼批次，标志只记录授权、不代替授权。

新模型/协议效果、工程检查、历史固定答案计分重放和真人复评分别报告。当前结果与实际用量见[验证记录](improvement-validation-20261019.md)。
