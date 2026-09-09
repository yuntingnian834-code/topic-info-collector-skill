"""Verify that the public site shows the latest successful daily snapshot."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests


ROOT_DIR = Path(__file__).resolve().parents[3]
DEFAULT_REPORT_DIR = ROOT_DIR / "data" / "monitoring" / "freshness"
TIME_ZONE = ZoneInfo("Asia/Shanghai")
SNAPSHOT_PATTERN = re.compile(
    r"^\s*window\.SHU_SIGNAL_DATA\s*=\s*(\{[\s\S]*\})\s*;\s*$"
)


def fetch_site_snapshot(site_url: str, *, refresh: bool = False) -> dict:
    endpoint = urljoin(site_url.rstrip("/") + "/", "shu-signal-data.js")
    response = requests.get(
        endpoint,
        params={"refresh": "1"} if refresh else None,
        headers={"User-Agent": "SHU-SIGNAL-Freshness-Monitor/1.0"},
        timeout=45,
    )
    response.raise_for_status()
    match = SNAPSHOT_PATTERN.match(response.text)
    if not match:
        raise RuntimeError("Site data endpoint returned an invalid JavaScript payload")
    payload = json.loads(match.group(1))
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        raise RuntimeError("Site data payload is missing records")
    return payload


def expected_from_pipeline(path: Path | None) -> tuple[str | None, int | None]:
    if not path or not path.exists():
        return None, None
    payload = json.loads(path.read_text(encoding="utf-8"))
    date_string = payload.get("websiteDate") or payload.get("date")
    count = payload.get("websiteRecords")
    return str(date_string) if date_string else None, int(count) if count is not None else None


def assess_freshness(
    payload: dict,
    *,
    expected_date: str,
    expected_count: int | None = None,
    now: dt.datetime | None = None,
) -> dict:
    current = now or dt.datetime.now(TIME_ZONE)
    snapshot_date = str(payload.get("sampleDate") or "")
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    record_count = len(payload.get("records") or [])
    freshness = str(meta.get("freshness") or "unknown")
    alerts: list[dict] = []

    try:
        lag_days = (
            dt.date.fromisoformat(expected_date) - dt.date.fromisoformat(snapshot_date)
        ).days
    except ValueError:
        lag_days = None

    if freshness == "unavailable":
        alerts.append({
            "level": "P0",
            "code": "site_data_unavailable",
            "title": "网站数据不可用",
            "detail": "实时数据和历史成功快照均不可用。",
            "recommendedAction": "检查飞书凭证、数据接口和D1快照表。",
        })
    else:
        if freshness not in ("fresh", "stale"):
            alerts.append({
                "level": "P1",
                "code": "site_freshness_unknown",
                "title": "网站新鲜度状态缺失",
                "detail": f"接口返回的新鲜度状态为 {freshness or '空值'}。",
                "recommendedAction": "发布包含新鲜度元数据和D1快照的最新版网站。",
            })

    if freshness == "stale" or snapshot_date != expected_date:
        alerts.append({
            "level": "P0" if lag_days is not None and lag_days >= 2 else "P1",
            "code": "site_snapshot_stale",
            "title": "网站快照日期落后",
            "detail": f"期望{expected_date}，网站当前为{snapshot_date or '未知日期'}。",
            "recommendedAction": "刷新当天飞书快照并核对缓存写入。",
        })

    if expected_count is not None and record_count != expected_count:
        alerts.append({
            "level": "P0",
            "code": "site_record_count_mismatch",
            "title": "多端记录数不一致",
            "detail": f"任务产出{expected_count}条，网站返回{record_count}条。",
            "recommendedAction": "核对飞书当日视图、网页缓存和数据生成时间。",
        })
    elif expected_count is None and record_count == 0 and current.hour >= 8:
        alerts.append({
            "level": "P1",
            "code": "site_daily_snapshot_empty",
            "title": "当日网站快照为空",
            "detail": "北京时间08:00后网站仍未展示当日情报。",
            "recommendedAction": "确认每日采集任务是否完成，并强制刷新网站缓存。",
        })

    return {
        "checkedAt": current.isoformat(timespec="seconds"),
        "expectedDate": expected_date,
        "snapshotDate": snapshot_date,
        "lagDays": lag_days,
        "recordCount": record_count,
        "expectedCount": expected_count,
        "freshness": freshness,
        "source": meta.get("source"),
        "generatedAt": meta.get("generatedAt"),
        "status": "alert" if alerts else "healthy",
        "alerts": alerts,
    }


def build_markdown(report: dict) -> str:
    lines = [
        f"# SHU SIGNAL 数据新鲜度报告｜{report['checkedAt'][:10]}",
        "",
        f"- 状态：{'异常' if report['alerts'] else '正常'}",
        f"- 期望日期：{report['expectedDate']}",
        f"- 网站日期：{report['snapshotDate'] or '未知'}",
        f"- 网站记录：{report['recordCount']}条",
        f"- 任务记录：{report['expectedCount'] if report['expectedCount'] is not None else '未提供'}",
        f"- 数据来源：{report['source'] or '未知'}",
        f"- 新鲜度：{report['freshness']}",
        "",
        "## 告警",
        "",
    ]
    if report["alerts"]:
        lines.extend(
            f"- **{item['level']} · {item['title']}**：{item['detail']} 建议：{item['recommendedAction']}"
            for item in report["alerts"]
        )
    else:
        lines.append("- 网站日期与当日任务结果一致。")
    lines.append("")
    return "\n".join(lines)


def build_feishu_card(report: dict) -> dict:
    alerts = report["alerts"]
    detail = "；".join(f"{item['level']} {item['title']}" for item in alerts[:5]) or "无"
    content = (
        f"**期望日期：** {report['expectedDate']}｜**网站日期：** {report['snapshotDate'] or '未知'}\n"
        f"**网站记录：** {report['recordCount']}条｜**任务记录：** "
        f"{report['expectedCount'] if report['expectedCount'] is not None else '未提供'}\n"
        f"**数据状态：** {report['freshness']}｜**异常：** {detail}"
    )
    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": "SHU SIGNAL 数据新鲜度"},
                "template": "red" if any(item["level"] == "P0" for item in alerts) else "orange" if alerts else "green",
            },
            "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": content}}],
        },
    }


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    temporary.replace(path)


def run_freshness_monitor(
    site_url: str,
    *,
    refresh: bool = False,
    expected_date: str | None = None,
    expected_count: int | None = None,
    pipeline_result: Path | None = None,
    report_dir: Path | None = None,
    notification_mode: str = "alerts-only",
    webhook_url: str | None = None,
    now: dt.datetime | None = None,
) -> dict:
    pipeline_date, pipeline_count = expected_from_pipeline(pipeline_result)
    current = now or dt.datetime.now(TIME_ZONE)
    target_date = expected_date or pipeline_date or current.date().isoformat()
    target_count = expected_count if expected_count is not None else pipeline_count
    destination = report_dir or Path(os.getenv("FRESHNESS_REPORT_DIR", "") or DEFAULT_REPORT_DIR)

    try:
        payload = fetch_site_snapshot(site_url, refresh=refresh)
        report = assess_freshness(
            payload,
            expected_date=target_date,
            expected_count=target_count,
            now=current,
        )
    except Exception as error:
        report = {
            "checkedAt": current.isoformat(timespec="seconds"),
            "expectedDate": target_date,
            "snapshotDate": "",
            "lagDays": None,
            "recordCount": 0,
            "expectedCount": target_count,
            "freshness": "unavailable",
            "source": None,
            "generatedAt": None,
            "status": "alert",
            "alerts": [{
                "level": "P0",
                "code": "site_endpoint_failed",
                "title": "网站数据接口检查失败",
                "detail": str(error)[:180],
                "recommendedAction": "检查公开站点、数据接口和网络访问。",
            }],
        }

    _atomic_write(destination / "latest_freshness.json", json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    _atomic_write(destination / "latest_freshness.md", build_markdown(report))

    webhook = (webhook_url if webhook_url is not None else os.getenv("MONITORING_FEISHU_WEBHOOK_URL", "")).strip()
    should_send = notification_mode == "always" or (
        notification_mode == "alerts-only" and bool(report["alerts"])
    )
    report["webhookStatus"] = "not_configured"
    if webhook and should_send:
        response = requests.post(webhook, json=build_feishu_card(report), timeout=15)
        response.raise_for_status()
        result = response.json()
        if result.get("code") not in (0, None):
            raise RuntimeError(f"Monitoring webhook returned code {result.get('code')}")
        report["webhookStatus"] = "success"
    elif webhook:
        report["webhookStatus"] = "not_due"
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="检查SHU SIGNAL网站数据日期与记录数")
    parser.add_argument("--site-url", default=os.getenv("SHU_SIGNAL_SITE_URL", ""))
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--expected-date")
    parser.add_argument("--expected-count", type=int)
    parser.add_argument("--pipeline-result", type=Path)
    parser.add_argument("--notification-mode", choices=("always", "alerts-only", "never"), default="alerts-only")
    args = parser.parse_args()
    if not args.site_url:
        parser.error("请通过 --site-url 或 SHU_SIGNAL_SITE_URL 配置网站地址")
    report = run_freshness_monitor(
        args.site_url,
        refresh=args.refresh,
        expected_date=args.expected_date,
        expected_count=args.expected_count,
        pipeline_result=args.pipeline_result,
        notification_mode=args.notification_mode,
    )
    print(json.dumps(report, ensure_ascii=False))
    return 1 if report["alerts"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
