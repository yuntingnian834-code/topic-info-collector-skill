"""Best-effort runtime monitoring for the commodity intelligence skill.

The monitoring layer must never make the collection pipeline fail. Every write
opens a short-lived SQLite connection so callers do not need to manage global
state or thread ownership. Set MONITORING_DB_PATH to override the default
``data/monitoring.sqlite3`` location.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
import re
import sqlite3
import tempfile
import uuid
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

LOGGER = logging.getLogger(__name__)
ROOT_DIR = Path(__file__).resolve().parents[3]
DEFAULT_DB_PATH = ROOT_DIR / "data" / "monitoring.sqlite3"
DEFAULT_REPORT_DIR = ROOT_DIR / "data" / "monitoring"
SCHEMA_VERSION = "1"
DEFAULT_SKILL_VERSION = "1.5"


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")


def _db_path() -> Path:
    raw = os.getenv("MONITORING_DB_PATH", "").strip()
    if not raw:
        return DEFAULT_DB_PATH
    path = Path(raw).expanduser()
    return path if path.is_absolute() else ROOT_DIR / path


def _report_dir() -> Path:
    raw = os.getenv("MONITORING_REPORT_DIR", "").strip()
    if not raw:
        return DEFAULT_REPORT_DIR
    path = Path(raw).expanduser()
    return path if path.is_absolute() else ROOT_DIR / path


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS skill_runs (
            run_id TEXT PRIMARY KEY,
            run_date TEXT NOT NULL,
            trigger_type TEXT NOT NULL,
            environment TEXT NOT NULL DEFAULT 'production',
            skill_version TEXT,
            code_version TEXT,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            status TEXT NOT NULL,
            duration_ms INTEGER,
            candidate_count INTEGER NOT NULL DEFAULT 0,
            time_pass_count INTEGER NOT NULL DEFAULT 0,
            parse_success_count INTEGER NOT NULL DEFAULT 0,
            relevance_pass_count INTEGER NOT NULL DEFAULT 0,
            event_date_pass_count INTEGER NOT NULL DEFAULT 0,
            dedupe_pass_count INTEGER NOT NULL DEFAULT 0,
            written_count INTEGER NOT NULL DEFAULT 0,
            failed_count INTEGER NOT NULL DEFAULT 0,
            feishu_push_status TEXT,
            web_publish_status TEXT,
            error_stage TEXT,
            error_type TEXT,
            error_detail TEXT,
            schema_version TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS source_runs (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            feed_url TEXT NOT NULL,
            http_status INTEGER,
            latency_ms INTEGER,
            entry_count INTEGER NOT NULL DEFAULT 0,
            fresh_entry_count INTEGER NOT NULL DEFAULT 0,
            parse_success_count INTEGER NOT NULL DEFAULT 0,
            cache_hit INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            failure_reason TEXT,
            recorded_at TEXT NOT NULL,
            UNIQUE(run_id, source_id),
            FOREIGN KEY(run_id) REFERENCES skill_runs(run_id)
        );

        CREATE TABLE IF NOT EXISTS article_events (
            event_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            article_id TEXT NOT NULL,
            event_name TEXT NOT NULL,
            event_time TEXT NOT NULL,
            source_id TEXT,
            level1 TEXT,
            level2 TEXT,
            status TEXT,
            reason_code TEXT,
            reason_detail TEXT,
            properties_json TEXT NOT NULL DEFAULT '{}',
            schema_version TEXT NOT NULL,
            FOREIGN KEY(run_id) REFERENCES skill_runs(run_id)
        );

        CREATE INDEX IF NOT EXISTS idx_article_events_run_name
            ON article_events(run_id, event_name);
        CREATE INDEX IF NOT EXISTS idx_article_events_article
            ON article_events(run_id, article_id);
        CREATE INDEX IF NOT EXISTS idx_source_runs_run
            ON source_runs(run_id);
        """
    )
    columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(skill_runs)").fetchall()
    }
    if "environment" not in columns:
        conn.execute(
            "ALTER TABLE skill_runs ADD COLUMN environment TEXT NOT NULL DEFAULT 'production'"
        )
    conn.commit()


def normalize_url(url: str) -> str:
    """Return a stable URL representation without fragments/tracking params."""
    try:
        parts = urlsplit((url or "").strip())
        ignored = {"fbclid", "gclid", "mc_cid", "mc_eid"}
        query = [
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() not in ignored
        ]
        path = parts.path.rstrip("/") or "/"
        return urlunsplit(
            (parts.scheme.lower(), parts.netloc.lower(), path, urlencode(sorted(query)), "")
        )
    except Exception:
        return (url or "").strip()


def article_id_for_url(url: str) -> str:
    normalized = normalize_url(url)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


def source_id_for_url(url: str) -> str:
    """Return a stable feed/source identifier.

    The complete normalized URL is used because one domain can expose several
    independent feeds (for example, multiple Google News topic feeds).
    """
    normalized = normalize_url(url)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _clean_text(value: object, limit: int = 500) -> str | None:
    if value is None:
        return None
    cleaned = str(value).replace("\x00", "").strip()
    for name, secret in os.environ.items():
        if (
            secret
            and len(secret) >= 8
            and any(marker in name.upper() for marker in ("KEY", "SECRET", "TOKEN", "WEBHOOK"))
        ):
            cleaned = cleaned.replace(secret, "[REDACTED]")
    cleaned = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[REDACTED]", cleaned)
    cleaned = re.sub(
        r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+",
        r"\1[REDACTED]",
        cleaned,
    )
    return cleaned[:limit]


def start_run(
    run_date: str,
    trigger_type: str = "manual",
    *,
    run_id: str | None = None,
    skill_version: str | None = None,
    code_version: str | None = None,
    environment: str | None = None,
) -> str:
    run_id = run_id or f"run_{run_date.replace('-', '')}_{uuid.uuid4().hex[:10]}"
    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO skill_runs (
                    run_id, run_date, trigger_type, environment, skill_version, code_version,
                    started_at, status, schema_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?)
                ON CONFLICT(run_id) DO NOTHING
                """,
                (
                    run_id,
                    run_date,
                    trigger_type,
                    environment or os.getenv("SKILL_ENVIRONMENT", "production"),
                    skill_version or os.getenv("SKILL_VERSION", DEFAULT_SKILL_VERSION),
                    code_version or os.getenv("GITHUB_SHA", "")[:40] or None,
                    _utc_now(),
                    SCHEMA_VERSION,
                ),
            )
    except Exception as exc:
        LOGGER.warning("Monitoring start_run failed: %s", str(exc)[:160])
    return run_id


def track_article_event(
    run_id: str | None,
    article_id: str,
    event_name: str,
    *,
    source_id: str | None = None,
    level1: str | None = None,
    level2: str | None = None,
    status: str | None = None,
    reason_code: str | None = None,
    reason_detail: object = None,
    properties: dict | None = None,
) -> None:
    if not run_id:
        return
    try:
        payload = _clean_text(
            json.dumps(properties or {}, ensure_ascii=False, default=str), 8000
        ) or "{}"
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO article_events (
                    event_id, run_id, article_id, event_name, event_time,
                    source_id, level1, level2, status, reason_code,
                    reason_detail, properties_json, schema_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid.uuid4().hex,
                    run_id,
                    article_id,
                    event_name,
                    _utc_now(),
                    _clean_text(source_id, 80),
                    _clean_text(level1, 100),
                    _clean_text(level2, 200),
                    _clean_text(status, 40),
                    _clean_text(reason_code, 80),
                    _clean_text(reason_detail),
                    payload[:8000],
                    SCHEMA_VERSION,
                ),
            )
    except Exception as exc:
        LOGGER.warning("Monitoring event write failed: %s", str(exc)[:160])


def record_source_run(
    run_id: str | None,
    feed_url: str,
    *,
    status: str,
    http_status: int | None = None,
    latency_ms: int | None = None,
    entry_count: int = 0,
    fresh_entry_count: int = 0,
    parse_success_count: int = 0,
    cache_hit: bool = False,
    failure_reason: object = None,
) -> None:
    if not run_id:
        return
    source_id = source_id_for_url(feed_url)
    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO source_runs (
                    id, run_id, source_id, feed_url, http_status, latency_ms,
                    entry_count, fresh_entry_count, parse_success_count,
                    cache_hit, status, failure_reason, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, source_id) DO UPDATE SET
                    feed_url=excluded.feed_url,
                    http_status=COALESCE(excluded.http_status, source_runs.http_status),
                    latency_ms=COALESCE(excluded.latency_ms, source_runs.latency_ms),
                    entry_count=MAX(source_runs.entry_count, excluded.entry_count),
                    fresh_entry_count=MAX(source_runs.fresh_entry_count, excluded.fresh_entry_count),
                    parse_success_count=MAX(source_runs.parse_success_count, excluded.parse_success_count),
                    cache_hit=MAX(source_runs.cache_hit, excluded.cache_hit),
                    status=excluded.status,
                    failure_reason=excluded.failure_reason,
                    recorded_at=excluded.recorded_at
                """,
                (
                    uuid.uuid4().hex,
                    run_id,
                    source_id,
                    normalize_url(feed_url),
                    http_status,
                    latency_ms,
                    max(0, int(entry_count)),
                    max(0, int(fresh_entry_count)),
                    max(0, int(parse_success_count)),
                    int(cache_hit),
                    status,
                    _clean_text(failure_reason),
                    _utc_now(),
                ),
            )
    except Exception as exc:
        LOGGER.warning("Monitoring source write failed: %s", str(exc)[:160])


FUNNEL_EVENTS = {
    "candidate_count": "article_collected",
    "time_pass_count": "article_time_passed",
    "parse_success_count": "content_parse_completed",
    "relevance_pass_count": "relevance_passed",
    "event_date_pass_count": "event_date_passed",
    "dedupe_pass_count": "dedupe_passed",
    "written_count": "record_written",
}


def run_funnel(run_id: str) -> dict[str, int]:
    result = {key: 0 for key in FUNNEL_EVENTS}
    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT event_name, COUNT(DISTINCT article_id) AS count
                FROM article_events
                WHERE run_id = ?
                GROUP BY event_name
                """,
                (run_id,),
            ).fetchall()
        counts = {row["event_name"]: int(row["count"]) for row in rows}
        for field, event_name in FUNNEL_EVENTS.items():
            result[field] = counts.get(event_name, 0)
    except Exception as exc:
        LOGGER.warning("Monitoring funnel read failed: %s", str(exc)[:160])
    return result


def get_source_summary(run_id: str) -> dict[str, int]:
    result = {
        "total_count": 0,
        "success_count": 0,
        "failed_count": 0,
        "entry_count": 0,
        "fresh_entry_count": 0,
        "parse_success_count": 0,
    }
    try:
        with _connect() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total_count,
                    SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS success_count,
                    SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed_count,
                    SUM(entry_count) AS entry_count,
                    SUM(fresh_entry_count) AS fresh_entry_count,
                    SUM(parse_success_count) AS parse_success_count
                FROM source_runs
                WHERE run_id=?
                """,
                (run_id,),
            ).fetchone()
        if row:
            for key in result:
                result[key] = int(row[key] or 0)
    except Exception as exc:
        LOGGER.warning("Monitoring source summary failed: %s", str(exc)[:160])
    return result


def get_event_counts(run_id: str) -> dict[str, int]:
    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT event_name, COUNT(DISTINCT article_id) AS count
                FROM article_events
                WHERE run_id=?
                GROUP BY event_name
                ORDER BY event_name
                """,
                (run_id,),
            ).fetchall()
        return {row["event_name"]: int(row["count"]) for row in rows}
    except Exception as exc:
        LOGGER.warning("Monitoring event count failed: %s", str(exc)[:160])
        return {}


def get_blocked_reasons(run_id: str) -> list[dict]:
    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT event_name, COALESCE(reason_code, 'unspecified') AS reason_code,
                       COUNT(DISTINCT article_id) AS count
                FROM article_events
                WHERE run_id=? AND (status IN ('blocked', 'failed') OR event_name LIKE '%_failed')
                GROUP BY event_name, COALESCE(reason_code, 'unspecified')
                ORDER BY count DESC, event_name
                """,
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]
    except Exception as exc:
        LOGGER.warning("Monitoring blocked reason read failed: %s", str(exc)[:160])
        return []


def finish_run(
    run_id: str | None,
    *,
    status: str,
    failed_count: int | None = None,
    feishu_push_status: str | None = None,
    web_publish_status: str | None = None,
    error_stage: str | None = None,
    error: object = None,
) -> dict[str, int]:
    if not run_id:
        return {key: 0 for key in FUNNEL_EVENTS}
    funnel = run_funnel(run_id)
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT started_at FROM skill_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            now = dt.datetime.now(dt.timezone.utc)
            duration_ms = None
            if row:
                started = dt.datetime.fromisoformat(row["started_at"])
                duration_ms = max(0, int((now - started).total_seconds() * 1000))
            if failed_count is None:
                failed_count = conn.execute(
                    """
                    SELECT COUNT(DISTINCT article_id) FROM article_events
                    WHERE run_id = ? AND event_name IN (
                        'content_parse_failed', 'structured_output_failed',
                        'record_write_failed'
                    )
                    """,
                    (run_id,),
                ).fetchone()[0]
            else:
                event_failed_count = conn.execute(
                    """
                    SELECT COUNT(DISTINCT article_id) FROM article_events
                    WHERE run_id = ? AND event_name IN (
                        'content_parse_failed', 'structured_output_failed',
                        'record_write_failed'
                    )
                    """,
                    (run_id,),
                ).fetchone()[0]
                failed_count = max(int(failed_count), int(event_failed_count))
            conn.execute(
                """
                UPDATE skill_runs SET
                    completed_at=?, status=?, duration_ms=?,
                    candidate_count=?, time_pass_count=?, parse_success_count=?,
                    relevance_pass_count=?, event_date_pass_count=?,
                    dedupe_pass_count=?, written_count=?, failed_count=?,
                    feishu_push_status=COALESCE(?, feishu_push_status),
                    web_publish_status=COALESCE(?, web_publish_status),
                    error_stage=?, error_type=?, error_detail=?
                WHERE run_id=?
                """,
                (
                    now.isoformat(timespec="milliseconds"),
                    status,
                    duration_ms,
                    funnel["candidate_count"],
                    funnel["time_pass_count"],
                    funnel["parse_success_count"],
                    funnel["relevance_pass_count"],
                    funnel["event_date_pass_count"],
                    funnel["dedupe_pass_count"],
                    funnel["written_count"],
                    max(0, int(failed_count)),
                    feishu_push_status,
                    web_publish_status,
                    _clean_text(error_stage, 80),
                    type(error).__name__ if error is not None else None,
                    _clean_text(error),
                    run_id,
                ),
            )
    except Exception as exc:
        LOGGER.warning("Monitoring finish_run failed: %s", str(exc)[:160])
    return funnel


def get_run(run_id: str) -> dict | None:
    try:
        with _connect() as conn:
            row = conn.execute("SELECT * FROM skill_runs WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row else None
    except Exception as exc:
        LOGGER.warning("Monitoring get_run failed: %s", str(exc)[:160])
        return None


def latest_run(*, include_evaluation: bool = False) -> dict | None:
    try:
        with _connect() as conn:
            if include_evaluation:
                query = "SELECT * FROM skill_runs ORDER BY started_at DESC LIMIT 1"
            else:
                query = (
                    "SELECT * FROM skill_runs WHERE trigger_type!='evaluation' "
                    "ORDER BY started_at DESC LIMIT 1"
                )
            row = conn.execute(query).fetchone()
        return dict(row) if row else None
    except Exception as exc:
        LOGGER.warning("Monitoring latest_run failed: %s", str(exc)[:160])
        return None


def format_run_summary(run: dict | None) -> str:
    if not run:
        return "暂无Skill运行监控数据"
    return (
        f"Skill运行 {run['run_id']}｜状态 {run['status']}\n"
        f"候选 {run['candidate_count']} → 时间通过 {run['time_pass_count']} → "
        f"正文成功 {run['parse_success_count']} → 相关性通过 {run['relevance_pass_count']} → "
        f"事件日期通过 {run['event_date_pass_count']} → 去重通过 {run['dedupe_pass_count']} → "
        f"写入 {run['written_count']}｜失败 {run['failed_count']}"
    )


def build_run_report(run_id: str) -> dict:
    run = get_run(run_id)
    if not run:
        return {}
    funnel = run_funnel(run_id)
    source = get_source_summary(run_id)
    events = get_event_counts(run_id)
    losses = {
        "time_blocked": max(0, funnel["candidate_count"] - funnel["time_pass_count"]),
        "parse_failed": max(0, funnel["time_pass_count"] - funnel["parse_success_count"]),
        "relevance_blocked": max(
            0, funnel["parse_success_count"] - funnel["relevance_pass_count"]
        ),
        "event_date_blocked": max(
            0, funnel["relevance_pass_count"] - funnel["event_date_pass_count"]
        ),
        "duplicate_blocked": max(
            0, funnel["event_date_pass_count"] - funnel["dedupe_pass_count"]
        ),
        "write_failed": max(0, funnel["dedupe_pass_count"] - funnel["written_count"]),
    }
    summary_matches = all(int(run.get(key) or 0) == value for key, value in funnel.items())
    try:
        from monitoring.phase2.model_monitor import get_model_summary

        model_quality = get_model_summary(run_id)
    except Exception:
        model_quality = {"total_calls": 0}
    report = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": _utc_now(),
        "run": run,
        "sources": source,
        "funnel": funnel,
        "losses": losses,
        "eventCounts": events,
        "blockedReasons": get_blocked_reasons(run_id),
        "modelQuality": model_quality,
        "checks": {"summaryMatchesEvents": summary_matches},
    }
    try:
        from monitoring.phase4.governance import apply_governance

        report["governance"] = apply_governance(report)
    except Exception:
        report["governance"] = {
            "alerts": [],
            "alertCounts": {"P0": 0, "P1": 0},
            "reviewQueueCreated": {"retained": 0, "blocked": 0},
            "pendingReviewCount": 0,
        }
    return report


def _report_markdown(report: dict) -> str:
    run = report["run"]
    source = report["sources"]
    funnel = report["funnel"]
    losses = report["losses"]
    model_quality = report.get("modelQuality") or {}
    governance = report.get("governance") or {}
    lines = [
        f"# SHU SIGNAL Skill运行报告｜{run['run_date']}",
        "",
        f"- 状态：`{run['status']}`",
        f"- 运行批次：`{run['run_id']}`",
        f"- 触发方式：`{run['trigger_type']}`",
        f"- 环境：`{run.get('environment', 'production')}`",
        f"- 耗时：{run.get('duration_ms') or 0} ms",
        "",
        "## 信息源健康",
        "",
        f"成功 {source['success_count']} / 总计 {source['total_count']}，失败 {source['failed_count']}。",
        "",
        "## 提纯漏斗",
        "",
        f"候选 {funnel['candidate_count']} → 时间通过 {funnel['time_pass_count']} → "
        f"正文成功 {funnel['parse_success_count']} → 相关性通过 {funnel['relevance_pass_count']} → "
        f"事件日期通过 {funnel['event_date_pass_count']} → 去重通过 {funnel['dedupe_pass_count']} → "
        f"写入 {funnel['written_count']}",
        "",
        "## 损耗定位",
        "",
        f"时间拦截 {losses['time_blocked']}；正文失败 {losses['parse_failed']}；"
        f"相关性拦截 {losses['relevance_blocked']}；事件日期拦截 {losses['event_date_blocked']}；"
        f"重复拦截 {losses['duplicate_blocked']}；写入失败 {losses['write_failed']}。",
        "",
    ]
    if model_quality.get("total_calls"):
        lines.extend(
            [
                "## 模型与Prompt质量",
                "",
                f"模型调用 {model_quality['total_calls']} 次，成功率 "
                f"{model_quality['model_success_rate']}%；JSON一次成功率 "
                f"{model_quality['first_pass_json_valid_rate']}%；JSON修复 "
                f"{model_quality['json_repair_count']} 次；降级 "
                f"{model_quality['fallback_count']} 次。",
                "",
                f"总Token {model_quality['total_tokens']}；平均耗时 "
                f"{model_quality['average_latency_ms']} ms；P95耗时 "
                f"{model_quality['p95_latency_ms']} ms；预估成本 "
                f"{model_quality['estimated_cost_usd'] if model_quality['estimated_cost_usd'] is not None else '未配置费率'}。",
                "",
            ]
        )
    lines.extend(
        [
            "## 交付状态",
            "",
            f"飞书群推送：`{run.get('feishu_push_status') or '未记录'}`；"
            f"网页快照：`{run.get('web_publish_status') or '未记录'}`。",
            "",
            f"汇总与事件明细一致：`{report['checks']['summaryMatchesEvents']}`",
            "",
        ]
    )
    if report["blockedReasons"]:
        lines.extend(["## 拦截与失败原因", ""])
        for item in report["blockedReasons"]:
            lines.append(
                f"- `{item['event_name']}` / `{item['reason_code']}`：{item['count']} 篇"
            )
        lines.append("")
    alerts = governance.get("alerts") or []
    lines.extend(["## 告警与治理", ""])
    if alerts:
        for alert in alerts:
            lines.append(
                f"- **{alert['severity']} · {alert['title']}**：{alert['detail']} "
                f"建议：{alert['recommendedAction']}"
            )
    else:
        lines.append("- 当前规则未触发P0/P1告警。")
    created = governance.get("reviewQueueCreated") or {}
    lines.extend(
        [
            f"- 本次抽检入队：保留 {created.get('retained', 0)}，拦截 {created.get('blocked', 0)}。",
            f"- 待抽检总数：{governance.get('pendingReviewCount', 0)}。",
            "",
        ]
    )
    return "\n".join(lines)


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(content)
        temp_path = Path(handle.name)
    temp_path.replace(path)


def build_feishu_monitoring_card(report: dict) -> dict:
    run = report["run"]
    source = report["sources"]
    funnel = report["funnel"]
    model_quality = report.get("modelQuality") or {}
    governance = report.get("governance") or {}
    alert_counts = governance.get("alertCounts") or {"P0": 0, "P1": 0}
    has_alert = int(alert_counts.get("P0") or 0) + int(alert_counts.get("P1") or 0) > 0
    status_icon = "✅" if run["status"] in ("success", "empty_success") and not has_alert else "⚠️"
    model_line = ""
    if model_quality.get("total_calls"):
        model_line = (
            f"\n**模型：** {model_quality['total_calls']}次｜成功率 "
            f"{model_quality['model_success_rate']}%｜JSON一次成功率 "
            f"{model_quality['first_pass_json_valid_rate']}%｜Token "
            f"{model_quality['total_tokens']}"
        )
    alerts = governance.get("alerts") or []
    alert_line = ""
    if alerts:
        compact_alerts = "；".join(
            f"{item['severity']} {item['title']}" for item in alerts[:4]
        )
        alert_line = f"\n**告警：** {compact_alerts}"
    review_created = governance.get("reviewQueueCreated") or {}
    review_line = ""
    if int(review_created.get("retained") or 0) + int(review_created.get("blocked") or 0) > 0:
        review_line = (
            f"\n**周度抽检：** 保留{review_created.get('retained', 0)} ｜ "
            f"拦截{review_created.get('blocked', 0)}"
        )
    content = (
        f"**状态：** {status_icon} {run['status']}\n"
        f"**运行批次：** `{run['run_id']}`\n"
        f"**信息源：** 成功 {source['success_count']} / {source['total_count']}\n"
        f"**提纯漏斗：** 候选 {funnel['candidate_count']} → 时间 {funnel['time_pass_count']} → "
        f"正文 {funnel['parse_success_count']} → 相关性 {funnel['relevance_pass_count']} → "
        f"日期 {funnel['event_date_pass_count']} → 去重 {funnel['dedupe_pass_count']} → "
        f"写入 {funnel['written_count']}"
        f"{model_line}\n"
        f"**交付：** 飞书 {run.get('feishu_push_status') or '未记录'} ｜ "
        f"网页 {run.get('web_publish_status') or '未记录'}"
        f"{alert_line}{review_line}"
    )
    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"SHU SIGNAL Skill运行报告｜{run['run_date']}",
                },
                "template": (
                    "red" if int(alert_counts.get("P0") or 0) > 0
                    else "green" if status_icon == "✅" else "orange"
                ),
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": content}}
            ],
        },
    }


def publish_run_report(run_id: str | None) -> dict:
    """Persist a readable report and optionally send it to a separate webhook."""
    if not run_id:
        return {"status": "skipped"}
    try:
        report = build_run_report(run_id)
        if not report:
            return {"status": "missing_run"}
        report_dir = _report_dir()
        json_text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        markdown_text = _report_markdown(report)
        json_path = report_dir / f"{run_id}.json"
        markdown_path = report_dir / f"{run_id}.md"
        _atomic_write(json_path, json_text)
        _atomic_write(markdown_path, markdown_text)
        _atomic_write(report_dir / "latest_run.json", json_text)
        _atomic_write(report_dir / "latest_run.md", markdown_text)

        webhook_status = "not_configured"
        webhook_url = os.getenv("MONITORING_FEISHU_WEBHOOK_URL", "").strip()
        if webhook_url:
            import requests

            response = requests.post(
                webhook_url,
                json=build_feishu_monitoring_card(report),
                timeout=15,
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("code") not in (0, None):
                raise RuntimeError(f"Monitoring webhook returned code {payload.get('code')}")
            webhook_status = "success"
        return {
            "status": "success",
            "jsonPath": str(json_path),
            "markdownPath": str(markdown_path),
            "webhookStatus": webhook_status,
        }
    except Exception as exc:
        LOGGER.warning("Monitoring report publication failed: %s", _clean_text(exc, 160))
        return {"status": "failed", "error": _clean_text(exc, 160)}


if __name__ == "__main__":
    print(format_run_summary(latest_run()))
