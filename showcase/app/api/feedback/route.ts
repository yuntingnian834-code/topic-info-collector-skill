import { NextRequest, NextResponse } from "next/server";
import {
  getFeedback,
  saveFeedback,
  type FeedbackValue,
} from "@/db/feedback";
import { syncFeedbackReview } from "@/db/quality-reviews";


export const dynamic = "force-dynamic";

const feedbackValues = new Set<FeedbackValue>(["useful", "not_useful"]);
const feedbackReasons = new Set([
  "irrelevant",
  "outdated",
  "missing_data",
  "unclear",
  "other",
]);
const safeId = /^[A-Za-z0-9._:-]{1,100}$/;


function safeText(value: unknown, limit: number): string {
  return typeof value === "string" ? value.trim().slice(0, limit) : "";
}


function unavailable() {
  return NextResponse.json(
    { ok: false, error: "反馈服务暂时不可用，请稍后重试。" },
    { status: 503 },
  );
}


export async function GET(request: NextRequest) {
  const intelligenceId = safeText(
    request.nextUrl.searchParams.get("intelligenceId"),
    100,
  );
  const anonymousUserId = safeText(
    request.nextUrl.searchParams.get("anonymousUserId"),
    100,
  );
  if (!safeId.test(intelligenceId) || !safeId.test(anonymousUserId)) {
    return NextResponse.json({ ok: false, error: "参数不完整。" }, { status: 400 });
  }
  try {
    const feedback = await getFeedback(intelligenceId, anonymousUserId);
    return NextResponse.json({ ok: true, feedback });
  } catch {
    return unavailable();
  }
}


export async function POST(request: NextRequest) {
  let body: Record<string, unknown>;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ ok: false, error: "请求格式错误。" }, { status: 400 });
  }

  const intelligenceId = safeText(body.intelligenceId, 100);
  const anonymousUserId = safeText(body.anonymousUserId, 100);
  const sessionId = safeText(body.sessionId, 100);
  const feedbackValue = safeText(body.feedbackValue, 20) as FeedbackValue;
  const reason = safeText(body.reason, 40) || null;
  const channel = safeText(body.channel, 40) || "direct";
  const pagePath = safeText(body.pagePath, 120) || "/intelligence-detail.html";

  if (
    !safeId.test(intelligenceId) ||
    !safeId.test(anonymousUserId) ||
    !safeId.test(sessionId) ||
    !feedbackValues.has(feedbackValue)
  ) {
    return NextResponse.json({ ok: false, error: "反馈参数不完整。" }, { status: 400 });
  }
  if (feedbackValue === "not_useful" && (!reason || !feedbackReasons.has(reason))) {
    return NextResponse.json({ ok: false, error: "请选择反馈原因。" }, { status: 400 });
  }

  try {
    await saveFeedback({
      intelligenceId,
      anonymousUserId,
      sessionId,
      feedbackValue,
      reason: feedbackValue === "useful" ? null : reason,
      channel,
      pagePath,
    });
    try {
      await syncFeedbackReview(intelligenceId);
    } catch {
      // Bad Case回流失败不能阻断用户反馈。
    }
    return NextResponse.json({ ok: true });
  } catch {
    return unavailable();
  }
}
