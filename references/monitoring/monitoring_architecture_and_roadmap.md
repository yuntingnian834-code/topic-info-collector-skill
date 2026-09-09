# Commodity Intel Skill 运行数据监控方案

## 1. 监控目标

Skill运行监控需要持续回答四个问题：

1. **有没有正常运行：**任务是否按时启动、是否完成、耗时多久。
2. **卡在哪个环节：**RSS、正文解析、LLM、日期校验、去重、飞书写入还是网页发布。
3. **产出质量是否异常：**候选数据是否骤降、过滤是否突然变严、JSON和日期提取是否稳定。
4. **结果是否成功交付：**飞书数据、群卡片和网页快照是否为同一批数据。

第一版只监控Skill的后台运行和数据质量，不包含网页用户点击。用户行为埋点作为独立模块建设。

## 2. 监控链路

```text
Skill触发
  ↓
加载课题与信息源
  ↓
RSS抓取
  ↓
时间窗口过滤
  ↓
正文解析
  ↓
LLM相关性与结构化抽取
  ↓
事件日期校验
  ↓
URL/内容指纹去重
  ↓
飞书写入
  ↓
飞书简报推送
  ↓
网页快照生成与发布
  ↓
Skill运行结束
```

每次运行生成唯一的`run_id`，每篇文章生成稳定的`article_id`。所有日志、模型调用和交付状态都通过这两个ID关联。

## 3. 运行状态定义

每次Skill运行只能进入以下状态之一：

| 状态 | 含义 |
|---|---|
| `running` | 已启动，尚未结束 |
| `success` | 采集、写入、推送和网页发布均成功 |
| `partial_success` | 核心数据已生成，但部分来源、推送或网页发布失败 |
| `failed` | 核心采集或写入失败，无法形成有效产出 |
| `skipped` | 手动跳过采集、推送或网页发布，并非系统故障 |
| `empty_success` | 正常执行但当天无新增情报 |

需要区分`empty_success`和`failed`：没有新增资讯不一定是故障，但所有信息源都抓取失败导致0条数据属于故障。

## 4. 五层监控指标

### 4.1 任务级监控

每次运行记录：

```text
run_id
run_date
trigger_type：schedule/manual/retry
skill_version
code_version
started_at
completed_at
status
duration_ms
error_stage
error_type
```

核心指标：

- 每日运行成功率
- 按时启动率
- 总运行时长及P95
- `partial_success`和`failed`次数
- 连续失败天数
- 手动重跑次数

### 4.2 信息源健康监控

每个RSS源每次运行记录：

```text
source_id
feed_url
http_status
request_latency_ms
cache_hit
entry_count
fresh_entry_count
parse_success_count
last_success_at
failure_reason
```

核心指标：

- 信息源请求成功率
- 来源连续失败次数
- 每个来源候选文章数
- 新鲜文章占比
- 正文解析成功率
- 来源最后成功时间

信息源连续返回0条与请求失败需要分开记录。前者可能只是当天没有更新，后者属于技术故障。

### 4.3 提纯漏斗监控

每次运行汇总：

```text
rss_entry_count
candidate_count
time_pass_count
content_parse_success_count
relevance_pass_count
event_date_pass_count
url_dedupe_pass_count
content_dedupe_pass_count
written_count
```

由此计算：

- 时间通过率
- 正文解析成功率
- 相关性通过率
- 事件日期通过率
- URL重复率
- 内容重复率
- 最终写入率

监控重点不是要求每一步通过率越高越好，而是观察相对自身历史基线是否突然变化。例如相关性通过率突然从过去平均水平降到很低，可能是Prompt、模型或信息源内容发生变化。

### 4.4 LLM质量与成本监控

每次模型调用记录：

```text
call_id
run_id
article_id
call_type：relevance_extract/json_repair/summary_fallback/daily_summary
model_name
model_version
prompt_version
temperature
input_chars
input_tokens
output_tokens
latency_ms
json_valid
retry_count
fallback_used
result_status
error_type
```

核心指标：

- 模型调用成功率
- JSON一次解析成功率
- JSON修复触发率
- 中文降级触发率
- 事件日期提取成功率
- 平均及P95响应时间
- 每条最终情报平均Token数
- 每次Skill运行总Token及估算成本
- 不同Prompt版本的相关性通过率与人工准确率

### 4.5 交付一致性监控

记录：

```text
feishu_written_count
feishu_query_count
feishu_push_status
feishu_push_version
web_snapshot_count
web_snapshot_date
web_publish_status
data_version
```

核心校验：

```text
written_count = 飞书当日新增数
飞书回读数 = 网页快照记录数
飞书简报日期 = 网页快照日期 = run_date
```

如果数量或版本不一致，运行状态应为`partial_success`，不能标记为完全成功。

## 5. 事件设计

### 任务事件

- `skill_run_started`
- `skill_config_loaded`
- `skill_run_completed`
- `skill_run_failed`
- `skill_run_empty`

### 来源事件

- `feed_fetch_started`
- `feed_fetch_completed`
- `feed_fetch_failed`
- `feed_cache_hit`

### 文章事件

- `article_collected`
- `article_time_blocked`
- `content_parse_completed`
- `content_parse_failed`
- `relevance_classified`
- `structured_output_completed`
- `structured_output_failed`
- `event_date_blocked`
- `url_duplicate_blocked`
- `content_duplicate_blocked`
- `record_written`
- `record_write_failed`

### 交付事件

- `feishu_push_completed`
- `feishu_push_failed`
- `feishu_push_skipped`
- `web_snapshot_completed`
- `web_publish_completed`
- `web_publish_failed`
- `delivery_mismatch_detected`

## 6. 数据表设计

### 6.1 `skill_runs`：一次运行一条记录

```text
run_id                 主键
run_date
trigger_type
skill_version
code_version
started_at
completed_at
status
duration_ms
candidate_count
time_pass_count
parse_success_count
relevance_pass_count
event_date_pass_count
dedupe_pass_count
written_count
failed_count
feishu_push_status
web_publish_status
error_stage
error_type
```

### 6.2 `source_runs`：每个来源每次运行一条记录

```text
id                     主键
run_id                 外键
source_id
feed_url
http_status
latency_ms
entry_count
fresh_entry_count
parse_success_count
status
failure_reason
```

### 6.3 `article_events`：每篇文章的处理轨迹

```text
event_id               主键
run_id                 外键
article_id
event_name
event_time
source_id
level1
level2
status
reason_code
reason_detail
properties_json
```

### 6.4 `model_calls`：每次模型调用一条记录

```text
call_id                主键
run_id
article_id
call_type
model_name
model_version
prompt_version
input_tokens
output_tokens
latency_ms
json_valid
retry_count
fallback_used
status
error_type
```

### 6.5 `quality_reviews`：人工抽检与Bad Case

```text
review_id              主键
article_id
sample_type            retained/blocked
expected_relevance
actual_relevance
event_date_correct
metrics_correct
summary_faithful
bad_case_type
prompt_version
reviewer
reviewed_at
fix_status
regression_status
```

## 7. 告警分级

### P0：必须立即处理

- Skill核心采集或写入失败。
- 运行超过现有Workflow的最大时间仍未结束。
- 所有信息源抓取失败。
- 已确认写入后仍出现重复记录。
- 飞书与网页数据日期或版本不一致。
- 密钥、原文敏感数据意外出现在监控日志中。

### P1：当天处理

- 较多信息源同时失败。
- 正文解析成功率、JSON成功率明显低于过去7日基线。
- 模型降级或重试比例显著上升。
- 有候选数据但最终写入数为0。
- 飞书推送或网页发布失败，但数据已成功入库。

### P2：周度复盘

- 单个低频来源连续多次无更新。
- 相关性通过率、重复率出现缓慢变化。
- Token成本或处理时间持续上升。
- 某类Bad Case在一周内重复出现。

## 8. 初始告警规则

上线第一周先收集基线，不宜一次设置过多固定阈值。可先使用以下规则：

| 指标 | 初始判断方式 | 告警 |
|---|---|---|
| Skill失败 | `status=failed` | P0 |
| 运行卡住 | 超过既定最大运行时间 | P0 |
| 全来源失败 | 成功来源数为0 | P0 |
| 多端不一致 | 飞书数量或日期不等于网页快照 | P0 |
| 有候选但0写入 | `candidate_count>0`且`written_count=0` | P1，需结合重复率判断 |
| 来源异常 | 连续3次请求失败 | P1 |
| 数据量异常 | 低于过去7日均值50%或高于200% | P1，初期可调整 |
| JSON异常 | 失败率高于过去7日均值明显区间 | P1 |
| 降级异常 | 降级率高于过去7日基线 | P1 |
| 成本异常 | 单条情报Token成本较7日均值显著上升 | P2 |

数据量具有新闻周期波动，因此“当天0条”不能自动判为故障。必须结合来源请求成功率、候选数和重复数判断。

## 9. 每日监控卡片

每天运行结束后向飞书发送内部监控卡片：

```text
SHU SIGNAL Skill运行报告｜2026-09-04

状态：成功
运行批次：run_20260904_xxxx
运行时长：xx分钟

信息源：成功 xx / 总计 xx
RSS条目：xxx
候选文章：xxx
时间通过：xxx
正文成功：xxx
相关性通过：xxx
日期通过：xxx
去重后写入：xxx

JSON一次成功率：xx%
模型重试：x次
中文降级：x次
Token总量：xxxx

飞书写入：成功
群简报：成功
网页发布：成功

异常：2个来源请求失败｜查看明细
```

无异常时只发简洁摘要；有P0/P1异常时展示错误阶段、原因和建议动作。

## 10. 监控看板

### 每日总览

- 最近一次运行状态
- 今日运行时长
- 成功/失败来源数
- 完整提纯漏斗
- 模型调用成功率
- 飞书与网页交付状态

### 7日趋势

- 候选数与最终写入数
- 相关性通过率
- 解析成功率
- 重复率
- JSON修复率与降级率
- Token成本与P95耗时

### 来源健康

- 每个来源最近成功时间
- 连续失败次数
- 7日平均条目数
- 正文解析成功率
- 最终产生有效情报数

### Bad Case

- Bad Case类型分布
- 不同Prompt版本错误率
- 待修复与已回归数量

## 11. 与现有代码的接入点

| 文件/函数 | 监控职责 |
|---|---|
| `scheduler.py::run_pipeline()` | 定时触发与调度异常 |
| `daily_pipeline.py::run_daily_pipeline()` | 创建`run_id`、汇总最终状态 |
| `feishu.py::get_info_points()` | 配置加载成功率与课题数 |
| `fetch_data.py::_parse_rss_feed()` | 来源请求、缓存、条目和时间拦截 |
| `fetch_data.py::_fetch_full_text()` | 正文成功、策略、字数、超时 |
| `fetch_data.py::_deepseek_chat()` | 模型耗时、Token、错误 |
| `fetch_data.py::summarize_v2()` | Prompt版本、相关性、JSON重试、降级 |
| `fetch_data.py::_build_record()` | 事件日期拦截 |
| `fetch_data.py::fetch_and_write()` | 去重、写入、运行漏斗 |
| `reporter.py::send_daily_report()` | 群简报发送或跳过 |
| `export_web_snapshot.py::export()` | 网页快照日期、版本和记录数 |

## 12. 实现原则

### 监控不阻塞主业务

统一封装：

```python
track_event(...)
```

内部捕获异常。监控写入失败时只记录告警，不中断采集、飞书写入或网页发布。

### 使用批量写入

文章级事件先进入内存缓冲区，达到一定数量或运行结束时批量写入，避免每个事件都发起远程请求。

### 监控自身失败处理

- 远程数据库不可用时写入本地临时缓冲文件。
- GitHub Actions结束前尝试补传。
- 仍失败时保存为任务Artifact并发送告警。
- 下一次运行不得把旧事件误记到新`run_id`。

### 版本可追溯

每次运行记录：

```text
skill_version
code_version/git_sha
prompt_version
model_name/model_version
schema_version
```

没有版本信息，就无法判断质量变化是否由Prompt、模型或代码更新造成。

### 数据安全

- 不记录API Key、飞书Secret和Webhook地址。
- 不把完整文章正文写入监控事件。
- 错误信息做长度截断和敏感字段清理。
- 测试与生产数据使用不同`environment`。

## 13. 分阶段执行

### 第一阶段：任务与漏斗监控（P0）

1. 建立`skill_runs`、`source_runs`、`article_events`。
2. 在`daily_pipeline.py`生成并传递`run_id`。
3. 接入时间、解析、相关性、日期、去重和写入事件。
4. 生成每日提纯漏斗和运行卡片。
5. 核对汇总数字可以由明细重新计算。

完成标志：能够准确解释每次运行从候选文章到最终写入的全部损耗。

#### 实施结果（2026-09-07）

第一阶段已完成代码接入并通过离线测试：

- `scripts/monitoring/phase1/runtime_monitor.py` 已建立本地 SQLite 的 `skill_runs`、`source_runs`、`article_events` 三张表。
- `daily_pipeline.py` 为每次任务生成并向采集链路传递 `run_id`；文章使用清理跟踪参数后的 URL 生成稳定 `article_id`。
- 已接入候选、时间、正文解析、相关性、事件日期、URL/内容去重与飞书写入事件，并保留具体拦截原因。
- 运行结束后会重新从文章事件计算漏斗，写回运行汇总，并在报告中校验汇总与明细是否一致。
- 已区分 `success`、`partial_success`、`failed`、`empty_success`，全来源失败会停止推送和网页更新，多端数量或日期不一致会阻止发布。
- 每次运行生成本地 JSON 与 Markdown 报告；配置独立监控 Webhook 后可发送飞书运行卡片。GitHub Actions 会保留 30 天监控构件。
- 自动化测试覆盖正常成功、来源全部失败、多端不一致、重复事件不重复计数、同域名多 RSS 源和监控存储故障降级。

测试命令：`python3 -m unittest discover -s tests -p 'test_*.py' -v`。

### 第二阶段：模型与Prompt监控（P0）

1. 建立`model_calls`。
2. 为所有Prompt增加显式版本号。
3. 记录耗时、Token、JSON修复和降级。
4. 建立模型质量与成本趋势。

完成标志：质量下降时能够判断是否与模型或Prompt版本有关。

#### 实施结果（2026-09-07）

- 新增 `prompt_versions` 与 `model_calls`，4类模型调用都有独立Prompt版本。
- 记录模型名称/返回版本、temperature、输入输出Token、延迟、JSON合法性、重试、降级、状态与脱敏错误。
- 运行报告已展示调用成功率、JSON一次成功率、修复率、降级率、事件日期提取率、Token、单条写入Token、平均/P95延迟和可选成本。
- 新增按最近7天聚合的Prompt版本对比，便于区分模型故障、结构化输出问题与Prompt退化。
- 建立6条合成固定评测样本，检查相关性、事件日期和关键词；失败用例写入明细并以 `regression` 标记。
- 评测批次与正式运行隔离，不会覆盖最近一次正式运行；监控库不保存生产正文或完整Prompt。

实现、口径与运行命令见 `references/monitoring/phase2/README.md`。

### 第三阶段：网页行为与用户价值监控（P1）

1. 接入页面、筛选、搜索、详情、原文、时间线和反馈事件。
2. 使用匿名用户ID、会话ID与渠道归因串联访问路径。
3. 建立用户价值指标口径并隔离测试流量。

完成标志：能够计算完整访问漏斗，并识别哪些情报被真实用户认可。

#### 实施结果（2026-09-07）

已覆盖5个页面、10类事件、2张D1表和8项核心指标；实现搜索防抖、渠道归因、事件幂等、测试流量隔离、反馈更新和联系方式脱敏。详见 `references/monitoring/phase3/README.md`。

### 第四阶段：告警、Bad Case与持续维护（P1）

1. 设置P0/P1/P2告警和最小样本量。
2. 建立网页与Skill两类`quality_reviews`。
3. 每周抽检保留和拦截文章，用户负反馈自动回流。
4. 建立7天/30天看板、自动周报与月度复盘。
5. Prompt修改后运行固定评测集，并明确数据保留策略。

完成标志：形成“发现—归因—修复—回归”的维护闭环。

#### 实施结果（2026-09-07）

已新增用户价值看板、10条后台运行告警、5条网页价值告警、Bad Case状态流转、周一双向抽检、每日异常检查、周报/月报和180/365天保留策略。详见 `references/monitoring/phase4/README.md`。

### 第五阶段：数据新鲜度与发布可靠性（P1）

1. 移除页面固定日期，展示真实快照日期和新鲜度。
2. 建立D1每日成功快照，降低重复读取飞书历史数据的延迟。
3. 实时读取失败时回退最近成功快照，并明确标记过期。
4. 每日任务完成后预热缓存并核对网页日期、记录数。
5. 北京时间08:00和12:00执行独立新鲜度检查。

完成标志：采集成功后可以证明用户端看到的日期和记录数一致，读取失败也不会回退到演示数据。

#### 实施结果（2026-09-08）

已完成页面日期动态化、D1每日快照、失败回退、刷新限流、任务后预热、独立新鲜度监控和P0/P1告警。详见 `references/monitoring/phase5/README.md`。

## 14. 验收标准

1. 每次运行有且只有一条开始和结束记录。
2. 运行汇总与文章事件明细数量一致。
3. 每条最终情报可以追溯到运行批次、原始文章、模型和Prompt版本。
4. 能区分正常空数据、全部来源失败和全部被过滤。
5. 能定位任务失败的具体阶段和错误类型。
6. 飞书与网页数量、日期和数据版本一致。
7. 监控写入失败不影响主业务。
8. 测试环境数据不进入正式看板。
9. 日志中不包含凭证与不必要的文章正文。
10. Prompt或模型升级前能够执行固定回归测试。

## 15. 面试表达

> 我将Skill运行监控分成五层：任务状态、信息源健康、数据提纯漏斗、LLM质量与成本、多端交付一致性。每次运行生成唯一run_id，每篇文章生成article_id，记录它经过时间过滤、正文解析、相关性判断、事件日期、去重和写入各环节的状态。同时记录模型与Prompt版本、JSON成功率、重试、降级、Token和耗时。运行结束后自动生成飞书监控卡片；出现任务失败、来源异常或多端数据不一致时分级告警。后续结合人工抽检和Bad Case库，形成监控、归因、修复和回归验证的维护闭环。
