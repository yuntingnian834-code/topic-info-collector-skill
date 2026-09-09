import { env } from "cloudflare:workers";


export type ReviewStatus = "open" | "in_review" | "resolved";

type FeedbackReasonRow = {
  reason: string | null;
  reason_count: number;
  total_count: number;
};


function database(): D1Database {
  if (!env.DB) throw new Error("Quality review storage is unavailable");
  return env.DB;
}


function stableHash(value: string): string {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16).padStart(8, "0");
}


export async function recordSearchZeroReview(keyword: string): Promise<void> {
  const cleanKeyword = keyword.trim().slice(0, 120);
  if (!cleanKeyword) return;
  const now = Date.now();
  const caseKey = `search:${stableHash(cleanKeyword.toLowerCase())}`;
  await database()
    .prepare(
      `INSERT INTO quality_reviews (
        review_id, case_key, source_type, keyword, bad_case_type,
        occurrence_count, status, first_seen_at, last_seen_at
      ) VALUES (?, ?, 'search_zero_result', ?, 'zero_result', 1, 'open', ?, ?)
      ON CONFLICT(case_key) DO UPDATE SET
        occurrence_count=quality_reviews.occurrence_count + 1,
        status=CASE WHEN quality_reviews.status='resolved' THEN 'open' ELSE quality_reviews.status END,
        last_seen_at=excluded.last_seen_at,
        resolved_at=NULL`,
    )
    .bind(crypto.randomUUID(), caseKey, cleanKeyword, now, now)
    .run();
}


export async function syncFeedbackReview(intelligenceId: string): Promise<void> {
  const summary = await database()
    .prepare(
      `WITH reason_counts AS (
        SELECT COALESCE(reason, 'other') AS reason, COUNT(*) AS reason_count
        FROM intelligence_feedback
        WHERE intelligence_id=? AND feedback_value='not_useful'
        GROUP BY COALESCE(reason, 'other')
      )
      SELECT reason, reason_count, SUM(reason_count) OVER () AS total_count
      FROM reason_counts
      ORDER BY reason_count DESC, reason
      LIMIT 1`,
    )
    .bind(intelligenceId)
    .first<FeedbackReasonRow>();

  const caseKey = `feedback:${intelligenceId}`;
  const now = Date.now();
  if (!summary) {
    await database()
      .prepare(
        `UPDATE quality_reviews
         SET occurrence_count=0, status='resolved', resolved_at=?, last_seen_at=?
         WHERE case_key=? AND source_type='feedback'`,
      )
      .bind(now, now, caseKey)
      .run();
    return;
  }

  await database()
    .prepare(
      `INSERT INTO quality_reviews (
        review_id, case_key, source_type, intelligence_id, bad_case_type,
        occurrence_count, status, first_seen_at, last_seen_at
      ) VALUES (?, ?, 'feedback', ?, ?, ?, 'open', ?, ?)
      ON CONFLICT(case_key) DO UPDATE SET
        bad_case_type=excluded.bad_case_type,
        occurrence_count=excluded.occurrence_count,
        status=CASE WHEN quality_reviews.status='resolved' THEN 'open' ELSE quality_reviews.status END,
        last_seen_at=excluded.last_seen_at,
        resolved_at=NULL`,
    )
    .bind(
      crypto.randomUUID(),
      caseKey,
      intelligenceId,
      summary.reason || "other",
      Number(summary.total_count || 0),
      now,
      now,
    )
    .run();
}


export async function updateQualityReview(
  reviewId: string,
  status: ReviewStatus,
): Promise<boolean> {
  const now = Date.now();
  const result = await database()
    .prepare(
      `UPDATE quality_reviews
       SET status=?, resolved_at=?
       WHERE review_id=?`,
    )
    .bind(status, status === "resolved" ? now : null, reviewId)
    .run();
  return Number(result.meta.changes || 0) > 0;
}
