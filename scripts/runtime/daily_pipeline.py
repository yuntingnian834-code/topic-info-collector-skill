"""Run the complete SHU SIGNAL daily publication chain.

Order is deliberately strict:
1. collect and write Feishu records;
2. read back the complete Beijing-date dataset;
3. push the Feishu group report when this run wrote new records;
4. export the public website snapshot.

If a Feishu push is required and fails, the website snapshot is not advanced.
On a retry, existing Feishu records can still repair a stale website snapshot
without sending the same group notification twice.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))

from export_web_snapshot import export  # noqa: E402
from feishu import get_today_records  # noqa: E402
from fetch_data import collection_source_health, fetch_and_write  # noqa: E402
from monitoring import (  # noqa: E402
    finish_run,
    format_run_summary,
    get_run,
    get_source_summary,
    publish_run_report,
    start_run,
)
from reporter import send_daily_report  # noqa: E402

TZ = ZoneInfo("Asia/Shanghai")
ROOT_DIR = SCRIPTS_DIR.parent
WEBSITE_SNAPSHOT = ROOT_DIR / "showcase" / "shu-signal-data.js"


class PipelineStageError(RuntimeError):
    def __init__(self, stage: str, original: Exception):
        self.stage = stage
        self.original = original
        super().__init__(f"{stage}: {original}")


def _stage_call(stage: str, function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except Exception as exc:
        raise PipelineStageError(stage, exc) from exc


def _trigger_type() -> str:
    explicit = os.getenv("SKILL_TRIGGER_TYPE", "").strip()
    if explicit:
        return explicit
    if os.getenv("GITHUB_ACTIONS", "").lower() == "true":
        try:
            if int(os.getenv("GITHUB_RUN_ATTEMPT", "1")) > 1:
                return "retry"
        except ValueError:
            pass
        return "schedule" if os.getenv("GITHUB_EVENT_NAME") == "schedule" else "manual"
    return "manual"


def current_website_date() -> str | None:
    """Read the already-published snapshot date without executing JavaScript."""
    try:
        content = WEBSITE_SNAPSHOT.read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r'"sampleDate"\s*:\s*"(\d{4}-\d{2}-\d{2})"', content)
    return match.group(1) if match else None


def _run_daily_pipeline_impl(
    date_string: str | None = None,
    *,
    skip_collect: bool = False,
    skip_push: bool = False,
    force_push: bool = False,
    run_id: str | None = None,
) -> dict:
    run_date = date_string or dt.datetime.now(TZ).date().isoformat()
    today = dt.datetime.now(TZ).date().isoformat()
    if not skip_collect and run_date != today:
        raise PipelineStageError(
            "input_validation",
            ValueError("指定历史日期时必须同时使用 --skip-collect，避免把今日采集写入错误日期"),
        )
    print(f"\nSHU SIGNAL 自动更新流程 · {run_date}")
    print("1/4 采集并写入飞书每日简报表")

    if skip_collect:
        written, failed = 0, 0
        print("  已按参数跳过采集")
    else:
        written, failed = _stage_call("collect", fetch_and_write, run_id=run_id)
        source_summary = get_source_summary(run_id)
        runtime_source_summary = collection_source_health()
        all_sources_failed = (
            source_summary["total_count"] > 0
            and source_summary["success_count"] == 0
        ) or (
            runtime_source_summary["total_count"] > 0
            and runtime_source_summary["success_count"] == 0
        )
        if all_sources_failed:
            raise PipelineStageError(
                "source_health",
                RuntimeError("全部RSS信息源抓取失败，已停止推送和网页快照更新"),
            )

    print("2/4 回读飞书当日完整数据")
    records = _stage_call("feishu_readback", get_today_records, run_date)
    record_count = len(records)
    print(f"  飞书当日表共 {record_count} 条")

    print("3/4 发送飞书群每日简报")
    pushed = 0
    push_status = "skipped"
    website_was_current = current_website_date() == run_date
    if not records:
        print("  当日无记录，跳过空简报")
        push_status = "skipped_empty"
    elif skip_push:
        print("  已按参数跳过群推送")
        push_status = "skipped_by_option"
    elif written > 0 or force_push or not website_was_current:
        # Webhook 异常会直接终止流程，因此不会提前刷新网站。
        pushed = _stage_call(
            "feishu_push", send_daily_report, run_date, run_id=run_id
        )
        push_status = "success"
    else:
        print("  当日网站快照已存在且本次无新增，避免重复发送")
        push_status = "skipped_duplicate_guard"

    print("4/4 生成网站当日数据快照")
    website_meta = _stage_call("web_snapshot", export, run_date)
    delivery_consistent = (
        website_meta["recordCount"] == record_count
        and website_meta.get("sampleDate", run_date) == run_date
    )
    if not delivery_consistent:
        raise PipelineStageError(
            "delivery_consistency",
            RuntimeError(
                "飞书回读与网页快照不一致："
                f"Feishu={record_count}, Web={website_meta['recordCount']}, "
                f"WebDate={website_meta.get('sampleDate')}"
            ),
        )

    result = {
        "date": run_date,
        "written": written,
        "failed": failed,
        "records": record_count,
        "pushed": pushed,
        "feishuPushStatus": push_status,
        "websiteRecords": website_meta["recordCount"],
        "websiteGeneratedAt": website_meta["generatedAt"],
        "webPublishStatus": "success",
        "websiteDate": website_meta.get("sampleDate", run_date),
        "deliveryConsistent": delivery_consistent,
        "status": "success",
        "runId": run_id,
    }
    return result


def run_daily_pipeline(
    date_string: str | None = None,
    *,
    skip_collect: bool = False,
    skip_push: bool = False,
    force_push: bool = False,
) -> dict:
    run_date = date_string or dt.datetime.now(TZ).date().isoformat()
    run_id = start_run(run_date, _trigger_type())
    try:
        result = _run_daily_pipeline_impl(
            date_string,
            skip_collect=skip_collect,
            skip_push=skip_push,
            force_push=force_push,
            run_id=run_id,
        )
    except Exception as exc:
        error_stage = getattr(exc, "stage", "daily_pipeline")
        original_error = getattr(exc, "original", exc)
        delivery_only_failure = error_stage in (
            "feishu_readback",
            "feishu_push",
            "web_snapshot",
            "delivery_consistency",
        )
        if error_stage == "feishu_push":
            failed_push_status = "failed"
        elif error_stage in ("collect", "source_health", "feishu_readback"):
            failed_push_status = "skipped_upstream_failure"
        elif error_stage == "input_validation":
            failed_push_status = "not_started"
        elif error_stage in ("web_snapshot", "delivery_consistency"):
            failed_push_status = "completed_before_failure"
        else:
            failed_push_status = None

        if error_stage == "delivery_consistency":
            failed_web_status = "inconsistent"
        elif error_stage == "web_snapshot":
            failed_web_status = "failed"
        elif error_stage in ("collect", "source_health", "feishu_readback", "feishu_push"):
            failed_web_status = "skipped_upstream_failure"
        elif error_stage == "input_validation":
            failed_web_status = "not_started"
        else:
            failed_web_status = None
        finish_run(
            run_id,
            status="partial_success" if delivery_only_failure else "failed",
            failed_count=1,
            feishu_push_status=failed_push_status,
            web_publish_status=failed_web_status,
            error_stage=error_stage,
            error=original_error,
        )
        print(format_run_summary(get_run(run_id)))
        publish_run_report(run_id)
        raise

    source_summary = get_source_summary(run_id)
    all_sources_failed = (
        not skip_collect
        and source_summary["total_count"] > 0
        and source_summary["success_count"] == 0
    )
    if all_sources_failed:
        status = "failed"
    elif result["failed"] or source_summary["failed_count"] or not result["deliveryConsistent"]:
        status = "partial_success"
    elif result["records"] == 0:
        status = "empty_success"
    else:
        status = "success"
    finish_run(
        run_id,
        status=status,
        failed_count=result["failed"],
        feishu_push_status=result["feishuPushStatus"],
        web_publish_status=result["webPublishStatus"],
    )
    result["status"] = status
    print("自动更新完成：" + json.dumps(result, ensure_ascii=False))
    result["monitoring"] = get_run(run_id)
    result["sourceMonitoring"] = source_summary
    print(format_run_summary(result["monitoring"]))
    result["monitoringReport"] = publish_run_report(run_id)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行采集、飞书推送和网站更新的完整链路")
    parser.add_argument("--date", help="指定北京时间日期，格式 YYYY-MM-DD")
    parser.add_argument("--skip-collect", action="store_true", help="不采集，只回读飞书并重建网站")
    parser.add_argument("--skip-push", action="store_true", help="不发送飞书群消息")
    parser.add_argument("--force-push", action="store_true", help="即使本次没有新增记录也重新推送")
    parser.add_argument("--result", type=Path, help="把无敏感信息的运行结果写入 JSON 文件")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = run_daily_pipeline(
            args.date,
            skip_collect=args.skip_collect,
            skip_push=args.skip_push,
            force_push=args.force_push,
        )
    except Exception as exc:
        print(f"[自动更新失败] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if args.result:
        args.result.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
