"""Phase 2 monitoring for model quality, prompt versions, latency, and cost."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import sys
import uuid
from collections import defaultdict
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[2]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from monitoring.phase1.runtime_monitor import (
    SCHEMA_VERSION,
    _clean_text,
    _connect,
    _utc_now,
    latest_run,
)


PROMPT_SPECS = {
    "relevance_extract_v1.0": {
        "call_type": "relevance_extract",
        "description": "相关性判断、事件日期与情报字段结构化提取",
    },
    "json_repair_v1.0": {
        "call_type": "json_repair",
        "description": "主提炼结果无法解析时执行一次JSON修复",
    },
    "summary_fallback_v1.0": {
        "call_type": "summary_fallback",
        "description": "结构化洞察缺失时生成中文商业摘要",
    },
    "daily_summary_v1.0": {
        "call_type": "daily_summary",
        "description": "将当日情报压缩为飞书简报核心看点",
    },
}

PROMPT_VERSIONS = {
    spec["call_type"]: version for version, spec in PROMPT_SPECS.items()
}


def _ensure_model_schema(conn) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS prompt_versions (
            prompt_version TEXT PRIMARY KEY,
            call_type TEXT NOT NULL,
            description TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            registered_at TEXT NOT NULL,
            schema_version TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS model_calls (
            call_id TEXT PRIMARY KEY,
            run_id TEXT,
            article_id TEXT,
            call_type TEXT NOT NULL,
            model_name TEXT NOT NULL,
            model_version TEXT,
            prompt_version TEXT NOT NULL,
            temperature REAL,
            input_chars INTEGER NOT NULL DEFAULT 0,
            input_tokens INTEGER,
            output_tokens INTEGER,
            latency_ms INTEGER,
            json_valid INTEGER,
            retry_count INTEGER NOT NULL DEFAULT 0,
            fallback_used INTEGER NOT NULL DEFAULT 0,
            result_status TEXT NOT NULL,
            error_type TEXT,
            error_detail TEXT,
            estimated_cost_usd REAL,
            created_at TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            FOREIGN KEY(run_id) REFERENCES skill_runs(run_id),
            FOREIGN KEY(prompt_version) REFERENCES prompt_versions(prompt_version)
        );

        CREATE INDEX IF NOT EXISTS idx_model_calls_run
            ON model_calls(run_id);
        CREATE INDEX IF NOT EXISTS idx_model_calls_prompt
            ON model_calls(prompt_version, created_at);
        CREATE INDEX IF NOT EXISTS idx_model_calls_status
            ON model_calls(result_status, created_at);

        CREATE TABLE IF NOT EXISTS evaluation_runs (
            eval_run_id TEXT PRIMARY KEY,
            run_id TEXT,
            prompt_version TEXT NOT NULL,
            model_name TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            status TEXT NOT NULL,
            total_cases INTEGER NOT NULL DEFAULT 0,
            passed_cases INTEGER NOT NULL DEFAULT 0,
            accuracy REAL,
            schema_version TEXT NOT NULL,
            FOREIGN KEY(run_id) REFERENCES skill_runs(run_id)
        );

        CREATE TABLE IF NOT EXISTS evaluation_results (
            result_id TEXT PRIMARY KEY,
            eval_run_id TEXT NOT NULL,
            case_id TEXT NOT NULL,
            article_id TEXT,
            expected_relevant INTEGER NOT NULL,
            actual_relevant INTEGER NOT NULL,
            expected_event_date TEXT,
            actual_event_date TEXT,
            expected_keyword TEXT,
            keyword_matched INTEGER,
            passed INTEGER NOT NULL,
            error_type TEXT,
            error_detail TEXT,
            recorded_at TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            UNIQUE(eval_run_id, case_id),
            FOREIGN KEY(eval_run_id) REFERENCES evaluation_runs(eval_run_id)
        );

        CREATE INDEX IF NOT EXISTS idx_evaluation_runs_prompt
            ON evaluation_runs(prompt_version, started_at);
        CREATE INDEX IF NOT EXISTS idx_evaluation_results_run
            ON evaluation_results(eval_run_id, passed);
        """
    )
    conn.commit()


def _register_prompt(conn, prompt_version: str, call_type: str) -> None:
    spec = PROMPT_SPECS.get(prompt_version, {})
    conn.execute(
        """
        INSERT INTO prompt_versions (
            prompt_version, call_type, description, is_active,
            registered_at, schema_version
        ) VALUES (?, ?, ?, 1, ?, ?)
        ON CONFLICT(prompt_version) DO UPDATE SET
            call_type=excluded.call_type,
            description=excluded.description,
            is_active=1
        """,
        (
            prompt_version,
            call_type,
            _clean_text(spec.get("description"), 300),
            _utc_now(),
            SCHEMA_VERSION,
        ),
    )


def _optional_int(value) -> int | None:
    try:
        return max(0, int(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


def _rate_from_env(name: str) -> float | None:
    try:
        raw = os.getenv(name, "").strip()
        return float(raw) if raw else None
    except ValueError:
        return None


def estimate_cost_usd(input_tokens, output_tokens) -> float | None:
    input_rate = _rate_from_env("MODEL_INPUT_COST_PER_MILLION")
    output_rate = _rate_from_env("MODEL_OUTPUT_COST_PER_MILLION")
    if input_rate is None or output_rate is None:
        return None
    input_count = _optional_int(input_tokens) or 0
    output_count = _optional_int(output_tokens) or 0
    return round(
        (input_count * input_rate + output_count * output_rate) / 1_000_000,
        8,
    )


def record_model_call(
    *,
    run_id: str | None,
    article_id: str | None,
    call_type: str,
    model_name: str,
    prompt_version: str,
    result_status: str,
    model_version: str | None = None,
    temperature: float | None = None,
    input_chars: int = 0,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    latency_ms: int | None = None,
    json_valid: bool | None = None,
    retry_count: int = 0,
    fallback_used: bool = False,
    error: object = None,
) -> str:
    """Write one model-call record without ever raising into the main pipeline."""
    call_id = uuid.uuid4().hex
    try:
        with _connect() as conn:
            _ensure_model_schema(conn)
            _register_prompt(conn, prompt_version, call_type)
            conn.execute(
                """
                INSERT INTO model_calls (
                    call_id, run_id, article_id, call_type, model_name,
                    model_version, prompt_version, temperature, input_chars,
                    input_tokens, output_tokens, latency_ms, json_valid,
                    retry_count, fallback_used, result_status, error_type,
                    error_detail, estimated_cost_usd, created_at, schema_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    call_id,
                    _clean_text(run_id, 80),
                    _clean_text(article_id, 80),
                    _clean_text(call_type, 80) or "unknown",
                    _clean_text(model_name, 100) or "unknown",
                    _clean_text(model_version, 100),
                    _clean_text(prompt_version, 100) or "unversioned",
                    temperature,
                    max(0, int(input_chars or 0)),
                    _optional_int(input_tokens),
                    _optional_int(output_tokens),
                    _optional_int(latency_ms),
                    None if json_valid is None else int(bool(json_valid)),
                    max(0, int(retry_count or 0)),
                    int(bool(fallback_used)),
                    _clean_text(result_status, 50) or "unknown",
                    type(error).__name__ if error is not None else None,
                    _clean_text(error),
                    estimate_cost_usd(input_tokens, output_tokens),
                    _utc_now(),
                    SCHEMA_VERSION,
                ),
            )
    except Exception:
        # Model monitoring is best-effort and must never change model-call behavior.
        pass
    return call_id


def update_model_call(
    call_id: str | None,
    *,
    json_valid: bool | None = None,
    result_status: str | None = None,
) -> None:
    if not call_id:
        return
    try:
        updates = []
        values = []
        if json_valid is not None:
            updates.append("json_valid=?")
            values.append(int(bool(json_valid)))
        if result_status is not None:
            updates.append("result_status=?")
            values.append(_clean_text(result_status, 50))
        if not updates:
            return
        values.append(call_id)
        with _connect() as conn:
            _ensure_model_schema(conn)
            conn.execute(
                f"UPDATE model_calls SET {', '.join(updates)} WHERE call_id=?",
                values,
            )
    except Exception:
        pass


def start_evaluation_run(
    *,
    run_id: str | None,
    prompt_version: str,
    model_name: str,
) -> str:
    """Create one fixed-dataset regression run without storing prompt/article text."""
    eval_run_id = f"eval_{uuid.uuid4().hex}"
    try:
        with _connect() as conn:
            _ensure_model_schema(conn)
            _register_prompt(conn, prompt_version, "relevance_extract")
            conn.execute(
                """
                INSERT INTO evaluation_runs (
                    eval_run_id, run_id, prompt_version, model_name,
                    started_at, status, schema_version
                ) VALUES (?, ?, ?, ?, ?, 'running', ?)
                """,
                (
                    eval_run_id,
                    _clean_text(run_id, 80),
                    _clean_text(prompt_version, 100) or "unversioned",
                    _clean_text(model_name, 100) or "unknown",
                    _utc_now(),
                    SCHEMA_VERSION,
                ),
            )
    except Exception:
        pass
    return eval_run_id


def record_evaluation_result(
    *,
    eval_run_id: str,
    case_id: str,
    article_id: str | None,
    expected_relevant: bool,
    actual_relevant: bool,
    expected_event_date: str | None = None,
    actual_event_date: str | None = None,
    expected_keyword: str | None = None,
    keyword_matched: bool | None = None,
    passed: bool,
    error: object = None,
) -> None:
    try:
        with _connect() as conn:
            _ensure_model_schema(conn)
            conn.execute(
                """
                INSERT INTO evaluation_results (
                    result_id, eval_run_id, case_id, article_id,
                    expected_relevant, actual_relevant,
                    expected_event_date, actual_event_date,
                    expected_keyword, keyword_matched, passed,
                    error_type, error_detail, recorded_at, schema_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(eval_run_id, case_id) DO UPDATE SET
                    article_id=excluded.article_id,
                    expected_relevant=excluded.expected_relevant,
                    actual_relevant=excluded.actual_relevant,
                    expected_event_date=excluded.expected_event_date,
                    actual_event_date=excluded.actual_event_date,
                    expected_keyword=excluded.expected_keyword,
                    keyword_matched=excluded.keyword_matched,
                    passed=excluded.passed,
                    error_type=excluded.error_type,
                    error_detail=excluded.error_detail,
                    recorded_at=excluded.recorded_at
                """,
                (
                    uuid.uuid4().hex,
                    eval_run_id,
                    _clean_text(case_id, 100) or "unknown",
                    _clean_text(article_id, 80),
                    int(bool(expected_relevant)),
                    int(bool(actual_relevant)),
                    _clean_text(expected_event_date, 20),
                    _clean_text(actual_event_date, 20),
                    _clean_text(expected_keyword, 100),
                    None if keyword_matched is None else int(bool(keyword_matched)),
                    int(bool(passed)),
                    type(error).__name__ if error is not None else None,
                    _clean_text(error),
                    _utc_now(),
                    SCHEMA_VERSION,
                ),
            )
    except Exception:
        pass


def finish_evaluation_run(
    eval_run_id: str,
    *,
    status: str,
    total_cases: int,
    passed_cases: int,
) -> dict:
    total = max(0, int(total_cases))
    passed = min(total, max(0, int(passed_cases)))
    accuracy = _percentage(passed, total)
    try:
        with _connect() as conn:
            _ensure_model_schema(conn)
            conn.execute(
                """
                UPDATE evaluation_runs SET
                    completed_at=?, status=?, total_cases=?, passed_cases=?, accuracy=?
                WHERE eval_run_id=?
                """,
                (
                    _utc_now(),
                    _clean_text(status, 50) or "unknown",
                    total,
                    passed,
                    accuracy,
                    eval_run_id,
                ),
            )
    except Exception:
        pass
    return {
        "eval_run_id": eval_run_id,
        "status": status,
        "total_cases": total,
        "passed_cases": passed,
        "accuracy": accuracy,
    }


def get_evaluation_run(eval_run_id: str) -> dict:
    try:
        with _connect() as conn:
            _ensure_model_schema(conn)
            run = conn.execute(
                "SELECT * FROM evaluation_runs WHERE eval_run_id=?", (eval_run_id,)
            ).fetchone()
            if not run:
                return {}
            results = conn.execute(
                """
                SELECT case_id, article_id, expected_relevant, actual_relevant,
                       expected_event_date, actual_event_date, expected_keyword,
                       keyword_matched, passed, error_type, error_detail, recorded_at
                FROM evaluation_results
                WHERE eval_run_id=? ORDER BY case_id
                """,
                (eval_run_id,),
            ).fetchall()
        payload = dict(run)
        payload["results"] = [dict(row) for row in results]
        return payload
    except Exception:
        return {}


def _percentage(numerator: int, denominator: int) -> float | None:
    if not denominator:
        return None
    return round(numerator / denominator * 100, 2)


def _percentile_95(values: list[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


def get_model_summary(run_id: str) -> dict:
    empty = {
        "total_calls": 0,
        "successful_calls": 0,
        "failed_calls": 0,
        "valid_result_calls": 0,
        "invalid_output_calls": 0,
        "model_success_rate": None,
        "first_pass_json_valid_rate": None,
        "json_repair_count": 0,
        "json_repair_rate": None,
        "fallback_count": 0,
        "fallback_rate": None,
        "event_date_extraction_rate": None,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "tokens_per_written_record": None,
        "average_latency_ms": None,
        "p95_latency_ms": None,
        "estimated_cost_usd": None,
        "prompt_breakdown": [],
    }
    try:
        with _connect() as conn:
            _ensure_model_schema(conn)
            rows = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM model_calls WHERE run_id=? ORDER BY created_at",
                    (run_id,),
                ).fetchall()
            ]
            written_row = conn.execute(
                "SELECT written_count FROM skill_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            event_rows = conn.execute(
                """
                SELECT article_id, event_name, reason_detail, properties_json
                FROM article_events
                WHERE run_id=? AND event_name IN ('event_date_passed', 'event_date_blocked')
                """,
                (run_id,),
            ).fetchall()
        if not rows:
            return empty

        api_successful = sum(1 for row in rows if row["result_status"] != "failed")
        valid_results = sum(1 for row in rows if row["result_status"] == "success")
        invalid_outputs = sum(1 for row in rows if row["result_status"] == "invalid_output")
        main_calls = [row for row in rows if row["call_type"] == "relevance_extract"]
        first_pass_valid = sum(1 for row in main_calls if row["json_valid"] == 1)
        repair_count = sum(1 for row in rows if row["call_type"] == "json_repair")
        fallback_count = sum(1 for row in rows if row["fallback_used"] == 1)
        input_tokens = sum(int(row["input_tokens"] or 0) for row in rows)
        output_tokens = sum(int(row["output_tokens"] or 0) for row in rows)
        latencies = [int(row["latency_ms"]) for row in rows if row["latency_ms"] is not None]
        costs = [float(row["estimated_cost_usd"]) for row in rows if row["estimated_cost_usd"] is not None]
        written_count = int(written_row["written_count"] or 0) if written_row else 0

        event_articles = set()
        extracted_articles = set()
        for row in event_rows:
            event_articles.add(row["article_id"])
            try:
                event_date = json.loads(row["properties_json"] or "{}").get("event_date")
            except (TypeError, json.JSONDecodeError):
                event_date = None
            if event_date or (row["event_name"] == "event_date_blocked" and row["reason_detail"]):
                extracted_articles.add(row["article_id"])

        prompt_groups = defaultdict(list)
        for row in rows:
            prompt_groups[(row["prompt_version"], row["call_type"])].append(row)
        prompt_breakdown = []
        for (version, call_type), group in sorted(prompt_groups.items()):
            group_success = sum(1 for row in group if row["result_status"] != "failed")
            group_valid = sum(1 for row in group if row["result_status"] == "success")
            json_rows = [row for row in group if row["json_valid"] is not None]
            prompt_breakdown.append(
                {
                    "prompt_version": version,
                    "call_type": call_type,
                    "call_count": len(group),
                    "success_rate": _percentage(group_success, len(group)),
                    "valid_result_rate": _percentage(group_valid, len(group)),
                    "json_valid_rate": _percentage(
                        sum(1 for row in json_rows if row["json_valid"] == 1),
                        len(json_rows),
                    ),
                }
            )

        total_tokens = input_tokens + output_tokens
        return {
            "total_calls": len(rows),
            "successful_calls": api_successful,
            "failed_calls": len(rows) - api_successful,
            "valid_result_calls": valid_results,
            "invalid_output_calls": invalid_outputs,
            "model_success_rate": _percentage(api_successful, len(rows)),
            "first_pass_json_valid_rate": _percentage(first_pass_valid, len(main_calls)),
            "json_repair_count": repair_count,
            "json_repair_rate": _percentage(repair_count, len(main_calls)),
            "fallback_count": fallback_count,
            "fallback_rate": _percentage(fallback_count, len(main_calls)),
            "event_date_extraction_rate": _percentage(
                len(extracted_articles), len(event_articles)
            ),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "tokens_per_written_record": (
                round(total_tokens / written_count, 2) if written_count else None
            ),
            "average_latency_ms": (
                round(sum(latencies) / len(latencies), 2) if latencies else None
            ),
            "p95_latency_ms": _percentile_95(latencies),
            "estimated_cost_usd": round(sum(costs), 8) if costs else None,
            "prompt_breakdown": prompt_breakdown,
        }
    except Exception:
        return empty


def get_prompt_comparison(
    days: int = 7, *, include_evaluation: bool = False
) -> list[dict]:
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=max(1, days))).isoformat()
    try:
        with _connect() as conn:
            _ensure_model_schema(conn)
            evaluation_clause = "" if include_evaluation else (
                "AND (skill_runs.trigger_type IS NULL "
                "OR skill_runs.trigger_type!='evaluation')"
            )
            rows = conn.execute(
                f"""
                SELECT model_calls.prompt_version, model_calls.call_type,
                       COUNT(*) AS call_count,
                       SUM(CASE WHEN result_status!='failed' THEN 1 ELSE 0 END) AS success_count,
                       SUM(CASE WHEN result_status='success' THEN 1 ELSE 0 END) AS valid_result_count,
                       SUM(CASE WHEN json_valid=1 THEN 1 ELSE 0 END) AS json_valid_count,
                       SUM(CASE WHEN json_valid IS NOT NULL THEN 1 ELSE 0 END) AS json_evaluated_count,
                       AVG(latency_ms) AS average_latency_ms,
                       SUM(COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0)) AS total_tokens
                FROM model_calls
                LEFT JOIN skill_runs ON skill_runs.run_id=model_calls.run_id
                WHERE model_calls.created_at>=? {evaluation_clause}
                GROUP BY model_calls.prompt_version, model_calls.call_type
                ORDER BY model_calls.call_type, model_calls.prompt_version
                """,
                (cutoff,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["success_rate"] = _percentage(
                int(item.pop("success_count") or 0), int(item["call_count"] or 0)
            )
            item["valid_result_rate"] = _percentage(
                int(item.pop("valid_result_count") or 0), int(item["call_count"] or 0)
            )
            item["json_valid_rate"] = _percentage(
                int(item.pop("json_valid_count") or 0),
                int(item.pop("json_evaluated_count") or 0),
            )
            item["average_latency_ms"] = (
                round(float(item["average_latency_ms"]), 2)
                if item["average_latency_ms"] is not None
                else None
            )
            item["total_tokens"] = int(item["total_tokens"] or 0)
            result.append(item)
        return result
    except Exception:
        return []


def format_model_summary(summary: dict) -> str:
    if not summary.get("total_calls"):
        return "本次运行无模型调用数据"
    return (
        f"模型调用 {summary['total_calls']} 次｜成功率 {summary['model_success_rate']}%｜"
        f"JSON一次成功率 {summary['first_pass_json_valid_rate']}%｜"
        f"修复 {summary['json_repair_count']} 次｜降级 {summary['fallback_count']} 次｜"
        f"Token {summary['total_tokens']}｜P95 {summary['p95_latency_ms']} ms"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="查看Skill模型与Prompt监控指标")
    parser.add_argument("--run-id", help="指定运行批次；默认查看最近一次")
    parser.add_argument("--days", type=int, default=7, help="Prompt对比时间窗口")
    parser.add_argument(
        "--include-evaluation",
        action="store_true",
        help="在Prompt对比中包含固定评测调用",
    )
    args = parser.parse_args()
    run = {"run_id": args.run_id} if args.run_id else latest_run()
    if not run:
        print("暂无Skill运行数据")
        return 0
    print(format_model_summary(get_model_summary(run["run_id"])))
    comparison = get_prompt_comparison(
        args.days, include_evaluation=args.include_evaluation
    )
    if comparison:
        print(json.dumps(comparison, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
