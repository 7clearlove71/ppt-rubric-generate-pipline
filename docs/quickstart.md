# 安装与快速开始

当前发布的是研究工具，协议为 contract-v2.3，尚未通过生产质量验收。先用离线示例验证安装；继续原项目请按 [新机接续](new-machine.md) 迁移清单，再配置自己的 API 凭据。本文命令均从仓库根目录执行，适用于 macOS/Linux；Windows 建议使用 WSL。

## 1. 安装

```bash
git clone https://github.com/7clearlove71/ppt-rubric-generate-pipline.git
cd ppt-rubric-generate-pipline
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[test]'
python -m pytest -q
python -m ppt_rubric contract --help
```

需要 Python 3.9+。渲染 PPTX 另外需要 headless LibreOffice 及材料所用字体；仅运行离线 plan 不需要 LibreOffice。Ubuntu 可安装 `libreoffice-impress` 和 `fonts-noto-cjk`；macOS 安装 LibreOffice 后，把评分配置中的 `soffice` 设置为 `/Applications/LibreOffice.app/Contents/MacOS/soffice`，或配置 PATH。Codex 原开发机使用已配置的 bundled 运行时。

公开配置默认 `soffice: "soffice"`，支持 PATH 查找或绝对路径；`font_dirs: []` 继承系统字体环境。设置自定义字体目录时使用本机真实路径。复制配置到被 Git 忽略的 `configs/local/` 后修改，并在下列命令中替换配置路径。相同实验的配置应冻结，不能中途修改模型或判据后拼接结果。

## 2. 无密钥离线示例

```bash
python examples/make_plan_manifest.py
python -m ppt_rubric contract plan \
  --manifest artifacts/demo/manifest.json \
  --config configs/contract-v2-k3.json \
  --verification-config configs/contract-v2-auditor.json \
  --out artifacts/demo/plan.json
```

示例是虚构项目周报。输出包含来源数、构建逻辑调用范围与 HTTP 尝试上界，不读取凭据、不渲染、不调用模型。候选路径是明确的占位符，**只能演示来源计划，不能用于评分**；真实评分按下一节导入 PPTX。重复执行示例脚本会拒绝覆盖已有 manifest。

## 3. 导入自己的材料

准备至少两份同题候选：

```text
data/my-input/
└── demo/
    ├── prompt.txt
    ├── attachments/          # 可选来源材料
    │   └── facts.md
    ├── candidate-one/
    │   └── result.pptx
    └── candidate-two/
        └── result.pptx
```

```bash
python -m ppt_rubric ingest --source data/my-input --out data/my-manifest.json
python -m ppt_rubric contract plan \
  --manifest data/my-manifest.json \
  --config configs/contract-v2-k3.json \
  --verification-config configs/contract-v2-auditor.json \
  --out artifacts/my-run/plan.json
```

导入记录文件哈希、匿名候选编号和开发/留出划分。导入器目前要求至少两份候选，但 rubric 构建只读来源，不读取候选内容。单例归入开发集；多例默认约70%为开发集。不要手改冻结 manifest；输入变化后导入新文件。支持的附件类型、留出集隔离与容量限制见 [README](../README.md)。

## 4. 配置两套 API 凭据

当前 contract 配置：百炼 K3 负责提取与主评，MaaS Kimi-K2.6 负责独立审核与内容复核。需要账户实际具备这些模型的访问权限。环境变量名由配置的 `api_key_env` 指定：

```bash
# 在自己终端设置，或由凭据管理器注入；不要把真实值写入仓库。
export BAILIAN_API_KEY='<你的百炼密钥>'
export MAAS_API_KEY='<你的MaaS密钥>'
```

本项目不会自动加载 `.env`。显式 `api_key_env` 缺失时直接报错，不回退到另一个供应商的密钥。历史配置保留 `PPT_RUBRIC_API_KEY` / macOS 钥匙串兼容方式。其他 OpenAI 兼容供应商可修改 `base_url`、模型名与专属 `api_key_env`，并移除其不支持的 `request_options`；接口兼容不保证多模态能力和输出质量。

## 5. 生成、评分与导出

下列 generate/judge 命令会产生 API 费用。先查看 plan，自行确认用例范围与成本；可在两端配置分别设置 `max_api_requests`（含失败重试），恢复时先扣除已有请求账本。这个上限是每次启动、每个 Client 的限制，不是跨命令总预算。模型价格由供应商决定，本项目不估算未知单价。

```bash
python -m ppt_rubric contract generate \
  --manifest data/my-manifest.json --cases demo \
  --config configs/contract-v2-k3.json \
  --verification-config configs/contract-v2-auditor.json \
  --out artifacts/my-run/rubrics

python -m ppt_rubric contract judge \
  --manifest data/my-manifest.json --cases demo \
  --config configs/contract-v2-judge.json \
  --verification-config configs/contract-v2-verifier.json \
  --rubrics artifacts/my-run/rubrics --out artifacts/my-run/judgments

python -m ppt_rubric.contract_report \
  --manifest data/my-manifest.json \
  --run artifacts/my-run --out artifacts/my-run/report.json

python -m ppt_rubric contract export \
  --manifest data/my-manifest.json --cases demo \
  --config configs/contract-v2-k3.json \
  --rubrics artifacts/my-run/rubrics --out artifacts/my-run/rubrics.jsonl
```

将 `demo` 换成实际用例 ID。生成失败的用例不能进入评分；退出码1表示生成失败，评分退出码2表示仍有未决项。多用例百炼批量调用需由凭据所有者确认范围，再加 `--confirm-bailian-bulk`；该参数不是模型质量通过证明。并行用 `--workers`，不要多进程绕过共享限流。

导出保留 schema 2 的作用域、聚合与分值。视觉每条在整份 PPT 只计一次权重，unknown 保留区间；不能按页重复扣分，也不能把 unknown 当作 no。人工标签仅在离线报告阶段提供 `--labels`，不进入模型请求。

## 常见问题

- **找不到 manifest**：原项目运行 `python scripts/prepare_new_machine.py` 并使用 `--manifest data/manifest-local.json`；自备数据先 ingest。
- **找不到 soffice / 字体变化**：配置本机 LibreOffice 路径及字体，并抽查渲染页；字体替代可能改变布局。
- **401/403 或模型不存在**：检查供应商域名、账户权限、模型名称与对应环境变量，不向他人发送密钥。
- **429、超时或输出校验失败**：保留日志，查看限流/传输熔断；核算已有消费后才恢复。失败或模型分歧不应通过无限重试消除。
- **自动 approved 但标准有误**：自动审核不能替代来源与语义验收。已知局限见 [最终结果](final-revision-results-20261014.md)。

读懂方法：[方案概览与完整 prompt](pipeline-overview.md)。理解目录与公开范围：[发布范围](publication-scope.md)。
