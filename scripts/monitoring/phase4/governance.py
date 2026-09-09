"""Alert evaluation and Bad Case governance for SHU SIGNAL monitoring.

All functions are best-effort: governance failures must never block the data
collection, Feishu delivery, or website publication pipeline.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import uuid
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[2]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from monitoring.phase1.runtime_monitor import (  # noqa: E402
    SCHEMA_VERSION,
    _clean_text,
    _connect,
    _utc_now,
    build_run_report,
    latest_run,
)


ALERT_RULES = {
    "run_failed": ("P0", "Skill运行失败", "定位 error_stage 后重跑，并确认没有产生不完整交付。"),
    "all_sources_failed": ("P0", "全部信息源失败", "检查网络、RSS地址与来源限流状态，恢复前停止下游发布。"),
    "delivery_mismatch": ("P0", "多端交付不一致", "核对飞书回读数、网页记录数和日期，保持当前版本不发布。"),
    "candidate_no_write": ("P1", "有候选但最终写入为0", "检查相关性、日期和去重损耗，确认是否为正常重复。"),
    "source_failure_ratio": ("P1", "信息源失败比例偏高", "优先处理连续失败来源，并准备替代RSS源。"),
    "candidate_volume_low": ("P1", "候选量低于7日基线", "检查主要来源是否失效，以及当天是否属于正常低新闻周期。"),
    "candidate_volume_high": ("P1", "候选量高于7日基线", "检查是否发生来源重复、时间窗口放宽或突发新闻。"),
    "model_success_low": ("P1", "模型调用成功率偏低", "检查接口错误类型、限流与超时，必要时启用降级方案。"),
    "json_valid_low": ("P1", "JSON一次成功率偏低", "抽取失败样本进入Bad Case，检查Prompt与字段约束。"),
    "fallback_high": ("P1", "模型降级比例偏高", "检查主提炼链路稳定性，并比较当前Prompt版本与7日基线。"),
}


def _ensure_governance_schema(conn) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS runtime_alerts (
            alert_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            rule_key TEXT NOT NULL,
            severity TEXT NOT NULL,
            title TEXT NOT NULL,
            detail TEXT,
            recommended_action TEXT,
            current_value REAL,
            threshold_value REAL,
            created_at TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            UNIQUE(run_id, rule_key),
            FOREIGN KEY(run_id) REFERENCES skill_runs(run_id)
        );

        CREATE TABLE IF NOT EXISTS quality_reviews (
            review_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            article_id TEXT NOT NULL,
            article_title TEXT,
            sample_type TEXT NOT NULL,
            actual_relevance INTEGER,
            expected_relevance INTEGER,
            event_date_correct INTEGER,
            metrics_correct INTEGER,
            summary_faithful INTEGER,
            bad_case_type TEXT,
            prompt_version TEXT,
            reviewer TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            reviewed_at TEXT,
            created_at TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            UNIQUE(run_id, article_id, sample_type),
            FOREIGN KEY(run_id) REFERENCES skill_runs(run_id)
        );

        CREATE INDEX IF NOT EXISTS idx_runtime_alerts_run_severity
            ON runtime_alerts(run_id, severity);
        CREATE INDEX IF NOT EXISTS idx_quality_reviews_status_created
            ON quality_reviews(status, created_at);
        CREATE INDEX IF NOT EXISTS idx_quality_reviews_article
            ON quality_reviews(article_id, created_at);
        """
    )
    conn.commit()


def _baseline_candidates(run: dict, days: int = 7) -> tuple[float | None, int]:
    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT candidate_count
                FROM skill_runs
                WHERE run_id != ?
                  AND environment = 'production'
                  AND trigger_type != 'evaluation'
                  AND status IN ('success', 'partial_success', 'empty_success')
                  AND date(run_date) < date(?)
                  AND date(run_date) >= date(?, ?)
                ORDER BY run_date DESC, started_at DESC
                """,
                (
                    run["run_id"],
                    run["run_date"],
                    run["run_date"],
                    f"-{max(1, int(days))} days",
                ),
            ).fetchall()
        if len(rows) < 3:
            return None, len(rows)
        return sum(int(row["candidate_count"] or 0) for row in rows) / len(rows), len(rows)
    except Exception:
        return None, 0


def _alert(rule_key: str, detail: str, current=None, threshold=None) -> dict:
    severity, title, action = ALERT_RULES[rule_key]
    return {
        "ruleKey": rule_key,
        "severity": severity,
        "title": title,
        "detail": detail,
        "recommendedAction": action,
        "currentValue": current,
        "thresholdValue": threshold,
    }


def evaluate_runtime_alerts(report: dict) -> list[dict]:
    """Return P0/P1 alerts using minimum-volume guards to reduce noise."""
    run = report.get("run") or {}
    source = report.get("sources") or {}
    funnel = report.get("funnel") or {}
    model = report.get("modelQuality") or {}
    checks = report.get("checks") or {}
    alerts: list[dict] = []

    if run.get("status") == "failed":
        alerts.append(_alert("run_failed", f"失败阶段：{run.get('error_stage') or '未记录'}。"))
    if int(source.get("total_count") or 0) > 0 and int(source.get("success_count") or 0) == 0:
        alerts.append(_alert("all_sources_failed", f"{source.get('total_count')}个来源均未成功。", 0, 1))
    if run.get("web_publish_status") == "inconsistent" or checks.get("summaryMatchesEvents") is False:
        alerts.append(_alert("delivery_mismatch", "运行汇总、飞书回读或网页快照存在数量/版本差异。"))

    candidates = int(funnel.get("candidate_count") or 0)
    written = int(funnel.get("written_count") or 0)
    if candidates > 0 and written == 0:
        alerts.append(_alert("candidate_no_write", f"候选{candidates}篇，最终写入0篇。", 0, 1))

    source_total = int(source.get("total_count") or 0)
    source_failed = int(source.get("failed_count") or 0)
    source_failure_rate = round(source_failed / source_total * 100, 1) if source_total else 0
    if source_total >= 3 and source_failure_rate >= 30:
        alerts.append(_alert("source_failure_ratio", f"失败{source_failed}/{source_total}，占{source_failure_rate}%。", source_failure_rate, 30))

    baseline, baseline_count = _baseline_candidates(run)
    if baseline is not None and baseline > 0:
        ratio = candidates / baseline
        if ratio < 0.5:
            alerts.append(_alert("candidate_volume_low", f"当前{candidates}篇；过去{baseline_count}次均值{baseline:.1f}篇。", candidates, baseline * 0.5))
        elif ratio > 2:
            alerts.append(_alert("candidate_volume_high", f"当前{candidates}篇；过去{baseline_count}次均值{baseline:.1f}篇。", candidates, baseline * 2))

    total_calls = int(model.get("total_calls") or 0)
    if total_calls >= 5:
        model_success = float(model.get("model_success_rate") or 0)
        json_valid = float(model.get("first_pass_json_valid_rate") or 0)
        fallback_rate = float(model.get("fallback_rate") or 0)
        if model_success < 90:
            alerts.append(_alert("model_success_low", f"{total_calls}次调用，成功率{model_success}%。", model_success, 90))
        if json_valid < 85:
            alerts.append(_alert("json_valid_low", f"JSON一次成功率{json_valid}%。", json_valid, 85))
        if fallback_rate > 20:
            alerts.append(_alert("fallback_high", f"降级率{fallback_rate}%。", fallback_rate, 20))
    return alerts


def _save_alerts(run_id: str, alerts: list[dict]) -> None:
    with _connect() as conn:
        _ensure_governance_schema(conn)
        for alert in alerts:
            conn.execute(
                """
                INSERT INTO runtime_alerts (
                    alert_id, run_id, rule_key, severity, title, detail,
                    recommended_action, current_value, threshold_value,
                    created_at, schema_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, rule_key) DO UPDATE SET
                    severity=excluded.severity,
                    title=excluded.title,
                    detail=excluded.detail,
                    recommended_action=excluded.recommended_action,
                    current_value=excluded.current_value,
                    threshold_value=excluded.threshold_value
                """,
                (
                    uuid.uuid4().hex,
                    run_id,
                    alert["ruleKey"],
                    alert["severity"],
                    alert["title"],
                    _clean_text(alert["detail"], 500),
                    _clean_text(alert["recommendedAction"], 500),
                    alert.get("currentValue"),
                    alert.get("thresholdValue"),
                    _utc_now(),
                    SCHEMA_VERSION,
                ),
            )


def _article_title(conn, run_id: str, article_id: str) -> str | None:
    row = conn.execute(
        """
        SELECT properties_json FROM article_events
        WHERE run_id=? AND article_id=? AND event_name='article_collected'
        ORDER BY event_time LIMIT 1
        """,
        (run_id, article_id),
    ).fetchone()
    if not row:
        return None
    try:
        return _clean_text(json.loads(row["properties_json"]).get("title"), 160)
    except Exception:
        return None


def _prompt_version(conn, run_id: str, article_id: str) -> str | None:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='model_calls'"
    ).fetchone()
    if not exists:
        return None
    row = conn.execute(
        """
        SELECT prompt_version FROM model_calls
        WHERE run_id=? AND article_id=? AND call_type='relevance_extract'
        ORDER BY created_at DESC LIMIT 1
        """,
        (run_id, article_id),
    ).fetchone()
    return _clean_text(row["prompt_version"], 100) if row else None


def create_review_queue(run_id: str, sample_size: int = 5) -> dict[str, int]:
    """Sample retained and blocked articles; repeat calls remain idempotent."""
    sample_size = max(1, min(int(sample_size), 20))
    created = {"retained": 0, "blocked": 0}
    with _connect() as conn:
        _ensure_governance_schema(conn)
        retained = conn.execute(
            """
            SELECT DISTINCT article_id FROM article_events
            WHERE run_id=? AND event_name='record_written'
            ORDER BY article_id LIMIT ?
            """,
            (run_id, sample_size),
        ).fetchall()
        blocked = conn.execute(
            """
            SELECT DISTINCT article_id FROM article_events
            WHERE run_id=? AND event_name IN (
                'relevance_blocked', 'event_date_blocked',
                'content_parse_failed', 'structured_output_failed'
            )
            ORDER BY article_id LIMIT ?
            """,
            (run_id, sample_size),
        ).fetchall()
        for sample_type, rows in (("retained", retained), ("blocked", blocked)):
            for row in rows:
                article_id = row["article_id"]
                actual_relevance = 1 if sample_type == "retained" else 0
                result = conn.execute(
                    """
                    INSERT INTO quality_reviews (
                        review_id, run_id, article_id, article_title,
                        sample_type, actual_relevance, prompt_version,
                        status, created_at, schema_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                    ON CONFLICT(run_id, article_id, sample_type) DO NOTHING
                    """,
                    (
                        uuid.uuid4().hex,
                        run_id,
                        article_id,
                        _article_title(conn, run_id, article_id),
                        sample_type,
                        actual_relevance,
                        _prompt_version(conn, run_id, article_id),
                        _utc_now(),
                        SCHEMA_VERSION,
                    ),
                )
                created[sample_type] += max(0, int(result.rowcount or 0))
    return created


def get_review_queue(status: str | None = None, limit: int = 100) -> list[dict]:
    try:
        with _connect() as conn:
            _ensure_governance_schema(conn)
            if status:
                rows = conn.execute(
                    "SELECT * FROM quality_reviews WHERE status=? ORDER BY created_at DESC LIMIT ?",
                    (status, max(1, min(limit, 500))),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM quality_reviews ORDER BY created_at DESC LIMIT ?",
                    (max(1, min(limit, 500)),),
                ).fetchall()
        return [dict(row) for row in rows]
    except Exception:
        return []


def update_review(
    review_id: str,
    *,
    status: str,
    expected_relevance: bool | None = None,
    event_date_correct: bool | None = None,
    metrics_correct: bool | None = None,
    summary_faithful: bool | None = None,
    bad_case_type: str | None = None,
    reviewer: str | None = None,
) -> bool:
    if status not in {"pending", "reviewed", "fixed", "regression_passed"}:
        return False
    try:
        with _connect() as conn:
            _ensure_governance_schema(conn)
            result = conn.execute(
                """
                UPDATE quality_reviews SET
                    status=?, expected_relevance=?, event_date_correct=?,
                    metrics_correct=?, summary_faithful=?, bad_case_type=?,
                    reviewer=?, reviewed_at=?
                WHERE review_id=?
                """,
                (
                    status,
                    None if expected_relevance is None else int(expected_relevance),
                    None if event_date_correct is None else int(event_date_correct),
                    None if metrics_correct is None else int(metrics_correct),
                    None if summary_faithful is None else int(summary_faithful),
                    _clean_text(bad_case_type, 80),
                    _clean_text(reviewer, 80),
                    _utc_now(),
                    review_id,
                ),
            )
        return int(result.rowcount or 0) > 0
    except Exception:
        return False


def retention_preview(run_days: int = 180, review_days: int = 365) -> dict[str, int]:
    """Count deletable rows only; Phase 4 never deletes monitoring data implicitly."""
    try:
        with _connect() as conn:
            _ensure_governance_schema(conn)
            old_runs = conn.execute(
                "SELECT COUNT(*) FROM skill_runs WHERE started_at < datetime('now', ?)",
                (f"-{max(1, int(run_days))} days",),
            ).fetchone()[0]
            old_reviews = conn.execute(
                """
                SELECT COUNT(*) FROM quality_reviews
                WHERE status='regression_passed' AND reviewed_at < datetime('now', ?)
                """,
                (f"-{max(1, int(review_days))} days",),
            ).fetchone()[0]
        return {"oldRuns": int(old_runs or 0), "oldResolvedReviews": int(old_reviews or 0)}
    except Exception:
        return {"oldRuns": 0, "oldResolvedReviews": 0}


def apply_governance(report: dict, *, force_review_queue: bool = False) -> dict:
    """Persist alerts and optionally create the Monday review queue."""
    empty = {
        "alerts": [],
        "alertCounts": {"P0": 0, "P1": 0},
        "reviewQueueCreated": {"retained": 0, "blocked": 0},
        "pendingReviewCount": 0,
        "retentionPreview": {"oldRuns": 0, "oldResolvedReviews": 0},
    }
    try:
        run = report.get("run") or {}
        run_id = run.get("run_id")
        if not run_id:
            return empty
        alerts = evaluate_runtime_alerts(report)
        _save_alerts(run_id, alerts)
        run_date = dt.date.fromisoformat(run.get("run_date"))
        create_weekly = force_review_queue or run_date.weekday() == 0
        created = create_review_queue(run_id) if create_weekly else {"retained": 0, "blocked": 0}
        pending_count = len(get_review_queue("pending", limit=500))
        return {
            "alerts": alerts,
            "alertCounts": {
                "P0": sum(1 for item in alerts if item["severity"] == "P0"),
                "P1": sum(1 for item in alerts if item["severity"] == "P1"),
            },
            "reviewQueueCreated": created,
            "pendingReviewCount": pending_count,
            "retentionPreview": retention_preview(),
        }
    except Exception:
        return empty


def main() -> int:
    parser = argparse.ArgumentParser(description="查看第四阶段告警与Bad Case治理状态")
    parser.add_argument("--force-review-queue", action="store_true", help="立即为最近运行建立抽检队列")
    parser.add_argument("--json", action="store_true", help="输出JSON")
    args = parser.parse_args()
    run = latest_run()
    if not run:
        print("暂无Skill运行数据")
        return 1
    report = build_run_report(run["run_id"])
    governance = apply_governance(report, force_review_queue=args.force_review_queue)
    if args.json:
        print(json.dumps(governance, ensure_ascii=False, indent=2))
    else:
        counts = governance["alertCounts"]
        created = governance["reviewQueueCreated"]
        print(f"告警 P0 {counts['P0']} / P1 {counts['P1']}")
        print(f"本次抽检入队：保留{created['retained']}，拦截{created['blocked']}")
        print(f"待抽检：{governance['pendingReviewCount']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
