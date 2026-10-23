# 用户输入与结果格式约定（contract-v2.5.2）

用户只需提供任务、可选来源材料及候选PPTX，系统自行生成rubric并评分，不需要附带人工评分或人工排序。当前实现版本为`2026-10-23.contract-v2.5.2`，命令入口保留`contract-v2.5`，支持轻量`light`与全量`full`；默认light。安装、凭据与完整命令见[快速开始](quickstart.md)。

推荐使用新的通用`python -m ppt_rubric batch --source inputs --out <专用运行目录> ...`，自动处理整个批次并支持同命令续跑，不需要先手动ingest。输入沿用下文目录约定；batch额外提供`summary.json/.md`、`progress.log/.json`、`state.sqlite3`及根目录候选映射。准确的批量命令、输出树和恢复规则见[批量使用说明](batch.md)。下文第3—4节的手动导入及report目录适用于原分阶段入口。

## 1. 推荐的输入目录

```text
inputs/                         # 一次导入的批次
├── task-001/                    # 一项任务；名称成为case_id
│   ├── prompt.txt              # 必填：UTF-8、非空的原始制作任务
│   ├── attachments/            # 可选：所有候选共用的来源材料
│   │   ├── report.pdf
│   │   └── reference-slides.pptx
│   ├── proposal-001/            # 一份候选；目录名用于辨认作品
│   │   └── result.pptx          # 必填：固定文件名，小写扩展名
│   ├── proposal-002/
│   │   └── result.pptx
│   └── proposal-003/            # 可有更多候选
│       └── result.pptx
└── task-002/                    # 另一项任务，单独生成评分表
    ├── prompt.txt
    ├── proposal-001/
    │   └── result.pptx
    └── proposal-002/
        └── result.pptx
```

| 层级/文件 | 要求 |
|---|---|
| 批次目录 | 直接子目录都是任务；不再套一层分组目录。普通根目录文件不作为任务导入。 |
| 任务目录 | 名称在批次内唯一；同一目录内的全部候选回答同一个任务，共用同一套rubric。 |
| `prompt.txt` | 推荐放在任务根目录，写制作任务、受众、用途、给定事实和交付要求。不要用候选内容替代原始任务。 |
| 候选目录 | 每个任务至少两份候选；目录名自定，例如方案编号、提交编号或供应商名。建议用有业务含义且稳定的名称，不需要是模型名。 |
| `result.pptx` | 每个候选一份可正常打开、未加密的PPTX。导入器按这个固定名称发现候选；其他名字的PPTX不会自动当作候选。 |
| `attachments/` | 保留给来源材料，不作候选目录使用；里面可以按子目录组织材料。没有附件时可以省略。 |

每份候选只需自己的PPTX，不需要轨迹文件、模型回答、人工评测文件或评分文件。候选可以有不同页数。当前不支持直接以候选PDF、图片合集或旧版`.ppt`代替`result.pptx`。

导入器也兼容候选目录内各自的`prompt.txt`，但所有候选的任务文本必须一致；若同时存在任务根目录的`prompt.txt`，它也必须一致。建议统一采用上面的共享任务写法。

## 2. 任务文本和来源材料

`prompt.txt`示例：

```text
制作一套产业项目申报汇报PPT，受众为主管部门评审人员。
总投资280万元：建设120万、设备60万、培训30万、推广40万、流动资金30万。
第一年建成800平方米工坊并培训50人；第二年年产10万件；第三年建设品牌并入驻电商。
需要说明实施计划、资金与规划的对应关系，以及带动就业的机制。
```

这只是格式示例。真实任务应写清实际要求；系统根据用户任务和附件生成标准，不要求套固定行业模板。

附件放在任务根目录的 `attachments/` 中，所有候选共用；支持嵌套子目录。建议在 `prompt.txt` 中用相对文件名说明每份材料的用途，例如：

```text
依据 attachments/report.pdf 中的2025年度经营数据制作汇报。
attachments/reference-slides.pptx 仅供结构参考，不要求复制其事实、配色或排版。
保留报告中的单位和统计口径；材料冲突时说明待确认，不自行补数。
```

| 来源附件类型 | 读取方式与限制 |
|---|---|
| `.pdf` | 每页渲染为图片，同时提取文本层；支持文字、图表、扫描及混合页面。图文阶段无文本页仍发送图片；纯文本阶段遇无文本材料页明确报错，不生成虚构的OCR文本。未加密、非空且能正常打开。 |
| `.pptx` | 用配置的LibreOffice转换为PDF，再逐页读取图片与PDF文字。使用实际幻灯片顺序；隐藏页、空稿或转换页数不匹配明确失败。备注、嵌入文件、音视频及动画过程不作为来源读取；请将必要信息放入可见页面。 |
| 旧文本输入 | UTF-8 `.txt/.md/.csv/.json` 继续兼容；新材料推荐使用PDF/PPTX。 |
| 其他类型 | 旧版`.ppt`、DOCX、XLSX、独立图片等不支持，明确报错，不自动忽略。 |

各阶段默认支持图片，可通过 `role_supports_images`独立关闭；设置与评分范围见[模型能力配置](model-capabilities.md)。关闭图片的阶段只接收材料文字；纯文本judge只评内容，原内容满分80，视觉20分显式未评。需求提取读取原始prompt和全部材料页；逐条生成rubric读取当前需求、原始prompt及所引用材料页；全量审核读取全部材料页和完整rubric；judge读取prompt、全部材料和当前候选。材料图片用 `material/attachment_N_page_P` 标签，候选图片用文档ID标签，二者不会混为同一份待评作品。附件背景或模板不自动成为全部必须复述的义务，具体用途由prompt决定。能力设计及离线验证范围见[附件能力说明](reference-materials.md)。

下述图片容量适用于图文阶段；纯文本judge对材料和候选合计文字检查字符上限。默认材料总页数上限 `max_source_pages=40`，材料渲染 `source_dpi=120`；生成和judge配置须保持一致。全部来源文字（含prompt）不得超过 `max_case_text_chars`（公开配置100000字符）。每请求图片上限为 `max_images_per_request`（公开配置60张），材料页和候选页共用额度：内容判断需要“材料总页数＋候选页数”，视觉判断需要“材料总页数＋候选当前页＋可用局部图”。容量不足明确失败，不静默截断，也不自动抽样或压缩成摘要；仍须满足供应商实际上下文容量，当前未验证线上大附件吞吐或阅读质量。

材料原文件、缓存PDF、逐页图片、页码、引用和渲染参数参与指纹校验。材料或已冻结图片变化、缺失时拒绝沿用旧结果。新版本不迁移历史rubric/检查点；升级后使用新运行目录。命令行 `--cache` 决定分阶段的材料缓存位置，batch统一放在运行目录的 `cache/`；分阶段构建与评分请沿用相同缓存和材料渲染配置。

## 3. 导入为manifest

从仓库根目录执行：

```bash
python -m ppt_rubric ingest \
  --source inputs \
  --out artifacts/my-batch/manifest.json \
  --split dev
```

这里的`--split dev`使用户提交的所有任务都进入默认可评分分区，不要求人工数据。省略此参数时仍保留研究工具原有的约70%开发/30%留出划分，后续默认评分只选择开发分区；普通用户批次请明确使用`--split dev`。

导入器会自动生成：

- `case_id`：任务目录名，例如`task-001`。
- `candidate_id`：本题候选编号，按固定seed随机打乱后分配`A`、`B`……`Z`、`AA`、`AB`……，不代表质量或排名。
- 原始PPTX绝对路径、各候选完整SHA-256、附件路径、相对名称及指纹。
- `manifest_id`：整份输入清单的指纹；JSON中的字段名为`id`。

不要假定`A`就是字母排序第一的目录。输入新增、删除或变化后，应导入新的manifest并使用新的运行目录；不要手改已经冻结的manifest或指纹。相同输入和seed重复导入相同输出文件时允许复用，内容不同则拒绝覆盖。

manifest结构示意如下。路径和全零指纹是占位值，不能直接拿此示意评分；请使用导入器计算实际值：

```json
{
  "schema_version": 1,
  "seed": 20260930,
  "cases": [
    {
      "id": "task-001",
      "split": "dev",
      "prompt": "原始制作任务全文",
      "attachments": [
        {
          "path": "/srv/inputs/task-001/attachments/report.pdf",
          "name": "report.pdf",
          "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
        }
      ],
      "candidates": [
        {
          "id": "A",
          "pptx": "/srv/inputs/task-001/proposal-002/result.pptx",
          "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
        },
        {
          "id": "B",
          "pptx": "/srv/inputs/task-001/proposal-001/result.pptx",
          "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
        }
      ]
    }
  ],
  "id": "0000000000000000000000000000000000000000000000000000000000000000"
}
```

默认展示名来自候选目录和文件名，例如`proposal-002/result.pptx`。自定义集成可在创建manifest时为候选添加可选的非空字符串`display_name`，并用`ppt_rubric.common.digest`计算整个manifest（去除`id`字段）的指纹。它只用于输出展示，不传给评分模型。普通目录导入无需填写此字段。

## 4. 输出目录与映射

按[快速开始](quickstart.md)运行后，主要结果为：

```text
artifacts/my-batch/
├── manifest.json
├── plan.json
├── rubrics/
│   ├── task-001/rubric.json
│   └── task-002/rubric.json
├── judgments/
│   ├── run-summary.json
│   ├── candidate-mapping.json
│   ├── candidate-mapping.csv
│   ├── candidate-mapping.md
│   ├── task-001/
│   │   ├── A.json
│   │   ├── A-assessment.json
│   │   ├── B.json
│   │   ├── B-assessment.json
│   │   └── ...
│   └── task-002/...
└── report/
    ├── summary.json
    ├── candidate-mapping.json
    ├── candidate-mapping.csv
    └── candidate-mapping.md
```

`judgments`中的映射随评分生成；`report`中的映射随离线汇总生成。后者可以在不调用API的情况下重新导出。分数和原始判断保存在逐候选报告，映射通过`judgment_file`指向它。

`candidate-mapping.json`当前为schema 2。每一行只有作品身份和自动评分信息：

| 字段 | 含义 |
|---|---|
| `candidate_key` | 用例/候选组合键，例如`task-001/A`；排序结果按此键关联回原文件。 |
| `case_id`、`candidate_id` | 原始任务ID和本题候选ID。 |
| `display_name` | 用户提供的名称，或来源目录/文件名；不是系统核实过的生成模型身份。 |
| `input_pptx`、`input_filename`、`input_directory` | 评分时对应的原始文件和目录。 |
| `sha256` | 完整文件指纹，用于区分同名文件、核对具体版本。 |
| `page_count` | 新评分保留实际页数；历史或未准备成功的作品可能为null。 |
| `document_id` | 评分内部对文档的内容引用标识；没有报告时可能为null。 |
| `score_status` | `complete`、`not_comparable`、`incomplete`、`pending`、`missing`或`error`。 |
| `score` | 当前比较分数，满分见`score_possible`；全部剔除、未完成或失败时为null。 |
| `score_lower`、`score_upper` | 已确定得分与未决项可能得分形成的上下界。 |
| `unresolved_weight` | 尚未确定的分值。 |
| `score_possible` | 共同剔除unknown后剩余的比较满分，不重新分配或归一化；历史报告为null。 |
| `excluded_weight`、`excluded_rubric_ids` | 同题所有候选共同剔除的分值与条目ID。 |
| `evaluation_mode`、`unassessed_weight` | `full`或`content_only`及因能力未评的权重；纯文本模式视觉20分未评，不计入unknown剔除。历史结果可为null。 |
| `comparison_policy` | 当前为`exclude_unknown_across_candidates_v1`；历史报告为null。 |
| `judgment_file` | 逐候选评分结果路径；pending/missing/error时可能尚未生成文件。 |

根对象保留`manifest_id`、`schema_version`、`candidates`和三种导出文件的位置。CSV另在每行保留`manifest_id`；Markdown提供直接可读的静态表格。JSON/CSV保留原始数值，浮点合计可能有极小舍入误差。完整的[JSON结构示例](../examples/candidate-mapping-v2.json)使用虚构路径、指纹与分数，仅演示格式，不是一次实际评测。

输出不含人工编号、评测者或人工得分字段。批量身份关联使用`manifest_id + candidate_key + sha256`，不只依赖文件名。即使两个提交同名或哈希相同，也分别保留候选行。

## 5. 分数与排序约定

- 同一任务的全部候选使用同一套rubric；不同任务各自构建标准，不将跨任务分数拼成统一名次。
- 当前2.5待本题候选全部评完，取最终rubric判断为unknown的条目并集；任一候选unknown，该条在所有候选中剔除，包括其他候选在该条的yes或no。模型材料不足与接口失败产生的unknown均适用，原始原因和执行状态仍保留。
- 剩余条目沿用原分值，不重新分配或归一化。若剔除10分，则全部候选在同一90分满分下比较；两候选持平阈值仍为原分值尺度的3分。
- 视觉先逐页聚合：任意no则该条no，否则有unknown则该条unknown；只有聚合后的unknown触发共同剔除，单页unknown与其他页no并存时保留该条no。
- `complete`可使用`score`降序查看同题作品；还应结合评分理由及同题持平阈值。
- 全部条目剔除时为`not_comparable`，分数及上下界为null，不生成0分或持平结论。
- 逐候选报告的`assessment.summary`与原始模型判断保留原区间；顶层`summary`和映射中的分数使用共同剔除后的口径，`comparison`记录完整剔除信息。共同剔除后的complete表示剩余条目已确定，不表示原始评测没有unknown或执行错误。
- 历史报告及`incomplete`仍保留原区间，不自动套用新规则或重算；不能把上界当完整分数、把unknown当no、或用区间中点伪造确定名次。
- 当前离线汇总对两份候选提供`interval_preference`（3分持平阈值）；多于两份时保留每份分数/区间及映射，没有自动生成多候选的严格总名次。
- 映射表按manifest顺序输出，不代表排名；消费者排序后保留`candidate_key`即可准确关联回原PPTX。
- `ready`只表示rubric可以执行。轻量模式审核为`skipped`；全量审核未通过仍可能评分，必须同时查看`source_audit`状态。

远程使用时，结果里的路径指运行机器上的文件，不保证能在客户端直接打开；映射表同时提供可读名称和完整指纹，下载或转交结果时请保留清单与对应文件。
