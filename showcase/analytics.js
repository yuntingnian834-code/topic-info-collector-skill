(function () {
  "use strict";

  if (window.ShuAnalytics) return;

  const schemaVersion = "1";
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
  const pageNames = {
    "": "home",
    "/": "home",
    "shu-signal-draft.html": "home",
    "intelligence.html": "intelligence_list",
    "intelligence-detail.html": "intelligence_detail",
    "daily-brief.html": "daily_brief",
    "event-timeline.html": "event_timeline",
    "monitoring-dashboard.html": "monitoring_dashboard",
  };

  function randomId() {
    try {
      return crypto.randomUUID();
    } catch {
      return `fallback-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    }
  }

  function storageId(storage, key) {
    try {
      let value = storage.getItem(key);
      if (!value) {
        value = randomId();
        storage.setItem(key, value);
      }
      return value;
    } catch {
      return randomId();
    }
  }

  function safeStorageGet(storage, key) {
    try {
      return storage.getItem(key);
    } catch {
      return null;
    }
  }

  function safeStorageSet(storage, key, value) {
    try {
      if (value === null) storage.removeItem(key);
      else storage.setItem(key, value);
    } catch {}
  }

  function safeText(value, limit) {
    return typeof value === "string" ? value.trim().slice(0, limit) : "";
  }

  const query = new URLSearchParams(location.search);
  const analyticsControl = query.get("analytics");
  if (analyticsControl === "off") {
    safeStorageSet(localStorage, "shu_signal_analytics_disabled", "1");
  } else if (analyticsControl === "on") {
    safeStorageSet(localStorage, "shu_signal_analytics_disabled", null);
  }

  const pathname = location.pathname || "/";
  const basename = pathname.split("/").pop() || "";
  const environment = ["localhost", "127.0.0.1", "[::1]"].includes(location.hostname)
    ? "test"
    : "production";
  const disabled = safeStorageGet(localStorage, "shu_signal_analytics_disabled") === "1"
    || navigator.doNotTrack === "1";

  function attribution() {
    const stored = safeStorageGet(sessionStorage, "shu_signal_attribution");
    let existing = null;
    try {
      existing = stored ? JSON.parse(stored) : null;
    } catch {}

    const source = safeText(query.get("utm_source"), 40);
    const medium = safeText(query.get("utm_medium"), 60);
    const campaign = safeText(query.get("utm_campaign"), 80);
    if (source || medium || campaign) {
      const current = { channel: source || "campaign", medium, campaign };
      safeStorageSet(sessionStorage, "shu_signal_attribution", JSON.stringify(current));
      return current;
    }
    if (existing?.channel) return existing;

    let referrerChannel = "direct";
    try {
      if (document.referrer) referrerChannel = new URL(document.referrer).hostname || "referral";
    } catch {}
    const current = { channel: safeText(referrerChannel, 40) || "direct", medium: "", campaign: "" };
    safeStorageSet(sessionStorage, "shu_signal_attribution", JSON.stringify(current));
    return current;
  }

  const identity = Object.freeze({
    anonymousUserId: storageId(localStorage, "shu_signal_anonymous_user_id"),
    sessionId: storageId(sessionStorage, "shu_signal_session_id"),
  });
  const sourceAttribution = attribution();
  const context = {
    page: pageNames[basename] || pageNames[pathname] || "other",
    channel: sourceAttribution.channel,
    environment,
    topicId: null,
    intelligenceId: null,
    category: null,
  };

  function setContext(fields = {}) {
    ["topicId", "intelligenceId", "category"].forEach((key) => {
      if (typeof fields[key] === "string") context[key] = safeText(fields[key], 100) || null;
    });
  }

  async function track(eventName, fields = {}, options = {}) {
    if (disabled || !eventNames.has(eventName)) return false;
    try {
      const payload = {
        eventId: randomId(),
        eventName,
        eventTime: Date.now(),
        schemaVersion,
        environment: context.environment,
        channel: context.channel,
        anonymousUserId: identity.anonymousUserId,
        sessionId: identity.sessionId,
        page: context.page,
        intelligenceId: safeText(fields.intelligenceId || context.intelligenceId, 100) || null,
        topicId: safeText(fields.topicId || context.topicId, 100) || null,
        category: safeText(fields.category || context.category, 60) || null,
        position: Number.isInteger(fields.position) ? fields.position : null,
        properties: fields.properties && typeof fields.properties === "object" ? fields.properties : {},
      };
      const body = JSON.stringify(payload);
      if (options.transport === "beacon" && typeof navigator.sendBeacon === "function" && typeof Blob === "function") {
        return navigator.sendBeacon("/api/events", new Blob([body], { type: "application/json" }));
      }
      const response = await fetch("/api/events", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body,
        keepalive: true,
      });
      return response.ok;
    } catch {
      return false;
    }
  }

  function debounce(callback, delay = 500) {
    let timer;
    return (...args) => {
      clearTimeout(timer);
      timer = setTimeout(() => callback(...args), delay);
    };
  }

  window.ShuAnalytics = Object.freeze({
    disabled,
    identity,
    setContext,
    track,
    debounce,
    get context() {
      return { ...context };
    },
  });

  track("page_view", {
    properties: {
      medium: sourceAttribution.medium || null,
      campaign: sourceAttribution.campaign || null,
    },
  });
})();
