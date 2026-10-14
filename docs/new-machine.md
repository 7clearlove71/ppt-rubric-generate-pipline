# 新机接续

代码安装与供应商凭据见 [quickstart](quickstart.md)。迁移原实验还需要原始 `data/`、`artifacts/` 和证据缓存；当前公开范围见 [publication-scope](publication-scope.md)。本次已获所有者明确批准，将这些材料一并上传现有公开仓库。

## 本机盘点（2026-10-14）

| 材料 | 近似磁盘占用 | 接续用途 |
|---|---:|---|
| `data/` | 475 MB | 两版原始样本、PPTX、生成轨迹、人工记录、冻结 manifest；两版大部分文件相同，Git 可按内容去重 |
| `artifacts/` | 169 MB | 历次成功与失败、请求账本、授权关闭状态、冻结源码、QA、人工评测页面 |
| `.cache/api/` | 9.6 MB | 付费调用成功缓存；有效性仍由当前协议检查 |
| `.cache/render/` | 73 MB | 历史页面、裁剪图、PDF及几何信息；用于核对当时实际评分证据 |
| `.cache/codex-review-20260930/`、`.cache/font-test/` | 约13 MB | 早期模型检查和字体诊断；不当作人工真值 |
| `configs/local/` | 约44 KB | 原机器配置，仅作历史参考；新机使用可移植公开配置 |
| 根 `AGENTS.md` | 24 KB | 项目约定、授权和用量历史；已关闭授权不延续 |
| `research/` | 约1 MB | PresentBench 研究参考副本与来源元数据 |

无需迁移虚拟环境、Python字节码、pytest缓存、egg-info、安装包缓存、字体系统缓存、临时发布测试checkout与测试环境。它们可在新机重建。LibreOffice可执行文件及字体须单独安装；本项目没有打包系统运行时或钥匙串凭据。

2026-10-14所有者明确选择“全部上传到现有公开仓库”。本次快照保留两版样本及历史证据；不上传凭据与可重建环境。

## 取得实验材料后的离线检查

从仓库根目录执行：

```bash
python -m pip install -e '.[test]'
python -m pytest -q
python scripts/verify_migration_snapshot.py
python scripts/prepare_new_machine.py
python -m ppt_rubric contract plan \
  --manifest data/manifest-local.json \
  --config configs/contract-v2-k3.json \
  --verification-config configs/contract-v2-auditor.json \
  --out artifacts/new-machine/plan.json
```

迁移工具只读取清单及校验候选/附件文件哈希，不调用模型、不读取人工标注正文。它保留原 manifest、新旧候选 A/B 映射及开发/留出划分，在 `data/manifest-local.json` 写入新机路径。目录变化会产生新 manifest ID，这是正常的；后续新实验明确使用这个清单和新的输出目录。历史 rubric 和标签不能被强行改ID来伪装为新实验产物。

历史汇总可使用原清单离线读取（无须改写旧路径）：

```bash
python -m ppt_rubric.contract_report \
  --manifest data/manifest.json \
  --run artifacts/final-revision-20261014 \
  --out artifacts/new-machine/historical-report.json
```

原始人工标签及人工比较结果保存在各轮历史报告中。需要新清单对应的标签时，通过 `import-labels --manifest data/manifest-local.json --source data/10_top_sample_20261010 --out artifacts/new-machine/human-labels-dev.json` 正常导入开发集；不要读取留出标签来调整规则。

历史路径及冻结配置原样保留供溯源，不能把旧绝对路径直接当作新机可用路径。更换字体、渲染器或路径可能导致缓存失效；保留旧渲染证据不意味着强制复用旧缓存。新评分应配置当地LibreOffice和字体，抽查页面后运行。

## 当前工作起点

优先阅读 `docs/final-revision-results-20261014.md`、`docs/contract-v23-source-only-audit.md`、`docs/final-revision-history-audit.md` 以及最终轮次的两份 `final-*-semantic-qa.json`。当前 contract-v2.3 未通过生产质量验收；来源提取和共同漏检仍有实质问题。所有历史付费授权已关闭，迁移不自动启动模型调用，也不恢复旧额度。
