import { index, integer, sqliteTable, text, uniqueIndex } from "drizzle-orm/sqlite-core";


export const intelligenceFeedback = sqliteTable(
  "intelligence_feedback",
  {
    feedbackId: text("feedback_id").primaryKey(),
    intelligenceId: text("intelligence_id").notNull(),
    anonymousUserId: text("anonymous_user_id").notNull(),
    sessionId: text("session_id").notNull(),
    feedbackValue: text("feedback_value", {
      enum: ["useful", "not_useful"],
    }).notNull(),
    reason: text("reason"),
    channel: text("channel").notNull().default("direct"),
    pagePath: text("page_path").notNull().default("/intelligence-detail.html"),
    createdAt: integer("created_at").notNull(),
    updatedAt: integer("updated_at").notNull(),
  },
  (table) => [
    uniqueIndex("uq_intelligence_feedback_user_article").on(
      table.anonymousUserId,
      table.intelligenceId,
    ),
    index("idx_intelligence_feedback_article").on(table.intelligenceId),
    index("idx_intelligence_feedback_updated").on(table.updatedAt),
  ],
);


export const userEvents = sqliteTable(
  "user_events",
  {
    eventId: text("event_id").primaryKey(),
    eventName: text("event_name").notNull(),
    eventTime: integer("event_time").notNull(),
    receivedAt: integer("received_at").notNull(),
    schemaVersion: text("schema_version").notNull().default("1"),
    environment: text("environment").notNull(),
    channel: text("channel").notNull().default("direct"),
    anonymousUserId: text("anonymous_user_id").notNull(),
    sessionId: text("session_id").notNull(),
    page: text("page").notNull(),
    intelligenceId: text("intelligence_id"),
    topicId: text("topic_id"),
    category: text("category"),
    position: integer("position"),
    propertiesJson: text("properties_json").notNull().default("{}"),
  },
  (table) => [
    index("idx_user_events_name_time").on(table.eventName, table.eventTime),
    index("idx_user_events_environment_time").on(
      table.environment,
      table.eventTime,
    ),
    index("idx_user_events_session_time").on(table.sessionId, table.eventTime),
    index("idx_user_events_intelligence_name").on(
      table.intelligenceId,
      table.eventName,
    ),
  ],
);


export const qualityReviews = sqliteTable(
  "quality_reviews",
  {
    reviewId: text("review_id").primaryKey(),
    caseKey: text("case_key").notNull(),
    sourceType: text("source_type", {
      enum: ["feedback", "search_zero_result"],
    }).notNull(),
    intelligenceId: text("intelligence_id"),
    keyword: text("keyword"),
    badCaseType: text("bad_case_type").notNull(),
    occurrenceCount: integer("occurrence_count").notNull().default(1),
    status: text("status", {
      enum: ["open", "in_review", "resolved"],
    }).notNull().default("open"),
    firstSeenAt: integer("first_seen_at").notNull(),
    lastSeenAt: integer("last_seen_at").notNull(),
    resolvedAt: integer("resolved_at"),
  },
  (table) => [
    uniqueIndex("uq_quality_reviews_case_key").on(table.caseKey),
    index("idx_quality_reviews_status_seen").on(table.status, table.lastSeenAt),
    index("idx_quality_reviews_source_seen").on(
      table.sourceType,
      table.lastSeenAt,
    ),
  ],
);


export const siteSnapshots = sqliteTable(
  "site_snapshots",
  {
    snapshotDate: text("snapshot_date").primaryKey(),
    payloadJson: text("payload_json").notNull(),
    recordCount: integer("record_count").notNull().default(0),
    source: text("source").notNull().default("feishu"),
    generatedAt: integer("generated_at").notNull(),
    refreshedAt: integer("refreshed_at").notNull(),
  },
  (table) => [
    index("idx_site_snapshots_refreshed").on(table.refreshedAt),
  ],
);
