import { env } from "cloudflare:workers";


export type UserEventInput = {
  eventId: string;
  eventName: string;
  eventTime: number;
  schemaVersion: string;
  environment: "production" | "test";
  channel: string;
  anonymousUserId: string;
  sessionId: string;
  page: string;
  intelligenceId: string | null;
  topicId: string | null;
  category: string | null;
  position: number | null;
  propertiesJson: string;
};


function database(): D1Database {
  if (!env.DB) {
    throw new Error("Analytics storage is unavailable");
  }
  return env.DB;
}


export async function saveUserEvent(input: UserEventInput): Promise<boolean> {
  const result = await database()
    .prepare(
      `INSERT INTO user_events (
        event_id, event_name, event_time, received_at, schema_version,
        environment, channel, anonymous_user_id, session_id, page,
        intelligence_id, topic_id, category, position, properties_json
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(event_id) DO NOTHING`,
    )
    .bind(
      input.eventId,
      input.eventName,
      input.eventTime,
      Date.now(),
      input.schemaVersion,
      input.environment,
      input.channel,
      input.anonymousUserId,
      input.sessionId,
      input.page,
      input.intelligenceId,
      input.topicId,
      input.category,
      input.position,
      input.propertiesJson,
    )
    .run();
  return Number(result.meta.changes || 0) > 0;
}
