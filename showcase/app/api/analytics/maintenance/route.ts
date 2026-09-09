import { NextRequest, NextResponse } from "next/server";
import { analyticsRetention } from "@/db/analytics-summary";
import { hasMonitoringAdminAccess } from "@/lib/monitoring-auth";


export const dynamic = "force-dynamic";


export async function POST(request: NextRequest) {
  if (!hasMonitoringAdminAccess(request)) {
    return NextResponse.json({ ok: false, error: "无维护权限。" }, { status: 401 });
  }
  let apply = false;
  try {
    const body = await request.json() as { apply?: unknown };
    apply = body.apply === true;
  } catch {}
  try {
    const result = await analyticsRetention(180, 365, apply);
    return NextResponse.json({ ok: true, result });
  } catch {
    return NextResponse.json({ ok: false, error: "保留策略检查失败。" }, { status: 503 });
  }
}
