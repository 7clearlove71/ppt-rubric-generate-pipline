# 接口失败日志与局部恢复

2026-10-22 接入当前 contract-v2.5。此次仅代码修订和离线模拟验证，不发起新 API 调用，不改写历史账本、判断或分数。

通用`batch`另支持在专用运行目录重复同一命令自动续跑，覆盖未开始任务、部分生成和部分评分，使用SQLite保存累计账本与成功响应；不迁移或重开下文历史`judge --resume-from`的运行目录。断电遗留在途请求按已用额度记录远端结果未知，具体输入输出、信号停止及预算规则见[批量使用说明](batch.md)。下文严格新目录恢复命令保持原样。

## 错误记录

`requests.json` 保留每一次失败和后续重试，新增以下信息：

- `http_status`、`failure_stage`、`error_category`、`retryable`、`fatal`。
- `provider_error.code/type/param/message/provider_request_id`。服务端 Request ID 与本地 `ledger_request_id` 分开保存，并带 `invocation_id`。
- `request_context.document_id/rubric_id/pages`，用于关联逐候选报告、评分项和页码。

程序只保留允许的错误字段和可识别的通用错误说明。不记录任意响应头、凭据或请求回显；可能含原文的未知 message 明确标记 omitted，不假称已取得原始说明。服务未返回错误码、Request ID 或 usage 时保留缺失，不填造值或零消费。

评分报告的失败记录仍为 `status=unavailable`，原始 verdict 保留 `answer=unknown` 和 `unknown：HTTP400` 等文字，`unknown_origin=execution_error`。模型主动给出的 boxed unknown 则为 `model_insufficient_evidence`，成功判断不因恢复被重评。

原始 `assessment.summary` 保留区间。工作区现行比较计分独立读取最终 verdict；即使另一页的 no 已决定整条视觉结果、比较总分已经完整，`execution.status` 仍会报告尚未恢复的接口失败。

## 按错误码处理

| 类型 | 行为 |
| --- | --- |
| `Arrearage`、账号停服、预算耗尽；401/403 鉴权或权限失败 | 共享 Client 熔断，停止排队请求，保留检查点；已在途请求可能仍会完成 |
| `InvalidParameter`（含 `InternalError.Algo.InvalidParameter`）、内容检查失败 | 保留具体错误，不原样自动重试、不删除待评材料 |
| 已知内部服务错误、408/409/429、500/502/503/504 | 既有有界退避与总尝试上限；429 继续遵守共享等待和熔断 |
| 无法分类的 HTTP400 | 默认不重试，保留 unknown 和错误详情 |

可选配置 `retry_unknown_http400_once: true` 仅为无法分类的 400 增加至多一次原样诊断机会，仍受 `max_retries` 和请求预算限制；不修改提示词、材料或模型。已识别的账号、参数及内容检查错误不受此选项放行。原来的 HTTP400 失败行不会被成功重试覆盖。

单例 runner 的凭据读取顺序为配置所指环境变量优先，缺失或空白时才读取既有服务器凭据文件。不会写回或替换磁盘 key。普通 CLI 继续使用配置中的 `api_key_env`；不同 provider 的凭据边界保持。

## 只恢复失败或未完成的评分请求

以下命令示例会执行模型请求，只有在用户授权该次恢复范围后才运行。本次修订未执行此命令。

```bash
python -m ppt_rubric contract-v2.5 judge \
  --manifest artifacts/my-batch/manifest.json \
  --config configs/contract-v25-judge-fast.json \
  --rubrics artifacts/my-batch/rubrics \
  --cases 03 \
  --resume-from artifacts/my-batch/judgments \
  --out artifacts/my-batch/recovery-01 \
  --cache .cache \
  --recovery-request-cap 3
```

`--resume-from` 接受标准 judge 输出目录，或者含 `judgments`、`prepared-decks.json` 和唯一 `bailian-api` 账本的单例 runner 根目录。runner 冻结的准备稿会直接复用；无需重新提取需求、生成 rubric 或来源审核。这里的 3 仅为示例新增请求上限，包含失败和重试，不是额外授权或旧余额。

恢复要求新的空输出目录，且不能位于原运行目录内。旧目录、旧关闭授权和账本保持不变，新目录的 `recovery.json` 记录原账本路径与哈希、旧请求数、新增上限及实际新增数。累计上限为旧请求数加本次明确上限；恢复链继续累加，零次请求也保存空账本。未核对的在途请求、尚未关闭的原运行会被拒绝。

恢复前先检查全部所选候选的来源、rubric、完整稿图片及辅助文字、judge prompt、实际模型和请求参数。检查点有独立完整性哈希；已送评的局部图也按原路径和 SHA-256 校验后复用。间隔、并发、等待时限及新增预算可以调整，实际评分请求不变。

成功记录直接从检查点复用，包括 yes/no 和模型主动给出的 unknown，即使 API 缓存不存在也不会重新调用。只有 `unavailable` 或中断后尚未保存的请求会进入恢复。当前视觉每页一次调用全部5条，每页完成后保存原始响应及5条独立结果；成功页包含模型 unknown 整体复用，只恢复失败或未完成页。新记录的 `recovery_history` 保留历次失败（含 HTTP400、服务端错误及 Request ID），原始日志另在旧目录完整保留。当前 judge validator10 的按页协议与旧单条视觉检查点不兼容，不自动迁移。

新机制要求本次版本写出的 `recovery_binding`、逐请求 `input_hash` 和 `checkpoint_hash`。更早历史记录缺少这些绑定时会在发请求前明确拒绝，不自动补造签名或迁移旧报告。该入口只处理评分恢复；首版 rubric 生成失败仍按原生成流程处理。

单例 runner 和 judge 均区分执行完整性：账号/鉴权致命失败返回 1；仍有接口缺项返回 2，即使比较分数已经可算；成功执行且原有结果状态允许完成时返回 0。新的比较计分、原始区间和执行状态分别报告。

## 离线验证

测试全局拦截真实 HTTP transport；供应商响应、失败、并发和限额均使用本地模拟。覆盖错误脱敏、code/type/param/Request ID 传播、共享熔断、一次有界诊断、累计预算、环境 key 优先、缓存缺失时的局部恢复、失败历史保留，以及图像/来源/参数/检查点改变后的请求前拒绝。
