import { NextRequest, NextResponse } from "next/server";
import {
  updateQualityReview,
  type ReviewStatus,
} from "@/db/quality-reviews";
import { hasMonitoringAdminAccess } from "@/lib/monitoring-auth";


export const dynamic = "force-dynamic";

const safeId = /^[A-Za-z0-9._:-]{1,100}$/;
const statuses = new Set<ReviewStatus>(["open", "in_review", "resolved"]);


export async function PATCH(request: NextRequest) {
  if (!hasMonitoringAdminAccess(request)) {
    return NextResponse.json({ ok: false, error: "无维护权限。" }, { status: 401 });
  }
  let body: Record<string, unknown>;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ ok: false, error: "请求格式错误。" }, { status: 400 });
  }
  const reviewId = typeof body.reviewId === "string" ? body.reviewId.trim() : "";
  const status = typeof body.status === "string" ? body.status.trim() as ReviewStatus : "open";
  if (!safeId.test(reviewId) || !statuses.has(status)) {
    return NextResponse.json({ ok: false, error: "维护参数不完整。" }, { status: 400 });
  }
  try {
    const updated = await updateQualityReview(reviewId, status);
    return NextResponse.json({ ok: true, updated });
  } catch {
    return NextResponse.json({ ok: false, error: "状态更新失败。" }, { status: 503 });
  }
}
