# 02单例：固定权重调整后的contract-v2.5

2026-10-21，用户明确要求I_BOUNDARY从15分降至10分，V_READ从3.3333分升至8.333分，并重跑02。五维预算现为30/25/10/15/20；V_READ精确25/3分，其余固定视觉项不变。仅02/A（10页）、02/B（8页），不运行其他开发样例或留出，不读取新人工标签。

524项离线测试通过；当前rubric validator8、judge validator9、single_k3。K3提取与逐条生成，Kimi-K2.6独立来源审核（thinking enabled）；候选内容和视觉仅K3评分。冻结PDF/PNG复用，PDF文字重新提取，未新渲染。

来源首轮三次HTTP200响应都因item_ids使用Q需求ID而非R_Q评分ID被拒。一次有界技术恢复明确合法ID并完善错误反馈，不自动改答案或放宽校验；旧失败原文和关闭标记保留，恢复单独记录，同一累计账本扣除旧消费。复用本轮已成功的提取/问题缓存，未复用旧批准。有效来源审核先后拒绝6处、4处原子性问题，需求15→23→31，第三轮批准，共44条（39内容、5视觉）。

| 候选 | 完整分数 | 区间 | 未决权重 |
| --- | --- | --- | --- |
| A | 无 | 77.166667—78.166667 | 1 |
| B | 100 | 100—100 | 0 |

A的R_Q11b因HTTP400没有模型答案、没有usage，保留1分unknown；不是模型材料不足结论。3分持平阈值下，区间稳健偏好B，领先21.833333—22.833333分。没有候选独立复核或合并结果。

A扣R_Q15受众适配10、C_CONSISTENCY 1/6、V_READ 25/3、V_RESIDUE 10/3。V_READ失败页4/8/9，V_RESIDUE仅第8页失败；所有其他视觉判断yes。B所有条目yes。

实际语义局限必须与原始分数一起看：R_Q15以叠印不可读为理由，与V_READ重复；V_RESIDUE又把第8页同一叠印当破损占位残留，没有独立NaN/错误引用证据。A一致性理由把第7页未列流动资金推成第一年不使用，互斥关系支撑不足。R_Q07c重新带入按件计酬，R_Q15仍混入一般性“站得住”要求。来源审核批准不能证明原子性和无重叠。B信息诚信yes包含对资金申请资格等扩展的常识性归类，本轮未外部核查，不能称该事实已核实或已证伪。历史冻结送评图存在叠印，不直接代表用户端原稿表现，也未定位转换原因。原模型答案、rubric及分数未手改。

实际百炼240次（239成功、1次HTTP400）、MaaS6次（3次schema失败、3次成功），共246次；服务报告2,187,159 tokens，另1次usage缺失不算零。评分168次=2×39+18×5，167份有效回答各有一个合法boxed：161 yes、6 no；另1次接口unknown无模型答案。耗时115.76分钟，无在途worker，两份authorization均closed，未用操作上限不滚存。37份当前源码及冻结副本、20渲染资产、54个实际送评图片路径哈希有效。未计算新人工一致率，不报告生产验收通过。

完整过程、每轮全部需求、44条实际问题、全部判断原文与逐页视觉见[full-report.md](../artifacts/validation-20261021-contract-v25-case02-weights/full-report.md)。[机器汇总](../artifacts/validation-20261021-contract-v25-case02-weights/validation-summary.json)、[用量](../artifacts/validation-20261021-contract-v25-case02-weights/final-accounting.json)、[核对](../artifacts/validation-20261021-contract-v25-case02-weights/final-verification.json)、[语义观察](../artifacts/validation-20261021-contract-v25-case02-weights/observation-notes.json)均保留。唯一累计账本为本目录bailian-api/requests.json与maas-api/requests.json，初次失败快照不重复计数。
