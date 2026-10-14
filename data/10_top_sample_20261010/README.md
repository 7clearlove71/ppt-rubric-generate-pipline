# 10_top_sample_20261010 人工评测交付样本集

2026-10-10解压并修复中文文件名。各用例的人工评测原始记录为 `评测人_*.md`，共10份：01—05为 `评测人_zxm.md`，06—10为 `评测人_ldl.md`。旧 Codex 代评文件 `人工评测结果.txt` 和 `human_evaluation.json` 已删除。

本目录与 `../10_top_sample/` 的60个原始数据文件 SHA-256一致。现有 `data/manifest.json` 仍指向旧目录，保留候选 A/B 映射和开发/留出划分。开发集人工 Markdown已通过 `import-labels` 转为 `artifacts/validation-20261010-v1/human-labels-dev.json`；其中6例已决定、09未定。转换按真实候选身份与清单映射进行，02、09的人工 A/B 与清单相反。留出正文尚未读取。人工一致率尚未验证，留出用例04、07、08仅在规则冻结后用于最终验证。

从 [`10_top/`](../10_top/) 的 120 个 rollout（10 用例 × 2 模型 × 3 harness × 2 skill）中，**每个用例+模型只保留 1 条 rollout**，共 **20 条**。

总大小约 238 MB。

## 目录结构

```
10_top_sample_20261010/
├── 01/  … 10/                # 按用例编号分组
│   ├── 评测人_<标识>.md      # 人工评测原始记录
│   ├── glm-5.3/              # 每个模型一个目录
│   │   ├── prompt.txt        # 原始 prompt 纯文本
│   │   ├── result.pptx       # 最终 PPTX 产物
│   │   └── trajectory.jsonl  # 事件流轨迹
│   └── qwen3.8-max/
└── ...
```

每条样本三件套：`prompt.txt` + `result.pptx` + `trajectory.jsonl`。

## 抽样规则

- 每个 用例+模型 从 6 个 rollout（3 harness × 2 skill）中选 1 个
- **优先有 pptx 的**：20 条全部选到产出 pptx 的 rollout
- 多个有 pptx 时 **随机** 选 harness+skill（固定 seed=20260929，可复现）

## 20 条样本来源一览

| 用例 | 模型 | 来源 harness | 来源 skill |
|---|---|---|---|
| 01 | glm-5.3 | pi | ppt-master |
| 01 | qwen3.8-max | codex | pptx-craft |
| 02 | glm-5.3 | pi | pptx-craft |
| 02 | qwen3.8-max | codex | pptx-craft |
| 03 | glm-5.3 | claude | pptx-craft |
| 03 | qwen3.8-max | codex | pptx-craft |
| 04 | glm-5.3 | claude | pptx-craft |
| 04 | qwen3.8-max | pi | pptx-craft |
| 05 | glm-5.3 | codex | ppt-master |
| 05 | qwen3.8-max | codex | pptx-craft |
| 06 | glm-5.3 | claude | ppt-master |
| 06 | qwen3.8-max | pi | ppt-master |
| 07 | glm-5.3 | claude | ppt-master |
| 07 | qwen3.8-max | codex | ppt-master |
| 08 | glm-5.3 | pi | ppt-master |
| 08 | qwen3.8-max | pi | ppt-master |
| 09 | glm-5.3 | pi | ppt-master |
| 09 | qwen3.8-max | codex | pptx-craft |
| 10 | glm-5.3 | codex | pptx-craft |
| 10 | qwen3.8-max | codex | pptx-craft |

来源分布：claude ×5、codex ×8、pi ×7；ppt-master ×9、pptx-craft ×11。

## 字段说明

- `prompt.txt`：UTF-8 纯文本，对应 10 条原始 prompt 之一（同一条用例下两个模型的 prompt 相同）
- `result.pptx`：最终 PPTX 产物，统一命名
- `trajectory.jsonl`：事件流轨迹（兼容 Claude Code session jsonl 格式：system init、assistant、user、tool_result 等），行数因 harness/model 差异较大（176 行 ~ 76062 行），均非空

## 原始数据

完整 120 条 rollout 见 [`../10_top/`](../10_top/)，10 条原始 prompt 见 [`../10_top/代表性抽样_10条.jsonl`](../10_top/代表性抽样_10条.jsonl)。
