import { NextRequest, NextResponse } from "next/server";
import { saveUserEvent } from "@/db/events";
import { recordSearchZeroReview } from "@/db/quality-reviews";


export const dynamic = "force-dynamic";

const eventNames = new Set([
  "page_view",
  "category_filter_applied",
  "search_submitted",
  "search_zero_result",
  "intelligence_clicked",
  "detail_view",
  "source_clicked",
  "timeline_clicked",
  "timeline_view",
  "feedback_submitted",
]);
const pageNames = new Set([
  "home",
  "intelligence_list",
  "intelligence_detail",
  "daily_brief",
  "event_timeline",
  "monitoring_dashboard",
  "other",
]);
const propertyNames = new Set([
  "result_count",
  "surface",
  "keyword",
  "has_search",
  "feedback_value",
  "reason",
  "source",
  "placement",
  "campaign",
  "medium",
]);
const safeId = /^[A-Za-z0-9._:-]{1,100}$/;


function safeText(value: unknown, limit: number): string {
  return typeof value === "string" ? value.trim().slice(0, limit) : "";
}


function redactContactDetails(value: string): string {
  return value
    .replace(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi, "[redacted-email]")
    .replace(/(?<!\d)1[3-9]\d{9}(?!\d)/g, "[redacted-phone]");
}


function sanitizeProperties(value: unknown): string | null {
  if (value === undefined || value === null) return "{}";
  if (typeof value !== "object" || Array.isArray(value)) return null;
  const clean: Record<string, string | number | boolean | null> = {};
  for (const [key, propertyValue] of Object.entries(value)) {
    if (!propertyNames.has(key)) continue;
    if (typeof propertyValue === "string") {
      const cleaned = propertyValue.trim().slice(0, 120);
      clean[key] = key === "keyword" ? redactContactDetails(cleaned) : cleaned;
    }
    else if (typeof propertyValue === "number" && Number.isFinite(propertyValue)) clean[key] = propertyValue;
    else if (typeof propertyValue === "boolean" || propertyValue === null) clean[key] = propertyValue;
  }
  const serialized = JSON.stringify(clean);
  return serialized.length <= 2048 ? serialized : null;
}


export async function POST(request: NextRequest) {
  const contentLength = Number(request.headers.get("content-length") || 0);
  if (contentLength > 16_384) {
    return NextResponse.json({ ok: false, error: "事件数据过大。" }, { status: 413 });
  }

  let body: Record<string, unknown>;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ ok: false, error: "请求格式错误。" }, { status: 400 });
  }

  const eventId = safeText(body.eventId, 100);
  const eventName = safeText(body.eventName, 40);
  const eventTime = Number(body.eventTime);
  const schemaVersion = safeText(body.schemaVersion, 10);
  const environment = safeText(body.environment, 20);
  const channel = safeText(body.channel, 40) || "direct";
  const anonymousUserId = safeText(body.anonymousUserId, 100);
  const sessionId = safeText(body.sessionId, 100);
  const page = safeText(body.page, 40);
  const intelligenceId = safeText(body.intelligenceId, 100) || null;
  const topicId = safeText(body.topicId, 100) || null;
  const category = safeText(body.category, 60) || null;
  const position = body.position === null || body.position === undefined ? null : Number(body.position);
  const propertiesJson = sanitizeProperties(body.properties);
  const now = Date.now();

  if (
    !safeId.test(eventId)
    || !eventNames.has(eventName)
    || !Number.isInteger(eventTime)
    || eventTime < 1_577_836_800_000
    || eventTime > now + 300_000
    || schemaVersion !== "1"
    || !["production", "test"].includes(environment)
    || !channel
    || !safeId.test(anonymousUserId)
    || !safeId.test(sessionId)
    || !pageNames.has(page)
    || (intelligenceId !== null && !safeId.test(intelligenceId))
    || (topicId !== null && !safeId.test(topicId))
    || (position !== null && (!Number.isInteger(position) || position < 1 || position > 100_000))
    || propertiesJson === null
  ) {
    return NextResponse.json({ ok: false, error: "事件参数不完整。" }, { status: 400 });
  }

  try {
    const inserted = await saveUserEvent({
      eventId,
      eventName,
      eventTime,
      schemaVersion,
      environment: environment as "production" | "test",
      channel,
      anonymousUserId,
      sessionId,
      page,
      intelligenceId,
      topicId,
      category,
      position,
      propertiesJson,
    });
    if (inserted && eventName === "search_zero_result") {
      try {
        const properties = JSON.parse(propertiesJson) as { keyword?: unknown };
        if (typeof properties.keyword === "string") {
          await recordSearchZeroReview(properties.keyword);
        }
      } catch {
        // Bad Case回流失败不能影响已经成功写入的用户行为事件。
      }
    }
    return NextResponse.json({ ok: true });
  } catch {
    return NextResponse.json(
      { ok: false, error: "事件记录暂时不可用。" },
      { status: 503 },
    );
  }
}
