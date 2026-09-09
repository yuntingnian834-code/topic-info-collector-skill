# SHU SIGNAL 数据埋点实施规划

## 1. 项目目标

本次工作不是单纯增加访问量统计，而是补齐三类数据能力：

1. **提纯漏斗监控：**回答 130 条候选信息分别在哪个环节被拦截，以及为什么最终留下 48 条。
2. **AI 质量监控：**回答模型、Prompt、JSON 输出、日期提取和降级策略是否稳定。
3. **用户价值验证：**回答用户是否查看情报、是否打开原文、时间线是否有用，以及哪些信息真正被用户认可。

埋点系统必须满足一个原则：**埋点失败不能影响主业务采集、飞书写入和网页使用。**

## 2. 当前状态与缺口

### 已有能力

- 控制台输出相关性、日期和重复拦截原因。
- `fetch_and_write()` 返回写入成功数与失败数。
- 已有 URL、摘要内容指纹、超时、降级和幂等机制。
- 飞书、多维表格和网页已经形成交付链路。

### 当前缺口

- 日志以文本为主，无法按文章、来源、日期或 Prompt 版本聚合。
- 只有“130→48”的头尾结果，没有各过滤环节的完整漏斗。
- 缺少统一的 `pipeline_run_id`，无法追踪一次任务的完整执行过程。
- 没有记录模型版本、Prompt 版本、耗时、重试和降级情况。
- 网页没有记录搜索、筛选、详情、原文和时间线等用户行为。
- 没有用户有用性反馈和 Bad Case 闭环。

## 3. 总体架构

```text
Python每日采集任务
  ├─ pipeline_runs：每次任务汇总
  └─ article_events：每篇文章各关卡事件
              ↓
        数据监管看板

网页与飞书入口
  └─ POST /api/events
              ↓
        user_events：匿名行为事件
              ↓
       用户漏斗与有用性分析

人工抽检/用户反馈
  └─ quality_reviews：Bad Case与修复状态
```

### 推荐存储方案

- **后端与网页行为事件：**使用项目现有 Worker 接收事件，使用 D1 或其他持久数据库存储。
- **运营查看：**后续可将每日汇总同步到飞书“运行监控表”，方便非技术用户查看。
- **不建议：**把用户行为直接写入网页数据 JS 文件，静态文件无法可靠接收并发事件。

## 4. 统一事件规范

### 命名规则

- 使用小写蛇形命名：`article_collected`、`detail_view`。
- 事件名称描述“已经发生的动作”，不写成按钮中文或页面文案。
- 已发布事件尽量不改名；新增字段时增加 `schema_version`。

### 公共字段

每个事件至少包含：

| 字段 | 用途 |
|---|---|
| `event_id` | 事件唯一ID，用于幂等去重 |
| `event_name` | 事件名称 |
| `event_time` | UTC时间，展示时转换为北京时间 |
| `schema_version` | 埋点结构版本 |
| `environment` | `production`/`test` |
| `channel` | `pipeline`/`web`/`feishu` |
| `properties` | 事件扩展属性 |

### 业务标识

| 标识 | 说明 |
|---|---|
| `pipeline_run_id` | 一次完整采集任务ID |
| `article_id` | 原始文章ID，建议由规范化URL生成 |
| `intelligence_id` | 最终情报记录ID |
| `topic_id` | 研究课题或事件主题ID |
| `anonymous_user_id` | 匿名用户ID，不保存姓名手机号 |
| `session_id` | 一次网页访问会话 |

## 5. 第一类：提纯漏斗埋点

### 核心事件

| 顺序 | 事件 | 触发时机 | 关键属性 |
|---|---|---|---|
| 1 | `pipeline_started` | 每日任务开始 | 运行日期、触发方式 |
| 2 | `feed_fetch_started` | 开始请求RSS | feed URL、来源 |
| 3 | `feed_fetch_completed` | RSS请求完成 | 条目数、缓存命中、耗时 |
| 4 | `article_collected` | 获得候选条目 | 文章ID、来源、发布时间 |
| 5 | `article_time_blocked` | 年份或时间窗口失败 | 拦截原因、发布时间 |
| 6 | `content_parse_completed` | 正文解析完成 | 字数、解析策略、耗时 |
| 7 | `content_parse_failed` | 正文解析失败或超时 | 错误类型、耗时 |
| 8 | `relevance_classified` | 模型完成相关性判断 | true/false、拒绝原因 |
| 9 | `structured_output_completed` | JSON解析成功 | 字段完整度、事件日期 |
| 10 | `structured_output_failed` | JSON失败 | 是否重试、错误类型 |
| 11 | `event_date_blocked` | 事件日期不满足窗口 | 事件日期、窗口类型 |
| 12 | `url_duplicate_blocked` | URL重复 | 已存在记录ID |
| 13 | `content_duplicate_blocked` | 内容指纹重复 | 已存在记录ID |
| 14 | `record_written` | 飞书写入成功 | 情报ID、分类、课题 |
| 15 | `record_write_failed` | 飞书写入失败 | 状态码、错误类型 |
| 16 | `pipeline_completed` | 整次任务结束 | 各环节计数、总耗时 |

### 与现有代码的对应位置

| 代码位置 | 应增加的埋点 |
|---|---|
| `daily_pipeline.py::run_daily_pipeline()` | `pipeline_started/completed`、推送和网页发布状态 |
| `fetch_data.py::_parse_rss_feed()` | Feed请求、候选文章、时间拦截 |
| `fetch_data.py::_fetch_full_text()` | 正文解析成功、失败和超时 |
| `fetch_data.py::summarize_v2()` | 模型、相关性、JSON、重试、降级 |
| `fetch_data.py::_build_record()` | 事件日期拦截 |
| `fetch_data.py::fetch_and_write()` | URL/内容重复、写入成功或失败 |
| `reporter.py::send_daily_report()` | 飞书推送、空数据跳过 |
| `export_web_snapshot.py::export()` | 网页快照记录数和发布时间 |

### 必须记录的模型属性

```text
model_name
model_version
prompt_version
temperature
input_chars
input_tokens（接口可获得时）
output_tokens（接口可获得时）
latency_ms
json_valid
retry_count
fallback_used
```

## 6. 第二类：网页用户行为埋点

### P0事件

第一版只埋能够回答核心价值的事件：

| 事件 | 触发时机 | 关键属性 |
|---|---|---|
| `page_view` | 页面加载成功 | page、referrer、campaign |
| `category_filter_applied` | 点击分类筛选 | category、result_count |
| `search_submitted` | 用户确认搜索或停止输入 | keyword、result_count |
| `search_zero_result` | 搜索结果为0 | keyword |
| `intelligence_clicked` | 列表点击情报 | 情报ID、位置、分类 |
| `detail_view` | 详情页加载 | 情报ID、来源、课题 |
| `source_clicked` | 点击原文链接 | 情报ID、来源网站 |
| `timeline_clicked` | 点击事件时间线 | 情报ID、topic ID |
| `feedback_submitted` | 有用/无用反馈 | 情报ID、结果、原因 |

### 暂不优先

- 鼠标移动、滚动每10%等高频事件。
- 每个按钮的无业务意义点击。
- 无法对应产品问题的PV堆积。
- 未经必要性评估的个人身份信息。

### 前端代码位置

| 页面 | 现有交互 | 埋点 |
|---|---|---|
| `shu-signal-draft.html` | 首页分类、详情入口、完整情报库 | `page_view`、筛选、入口点击 |
| `intelligence.html` | 分类、搜索、列表点击 | 筛选、搜索、零结果、情报点击 |
| `intelligence-detail.html` | 原文、时间线 | 详情、原文、时间线点击 |
| `daily-brief.html` | 简报情报详情 | 简报查看、情报点击 |
| `event-timeline.html` | 时间线查看、返回详情 | 时间线查看、返回详情 |

### 飞书渠道归因

飞书卡片按钮链接增加渠道参数，例如：

```text
daily-brief.html?utm_source=feishu&utm_medium=card&utm_campaign=2026-09-04
```

网页记录 `utm_source` 后，可以统计飞书卡片到网页的点击转化。若飞书接口不提供已读数据，只能称为“卡片点击率”，不能称为“卡片打开率”。

## 7. 第三类：质量抽检与Bad Case

新增 `quality_reviews` 表：

| 字段 | 说明 |
|---|---|
| `review_id` | 抽检ID |
| `article_id` | 文章ID |
| `sample_type` | 最终保留/系统拦截 |
| `expected_relevance` | 人工判断 |
| `actual_relevance` | 模型判断 |
| `event_date_correct` | 日期是否正确 |
| `metrics_correct` | 数值与单位是否正确 |
| `summary_faithful` | 摘要是否忠实原文 |
| `bad_case_type` | 相关性、时效、事实、重复等 |
| `prompt_version` | 产生结果的Prompt版本 |
| `fix_status` | 待处理/已修复/已回归 |

每周同时抽检“最终保留”和“被系统拦截”的文章，分别发现误收和误杀。只检查最终结果无法计算Recall。

## 8. 数据表设计

### `pipeline_runs`

```text
run_id                 主键
run_date
trigger_type           schedule/manual/retry
started_at
completed_at
status
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
duration_ms
```

### `article_events`

```text
event_id               主键
run_id                 外键
article_id
event_name
event_time
source
level1
level2
status
reason
model_name
prompt_version
latency_ms
properties_json
```

### `user_events`

```text
event_id               主键
event_name
event_time
anonymous_user_id
session_id
page
channel
intelligence_id
topic_id
category
position
properties_json
```

## 9. 核心指标与看板

### 提纯漏斗

```text
候选文章数
→ 时间通过率
→ 正文解析成功率
→ 相关性通过率
→ 事件日期通过率
→ 去重通过率
→ 最终写入率
```

### AI质量

- JSON解析成功率
- 日期提取成功率与准确率
- Prompt重试率
- 中文降级触发率
- Precision、Recall、F1
- 数值与单位抽检准确率
- 单条情报平均耗时与Token成本

### 用户价值

- 飞书入口到网页点击率
- 列表到详情转化率
- 详情到原文转化率
- 详情到时间线转化率
- 搜索使用率与零结果率
- 情报有用率
- 每周被标记为有用的独立情报数

### 北极星指标

建议使用：**每周被用户标记为有用的独立情报数量。**

抓取数量和页面访问量只能说明系统运行或用户到访，不能直接证明情报产生了价值。

## 10. 分阶段实施计划

### 阶段一：事件字典与提纯漏斗（P0）

目标：回答“130条信息如何变成48条”。

任务：

1. 为每次任务生成 `pipeline_run_id`。
2. 为每篇文章生成稳定 `article_id`。
3. 建立 `pipeline_runs` 和 `article_events` 表。
4. 新增统一的 `track_pipeline_event()` 方法。
5. 在时间、解析、相关性、日期、去重和写入环节埋点。
6. 任务结束时生成漏斗汇总。
7. 区分正式环境和测试环境。

验收标准：

- 每次任务有且只有一条开始与结束记录。
- 各环节数量可以从明细重新计算，并与汇总一致。
- 每条最终情报可以追溯到对应文章和运行批次。
- 埋点写入失败不会中断主任务。
- 重复重试不会生成无法解释的重复事件。

### 阶段二：AI质量与版本监管（P0）

目标：回答“质量下降发生在哪里”。

任务：

1. 为主Prompt、JSON修复Prompt和摘要Prompt增加版本号。
2. 记录模型名称、耗时、重试和降级。
3. 建立 JSON 成功率、降级率和处理耗时看板。
4. 建立固定的人工标注评测集。
5. 模型或Prompt更新时运行回归测试。

验收标准：

- 可比较不同Prompt版本的结果。
- 能识别某天模型失败率或降级率异常。
- 未通过回归测试的Prompt不能替换生产版本。

### 阶段三：网页行为与反馈（P1）

目标：回答“用户是否真正使用并认可情报”。

任务：

1. 实现 `POST /api/events` 接口。
2. 建立前端统一 `track()` 函数，失败时静默处理。
3. 加入匿名用户ID、会话ID和渠道参数。
4. 埋首页、搜索、分类、详情、原文和时间线事件。
5. 在详情页增加“有用/无用”及原因反馈。
6. 排除本人测试访问和自动化测试流量。

验收标准：

- 同一用户会话可以串联为完整访问路径。
- 搜索词、筛选条件和结果数能够正确记录。
- 原文和时间线点击可以关联到具体情报。
- 不采集不必要的姓名、手机号和正文输入。

### 阶段四：异常告警与持续维护（P1）

目标：形成“监控—发现—归因—修复—回归”的治理闭环。

任务：

1. 为来源失败、数据量异常、JSON失败率和降级率设置告警规则。
2. 飞书发送每日运行摘要和异常告警。
3. 每周同时抽检保留与拦截样本。
4. 每月复盘失效信息源、Bad Case和成本。
5. 建立数据保留、归档和删除策略。

实施结果（2026-09-07）：已建立用户价值看板、运行与网页告警规则、双向人工抽检、用户Bad Case自动回流、周报/月报和保留策略。实现与阈值见 `references/monitoring/phase4/README.md`。

### 阶段五：数据新鲜度与发布可靠性（P1）

目标：回答“采集完成后，用户是否真的看到当天数据”。

任务：

1. 页面展示真实快照日期与新鲜度，不保留固定演示日期。
2. D1按天保存成功快照，实时读取失败时回退最近成功版本。
3. 每日任务结束后预热缓存并核对日期、记录数。
4. 每天08:00、12:00独立检查，异常发送P0/P1告警。

验收标准：

- 页面日期与北京时间当天一致，或明确标注“最近成功快照”。
- 任务记录数与网站记录数可自动校验。
- 飞书暂时不可用时，网页仍能展示最近成功数据。
- 新鲜度检查失败不会影响采集与飞书入库。

实施结果（2026-09-08）：已完成页面动态日期、D1每日快照、失败回退、自动预热和新鲜度告警。实现见 `references/monitoring/phase5/README.md`。

## 11. 开发任务拆分

### 后端

- `analytics.py`：统一事件模型、ID生成和容错写入。
- `analytics_store.py`：数据库写入和批量提交。
- `fetch_data.py`：增加文章级关卡事件。
- `daily_pipeline.py`：增加任务级汇总与渠道状态。
- `reporter.py`：增加飞书推送状态和渠道参数。
- `export_web_snapshot.py`：记录网页发布数据版本。

### 前端

- `analytics.js`：匿名ID、会话ID、事件发送和失败处理。
- 各HTML页面：加入页面、筛选、搜索、详情、原文、时间线埋点。
- 详情页：增加有用性反馈组件。
- Worker：增加 `/api/events`，校验事件白名单和字段长度。

### 数据与产品

- `tracking_plan.md`：事件字典、字段含义、触发规则。
- `metrics_dictionary.md`：指标口径、分子分母和时间窗口。
- `prompt_versions.md`：Prompt版本及变更原因。
- `bad_case_register.md`：Bad Case、修复和回归状态。

## 12. 埋点质量检查

上线前逐项检查：

1. 同一操作是否重复触发事件。
2. 页面跳转前事件是否成功发送，可使用 `sendBeacon` 或 `keepalive`。
3. 搜索输入是否做防抖，避免每输入一个字符产生一条事件。
4. `article_id`、`intelligence_id`和`topic_id`是否稳定。
5. 北京时间与UTC转换是否一致。
6. 测试环境数据是否与生产数据隔离。
7. 事件属性中是否意外包含密钥、正文或个人敏感信息。
8. 汇总漏斗是否可以由明细事件重新计算。
9. 事件存储失败时主业务是否仍可运行。
10. 指标名称是否有明确的分子、分母和时间窗口。

## 13. 两周建议排期

### 第一周

- 第1天：确定目标、事件字典和指标口径。
- 第2天：建立数据表与统一后端埋点方法。
- 第3～4天：接入采集、相关性、日期、去重和写入节点。
- 第5天：完成提纯漏斗核对和异常测试。

### 第二周

- 第1天：实现网页事件接口和前端 `track()`。
- 第2～3天：接入搜索、筛选、详情、原文和时间线。
- 第4天：增加有用性反馈与Bad Case表。
- 第5天：完成数据检查、看板和项目文档更新。

## 14. 完成后的面试表达

> 项目第一版解决了数据采集、提纯与多端交付，但复盘后我发现只有最终结果，缺少过程可观测性。因此第二阶段我补充了三层埋点：第一层以运行批次和文章ID为主键，记录每篇文章通过时间、解析、相关性、日期和去重关卡的状态，形成完整提纯漏斗；第二层记录模型和Prompt版本、JSON成功率、重试、降级、Token和耗时，支持质量归因；第三层记录网页搜索、筛选、详情、原文、时间线和有用性反馈，验证真实用户价值。同时建立Bad Case和回归测试机制，形成监控、归因、修复与验证的维护闭环。
