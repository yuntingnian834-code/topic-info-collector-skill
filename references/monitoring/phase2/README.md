# 第二阶段：模型与Prompt监控

## 范围

第二阶段回答“调用是否稳定、结构化输出是否可靠、Prompt版本是否退化、一次运行消耗了多少Token与时间”。实现位于 `scripts/monitoring/phase2/`，与第一阶段共用 `data/monitoring.sqlite3`。

## 已记录字段

- 关联：`run_id`、`article_id`、调用类型。
- 配置：模型名称/返回版本、Prompt版本、temperature。
- 性能：输入/输出Token、调用延迟、可选成本估算。
- 质量：JSON是否合法、重试次数、是否降级、结果状态、错误类型。

监控库不保存生产文章正文、完整Prompt、API密钥或Webhook。Prompt只保存版本号和用途说明。

## 指标定义

| 指标 | 定义 |
|---|---|
| 模型调用成功率 | 未发生API/网络失败的调用 ÷ 总调用 |
| JSON一次成功率 | 主提取调用首次返回合法JSON的数量 ÷ 主提取调用 |
| JSON修复率 | 修复调用数量 ÷ 主提取调用 |
| 降级率 | 触发中文摘要兜底数量 ÷ 主提取调用 |
| 事件日期提取率 | 有日期结果的候选文章 ÷ 进入日期关卡的候选文章 |
| Token/写入记录 | 本次所有调用Token ÷ 飞书成功写入数 |
| P95延迟 | 本次调用延迟的第95百分位 |

成本仅在 `.env` 配置 `MODEL_INPUT_COST_PER_MILLION` 与 `MODEL_OUTPUT_COST_PER_MILLION` 后估算，单位为美元/百万Token。

## Prompt版本

| 版本 | 用途 |
|---|---|
| `relevance_extract_v1.0` | 相关性、事件日期与情报字段提取 |
| `json_repair_v1.0` | 非法JSON的一次修复 |
| `summary_fallback_v1.0` | 结构化洞察缺失时的中文兜底 |
| `daily_summary_v1.0` | 飞书每日核心看点 |

修改Prompt时必须新建版本号，不得覆盖旧版本含义，以便跨版本比较。

## 固定回归评测

`evaluation_cases.json` 当前含6个合成样本，覆盖3个目标领域和3类噪音。每个样本检查相关性；正样本还检查事件日期和关键词。执行结果写入 `evaluation_runs` 与 `evaluation_results`，评测批次标记为 `evaluation`，不会替代最近一次正式运行，也不会默认混入7天正式运行对比。

```bash
# 查看正式运行指标
python scripts/monitoring/phase2/model_monitor.py

# 手动评测；会调用真实模型并产生Token消耗
python scripts/monitoring/phase2/evaluate_prompts.py
```

上线新Prompt前应先运行固定评测；任何用例失败均返回非零退出码，并标记为 `regression`。
