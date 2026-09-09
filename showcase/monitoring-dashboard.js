(function () {
  "use strict";

  const reasonLabels = {
    irrelevant: "内容不相关",
    outdated: "信息不及时",
    missing_data: "缺少关键数据",
    unclear: "结论不清晰",
    other: "其他",
    none: "未填写原因",
  };
  const caseLabels = {
    feedback: "负向反馈",
    search_zero_result: "零结果搜索",
  };
  const statusLabels = { open: "待处理", in_review: "处理中", resolved: "已解决" };
  let adminKey = "";
  const siteSnapshot = window.SHU_SIGNAL_DATA || {};

  const byId = (id) => document.getElementById(id);
  const percent = (value) => value === null || value === undefined ? "—" : `${value}%`;
  const ratio = (numerator, denominator) => denominator ? `${Math.round(numerator / denominator * 1000) / 10}%` : "—";

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function renderAlerts(alerts) {
    const list = byId("alert-list");
    list.replaceChildren();
    byId("alert-count").textContent = alerts.length ? `${alerts.length}项待关注` : "运行正常";
    byId("alert-count").className = `status-pill ${alerts.length ? "warning" : "healthy"}`;
    if (!alerts.length) {
      list.append(element("p", "empty-inline", "当前窗口未触发用户价值异常规则。"));
      return;
    }
    alerts.forEach((alert) => {
      const item = element("article", `alert-item ${alert.level.toLowerCase()}`);
      const level = element("span", "alert-level", alert.level);
      const copy = element("div", "alert-copy");
      copy.append(element("strong", "", alert.title));
      copy.append(element("p", "", alert.detail));
      copy.append(element("small", "", `建议：${alert.recommendedAction}`));
      item.append(level, copy);
      list.append(item);
    });
  }

  function freshnessAlert() {
    const freshness = siteSnapshot.meta?.freshness;
    if (freshness === "fresh") return [];
    return [{
      level: freshness === "unavailable" ? "P0" : "P1",
      title: freshness === "unavailable" ? "网页数据不可用" : "网页正在使用最近成功快照",
      detail: `当前数据日期：${siteSnapshot.sampleDate || "未知"}。`,
      recommendedAction: "检查每日采集结果并刷新网站数据缓存。",
    }];
  }

  function renderFreshness() {
    const meta = siteSnapshot.meta || {};
    const records = siteSnapshot.records || [];
    const labels = { fresh: "正常", stale: "已降级", unavailable: "不可用" };
    byId("metric-snapshot-date").textContent = siteSnapshot.sampleDate || "—";
    byId("metric-snapshot-count").textContent = `${records.length}条情报`;
    byId("metric-freshness").textContent = labels[meta.freshness] || "未知";
    byId("metric-snapshot-source").textContent = meta.source || "未标注来源";
  }

  function renderMetrics(metrics) {
    byId("metric-weekly-useful").textContent = metrics.weeklyUsefulIntelligence;
    byId("metric-useful-rate").textContent = percent(metrics.usefulRate);
    byId("metric-feedback-count").textContent = `${metrics.feedbackCount}条反馈`;
    byId("metric-library-detail").textContent = percent(metrics.libraryToDetailRate);
    byId("metric-detail-source").textContent = percent(metrics.detailToSourceRate);
    byId("metric-search-usage").textContent = percent(metrics.searchUsageRate);
    byId("metric-zero-rate").textContent = percent(metrics.searchZeroResultRate);
    byId("metric-search-count").textContent = `${metrics.searchCount}次搜索`;

    const journey = byId("journey");
    journey.replaceChildren();
    [
      ["全站访问", metrics.visitSessions],
      ["情报库访问", metrics.librarySessions],
      ["详情查看", metrics.detailSessions],
      ["原文点击", metrics.sourceSessions],
      ["时间线点击", metrics.timelineSessions],
    ].forEach(([label, value], index, steps) => {
      const step = element("div", "journey-step");
      step.append(element("span", "journey-index", String(index + 1)));
      const copy = element("div", "journey-copy");
      copy.append(element("small", "", label), element("strong", "", String(value)));
      step.append(copy);
      if (index > 0) {
        const previous = Number(steps[index - 1][1]);
        step.append(element("em", "", ratio(Number(value), previous)));
      }
      journey.append(step);
    });
  }

  function renderTrend(rows) {
    const chart = byId("trend-chart");
    chart.replaceChildren();
    if (!rows.length) {
      chart.append(element("p", "empty-inline", "当前窗口暂无正式环境行为数据。"));
      return;
    }
    const maximum = Math.max(1, ...rows.map((row) => row.pageViews));
    rows.forEach((row) => {
      const day = element("div", "trend-day");
      const bars = element("div", "trend-bars");
      [
        ["views", row.pageViews],
        ["details", row.detailViews],
        ["sources", row.sourceClicks],
      ].forEach(([kind, value]) => {
        const bar = element("span", `trend-bar ${kind}`);
        bar.style.height = `${Math.max(3, Number(value) / maximum * 100)}%`;
        bar.title = `${value}`;
        bars.append(bar);
      });
      day.append(bars, element("small", "", row.day.slice(5).replace("-", ".")));
      chart.append(day);
    });
  }

  function renderChannels(channels) {
    const body = byId("channel-table");
    body.replaceChildren();
    if (!channels.length) {
      const row = document.createElement("tr");
      const cell = element("td", "empty-table", "暂无渠道访问数据");
      cell.colSpan = 4;
      row.append(cell);
      body.append(row);
      return;
    }
    channels.forEach((channel) => {
      const row = document.createElement("tr");
      [channel.channel, channel.visitSessions, channel.feedbackUsers, ratio(channel.feedbackUsers, channel.visitSessions)]
        .forEach((value) => row.append(element("td", "", String(value))));
      body.append(row);
    });
  }

  function renderReasons(reasons) {
    const list = byId("reason-list");
    list.replaceChildren();
    if (!reasons.length) {
      list.append(element("p", "empty-inline", "当前窗口暂无“暂时没用”反馈。"));
      return;
    }
    const total = reasons.reduce((sum, item) => sum + item.count, 0);
    reasons.forEach((reason) => {
      const row = element("div", "reason-row");
      const head = element("div", "reason-head");
      head.append(
        element("span", "", reasonLabels[reason.reason] || reason.reason),
        element("strong", "", `${reason.count} · ${ratio(reason.count, total)}`),
      );
      const track = element("div", "reason-track");
      const fill = element("span", "reason-fill");
      fill.style.width = `${reason.count / total * 100}%`;
      track.append(fill);
      row.append(head, track);
      list.append(row);
    });
  }

  async function updateReview(reviewId, status) {
    const response = await fetch("/api/quality-reviews", {
      method: "PATCH",
      headers: {
        "content-type": "application/json",
        "x-monitoring-admin-key": adminKey,
      },
      body: JSON.stringify({ reviewId, status }),
    });
    if (!response.ok) throw new Error("update_failed");
    await loadDashboard();
  }

  function renderBadCases(cases, canManage) {
    const list = byId("bad-case-list");
    list.replaceChildren();
    if (!cases.length) {
      list.append(element("p", "empty-inline", "当前没有待处理Bad Case。"));
      return;
    }
    cases.forEach((item) => {
      const row = element("article", "bad-case-row");
      const marker = element("span", `case-marker ${item.sourceType}`, caseLabels[item.sourceType] || item.sourceType);
      const copy = element("div", "case-copy");
      const title = item.sourceType === "search_zero_result"
        ? (item.keyword || "零结果搜索词（维护者可查看）")
        : `情报 ${item.intelligenceId || "未知"}`;
      copy.append(element("strong", "", title));
      copy.append(element("small", "", `${reasonLabels[item.badCaseType] || item.badCaseType} · 出现${item.occurrenceCount}次 · 最近${new Date(item.lastSeenAt).toLocaleDateString("zh-CN")}`));
      row.append(marker, copy);
      if (canManage) {
        const select = document.createElement("select");
        select.className = "case-status-select";
        ["open", "in_review", "resolved"].forEach((status) => {
          const option = document.createElement("option");
          option.value = status;
          option.textContent = statusLabels[status];
          option.selected = item.status === status;
          select.append(option);
        });
        select.addEventListener("change", async () => {
          select.disabled = true;
          try {
            await updateReview(item.reviewId, select.value);
          } catch {
            byId("admin-status").textContent = "状态更新失败，请检查维护密钥后重试。";
            select.disabled = false;
          }
        });
        row.append(select);
      } else {
        row.append(element("span", `case-status ${item.status}`, statusLabels[item.status] || item.status));
      }
      list.append(row);
    });
  }

  async function loadDashboard() {
    const days = Number(byId("window-days").value || 7);
    byId("dashboard-updated").textContent = "正在加载…";
    byId("dashboard-error").hidden = true;
    try {
      const headers = adminKey ? { "x-monitoring-admin-key": adminKey } : {};
      const response = await fetch(`/api/analytics/summary?days=${days}`, { headers });
      if (!response.ok) throw new Error("summary_failed");
      const payload = await response.json();
      const summary = payload.summary;
      renderAlerts([...freshnessAlert(), ...summary.alerts]);
      renderMetrics(summary.metrics);
      renderTrend(summary.dailyTrend);
      renderChannels(summary.channels);
      renderReasons(summary.feedbackReasons);
      renderBadCases(summary.badCases, summary.canManage);
      byId("dashboard-updated").textContent = `更新于 ${new Date(summary.generatedAt).toLocaleString("zh-CN", { hour12: false })}`;
      if (adminKey) {
        byId("admin-status").textContent = summary.canManage
          ? "维护模式已启用：可查看零结果词并更新处理状态。"
          : "维护密钥无效，当前仍为公开聚合视图。";
      }
    } catch {
      byId("dashboard-error").hidden = false;
      byId("dashboard-updated").textContent = "加载失败";
    }
  }

  byId("refresh-dashboard").addEventListener("click", loadDashboard);
  byId("window-days").addEventListener("change", loadDashboard);
  byId("enable-admin").addEventListener("click", () => {
    adminKey = byId("admin-key").value.trim();
    byId("admin-key").value = "";
    loadDashboard();
  });
  renderFreshness();
  loadDashboard();
})();
