# 一条命令批量评测与断点续跑

当前通用入口为 `python -m ppt_rubric batch`，使用 contract-v2.5、单 K3 候选评分，以及原有 light/full 模式。它自动执行导入、来源读取、rubric 构建、PPT 渲染、逐项评分和结果汇总，不需要人工标签。每题独立推进；一题的文件或生成错误不阻止其他题，账号熔断或额度耗尽则停止启动新请求。

视觉 judge 每页一次 K3 调用，同时检查全部5条视觉 rubric，共用该页原图和局部放大图。响应为 `{"verdicts":[{"rubric_id":"V_READ","evidence":"该条独立理由","answer":"yes"}, ...]}`，每个预期ID恰好一次；缺项、重复/额外ID、空理由、非法结论或重复JSON键会被拒绝，经原有有界格式重试仍失败时，整页5条记录执行失败unknown，不发布部分有效答案。逐页按任意no优先、否则unknown、否则yes聚合，再执行原有同题unknown共同剔除与原权重计分。内容仍逐条完整看稿，不合批。每份候选逻辑评分调用为 `C＋P`（无视觉项时为C），C为内容条数、P为页数；失败重试另计。

视觉检查点每页保存一份原始响应和5条独立结果；成功页（含模型unknown）整体复用，只恢复失败或未完成页。HTTP400及恢复历史保留。当前judge validator10、rubric validator10，计划、导出和报告标明 `content_single_visual_page_batch`；旧单条视觉检查点不自动迁移。

## 输入约定

```text
inputs/
├── task-001/
│   ├── prompt.txt                 # UTF-8、非空，用户 query 全文
│   ├── attachments/               # 可选，所有候选共用的来源材料
│   │   └── facts.md
│   ├── proposal-001/result.pptx
│   └── proposal-002/result.pptx
└── task-002/
    ├── prompt.txt
    ├── proposal-001/result.pptx
    ├── proposal-002/result.pptx
    └── proposal-003/result.pptx
```

批次目录的直接子目录都是任务；每题至少两份候选，候选数和页数可以不同。任务目录名为稳定任务 ID，候选目录名用于输出中辨认作品，不能据此推断生成模型。文件名固定为 `prompt.txt` 和 `result.pptx`。候选内可兼容同文的 prompt，但推荐仅使用任务根目录的共享 query。

附件支持 UTF-8 txt/md/csv/json，以及有文本层、无图片和矢量绘图的纯文字 PDF。来源材料超长、图表/扫描 PDF 或不支持的附件明确报错，不静默丢弃。完整格式、容量及来源约束见 [input-format.md](input-format.md)。

新入口自动把**整个输入批次**导入可处理分区，不进行研究用的随机开发/留出划分。每题只构建一套 rubric，所有同题候选共用；展示名称、人工评测、生成轨迹不进入模型输入。

## 启动

先按 [quickstart.md](quickstart.md) 安装依赖、LibreOffice 和字体，并设置自己的环境变量凭据。以下命令会产生模型调用，数字只是示例累计上限，应按实际范围设置。

轻量模式只需 `BAILIAN_API_KEY`：

```bash
python -m ppt_rubric batch \
  --source inputs \
  --out artifacts/my-batch \
  --mode light \
  --max-api-requests 10000 \
  --workers 10 \
  --confirm-bailian-bulk
```

全量模式另需 `MAAS_API_KEY`，仅增加来源/rubric 审核：

```bash
python -m ppt_rubric batch \
  --source inputs \
  --out artifacts/my-batch-full \
  --mode full \
  --max-api-requests 10000 \
  --audit-max-api-requests 1000 \
  --workers 10 \
  --confirm-bailian-bulk
```

`--mode` 默认 light，默认不调用来源审核；显式 full 必须给出审核上限。`--confirm-bailian-bulk` 落实已获得的本次多任务调用授权，不代替授权。

| 参数 | 含义 |
| --- | --- |
| `--source` | 整个输入批次目录。输入与输出不能互相包含。 |
| `--out` | 专用运行目录；首次须为空，再次使用时自动续跑。旧单例或分阶段运行目录不自动迁移。 |
| `--mode light/full` | 与原 pipeline 的模式定义相同，唯一差别为 rubric 审核。 |
| `--max-api-requests N` | 百炼整批累计请求上限，包括生成、评分、失败、重试及中断在途请求。别名 `--bailian-cap`。 |
| `--audit-max-api-requests M` | full 的 MaaS 整批累计审核上限。别名 `--maas-cap`。两供应商各自计数，总请求上限为两者之和。 |
| `--workers` | 同时推进的任务数，默认 10；生成和评分共用同一个百炼 Client 的累计预算和熔断。 |
| `--log-interval` | 进度输出间隔，默认 10 秒。 |
| `--config` | 生成配置，默认 `configs/contract-v25-k3-fast.json`。 |
| `--judge-config` | 评分配置，默认 `configs/contract-v25-judge-fast.json`。 |
| `--source-audit-config` | full 审核配置；light 不读取这个文件，也不读取审核凭据。 |

不同机器可复制配置到 `configs/local/` 后使用自己的路径。生成和评分须使用相同百炼端点和凭据环境变量，具体请求的模型、选项及输出长度按各自配置保留；生成和评分分别按各自配置控制阶段并发和启动间隔，同一 Client 的总在途上限取两配置并发值的较大者。默认 judge 配置 api_concurrency=30、assessment_workers=30、启动间隔0秒；生成保留原配置（fast 为2并发、20秒间隔），不会把 judge 限制为2并发或20秒间隔。所有任务和候选共用 judge 的30并发额度，不是每个候选各30次在途；429退避、累计预算和熔断仍全局共享。30是客户端上限，未在线验证供应商配额或吞吐。

## 看进度

终端自动输出阶段变化和定时进度，不需要网页或 localhost：

```text
10:32:00 | 批量任务 1000题 / 3000份候选；模式 light；重复同一命令即可续跑
10:32:00 | 跳过已完成 327题；待执行或恢复 673题
10:32:10 | 进度 327/1000（32.7%） | 运行 2 | 失败 1 | 缺项 0 | 待处理 670 | 累计请求 83254 | 已用 00:00:10 | 当前 task-0328 评分 候选B V_READ 第7页；task-0329 生成标准 Q02
```

上面是格式示意，不是实际评测记录。“完成”以该题全部候选的接口执行完整为准；比较分数已经可算、有模型 unknown、以及执行有缺项是不同概念。计时为本次启动耗时；请求数为整个运行目录累计消费。定时输出在请求等待时仍持续，避免把没有任务完成误认为进程卡死。

- `progress.log`：追加保存终端日志，跨次启动保留。
- `progress.json`：最新进度快照，适合脚本监控。
- `state.sqlite3`：事务状态、请求账本和成功结果，为续跑依据；账本按请求增量写入。

## 断点续跑

**再次执行同一条启动命令，使用同一 source 和 out 即可。** 不需要重新导入，不需要找失败任务 ID，也不需要手动创建恢复目录。

1. 已完成任务经过输入及产物核对后跳过。
2. 未开始的任务正常启动，不要求存在检查点。
3. 生成中断时，从持久记录复用成功的提取、逐条问题和审核响应，再继续缺失调用，不依赖 `cache/api`。
4. 评分时复用成功 yes/no/**模型主动 unknown**；只有失败或尚无结果的请求重新执行。逐页局部图按原路径及 SHA 校验后复用，原 HTTP400 和失败诊断保留在账本、旧报告及 `recovery_history` 中。
5. 失败项每次启动只经过一遍恢复流程；底层仍采用原有有界重试，不会无限自动重评。

`Ctrl+C` 或 SIGTERM 会触发停止，等待已经在途的请求保存后退出；等待时间受请求超时限制。此前在线测试观察到信号前已进入本地队列的请求仍可能在信号后发出，尚不能保证严格以信号时刻为停止边界。系统文件锁防止两个进程同时修改同一运行目录，进程被杀死或机器重启后锁会自动释放。

突然断电或强制终止时，无法确定远端是否已完成某条在途请求。续跑会保留其原 `started` 状态信息，并记录 `process_interrupted`、`remote_outcome_unknown=true`，按一次已用请求保守计入额度，缺失 usage 不算零。若没有保存结果，可能重新发送该请求，不能保证供应商端恰好执行一次；已持久保存的成功结果不会因此重评。

调用上限**跨启动累计，不清零**。例如累计已用 980 次、上限 1000，下次启动最多再用 20 次。额度用完后保留进度；若使用者明确追加额度，将同一命令的上限改为新的**累计总额**即可，不把它理解为新增次数。每次启动的上限在 SQLite sessions 中保留。

输入任务、附件、候选、模式、实际评分参数或程序代码变化时，拒绝混用旧结果，须使用新运行目录。可调整 workers、请求间隔、超时及累计上限等调度参数。已保存结果、rubric、PDF、整页和局部图会校验；损坏或缺失时明确停止，不自动补造绑定。

运行目录中的 `cache/api` 是可丢弃的请求缓存；**准备稿引用的 PDF/PNG 和 state.sqlite3 必须保留**，不能把整个 cache 当作可随意删除的目录。路径属于运行机器，直接移动目录可能破坏绑定；不要手改数据库或冻结文件恢复执行。

## 输出约定

```text
artifacts/my-batch/
├── batch.json                       # 运行协议、输入和配置绑定
├── manifest.json                    # 全部任务、原始PPT路径、候选ID与SHA
├── state.sqlite3                    # 持久状态、成功响应、累计账本及启动历史
├── progress.log                     # 追加日志
├── progress.json                    # 当前进度
├── summary.json                     # 整批状态、逐任务/候选结果、实际用量
├── summary.md                       # 可直接阅读的任务状态表
├── run-summary.json                 # 已有逐题结果
├── candidate-mapping.json           # 原作品与自动分数映射
├── candidate-mapping.csv            # UTF-8 BOM，适合表格查看
├── candidate-mapping.md             # 静态映射表
├── rubrics/<task_id>/rubric.json
├── prepared/<task_id>.json           # 准备稿与PDF/PNG指纹
├── judgments/<task_id>/
│   ├── A.json                        # 候选比较分数、原始assessment、逐条理由
│   ├── A-assessment.json             # 每条请求完成后更新的评分检查点
│   ├── B.json
│   ├── B-assessment.json
│   └── checkpoint-history/          # 续跑前的评分检查点快照，保留原失败证据
├── bailian-api/requests.json         # 启动/正常结束时导出的累计账本快照
├── maas-api/requests.json            # full模式的累计审核账本快照
└── cache/                           # 请求缓存与实际渲染资产
```

根目录汇总和映射在启动及本次执行结束时生成；运行中以 progress 和 SQLite 为准。异常断电时 JSON 账本快照可能落后，SQLite 中已提交的记录为续跑依据，下一次启动会重新导出。

`summary.json` 的 `tasks` 按任务 ID 关联执行状态、阶段及候选结果；`accounting` 分供应商记录累计请求、成功/失败、已知 tokens、缺失 usage 数及上限。`candidate-mapping` 沿用 schema 2，通过 `manifest_id + candidate_key + sha256` 关联原作品，包含比较分数、剩余满分、共同剔除项和结果文件，不含人工字段。

同题 unknown 共同剔除、原始 assessment 区间、视觉 any-no 聚合、原分值尺度的 3 分持平阈值和五维权重保持原有政策。模型 unknown 可以是一次成功执行；接口错误则仍标执行缺项，不能因比较分数可算而冒充完成。多于两候选保留各份分数和身份，不生成严格总名次，不跨任务排序。

退出码：0 为整批接口执行完成；1 为任务致命错误或账号/鉴权熔断；2 为额度停止、待处理任务或接口缺项；130 为用户请求停止。修复可变的运行环境后重发同一命令即可；若要替换候选或修改 query，则另开运行目录。

## 验证边界

自动测试使用模拟 HTTP 和渲染，覆盖端到端批量流程、生成中断、评分失败、成功模型 unknown 复用、API 缓存丢失、真实子进程突然退出、信号停止、并发累计预算、失败隔离和输入/产物变化拒绝。1000 题、3000 候选测试验证导入、任务调度和结果行完整性，生成/渲染/评分使用替代函数，不是数千份真实 PPT 的吞吐或质量验收。
