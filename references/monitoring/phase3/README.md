# 第三阶段：网页用户行为与价值监控

本阶段已经形成“访问 → 筛选/搜索 → 点击情报 → 查看详情 → 打开原文/时间线 → 提交反馈”的完整匿名行为链路。网页埋点失败时静默降级，不影响浏览、跳转和反馈功能。

## 已完成能力

- 5个页面统一生成匿名用户ID和会话ID，并自动记录页面访问。
- 记录筛选、搜索、零结果、情报点击、详情查看、原文点击、时间线点击/查看和反馈提交。
- 搜索事件采用500毫秒防抖，同一关键词连续输入只记一次，减少高频噪声。
- 支持 `utm_source`、`utm_medium`、`utm_campaign` 渠道归因，并在同一会话内延续。
- 本地访问自动标记为 `test`；正式分析只统计 `production`。
- `?analytics=off` 可在当前浏览器持续关闭埋点，`?analytics=on` 恢复，用于排除项目维护者流量。
- 行为写入D1的 `user_events` 表；有用性反馈写入 `intelligence_feedback` 表。
- 事件ID主键保证重试不重复，用户与情报唯一约束保证反馈修改不累加。
- 不采集账户姓名、手机号、IP、User-Agent、文章正文或自由文本反馈；搜索词中的邮箱和中国大陆手机号会在入库前脱敏。

## 事件字典

| 事件 | 触发时机 | 关键字段 |
|---|---|---|
| `page_view` | 打开任一作品页 | page、channel、session_id |
| `category_filter_applied` | 切换分类 | category、result_count、surface |
| `search_submitted` | 停止输入500毫秒且关键词变化 | keyword、result_count、category |
| `search_zero_result` | 搜索结果为0 | keyword、category |
| `intelligence_clicked` | 从首页、情报库、日报或时间线进入详情 | intelligence_id、position、surface |
| `detail_view` | 详情数据加载完成 | intelligence_id、topic_id、category |
| `source_clicked` | 点击原文 | intelligence_id、source、placement |
| `timeline_clicked` | 从详情页点击时间线 | intelligence_id、topic_id |
| `timeline_view` | 时间线加载完成 | topic_id |
| `feedback_submitted` | 反馈接口成功后 | intelligence_id、feedback_value、reason |

## 核心指标口径

| 指标 | 口径 |
|---|---|
| 情报库搜索使用率 | 有 `search_submitted` 的会话数 ÷ 访问情报库的会话数 |
| 搜索零结果率 | `search_zero_result` 次数 ÷ `search_submitted` 次数 |
| 列表到详情转化率 | 有 `detail_view` 的会话数 ÷ 访问情报库的会话数 |
| 详情到原文点击率 | 有 `source_clicked` 的会话数 ÷ 有 `detail_view` 的会话数 |
| 详情到时间线点击率 | 有 `timeline_clicked` 的会话数 ÷ 有 `detail_view` 的会话数 |
| 情报有用率 | 当前为 `useful` 的反馈数 ÷ 全部当前有效反馈数 |
| 每周有效情报数 | 一周内至少获得一次 `useful` 反馈的去重情报数 |
| 渠道访问/反馈量 | 按 channel 统计的访问会话数与有效反馈数 |

推荐北极星指标为“每周有效情报数”，辅助观察搜索零结果率、详情到原文点击率和有用率。正式统计必须添加 `environment = 'production'` 条件；SQL口径见 `metrics.sql`。

## 阶段边界

第三阶段负责采集、存储、归因与指标口径。第四阶段完成可视化运营看板、自动周报、阈值告警和Bad Case自动回流；第五阶段继续补充网页数据新鲜度和发布一致性，分别见 `../phase4/README.md` 与 `../phase5/README.md`。
