import { env } from "cloudflare:workers";


type NumericRow = Record<string, number | string | null>;

export type MonitoringAlert = {
  ruleKey: string;
  level: "P1" | "P2";
  title: string;
  detail: string;
  recommendedAction: string;
};


function database(): D1Database {
  if (!env.DB) throw new Error("Analytics storage is unavailable");
  return env.DB;
}


function numberValue(value: unknown): number {
  const numeric = Number(value || 0);
  return Number.isFinite(numeric) ? numeric : 0;
}


function percentage(numerator: number, denominator: number): number | null {
  return denominator > 0 ? Math.round((numerator / denominator) * 1000) / 10 : null;
}


function evaluateAlerts(metrics: Record<string, number | null>, openBadCaseCount: number): MonitoringAlert[] {
  const alerts: MonitoringAlert[] = [];
  if (metrics.visitSessions === 0) {
    alerts.push({
      ruleKey: "no_production_traffic",
      level: "P2",
      title: "观察期内没有正式访问",
      detail: "当前窗口没有 production 页面访问，会导致用户价值指标无法判断。",
      recommendedAction: "检查是否已经发布埋点版本，并确认投放链接未携带 analytics=off。",
    });
  }
  if (numberValue(metrics.searchCount) >= 5 && numberValue(metrics.searchZeroResultRate) >= 30) {
    alerts.push({
      ruleKey: "search_zero_result_high",
      level: "P1",
      title: "搜索零结果率偏高",
      detail: `零结果率为 ${metrics.searchZeroResultRate}%，超过30%的初始阈值。`,
      recommendedAction: "复盘零结果词，补充同义词、分类标签或信息源覆盖。",
    });
  }
  if (numberValue(metrics.detailSessions) >= 10 && numberValue(metrics.detailToSourceRate) < 20) {
    alerts.push({
      ruleKey: "source_conversion_low",
      level: "P2",
      title: "详情到原文转化偏低",
      detail: `原文点击率为 ${metrics.detailToSourceRate}%，低于20%的初始阈值。`,
      recommendedAction: "检查来源可信度展示、原文按钮位置和摘要是否已满足全部需求。",
    });
  }
  if (numberValue(metrics.feedbackCount) >= 5 && numberValue(metrics.usefulRate) < 50) {
    alerts.push({
      ruleKey: "useful_rate_low",
      level: "P1",
      title: "情报有用率偏低",
      detail: `有用率为 ${metrics.usefulRate}%，低于50%的初始阈值。`,
      recommendedAction: "优先处理无用原因最多的情报，并回看相关性和摘要Prompt。",
    });
  }
  if (openBadCaseCount >= 10) {
    alerts.push({
      ruleKey: "bad_case_backlog_high",
      level: "P2",
      title: "待处理Bad Case积压",
      detail: `当前有 ${openBadCaseCount} 个待处理或处理中案例。`,
      recommendedAction: "按出现次数排序处理，并在修复后运行固定回归评测。",
    });
  }
  return alerts;
}


export async function getAnalyticsSummary(days: number, includeDetails: boolean) {
  const windowStart = Date.now() - days * 24 * 60 * 60 * 1000;
  const weeklyStart = Date.now() - 7 * 24 * 60 * 60 * 1000;
  const db = database();
  const statements = [
    db.prepare(
      `WITH window_events AS (
        SELECT * FROM user_events
        WHERE environment='production' AND event_time>=?
      )
       SELECT
        COUNT(DISTINCT CASE WHEN event_name='page_view' THEN session_id END) AS visit_sessions,
        COUNT(DISTINCT CASE WHEN event_name='page_view' AND page='intelligence_list' THEN session_id END) AS library_sessions,
        COUNT(DISTINCT CASE WHEN event_name='detail_view' AND session_id IN (
          SELECT session_id FROM window_events
          WHERE event_name='page_view' AND page='intelligence_list'
        ) THEN session_id END) AS library_detail_sessions,
        COUNT(DISTINCT CASE WHEN event_name='detail_view' THEN session_id END) AS detail_sessions,
        COUNT(DISTINCT CASE WHEN event_name='source_clicked' THEN session_id END) AS source_sessions,
        COUNT(DISTINCT CASE WHEN event_name='timeline_clicked' THEN session_id END) AS timeline_sessions,
        COUNT(DISTINCT CASE WHEN event_name='search_submitted' THEN session_id END) AS search_sessions,
        SUM(CASE WHEN event_name='search_submitted' THEN 1 ELSE 0 END) AS search_count,
        SUM(CASE WHEN event_name='search_zero_result' THEN 1 ELSE 0 END) AS zero_result_count
       FROM window_events`,
    ).bind(windowStart),
    db.prepare(
      `SELECT
        COUNT(*) AS feedback_count,
        SUM(CASE WHEN feedback_value='useful' THEN 1 ELSE 0 END) AS useful_count,
        COUNT(DISTINCT CASE WHEN feedback_value='useful' AND updated_at>=? THEN intelligence_id END) AS weekly_useful_intelligence
       FROM intelligence_feedback
       WHERE updated_at>=?`,
    ).bind(weeklyStart, windowStart),
    db.prepare(
      `SELECT
        date(event_time / 1000, 'unixepoch', '+8 hours') AS day,
        SUM(CASE WHEN event_name='page_view' THEN 1 ELSE 0 END) AS page_views,
        SUM(CASE WHEN event_name='detail_view' THEN 1 ELSE 0 END) AS detail_views,
        SUM(CASE WHEN event_name='source_clicked' THEN 1 ELSE 0 END) AS source_clicks
       FROM user_events
       WHERE environment='production' AND event_time>=?
       GROUP BY date(event_time / 1000, 'unixepoch', '+8 hours')
       ORDER BY day`,
    ).bind(windowStart),
    db.prepare(
      `SELECT channel, COUNT(DISTINCT session_id) AS visit_sessions
       FROM user_events
       WHERE environment='production' AND event_name='page_view' AND event_time>=?
       GROUP BY channel
       ORDER BY visit_sessions DESC`,
    ).bind(windowStart),
    db.prepare(
      `SELECT channel, COUNT(DISTINCT anonymous_user_id) AS feedback_users
       FROM intelligence_feedback
       WHERE updated_at>=?
       GROUP BY channel`,
    ).bind(windowStart),
    db.prepare(
      `SELECT COALESCE(reason, 'none') AS reason, COUNT(*) AS count
       FROM intelligence_feedback
       WHERE feedback_value='not_useful' AND updated_at>=?
       GROUP BY COALESCE(reason, 'none')
       ORDER BY count DESC`,
    ).bind(windowStart),
    db.prepare(
      `SELECT review_id, source_type, intelligence_id, keyword, bad_case_type,
              occurrence_count, status, first_seen_at, last_seen_at
       FROM quality_reviews
       WHERE status IN ('open', 'in_review')
       ORDER BY occurrence_count DESC, last_seen_at DESC
       LIMIT 30`,
    ),
  ];
  const [eventResult, feedbackResult, trendResult, channelResult, feedbackChannelResult, reasonResult, reviewResult] = await db.batch(statements);

  const eventRow = (eventResult.results[0] || {}) as NumericRow;
  const feedbackRow = (feedbackResult.results[0] || {}) as NumericRow;
  const visitSessions = numberValue(eventRow.visit_sessions);
  const librarySessions = numberValue(eventRow.library_sessions);
  const libraryDetailSessions = numberValue(eventRow.library_detail_sessions);
  const detailSessions = numberValue(eventRow.detail_sessions);
  const sourceSessions = numberValue(eventRow.source_sessions);
  const timelineSessions = numberValue(eventRow.timeline_sessions);
  const searchSessions = numberValue(eventRow.search_sessions);
  const searchCount = numberValue(eventRow.search_count);
  const zeroResultCount = numberValue(eventRow.zero_result_count);
  const feedbackCount = numberValue(feedbackRow.feedback_count);
  const usefulCount = numberValue(feedbackRow.useful_count);
  const metrics = {
    visitSessions,
    librarySessions,
    libraryDetailSessions,
    detailSessions,
    sourceSessions,
    timelineSessions,
    searchSessions,
    searchCount,
    zeroResultCount,
    feedbackCount,
    usefulCount,
    weeklyUsefulIntelligence: numberValue(feedbackRow.weekly_useful_intelligence),
    libraryToDetailRate: percentage(libraryDetailSessions, librarySessions),
    detailToSourceRate: percentage(sourceSessions, detailSessions),
    detailToTimelineRate: percentage(timelineSessions, detailSessions),
    searchUsageRate: percentage(searchSessions, librarySessions),
    searchZeroResultRate: percentage(zeroResultCount, searchCount),
    usefulRate: percentage(usefulCount, feedbackCount),
  };

  const feedbackByChannel = new Map(
    feedbackChannelResult.results.map((row) => [String(row.channel || "direct"), numberValue(row.feedback_users)]),
  );
  const channels = channelResult.results.map((row) => ({
    channel: String(row.channel || "direct"),
    visitSessions: numberValue(row.visit_sessions),
    feedbackUsers: feedbackByChannel.get(String(row.channel || "direct")) || 0,
  }));
  const badCases = reviewResult.results.map((row) => ({
    reviewId: String(row.review_id),
    sourceType: String(row.source_type),
    intelligenceId: row.intelligence_id ? String(row.intelligence_id) : null,
    keyword: includeDetails && row.keyword ? String(row.keyword) : null,
    badCaseType: String(row.bad_case_type),
    occurrenceCount: numberValue(row.occurrence_count),
    status: String(row.status),
    firstSeenAt: numberValue(row.first_seen_at),
    lastSeenAt: numberValue(row.last_seen_at),
  }));

  return {
    schemaVersion: "1",
    generatedAt: new Date().toISOString(),
    days,
    windowStart: new Date(windowStart).toISOString(),
    metrics,
    dailyTrend: trendResult.results.map((row) => ({
      day: String(row.day),
      pageViews: numberValue(row.page_views),
      detailViews: numberValue(row.detail_views),
      sourceClicks: numberValue(row.source_clicks),
    })),
    channels,
    feedbackReasons: reasonResult.results.map((row) => ({
      reason: String(row.reason),
      count: numberValue(row.count),
    })),
    badCases,
    alerts: evaluateAlerts(metrics, badCases.length),
    canManage: includeDetails,
  };
}


export async function analyticsRetention(daysForEvents = 180, daysForResolvedCases = 365, apply = false) {
  const eventCutoff = Date.now() - daysForEvents * 24 * 60 * 60 * 1000;
  const reviewCutoff = Date.now() - daysForResolvedCases * 24 * 60 * 60 * 1000;
  const db = database();
  if (apply) {
    const results = await db.batch([
      db.prepare("DELETE FROM user_events WHERE event_time<?").bind(eventCutoff),
      db.prepare("DELETE FROM quality_reviews WHERE status='resolved' AND resolved_at<?").bind(reviewCutoff),
    ]);
    return {
      applied: true,
      deletedEvents: numberValue(results[0].meta.changes),
      deletedResolvedCases: numberValue(results[1].meta.changes),
    };
  }
  const results = await db.batch([
    db.prepare("SELECT COUNT(*) AS count FROM user_events WHERE event_time<?").bind(eventCutoff),
    db.prepare("SELECT COUNT(*) AS count FROM quality_reviews WHERE status='resolved' AND resolved_at<?").bind(reviewCutoff),
  ]);
  return {
    applied: false,
    deletableEvents: numberValue(results[0].results[0]?.count),
    deletableResolvedCases: numberValue(results[1].results[0]?.count),
  };
}
