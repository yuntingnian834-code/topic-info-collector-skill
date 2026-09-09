import {
  getLatestSuccessfulSiteSnapshot,
  getSiteSnapshot,
  saveSiteSnapshot,
  type StoredSiteSnapshot,
} from "@/db/site-snapshots";


export const dynamic = "force-dynamic";

const FEISHU_API = "https://open.feishu.cn/open-apis";
const TIME_ZONE = "Asia/Shanghai";

type FeishuFields = Record<string, unknown>;

type PublicRecord = {
  id: string;
  collectedAt: string;
  eventDate: string;
  category: string;
  categoryKey: string;
  subtopic: string;
  researchCore: string;
  title: string;
  summary: string;
  metric: string;
  metrics: string[][];
  judgment: string;
  judgmentClass: string;
  strength: string;
  source: string;
  sourceUrl: string;
  eventGroup: string;
  evidence: string;
  transmission: string[];
};

function plain(value: unknown): string {
  if (value == null) return "";
  if (Array.isArray(value)) return value.map(plain).join("").trim();
  if (typeof value === "object") {
    const item = value as Record<string, unknown>;
    return plain(item.link ?? item.text ?? item.name ?? "");
  }
  return String(value).trim();
}

function cleanUrl(value: unknown): string {
  const raw = plain(value);
  const markdown = raw.match(/^\[(https?:\/\/[^\]]+)]\((https?:\/\/[^)]+)\)$/);
  return markdown?.[2] ?? raw;
}

function compact(value: string, limit: number): string {
  const normalized = value.replace(/\s+/g, " ").trim();
  return normalized.length <= limit
    ? normalized
    : `${normalized.slice(0, limit - 1)}…`;
}

function summaryPart(summary: string, label: string): string {
  const escaped = label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const nextLabel = "(?:商品品类|关键指标|市场/政策状态|期限结构|核心洞察)";
  const match = summary.match(
    new RegExp(`${escaped}[：:]\\s*([\\s\\S]*?)(?=\\n${nextLabel}[：:]|$)`),
  );
  return match?.[1]?.trim() ?? "";
}

function dateParts(date: Date): { year: string; month: string; day: string } {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: TIME_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(date);
  const lookup = Object.fromEntries(parts.map((item) => [item.type, item.value]));
  return { year: lookup.year, month: lookup.month, day: lookup.day };
}

function beijingDate(date = new Date()): string {
  const { year, month, day } = dateParts(date);
  return `${year}-${month}-${day}`;
}

function collectedDate(fields: FeishuFields): string | null {
  const timestamp = Number(fields["采集时间"]);
  return Number.isFinite(timestamp) ? beijingDate(new Date(timestamp)) : null;
}

function eventDate(summary: string, fallback: string): string {
  const match = summary.match(/报告时间[：:]\s*(\d{4})年(\d{1,2})月(\d{1,2})日/);
  if (!match) return fallback;
  const [, year, month, day] = match;
  const parsed = `${year}-${month.padStart(2, "0")}-${day.padStart(2, "0")}`;
  return parsed <= fallback ? parsed : fallback;
}

function category(raw: string): [string, string] {
  if (raw.includes("市场基本面") || raw.includes("价格监测")) {
    return ["市场基本面", "market"];
  }
  if (raw.includes("港口") || raw.includes("航道")) {
    return ["港口与航道", "port"];
  }
  if (raw.includes("航运") || raw.includes("货物运输") || raw.includes("物流")) {
    return ["航运物流", "shipping"];
  }
  return ["宏观政策", "policy"];
}

function hash(value: string): string {
  let result = 0x811c9dc5;
  for (let index = 0; index < value.length; index += 1) {
    result ^= value.charCodeAt(index);
    result = Math.imul(result, 0x01000193);
  }
  return (result >>> 0).toString(16).padStart(8, "0");
}

function topicKey(title: string): string {
  const normalized = title.replace(/最新动态$/, "").replace(/^[ ，、]+|[ ，、]+$/g, "") || title;
  return normalized === "铜" ? "copper-tightness" : `topic-${hash(normalized)}`;
}

function collectedTime(fields: FeishuFields): string {
  const timestamp = Number(fields["采集时间"]);
  const date = Number.isFinite(timestamp) ? new Date(timestamp) : new Date();
  const parts = new Intl.DateTimeFormat("zh-CN", {
    timeZone: TIME_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(date);
  const lookup = Object.fromEntries(parts.map((item) => [item.type, item.value]));
  return `${lookup.year}-${lookup.month}-${lookup.day} ${lookup.hour}:${lookup.minute}`;
}

function buildRecord(fields: FeishuFields, sampleDate: string): PublicRecord {
  const title = plain(fields["标题"]) || "未命名情报";
  const summary = plain(fields["摘要"]);
  const source = plain(fields["来源网站名称"]) || "未标注来源";
  const [level1, categoryKey] = category(plain(fields["一级分类"]));
  const metricsRaw = summaryPart(summary, "关键指标");
  const insight = summaryPart(summary, "核心洞察") || compact(summary, 220);
  const metricParts = metricsRaw
    .split(/[；;]/)
    .map((item) => compact(item, 34))
    .filter(Boolean)
    .slice(0, 3);
  const fallbacks = [source, sampleDate, "待持续跟踪"];
  while (metricParts.length < 3) metricParts.push(fallbacks[metricParts.length]);
  const group = topicKey(title);
  const commodity = summaryPart(summary, "商品品类") || title.replace(/最新动态$/, "");
  const sourceUrl = cleanUrl(fields["来源链接"]);

  return {
    id: `intel-${hash(`${title}${sourceUrl}`)}`,
    collectedAt: collectedTime(fields),
    eventDate: eventDate(summary, sampleDate),
    category: level1,
    categoryKey,
    subtopic: plain(fields["二级信息点"]) || commodity,
    researchCore: plain(fields["研究核心"]) || "该记录尚未配置研究核心。",
    title,
    summary,
    metric: metricsRaw ? compact(metricsRaw, 45) : "详见核心洞察",
    metrics: metricParts.map((item, index) => [item, `关键指标${index + 1}`]),
    judgment: "关注",
    judgmentClass: "neutral",
    strength: "待研判",
    source,
    sourceUrl,
    eventGroup: group,
    evidence: compact(insight, 220),
    transmission: [`${compact(commodity, 18)}数据更新`, "匹配研究框架", "进入持续跟踪"],
  };
}

async function tenantToken(): Promise<string> {
  const appId = process.env.FEISHU_APP_ID;
  const appSecret = process.env.FEISHU_APP_SECRET;
  if (!appId || !appSecret) throw new Error("Feishu credentials are not configured");

  const response = await fetch(`${FEISHU_API}/auth/v3/tenant_access_token/internal`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ app_id: appId, app_secret: appSecret }),
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`Feishu authentication failed: ${response.status}`);
  const payload = await response.json() as { code?: number; tenant_access_token?: string };
  if (payload.code !== 0 || !payload.tenant_access_token) {
    throw new Error("Feishu authentication returned an invalid response");
  }
  return payload.tenant_access_token;
}

async function allRecords(): Promise<FeishuFields[]> {
  const baseToken = process.env.DAILY_BASE_TOKEN;
  const tableId = process.env.DAILY_TABLE_ID;
  if (!baseToken || !tableId) throw new Error("Feishu table is not configured");

  const token = await tenantToken();
  const rows: FeishuFields[] = [];
  let pageToken = "";

  do {
    const url = new URL(
      `${FEISHU_API}/bitable/v1/apps/${baseToken}/tables/${tableId}/records`,
    );
    url.searchParams.set("page_size", "100");
    if (pageToken) url.searchParams.set("page_token", pageToken);
    const response = await fetch(url, {
      headers: { authorization: `Bearer ${token}` },
      cache: "no-store",
    });
    if (!response.ok) throw new Error(`Feishu records failed: ${response.status}`);
    const payload = await response.json() as {
      code?: number;
      data?: {
        items?: Array<{ fields?: FeishuFields }>;
        has_more?: boolean;
        page_token?: string;
      };
    };
    if (payload.code !== 0) throw new Error("Feishu records returned an invalid response");
    rows.push(...(payload.data?.items ?? []).map((item) => item.fields ?? {}));
    pageToken = payload.data?.has_more ? payload.data.page_token ?? "" : "";
  } while (pageToken);

  return rows;
}

function buildTimelines(
  fieldsList: FeishuFields[],
  sampleDate: string,
  todayRecords: PublicRecord[],
): Record<string, unknown> {
  const groups = new Map<string, Array<Record<string, unknown>>>();
  const titles = new Map<string, string>();

  for (const fields of fieldsList) {
    const title = plain(fields["标题"]);
    const summary = plain(fields["摘要"]);
    if (!title || !summary) continue;
    const key = topicKey(title);
    const fallback = collectedDate(fields) ?? sampleDate;
    const metric = summaryPart(summary, "关键指标");
    const insight = summaryPart(summary, "核心洞察") || summary;
    const item = {
      date: eventDate(summary, fallback),
      title,
      metric: metric ? compact(metric, 52) : "详见摘要",
      source: plain(fields["来源网站名称"]) || "未标注来源",
      signal: "关注",
      detail: compact(insight, 180),
    };
    groups.set(key, [...(groups.get(key) ?? []), item]);
    titles.set(key, title.replace(/最新动态$/, "").trim() || title);
  }

  const timelines: Record<string, unknown> = {};
  for (const key of new Set(todayRecords.map((record) => record.eventGroup))) {
    const events = groups.get(key) ?? [];
    const seen = new Set<string>();
    const unique = events
      .sort((left, right) => String(right.date).localeCompare(String(left.date)))
      .filter((event) => {
        const fingerprint = `${event.date}|${event.title}|${event.source}`;
        if (seen.has(fingerprint)) return false;
        seen.add(fingerprint);
        return true;
      })
      .slice(0, 6);
    if (unique.length < 2) continue;
    unique[0] = { ...unique[0], latest: true };
    const label = titles.get(key) ?? "相关事件";
    timelines[key] = {
      title: `${label}事件发展时间线`,
      subtopic: todayRecords.find((record) => record.eventGroup === key)?.subtopic ?? label,
      summary: `飞书情报库中已关联${events.length}条同主题记录，当前页面展示最近${unique.length}次进展。最新记录为“${unique[0].title}”，可结合下方指标与来源追踪事件变化。`,
      confidence: Math.min(92, 60 + (unique.length - 1) * 7),
      trend: "持续跟踪",
      events: unique,
    };
  }
  return timelines;
}

function safeJavascript(payload: unknown): string {
  return JSON.stringify(payload, null, 2)
    .replace(/</g, "\\u003c")
    .replace(/\u2028/g, "\\u2028")
    .replace(/\u2029/g, "\\u2029");
}

function response(
  payload: Record<string, unknown>,
  freshness: "fresh" | "stale" | "unavailable",
  source: string,
  requestedDate: string,
): Response {
  const meta = (payload.meta ?? {}) as Record<string, unknown>;
  const body = {
    ...payload,
    meta: {
      ...meta,
      source,
      freshness,
      requestedDate,
      snapshotDate: payload.sampleDate,
    },
  };
  return new Response(`window.SHU_SIGNAL_DATA = ${safeJavascript(body)};\n`, {
    headers: {
      "content-type": "application/javascript; charset=utf-8",
      "cache-control": freshness === "fresh"
        ? "public, max-age=30, s-maxage=60, stale-while-revalidate=120"
        : "no-store",
      "x-content-type-options": "nosniff",
      "x-shu-signal-data-date": String(payload.sampleDate ?? "unknown"),
      "x-shu-signal-freshness": freshness,
    },
  });
}


function cachedResponse(
  snapshot: StoredSiteSnapshot,
  requestedDate: string,
  freshness: "fresh" | "stale",
): Response {
  return response(
    snapshot.payload,
    freshness,
    freshness === "fresh" ? "D1 Daily Snapshot" : "D1 Last Successful Snapshot",
    requestedDate,
  );
}


async function safeSnapshot(snapshotDate: string) {
  try {
    return await getSiteSnapshot(snapshotDate);
  } catch {
    return null;
  }
}


async function safeLatestSnapshot() {
  try {
    return await getLatestSuccessfulSiteSnapshot();
  } catch {
    return null;
  }
}


export async function GET(request: Request) {
  const sampleDate = beijingDate();
  const refreshRequested = new URL(request.url).searchParams.get("refresh") === "1";
  const cached = await safeSnapshot(sampleDate);
  const cacheAge = cached ? Date.now() - cached.refreshedAt : Number.POSITIVE_INFINITY;
  const refreshThrottled = refreshRequested && cacheAge < 60_000;
  const emptyCacheStillFresh = cached?.recordCount === 0 && cacheAge < 10 * 60_000;

  if (cached && (!refreshRequested || refreshThrottled)) {
    if (cached.recordCount > 0 || emptyCacheStillFresh) {
      return cachedResponse(cached, sampleDate, "fresh");
    }
  }

  try {
    const fieldsList = await allRecords();
    const records = fieldsList
      .filter((fields) => collectedDate(fields) === sampleDate)
      .map((fields) => buildRecord(fields, sampleDate))
      .sort((left, right) => right.collectedAt.localeCompare(left.collectedAt));
    const headlines = records.slice(0, 4).map((record) => record.title).join("、");
    const payload: Record<string, unknown> = {
      sampleDate,
      dailySummary: records.length
        ? `今日共采集${records.length}条高纯度情报，重点涉及${headlines}。以下内容均来自飞书当日数据，点击标题可查看完整摘要和历史事件链。`
        : "今日暂未采集到新的高纯度情报，网站将在飞书数据写入后自动更新。",
      records,
      timeline: buildTimelines(fieldsList, sampleDate, records),
      meta: {
        source: "Feishu Bitable Live",
        generatedAt: new Date().toISOString(),
        recordCount: records.length,
      },
    };
    try {
      await saveSiteSnapshot(sampleDate, payload, records.length);
    } catch (error) {
      console.error("SHU SIGNAL snapshot cache write error", error);
    }
    return response(payload, "fresh", "Feishu Bitable Live", sampleDate);
  } catch (error) {
    console.error("SHU SIGNAL live data error", error);
    const fallbackSnapshot = cached ?? await safeLatestSnapshot();
    if (fallbackSnapshot) {
      return cachedResponse(fallbackSnapshot, sampleDate, "stale");
    }
    const fallback: Record<string, unknown> = {
      sampleDate,
      dailySummary: "实时数据暂时不可用，请稍后刷新。",
      records: [],
      timeline: {},
      meta: { source: "Feishu Bitable Live", generatedAt: new Date().toISOString(), recordCount: 0, error: true },
    };
    return response(fallback, "unavailable", "No Successful Snapshot", sampleDate);
  }
}
