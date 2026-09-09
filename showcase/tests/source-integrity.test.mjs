import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import test from "node:test";


const root = new URL("../", import.meta.url);
const publicPages = [
  "shu-signal-draft.html",
  "intelligence.html",
  "intelligence-detail.html",
  "event-timeline.html",
  "daily-brief.html",
  "monitoring-dashboard.html",
];


test("keeps all SHU SIGNAL static source pages", async () => {
  await Promise.all(publicPages.map((name) => access(new URL(name, root))));
  const homepage = await readFile(new URL("shu-signal-draft.html", root), "utf8");
  assert.match(homepage, /SHU SIGNAL/i);
  assert.match(homepage, /shu-signal-data\.js/);
  for (const filename of publicPages) {
    const source = await readFile(new URL(filename, root), "utf8");
    assert.match(source, /<script src="analytics\.js"><\/script>/);
  }
});


test("regenerates public copies while keeping data on dynamic routes", async () => {
  const [syncScript, page, route, detail, feedbackRoute, eventRoute, summaryRoute, reviewRoute, dashboard, dashboardScript, schema, snapshotStore, homepage, library, brief] = await Promise.all([
    readFile(new URL("scripts/sync-static-site.mjs", root), "utf8"),
    readFile(new URL("app/page.tsx", root), "utf8"),
    readFile(new URL("app/shu-signal-data.js/route.ts", root), "utf8"),
    readFile(new URL("intelligence-detail.html", root), "utf8"),
    readFile(new URL("app/api/feedback/route.ts", root), "utf8"),
    readFile(new URL("app/api/events/route.ts", root), "utf8"),
    readFile(new URL("app/api/analytics/summary/route.ts", root), "utf8"),
    readFile(new URL("app/api/quality-reviews/route.ts", root), "utf8"),
    readFile(new URL("monitoring-dashboard.html", root), "utf8"),
    readFile(new URL("monitoring-dashboard.js", root), "utf8"),
    readFile(new URL("db/schema.ts", root), "utf8"),
    readFile(new URL("db/site-snapshots.ts", root), "utf8"),
    readFile(new URL("shu-signal-draft.html", root), "utf8"),
    readFile(new URL("intelligence.html", root), "utf8"),
    readFile(new URL("daily-brief.html", root), "utf8"),
  ]);
  for (const filename of publicPages) {
    assert.match(syncScript, new RegExp(filename.replace(".", "\\.")));
  }
  assert.match(syncScript, /"analytics\.js"/);
  assert.match(syncScript, /"monitoring-dashboard\.js"/);
  assert.match(syncScript, /unlink\(path\.join\(publicDirectory, "shu-signal-data\.js"\)/);
  assert.match(page, /redirect\("\/shu-signal-draft\.html"\)/);
  assert.match(route, /export const dynamic = "force-dynamic"/);
  assert.match(route, /FEISHU_APP_ID/);
  assert.match(route, /DAILY_TABLE_ID/);
  assert.match(route, /getSiteSnapshot/);
  assert.match(route, /saveSiteSnapshot/);
  assert.match(route, /D1 Last Successful Snapshot/);
  assert.match(schema, /site_snapshots/);
  assert.match(snapshotStore, /onConflictDoUpdate/);
  assert.match(detail, /这条情报对你有用吗/);
  assert.match(detail, /\/api\/feedback/);
  const inlineScripts = [...detail.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi)];
  assert.ok(inlineScripts.length > 0);
  inlineScripts.forEach((match) => new Function(match[1]));
  assert.match(feedbackRoute, /saveFeedback/);
  assert.match(schema, /intelligence_feedback/);
  assert.match(eventRoute, /saveUserEvent/);
  assert.match(eventRoute, /search_zero_result/);
  assert.match(schema, /user_events/);
  assert.match(schema, /idx_user_events_session_time/);
  assert.match(schema, /quality_reviews/);
  assert.match(eventRoute, /recordSearchZeroReview/);
  assert.match(feedbackRoute, /syncFeedbackReview/);
  assert.match(summaryRoute, /getAnalyticsSummary/);
  assert.match(reviewRoute, /hasMonitoringAdminAccess/);
  assert.match(dashboard, /Bad Case维护台账/);
  assert.match(dashboard, /monitoring-dashboard\.js/);
  new Function(dashboardScript);
  for (const source of [homepage, library, brief]) {
    assert.doesNotMatch(source, /2026年8月6日|2026\.08\.06|2026-08-06|8月6日/);
  }
});
