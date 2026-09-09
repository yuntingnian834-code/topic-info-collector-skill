"""Fetch the hosted user-value summary and publish a weekly/alert report."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import tempfile
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests


ROOT_DIR = Path(__file__).resolve().parents[3]
DEFAULT_REPORT_DIR = ROOT_DIR / "data" / "monitoring" / "user_value"


def fetch_summary(site_url: str, days: int = 7) -> dict:
    endpoint = urljoin(site_url.rstrip("/") + "/", "api/analytics/summary")
    response = requests.get(endpoint, params={"days": days}, timeout=20)
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok") or not isinstance(payload.get("summary"), dict):
        raise RuntimeError("User-value endpoint returned an invalid payload")
    return payload["summary"]


def _pct(value) -> str:
    return "暂无样本" if value is None else f"{value}%"


def build_markdown(summary: dict) -> str:
    metrics = summary.get("metrics") or {}
    alerts = summary.get("alerts") or []
    lines = [
        f"# SHU SIGNAL 用户价值周报｜{summary.get('generatedAt', '')[:10]}",
        "",
        f"- 观察窗口：最近 {summary.get('days', 7)} 天",
        f"- 访问会话：{metrics.get('visitSessions', 0)}",
        f"- 每周有效情报：{metrics.get('weeklyUsefulIntelligence', 0)} 条",
        f"- 情报有用率：{_pct(metrics.get('usefulRate'))}",
        f"- 列表到详情：{_pct(metrics.get('libraryToDetailRate'))}",
        f"- 详情到原文：{_pct(metrics.get('detailToSourceRate'))}",
        f"- 搜索零结果率：{_pct(metrics.get('searchZeroResultRate'))}",
        f"- 待处理Bad Case：{len(summary.get('badCases') or [])}",
        "",
        "## 渠道",
        "",
    ]
    channels = summary.get("channels") or []
    if channels:
        lines.extend(
            f"- {item.get('channel', 'direct')}：访问{item.get('visitSessions', 0)}，反馈用户{item.get('feedbackUsers', 0)}"
            for item in channels
        )
    else:
        lines.append("- 暂无渠道数据。")
    lines.extend(["", "## 告警", ""])
    if alerts:
        lines.extend(
            f"- **{item.get('level')} · {item.get('title')}**：{item.get('detail')} 建议：{item.get('recommendedAction')}"
            for item in alerts
        )
    else:
        lines.append("- 当前未触发用户价值告警。")
    lines.append("")
    return "\n".join(lines)


def build_feishu_card(summary: dict) -> dict:
    metrics = summary.get("metrics") or {}
    alerts = summary.get("alerts") or []
    alert_text = "无"
    if alerts:
        alert_text = "；".join(f"{item.get('level')} {item.get('title')}" for item in alerts[:5])
    content = (
        f"**访问会话：** {metrics.get('visitSessions', 0)}\n"
        f"**每周有效情报：** {metrics.get('weeklyUsefulIntelligence', 0)}条\n"
        f"**有用率：** {_pct(metrics.get('usefulRate'))}｜"
        f"**列表→详情：** {_pct(metrics.get('libraryToDetailRate'))}｜"
        f"**详情→原文：** {_pct(metrics.get('detailToSourceRate'))}\n"
        f"**搜索零结果：** {_pct(metrics.get('searchZeroResultRate'))}｜"
        f"**待处理Bad Case：** {len(summary.get('badCases') or [])}\n"
        f"**异常：** {alert_text}"
    )
    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": "SHU SIGNAL 用户价值周报"},
                "template": "orange" if alerts else "green",
            },
            "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": content}}],
        },
    }


def should_notify(mode: str, summary: dict, now: dt.datetime | None = None) -> bool:
    if mode == "always":
        return True
    if mode == "never":
        return False
    has_alerts = bool(summary.get("alerts"))
    if mode == "alerts-only":
        return has_alerts
    current = now or dt.datetime.now(ZoneInfo("Asia/Shanghai"))
    return current.weekday() == 0 or has_alerts


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(content)
        temp_path = Path(handle.name)
    temp_path.replace(path)


def publish_user_value_report(
    site_url: str,
    *,
    days: int = 7,
    notification_mode: str = "auto",
    report_dir: Path | None = None,
    webhook_url: str | None = None,
) -> dict:
    summary = fetch_summary(site_url, days)
    destination = report_dir or Path(os.getenv("USER_VALUE_REPORT_DIR", "") or DEFAULT_REPORT_DIR)
    json_text = json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    markdown_text = build_markdown(summary)
    _atomic_write(destination / "latest_user_value.json", json_text)
    _atomic_write(destination / "latest_user_value.md", markdown_text)

    webhook_status = "not_configured"
    webhook = (webhook_url if webhook_url is not None else os.getenv("MONITORING_FEISHU_WEBHOOK_URL", "")).strip()
    if webhook and should_notify(notification_mode, summary):
        response = requests.post(webhook, json=build_feishu_card(summary), timeout=15)
        response.raise_for_status()
        result = response.json()
        if result.get("code") not in (0, None):
            raise RuntimeError(f"Monitoring webhook returned code {result.get('code')}")
        webhook_status = "success"
    elif webhook:
        webhook_status = "not_due"
    return {
        "status": "success",
        "webhookStatus": webhook_status,
        "alertCount": len(summary.get("alerts") or []),
        "reportDir": str(destination),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="生成SHU SIGNAL用户价值周报与异常通知")
    parser.add_argument("--site-url", default=os.getenv("SHU_SIGNAL_SITE_URL", ""))
    parser.add_argument("--days", type=int, choices=(7, 30), default=7)
    parser.add_argument("--notification-mode", choices=("auto", "always", "alerts-only", "never"), default="auto")
    args = parser.parse_args()
    if not args.site_url:
        parser.error("请通过 --site-url 或 SHU_SIGNAL_SITE_URL 配置网站地址")
    result = publish_user_value_report(
        args.site_url,
        days=args.days,
        notification_mode=args.notification_mode,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
