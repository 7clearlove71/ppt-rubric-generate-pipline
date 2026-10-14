# PresentBench 对齐记录

五个评测维度、判断方法与评测偏好，见 [PresentBench 评测方法与偏好指南](presentbench-evaluation-guide.md)。本文保留渲染、计分实现和项目差异的技术对齐记录。

查阅日期：2026-09-30。参考代码仓库固定在 commit `e70ff01da962274e1e3cc03f77ec435ad66c5eb6`。

来源：

- [论文](https://arxiv.org/html/2603.07244v1)
- [项目协议](https://presentbench.github.io/)
- [PPTX 转 PDF](https://github.com/PresentBench/PresentBench/blob/e70ff01da962274e1e3cc03f77ec435ad66c5eb6/utils/pptx_to_pdf.py)
- [PDF 转图片](https://github.com/PresentBench/PresentBench/blob/e70ff01da962274e1e3cc03f77ec435ad66c5eb6/utils/pdf_to_images.py)
- [OpenAI Chat API 图片输入](https://github.com/PresentBench/PresentBench/blob/e70ff01da962274e1e3cc03f77ec435ad66c5eb6/utils/api/base.py)
- [计分实现](https://github.com/PresentBench/PresentBench/blob/e70ff01da962274e1e3cc03f77ec435ad66c5eb6/scoring.py)

## 采用的原则

针对任务生成可验证条目；分别关注表达、视觉、完整性、正确性、忠实性；单条二元判断，部分满足不通过；定位页码与证据；按维度聚合。

论文中的 checklist 为人工设计，本项目研究自动生成与自动审核。人类一致率实验与 checklist 审核是两个不同层次，不能用审核模型的一次认可替代人工验证。

## 渲染和 judge 对齐

官方使用 LibreOffice headless 将 PPTX 转 PDF，每次转换独立用户 profile。通用 PDF 工具优先 Poppler `pdftoppm -r 150`，然后 PyMuPDF fallback。官方 OpenAI Chat API 路径直接使用 PyMuPDF，以 150 DPI 渲染全部页为 PNG，再编码为 `image_url` data URL。

本项目默认匹配 OpenAI Chat 路径：LibreOffice → PDF → PyMuPDF，150 DPI、全部页面、PNG。提供显式 Poppler 后端选项，避免无提示的后端变化。文本是额外辅助证据，图片仍是可见内容依据。

首次样例转换发现 bundled headless LibreOffice 未正确发现本机中文字体：PDF 文字层正常，但图像缺字。因此新增转换子进程专用 Fontconfig 配置，记录字体环境指纹，不改原 PPTX、系统字体和全局环境。此项是本机适配，不属于评分偏好。

## 有意保留的差异

| 项目 | PresentBench 参考协议 | 本项目 |
|---|---|---|
| rubric 来源 | 人工设计 | 自动提取要求、候选辅助、独立审核、有限修订 |
| 权重 | 论文五维等权 | 30/25/15/15/15，待开发集校准 |
| 忠实性 | 严格依据材料，逐页检查 | 根据信息政策检查，允许合理建议、常识和明确假设 |
| rubric 作用域 | 部分逐页动态展开 | 每题固定一套，允许明确“所有适用页”的作用域 |
| 缺失与错误 | 缺失可同时影响完整性和正确性 | 审核重复处罚，约定适用边界 |
| 页数 | 论文实验按要求的最大页数截断 | 不静默截断；超页若需求明确则按对应条目判断 |
| judge 返回 | 文本中的 boxed yes/no | 结构化 yes/no/unknown 与证据 |
| 不确定／调用失败 | 依具体实现处理 | 不出最终总分，报告上下界与未判权重 |

本项目独立实现配套程序，未把官方 checklist 复制为生产评分条目。研究目录中的源码仅用于核对，git 忽略该目录；项目文档保留来源与版本。
