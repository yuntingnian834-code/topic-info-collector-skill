import { env } from "cloudflare:workers";
import type { NextRequest } from "next/server";


function configuredKey(): string {
  const runtime = env as unknown as { MONITORING_ADMIN_KEY?: string };
  return String(runtime.MONITORING_ADMIN_KEY || process.env.MONITORING_ADMIN_KEY || "");
}


function constantTimeEqual(left: string, right: string): boolean {
  if (!left || left.length !== right.length) return false;
  let difference = 0;
  for (let index = 0; index < left.length; index += 1) {
    difference |= left.charCodeAt(index) ^ right.charCodeAt(index);
  }
  return difference === 0;
}


export function hasMonitoringAdminAccess(request: NextRequest): boolean {
  return constantTimeEqual(
    configuredKey(),
    request.headers.get("x-monitoring-admin-key") || "",
  );
}
