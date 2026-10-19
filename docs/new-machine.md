# 新机接续

代码安装、离线示例与供应商凭据配置见 [quickstart](quickstart.md)。GitHub 当前版本仅分发常规项目文件，发布边界见 [publication-scope](publication-scope.md)。

## 接续自行保留的历史材料

原始 data/、artifacts/、.cache/、configs/local/ 和 research/ 不随当前版本分发。迁移快照曾用于机器迁移，迁移完成后已从当前版本移除。所有者现有本机文件保持原样。

若已单独取得原始 data/ 和冻结 manifest，可运行：

```bash
python scripts/prepare_new_machine.py
python -m ppt_rubric contract plan \
  --manifest data/manifest-local.json \
  --config configs/contract-v2-k3.json \
  --verification-config configs/contract-v2-auditor.json \
  --out artifacts/new-machine/plan.json
```

迁移工具核对候选与附件哈希，另写本机清单，保留原 manifest、候选映射和开发/留出划分；不调用模型，不读取人工标签正文。没有历史数据时，使用 quickstart 的虚构离线示例或自行导入真实材料。

历史路径和冻结配置应原样保留供溯源，不要批量改写旧账本。新实验使用本机清单、新输出目录及本机 LibreOffice 和字体。旧渲染缓存不能保证跨环境直接复用，正式评分前需抽查渲染页面。

## 开发起点

阅读 docs/final-revision-results-20261014.md、docs/contract-v23-source-only-audit.md 和 docs/final-revision-history-audit.md；历史 QA 原始产物需从本地实验材料取得。现有研究结论不表示生产质量验收通过。

运行时、字体和凭据须独立配置。历史授权已关闭，迁移和安装不产生新的付费调用授权。
