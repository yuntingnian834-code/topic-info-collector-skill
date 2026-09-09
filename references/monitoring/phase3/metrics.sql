-- SHU SIGNAL 第三阶段核心指标示例（D1 / SQLite）
-- 所有行为指标只统计正式环境；时间窗口可按分析需要调整。

-- 1. 最近7天访问、详情、原文与时间线会话漏斗
WITH production_events AS (
  SELECT * FROM user_events
  WHERE environment = 'production'
    AND event_time >= unixepoch('now', '-7 days') * 1000
),
funnel AS (
  SELECT
    COUNT(DISTINCT CASE WHEN event_name = 'page_view' THEN session_id END) AS visit_sessions,
    COUNT(DISTINCT CASE WHEN event_name = 'page_view' AND page = 'intelligence_list' THEN session_id END) AS library_sessions,
    COUNT(DISTINCT CASE WHEN event_name = 'detail_view' AND session_id IN (
      SELECT session_id FROM production_events
      WHERE event_name = 'page_view' AND page = 'intelligence_list'
    ) THEN session_id END) AS library_detail_sessions,
    COUNT(DISTINCT CASE WHEN event_name = 'detail_view' THEN session_id END) AS detail_sessions,
    COUNT(DISTINCT CASE WHEN event_name = 'source_clicked' THEN session_id END) AS source_sessions,
    COUNT(DISTINCT CASE WHEN event_name = 'timeline_clicked' THEN session_id END) AS timeline_sessions
  FROM production_events
)
SELECT
  *,
  ROUND(100.0 * library_detail_sessions / NULLIF(library_sessions, 0), 1) AS library_to_detail_pct,
  ROUND(100.0 * source_sessions / NULLIF(detail_sessions, 0), 1) AS detail_to_source_pct,
  ROUND(100.0 * timeline_sessions / NULLIF(detail_sessions, 0), 1) AS detail_to_timeline_pct
FROM funnel;

-- 2. 最近7天搜索使用与零结果率
WITH production_events AS (
  SELECT * FROM user_events
  WHERE environment = 'production'
    AND event_time >= unixepoch('now', '-7 days') * 1000
),
search_metrics AS (
  SELECT
    COUNT(DISTINCT CASE WHEN event_name = 'page_view' AND page = 'intelligence_list' THEN session_id END) AS library_sessions,
    COUNT(DISTINCT CASE WHEN event_name = 'search_submitted' THEN session_id END) AS search_sessions,
    SUM(CASE WHEN event_name = 'search_submitted' THEN 1 ELSE 0 END) AS search_count,
    SUM(CASE WHEN event_name = 'search_zero_result' THEN 1 ELSE 0 END) AS zero_result_count
  FROM production_events
)
SELECT
  *,
  ROUND(100.0 * search_sessions / NULLIF(library_sessions, 0), 1) AS search_usage_pct,
  ROUND(100.0 * zero_result_count / NULLIF(search_count, 0), 1) AS zero_result_pct
FROM search_metrics;

-- 3. 当前反馈有用率与无用原因分布
SELECT
  feedback_value,
  COALESCE(reason, 'none') AS reason,
  COUNT(*) AS feedback_count,
  ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS feedback_pct
FROM intelligence_feedback
GROUP BY feedback_value, COALESCE(reason, 'none')
ORDER BY feedback_count DESC;

-- 4. 最近7天每个渠道的访问会话与反馈用户
WITH channel_visits AS (
  SELECT channel, COUNT(DISTINCT session_id) AS visit_sessions
  FROM user_events
  WHERE environment = 'production'
    AND event_name = 'page_view'
    AND event_time >= unixepoch('now', '-7 days') * 1000
  GROUP BY channel
),
channel_feedback AS (
  SELECT channel, COUNT(DISTINCT anonymous_user_id) AS feedback_users
  FROM intelligence_feedback
  WHERE updated_at >= unixepoch('now', '-7 days') * 1000
  GROUP BY channel
)
SELECT
  v.channel,
  v.visit_sessions,
  COALESCE(f.feedback_users, 0) AS feedback_users
FROM channel_visits v
LEFT JOIN channel_feedback f USING (channel)
ORDER BY v.visit_sessions DESC;

-- 5. 最近7天至少获得一次“有用”反馈的去重情报数（北极星指标）
SELECT COUNT(DISTINCT intelligence_id) AS weekly_useful_intelligence_count
FROM intelligence_feedback
WHERE feedback_value = 'useful'
  AND updated_at >= unixepoch('now', '-7 days') * 1000;
