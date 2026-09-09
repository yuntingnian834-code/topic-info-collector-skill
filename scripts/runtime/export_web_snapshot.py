"""Export a safe, read-only Feishu snapshot for the SHU SIGNAL HTML prototype.

The browser never receives Feishu credentials. This script runs locally/server-side,
reads Bitable through the existing Feishu application, and writes public record data
to showcase/shu-signal-data.js.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

from feishu import (  # noqa: E402
    BASE_URL,
    DAILY_BASE_TOKEN,
    DAILY_TABLE_ID,
    _headers,
    get_tenant_token,
)

TZ = ZoneInfo("Asia/Shanghai")
OUTPUT = ROOT_DIR / "showcase" / "shu-signal-data.js"


def text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "".join(
            str(item.get("text", "")) if isinstance(item, dict) else str(item)
            for item in value
        ).strip()
    return str(value).strip()


def clean_url(value) -> str:
    value = text(value)
    match = re.fullmatch(r"\[(https?://[^\]]+)]\((https?://[^)]+)\)", value)
    return match.group(2) if match else value


def short(value: str, limit: int) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    return value if len(value) <= limit else value[: limit - 1] + "…"


def summary_part(summary: str, label: str) -> str:
    match = re.search(rf"{re.escape(label)}[：:]\s*(.*?)(?=\n(?:商品品类|关键指标|市场/政策状态|核心洞察)[：:]|$)", summary, re.S)
    return match.group(1).strip() if match else ""


def event_date(summary: str, fallback: dt.date) -> dt.date:
    match = re.search(r"报告时间[：:]\s*(\d{4})年(\d{1,2})月(\d{1,2})日", summary)
    if not match:
        return fallback
    try:
        parsed = dt.date(*map(int, match.groups()))
        return parsed if parsed <= fallback else fallback
    except ValueError:
        return fallback


def category(raw: str) -> tuple[str, str]:
    raw = raw.strip()
    if "市场基本面" in raw or "价格监测" in raw:
        return "市场基本面", "market"
    if "港口" in raw or "航道" in raw:
        return "港口与航道", "port"
    if "航运" in raw or "货物运输" in raw or "物流" in raw:
        return "航运物流", "shipping"
    return "宏观政策", "policy"


def topic_key(title: str) -> str:
    key = re.sub(r"最新动态$", "", title).strip(" ，、") or title
    if key == "铜":
        return "copper-tightness"
    return "topic-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]


def load_all_records() -> list[dict]:
    token = get_tenant_token()
    rows: list[dict] = []
    page_token = None
    while True:
        url = f"{BASE_URL}/bitable/v1/apps/{DAILY_BASE_TOKEN}/tables/{DAILY_TABLE_ID}/records"
        params = {"page_size": 100}
        if page_token:
            params["page_token"] = page_token
        response = requests.get(url, headers=_headers(token), params=params, timeout=30)
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0:
            raise RuntimeError(payload)
        rows.extend(item.get("fields", {}) for item in payload["data"].get("items", []))
        if not payload["data"].get("has_more"):
            return rows
        page_token = payload["data"]["page_token"]


def record_date(fields: dict) -> dt.date | None:
    try:
        timestamp = int(fields.get("采集时间"))
    except (TypeError, ValueError):
        return None
    return dt.datetime.fromtimestamp(timestamp / 1000, TZ).date()


def build_record(fields: dict, sample_date: dt.date) -> dict:
    title = text(fields.get("标题")) or "未命名情报"
    summary = text(fields.get("摘要"))
    source = text(fields.get("来源网站名称")) or "未标注来源"
    level1, category_key = category(text(fields.get("一级分类")))
    metrics_raw = summary_part(summary, "关键指标")
    insight = summary_part(summary, "核心洞察") or short(summary, 220)
    metric_parts = [short(item, 34) for item in re.split(r"[；;]", metrics_raw) if item.strip()][:3]
    while len(metric_parts) < 3:
        fallback = [source, sample_date.isoformat(), "待持续跟踪"][len(metric_parts)]
        metric_parts.append(fallback)
    key = topic_key(title)
    digest = hashlib.sha1((title + clean_url(fields.get("来源链接"))).encode("utf-8")).hexdigest()[:12]
    collected = int(fields.get("采集时间"))
    collected_at = dt.datetime.fromtimestamp(collected / 1000, TZ)
    event = event_date(summary, sample_date)
    commodity = summary_part(summary, "商品品类") or re.sub(r"最新动态$", "", title)
    metric = short(metrics_raw, 45) if metrics_raw else "详见核心洞察"
    return {
        "id": f"intel-{digest}",
        "collectedAt": collected_at.strftime("%Y-%m-%d %H:%M"),
        "eventDate": event.isoformat(),
        "category": level1,
        "categoryKey": category_key,
        "subtopic": text(fields.get("二级信息点")) or commodity,
        "researchCore": text(fields.get("研究核心")) or "该记录尚未配置研究核心。",
        "title": title,
        "summary": summary,
        "metric": metric,
        "metrics": [[part, f"关键指标{index + 1}"] for index, part in enumerate(metric_parts)],
        "judgment": "关注",
        "judgmentClass": "neutral",
        "strength": "待研判",
        "source": source,
        "sourceUrl": clean_url(fields.get("来源链接")),
        "eventGroup": key,
        "evidence": short(insight, 220),
        "transmission": [f"{short(commodity, 18)}数据更新", "匹配研究框架", "进入持续跟踪"],
    }


def build_timelines(all_fields: list[dict], sample_date: dt.date, today_records: list[dict]) -> dict:
    groups: dict[str, list[dict]] = defaultdict(list)
    titles: dict[str, str] = {}
    for fields in all_fields:
        title = text(fields.get("标题"))
        summary = text(fields.get("摘要"))
        if not title or not summary:
            continue
        key = topic_key(title)
        collected_date = record_date(fields) or sample_date
        event = event_date(summary, collected_date)
        metric = summary_part(summary, "关键指标")
        insight = summary_part(summary, "核心洞察") or summary
        source = text(fields.get("来源网站名称")) or "未标注来源"
        groups[key].append({
            "date": event.isoformat(),
            "title": title,
            "metric": short(metric, 52) if metric else "详见摘要",
            "source": source,
            "signal": "关注",
            "detail": short(insight, 180),
        })
        titles[key] = re.sub(r"最新动态$", "", title).strip() or title

    today_groups = {record["eventGroup"] for record in today_records}
    timelines = {}
    for key in today_groups:
        events = groups.get(key, [])
        unique = []
        seen = set()
        for event in sorted(events, key=lambda item: item["date"], reverse=True):
            fingerprint = (event["date"], event["title"], event["source"])
            if fingerprint not in seen:
                seen.add(fingerprint)
                unique.append(event)
        if len(unique) < 2:
            continue
        unique = unique[:6]
        unique[0]["latest"] = True
        timelines[key] = {
            "title": f"{titles[key]}事件发展时间线",
            "subtopic": next((r["subtopic"] for r in today_records if r["eventGroup"] == key), titles[key]),
            "summary": f"飞书情报库中已关联{len(events)}条同主题记录，当前页面展示最近{len(unique)}次进展。最新记录为“{unique[0]['title']}”，可结合下方指标与来源追踪事件变化。",
            "confidence": min(92, 60 + (len(unique) - 1) * 7),
            "trend": "持续跟踪",
            "events": unique,
        }
    return timelines


def export(date_string: str | None = None) -> dict:
    sample_date = dt.date.fromisoformat(date_string) if date_string else dt.datetime.now(TZ).date()
    all_fields = load_all_records()
    today_fields = [fields for fields in all_fields if record_date(fields) == sample_date]
    records = [build_record(fields, sample_date) for fields in today_fields]
    records.sort(key=lambda item: item["collectedAt"], reverse=True)
    timelines = build_timelines(all_fields, sample_date, records)
    headline_titles = "、".join(record["title"] for record in records[:4])
    payload = {
        "sampleDate": sample_date.isoformat(),
        "dailySummary": f"今日共采集{len(records)}条高纯度情报，重点涉及{headline_titles}。以下内容均来自飞书当日视图，点击标题可查看完整摘要和历史事件链。",
        "records": records,
        "timeline": timelines,
        "meta": {
            "source": "Feishu Bitable",
            "generatedAt": dt.datetime.now(TZ).isoformat(timespec="seconds"),
            "recordCount": len(records),
        },
    }
    rendered = "window.SHU_SIGNAL_DATA = " + json.dumps(payload, ensure_ascii=False, indent=2) + ";\n"
    # 原子替换，防止网页恰好在写文件过程中读到半份 JSON。
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=OUTPUT.parent, delete=False
    ) as handle:
        handle.write(rendered)
        temp_path = Path(handle.name)
    temp_path.chmod(0o644)
    temp_path.replace(OUTPUT)
    print(f"exported {len(records)} records and {len(timelines)} timelines to {OUTPUT}")
    return {**payload["meta"], "sampleDate": payload["sampleDate"]}


if __name__ == "__main__":
    export(sys.argv[1] if len(sys.argv) > 1 else None)
