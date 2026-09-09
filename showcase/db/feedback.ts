import { env } from "cloudflare:workers";


export type FeedbackValue = "useful" | "not_useful";

export type FeedbackInput = {
  intelligenceId: string;
  anonymousUserId: string;
  sessionId: string;
  feedbackValue: FeedbackValue;
  reason: string | null;
  channel: string;
  pagePath: string;
};

type FeedbackRow = {
  feedback_value: FeedbackValue;
  reason: string | null;
  updated_at: number;
};


function database(): D1Database {
  if (!env.DB) {
    throw new Error("Feedback storage is unavailable");
  }
  return env.DB;
}


export async function saveFeedback(input: FeedbackInput): Promise<void> {
  const now = Date.now();
  await database()
    .prepare(
      `INSERT INTO intelligence_feedback (
        feedback_id, intelligence_id, anonymous_user_id, session_id,
        feedback_value, reason, channel, page_path, created_at, updated_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(anonymous_user_id, intelligence_id) DO UPDATE SET
        session_id=excluded.session_id,
        feedback_value=excluded.feedback_value,
        reason=excluded.reason,
        channel=excluded.channel,
        page_path=excluded.page_path,
        updated_at=excluded.updated_at`,
    )
    .bind(
      crypto.randomUUID(),
      input.intelligenceId,
      input.anonymousUserId,
      input.sessionId,
      input.feedbackValue,
      input.reason,
      input.channel,
      input.pagePath,
      now,
      now,
    )
    .run();
}


export async function getFeedback(
  intelligenceId: string,
  anonymousUserId: string,
): Promise<FeedbackRow | null> {
  return database()
    .prepare(
      `SELECT feedback_value, reason, updated_at
       FROM intelligence_feedback
       WHERE intelligence_id=? AND anonymous_user_id=?`,
    )
    .bind(intelligenceId, anonymousUserId)
    .first<FeedbackRow>();
}
