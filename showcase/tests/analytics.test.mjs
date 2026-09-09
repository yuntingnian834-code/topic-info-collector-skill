import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";


const source = await readFile(new URL("../analytics.js", import.meta.url), "utf8");


function storage() {
  const values = new Map();
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: (key) => values.delete(key),
  };
}


function loadAnalytics({ search = "", hostname = "localhost", referrer = "", failing = false } = {}) {
  const requests = [];
  let sequence = 0;
  const sandbox = {
    window: {},
    location: {
      search,
      hostname,
      pathname: "/intelligence.html",
    },
    document: { referrer },
    navigator: { doNotTrack: "0" },
    localStorage: storage(),
    sessionStorage: storage(),
    crypto: { randomUUID: () => `00000000-0000-4000-8000-${String(++sequence).padStart(12, "0")}` },
    fetch: async (url, options) => {
      requests.push({ url, options });
      if (failing) throw new Error("offline");
      return { ok: true };
    },
    URL,
    URLSearchParams,
    Blob,
    Math,
    Date,
    setTimeout,
    clearTimeout,
  };
  vm.runInNewContext(source, sandbox);
  return { analytics: sandbox.window.ShuAnalytics, requests, sandbox };
}


test("records anonymous page events with session attribution", async () => {
  const { analytics, requests } = loadAnalytics({
    search: "?utm_source=feishu&utm_medium=message&utm_campaign=daily_brief",
  });
  assert.equal(requests.length, 1);
  const pageView = JSON.parse(requests[0].options.body);
  assert.equal(pageView.eventName, "page_view");
  assert.equal(pageView.page, "intelligence_list");
  assert.equal(pageView.environment, "test");
  assert.equal(pageView.channel, "feishu");
  assert.equal(pageView.properties.campaign, "daily_brief");

  const ok = await analytics.track("search_submitted", {
    category: "market",
    properties: { keyword: "铜", result_count: 2 },
  });
  assert.equal(ok, true);
  assert.equal(requests.length, 2);
  const searchEvent = JSON.parse(requests[1].options.body);
  assert.equal(searchEvent.sessionId, pageView.sessionId);
  assert.equal(searchEvent.properties.result_count, 2);
});


test("never interrupts the page when analytics transport fails", async () => {
  const { analytics } = loadAnalytics({ failing: true });
  assert.equal(await analytics.track("source_clicked"), false);
});


test("supports a persistent analytics opt-out for self traffic", async () => {
  const { analytics, requests } = loadAnalytics({ search: "?analytics=off" });
  assert.equal(analytics.disabled, true);
  assert.equal(requests.length, 0);
  assert.equal(await analytics.track("page_view"), false);
});
