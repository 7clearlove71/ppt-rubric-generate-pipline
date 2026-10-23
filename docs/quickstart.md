# 安装与快速开始（contract-v2.5.2）

用户提供任务、可选来源材料和同题候选PPTX，pipeline自动生成rubric并逐候选评分，输出分数、证据、未决项和文件映射。无需人工评测文件。本文使用当前contract-v2.5.2（命令名保留contract-v2.5）、默认单K3图文评分与light/full模式（默认light、任务并发10、judge并发30）；输入的完整约定见[用户输入与结果格式](input-format.md)，生成或judge不支持图片时见[按阶段配置能力](model-capabilities.md)。

命令从仓库根目录执行，适用于macOS/Linux；Windows可使用WSL。工具的自动评分仍有语义判定和渲染局限，现有研究记录不等于生产质量验收。

## 推荐：一条命令运行整个批次

安装与配置凭据后，按第2节准备输入目录，即可执行：

```bash
python -m ppt_rubric batch \
  --source inputs --out artifacts/my-batch \
  --mode light --max-api-requests 10000 \
  --workers 10 --confirm-bailian-bulk
```

重复同一命令自动续跑，无需重新执行ingest或单独恢复judge。上限10000只是示例，按整批累计计算，包含生成/评分/失败/重试；重复启动不清零。full模式另需`--audit-max-api-requests`与MaaS凭据。每10秒自动输出清楚的进度，结束后直接查看`summary.md`和`candidate-mapping.csv`。完整输入输出、停止/重启和预算规则见[批量使用说明](batch.md)。下文仍保留分阶段操作，供需要分别调用各阶段的集成者使用。

## 1. 安装

```bash
git clone https://github.com/7clearlove71/ppt-rubric-generate-pipline.git
cd ppt-rubric-generate-pipline
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[test]'
python -m pytest -q
python -m ppt_rubric contract-v2.5 --help
```

需要Python 3.9+。评分还需要headless LibreOffice及材料所用字体；仅运行来源plan无需渲染。Ubuntu可安装`libreoffice-impress`及中文字体。macOS可把配置中的`soffice`设置为`/Applications/LibreOffice.app/Contents/MacOS/soffice`。原开发机使用其已配置的bundled运行时；新机器需配置自己的运行时，不能照搬服务器路径。迁移说明见[新机接续](new-machine.md)。

公开配置支持`soffice`的PATH查找或绝对路径，`font_dirs: []`继承系统字体。需要调整时，将配置复制到被Git忽略的`configs/local/`，在后续命令中使用新路径。请抽查渲染页，字体替代可能改变布局。

## 2. 准备和导入自己的批次

最小目录：

```text
inputs/
└── task-001/
    ├── prompt.txt
    ├── attachments/          # 可选，支持的来源格式见输入约定
    │   └── report.pdf
    ├── proposal-001/
    │   └── result.pptx
    └── proposal-002/
        └── result.pptx
```

更多任务直接放在`inputs/task-002/`等并列目录；每个任务至少两份候选，任务内候选必须回答同一需求。候选文件固定命名为`result.pptx`，任务文本为UTF-8。目录名用于辨认作品，不需要是模型名称。

```bash
python -m ppt_rubric ingest \
  --source inputs \
  --out artifacts/my-batch/manifest.json \
  --split dev

python -m ppt_rubric contract-v2.5 plan \
  --manifest artifacts/my-batch/manifest.json \
  --mode light \
  --config configs/contract-v25-k3.json \
  --out artifacts/my-batch/plan.json
```

`--split dev`让自己的整个批次进入默认可评分分区；省略时导入器保留研究数据的开发/留出随机划分。导入器自动生成候选ID、绝对路径和SHA-256，既有冻结清单不原地修改。plan只检查来源和调用规划，不读取凭据、不调用模型，也不证明候选PPTX可正常渲染。

需要无真实PPTX的离线安装示例时，可运行`python examples/make_plan_manifest.py`，再用上面的2.5 plan命令替换manifest为`artifacts/demo/manifest.json`。该脚本中的候选路径是占位符，只能演示plan，不能用于judge；已有示例manifest时脚本拒绝覆盖。

## 3. 配置API凭据和调用上限

轻量模式只使用百炼`kimi-k3`，负责需求提取、逐条问题生成、内容与视觉评分：

```bash
export BAILIAN_API_KEY='<你的百炼密钥>'
```

全量模式另使用MaaS `kimi-k2.6`进行来源/rubric审核，需要：

```bash
export MAAS_API_KEY='<你的MaaS密钥>'
```

程序不自动加载`.env`，也不会把密钥写进输出。模型和端点需要账户实际具有权限。当前2.5候选只由K3评分，没有第二模型的候选复核。

generate/judge会产生API调用。执行前由凭据所有者确认范围及预算；本仓库约定百炼多用例调用在获得明确授权后加`--confirm-bailian-bulk`。以下批量示例均含该标志，复制执行前应确认自己的调用范围。

可在本地生成/审核/评分配置设置`max_api_requests`作为每次Client启动的实际请求上限，失败和重试也计入。生成与judge是两条命令，分别启动Client；该上限不自动成为跨命令累计预算。恢复前先扣除已有账本消耗，不因换命令重置已批准总量。

## 4. 轻量模式：生成与评分

```bash
python -m ppt_rubric contract-v2.5 generate \
  --manifest artifacts/my-batch/manifest.json \
  --mode light \
  --config configs/contract-v25-k3.json \
  --workers 10 --confirm-bailian-bulk \
  --out artifacts/my-batch/rubrics

python -m ppt_rubric contract-v2.5 judge \
  --manifest artifacts/my-batch/manifest.json \
  --config configs/contract-v25-judge.json \
  --rubrics artifacts/my-batch/rubrics \
  --workers 10 --confirm-bailian-bulk \
  --out artifacts/my-batch/judgments
```

轻量模式跳过审核，首版完整rubric直接评分，状态为`ready`、审核为`skipped`，不是审核批准。judge继承rubric模式，不传`--mode`、审核配置或verifier配置。并行用例共用同一个Client的限流和预算，不要启动多个独立进程绕过限制。

只跑部分任务时，可在generate和judge两条命令都加`--cases task-001,task-002`。命令参数中的任务ID来自目录名；无需提供人工A/B或人工评分。

## 5. 全量模式

需要审核时，plan和生成步骤都切换为full，避免沿用light的调用规划：

```bash
python -m ppt_rubric contract-v2.5 plan \
  --manifest artifacts/my-batch/manifest.json \
  --mode full \
  --config configs/contract-v25-k3.json \
  --source-audit-config configs/contract-v25-auditor.json \
  --out artifacts/my-batch-full/plan.json

python -m ppt_rubric contract-v2.5 generate \
  --manifest artifacts/my-batch/manifest.json \
  --mode full \
  --config configs/contract-v25-k3.json \
  --source-audit-config configs/contract-v25-auditor.json \
  --workers 10 --confirm-bailian-bulk \
  --out artifacts/my-batch-full/rubrics
```

随后使用上节同样的judge命令，将rubrics和judgments路径换为`my-batch-full`。全量最多两轮修订/重审，仍未通过也继续评分，输出保留`not_passed`及原因；审核接口或格式错误保留`audit_error`，使用最近完整rubric。没有首版完整rubric时生成仍报错。

## 6. 读取结果与文件映射

评分结束后，`judgments`根目录自动生成：

- `run-summary.json`：每项任务、每份候选的分数/区间和候选身份。
- `candidate-mapping.json`：schema 2，供程序按`candidate_key`关联原始文件。
- `candidate-mapping.csv`：UTF-8 BOM，便于表格查看；含manifest ID和完整文件指纹。
- `candidate-mapping.md`：可以直接阅读的静态映射表。
- `<case_id>/<candidate_id>.json`：逐候选结果、判定及理由。

映射名称来自候选目录/文件名，A/B是自动分配的编号。请使用`manifest_id + candidate_key + sha256`定位作品，不只看字母或相同的`result.pptx`文件名。输出不含人工评测编号、评测者或人工得分。

需要统一汇总和两候选区间偏好时，运行离线报告：

```bash
python -m ppt_rubric.contract_report \
  --manifest artifacts/my-batch/manifest.json \
  --run artifacts/my-batch \
  --out artifacts/my-batch/report/summary.json
```

这一步不调用模型，同时在`report`目录生成三种映射文件。若只评分部分任务，汇总仍会列出其他所选分区任务的缺失状态。

当前2.5对同一任务取最终rubric为unknown的条目并集，在所有候选总分中共同剔除，包含模型材料不足和请求失败两种情况。其余条目原分值不变，不重新分配或归一化；`score`是剔除后的比较分数，`score_possible`为剩余满分，映射另列`excluded_weight/excluded_rubric_ids/comparison_policy`。全部剔除时为`not_comparable`，分数及上下界为null，不生成胜负。视觉先按“任意no，否则unknown，否则yes”聚合，再决定是否共同剔除。

原始判断、执行错误和区间保存在逐候选报告的`assessment`，顶层`comparison`记录完整剔除信息，顶层`summary`用于比较。两候选仍用原分值尺度的3分持平阈值；更多候选输出同一剩余满分下的逐份分数，不自动生成严格总名次。映射表按manifest顺序，不代表排名，不能跨任务排序。历史报告继续按原区间规则读取，不自动重算。

## 7. 可选：导出rubric

```bash
python -m ppt_rubric contract-v2.5 export \
  --manifest artifacts/my-batch/manifest.json \
  --config configs/contract-v25-k3.json \
  --rubrics artifacts/my-batch/rubrics \
  --out artifacts/my-batch/rubrics.jsonl
```

导出保留来源、权重、作用域、视觉聚合、模式及审核状态。视觉每条在整份PPT只计一次权重，不能按页重复扣分。

## 常见问题

- **只恢复接口失败项**：使用 `judge --resume-from <旧评分目录> --recovery-request-cap <本次新增上限> --out <新目录>`；成功 yes/no/模型 unknown 直接复用，原 `unknown：HTTP400` 和失败日志保留。全部所选输入与检查点通过校验后才请求；此前缺少恢复绑定的历史报告不自动迁移。完整命令与累计预算见[错误日志与恢复](http-failures-and-recovery.md)。本次代码修订没有执行恢复请求。
- **候选没有被导入**：确认层级是`批次/任务/候选/result.pptx`，每题至少两份，不要改候选固定文件名。
- **批次只运行了部分任务**：自己的完整批次使用`ingest --split dev`；不要手改已有manifest中的分区和指纹。
- **附件报错**：PDF支持扫描与图表，PPTX需可用的LibreOffice；检查是否加密、含隐藏页或超过材料页数/合计图片容量。支持范围见[附件能力](reference-materials.md)。
- **找不到soffice或字体变化**：配置运行机器上的LibreOffice及字体，并抽查渲染页。
- **400/401/403或模型不存在**：查看实际错误记录、参数、端点和权限；单凭HTTP状态码不能定位根因。
- **429、超时或输出校验失败**：保留账本，检查限流/熔断；核算已有消耗后再恢复，不无限重试。
- **退出码1/2**：1表示该命令遇到生成、执行或账号/鉴权致命错误；judge的2表示有接口缺项或结果状态未完整，即使比较分数已可算也不报执行成功。映射保留错误、缺失或未决状态，不将其写成0分。

研究用的人工比较工具保留在独立开发流程中，不属于以上用户输入和自动评分交付。
