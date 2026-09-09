import { NextRequest, NextResponse } from "next/server";
import { getAnalyticsSummary } from "@/db/analytics-summary";
import { hasMonitoringAdminAccess } from "@/lib/monitoring-auth";


export const dynamic = "force-dynamic";


export async function GET(request: NextRequest) {
  const requestedDays = Number(request.nextUrl.searchParams.get("days") || 7);
  const days = [7, 30].includes(requestedDays) ? requestedDays : 7;
  try {
    const summary = await getAnalyticsSummary(
      days,
      hasMonitoringAdminAccess(request),
    );
    return NextResponse.json({ ok: true, summary });
  } catch {
    return NextResponse.json(
      { ok: false, error: "监控汇总暂时不可用。" },
      { status: 503 },
    );
  }
}
