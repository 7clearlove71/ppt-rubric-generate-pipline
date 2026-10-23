# 按阶段配置图片能力（contract-v2.5.2）

当前实现 `2026-10-23.contract-v2.5.2`，包版本 `0.2.1`，rubric/judge validator均12。命令名仍为 `batch` / `contract-v2.5`，配置文件名仍使用v25。图片能力由配置显式声明，不按模型名称推断，不通过失败的API请求自动试探或降级。省略配置时全部默认支持图片，保留既有图文流程。

## 配置

在各自配置文件中设置 `role_supports_images`；只接受下面三个键和JSON布尔值，不接受字符串 `"false"` 或数字0：

```json
{
  "role_supports_images": {
    "generator": false,
    "reviewer": false,
    "judge": false
  }
}
```

| 键 | 生效的配置 | false时行为 |
| --- | --- | --- |
| `generator` | `--config`生成配置 | 需求提取/重新提取只读prompt及全部材料文字；rubric编写只读当前需求、prompt与引用材料文字。两类请求都不传图片。 |
| `reviewer` | full的`--source-audit-config`配置；未单独提供时使用生成Client | 来源审核读取全部材料文字及完整rubric，不传图片。light不创建审核Client。 |
| `judge` | `--judge-config`评分配置；分阶段judge的`--config` | 读取prompt、全部材料文字和当前候选整稿PDF文本层，只评分内容标准；不发送任何图片、不调用视觉评分或局部图分析。 |

三个值相互独立。例如生成模型只读文字、judge读图，或生成模型读图、judge只读文字，都可配置。full采用独立审核配置时，必须在该配置中设置 `reviewer`，生成配置内同名设置不会覆盖另一Client。

纯文本judge可配置所用服务的实际 `judge_model`，不要求K3，也不要求 `vision_model`。建议显式写 `"scoring_method": "single_text"`；从默认配置复制后仍保留 `single_k3` 时，`judge=false`也会选择并记录为 `single_text`。图片能力开启时保持当前K3及 `single_k3`约束。模型仍须能通过现有兼容接口返回理由和boxed yes/no/unknown；生成和审核仍遵守原JSON响应格式。模型名、端点、凭据及请求参数需按实际服务配置，声明能力不保证供应商接口兼容或模型质量。

例如复制现有配置到自己的文件后：

```json
// my-generator.json中的相关字段；实际JSON文件不要包含注释
{
  "generator_model": "你的文本生成模型名称",
  "role_supports_images": {"generator": false}
}
```

```json
// my-judge.json中的相关字段；实际JSON文件不要包含注释
{
  "judge_model": "你的文本评审模型名称",
  "scoring_method": "single_text",
  "role_supports_images": {"judge": false}
}
```

保留原配置的其他必需字段，使用正常batch命令传入：

```bash
python -m ppt_rubric batch \
  --source inputs --out artifacts/my-text-run \
  --config configs/my-generator.json \
  --judge-config configs/my-judge.json \
  --mode light --max-api-requests 1000 --confirm-bailian-bulk
```

这条命令会调用模型；1000仅为预算示例。batch生成与评分仍共用同一供应商Client，须使用相同端点和凭据配置；本次不增加跨供应商生成/评分调度。full另传自己的审核配置和审核累计预算。

## 纯文本材料与候选的边界

材料仍按PDF/PPTX的原有本地流程读取和冻结，PPTX仍需要LibreOffice；关闭图片输入不会改成读取PPTX XML，也不会省掉本地渲染与文件校验。每页来源ID、原文件及缓存PDF/图片指纹保留，但发给文本模型的来源目录不含图片路径或图片标签，不假称图片已提供。

附件任一页没有可提取文字时，该纯文本阶段明确报错，列出材料页ID；生成或full审核的检查在该题生成调用前完成，纯文本judge在该题任何评分调用前检查。扫描页需要有文本层的替代材料或图文模型，不自动OCR，不静默删页。

有文本的页面也可能包含未被提取的图表或图像。纯文本请求明确说明这一限制，不能从文本层遗漏推断原稿缺失；提取/审核保留不确定性，judge允许unknown。程序不声称检测或恢复全部视觉信息。

候选PPT全部页面没有文字时拒绝纯文本评分；若部分页无文字，则保留这些页及空文本记录，在输入说明中要求模型不要由此判断内容缺失。纯文本judge对全部材料文字加当前候选文字执行 `max_case_text_chars`上限检查，不截断。图文阶段仍执行材料/候选合计图片上限。材料总页数40及渲染配置仍沿用原约定。

## 内容评分与输出

冻结rubric仍保留全部五维100分，需求构建不依赖judge能力。`judge=false`只评四个内容维度，原始权重30/25/10/15共80分；视觉5条共20分显式跳过，不生成模型答案或unknown判断。视觉局部图分析及视觉模型请求均为0，评分逻辑调用量为每稿C。

报告与assessment保留 `evaluation_coverage`：`mode=content_only`、评过及跳过的条目ID、`score_possible=80`、`unassessed_weight=20`。`scoring_method=single_text`、`judgment_strategy=content_single_text_only`、`visual_evaluated=false`。`execution=complete`表示配置要求的内容请求执行完整，不能解读为视觉已评。

同题unknown共同剔除仍只针对实际评过的内容项，保持原分值、3分持平阈值及全剔除not_comparable。例如内容80分中有10分unknown，比较满分70，未评视觉仍单列20，unknown剔除权重为10。比较记录区分 `original_possible=100`、`scope_possible=80`、最终 `score_possible`及 `unassessed_weight=20`，不把未评视觉混作unknown。

JSON/CSV/Markdown候选映射新增 `evaluation_mode` 与 `unassessed_weight`（schema2的增补字段，历史报告无值）。仅内容分数以实际比较满分显示，不折算成百分制，不和完整图文分数直接混比。离线汇总校验评测范围、原始assessment及共同子集分数；纯文本模式不执行隐含归一化的五维敏感性分析。

计划、导出、batch汇总和单例runner的调用量/评分方式同步记录实际能力。单独plan/export使用本次配置中的judge能力；它们不会自动查找另一个judge配置文件，需要将所选judge能力同步到plan/export配置中。

能力、模型、请求参数及实际图文输入参与构建、缓存和评分检查点绑定。升级或切换能力后使用新运行目录，不将已有图文结果迁为纯文本，也不将纯文本结果当完整图文评分。文本模式仍要求原材料缓存完整，不能删除图像缓存来沿用原检查点；删除API响应缓存不影响已持久成功结果的复用。

## 离线验证

本轮全套 **783项离线测试通过**。覆盖纯文本/图文阶段组合、独立审核能力、非K3文本judge、实际HTTP请求无图片/无视觉调用、内容80分、unknown共同剔除及全unknown、候选映射、离线汇总、plan/export、缓存删除后续跑与能力切换拒绝。测试拦截真实HTTP，不调用付费API，不读取人工标签、不重评历史候选、不恢复09。不把离线请求组装和分数校验当作文本模型阅读质量验证。
