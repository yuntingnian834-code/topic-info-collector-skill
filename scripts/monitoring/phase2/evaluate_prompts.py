"""Run a fixed, synthetic regression set against the active extraction prompt.

This command calls the configured model unless ``summarizer`` is injected by a
test. It stores labels and outcomes, but never stores production article bodies
or prompt bodies in the monitoring database.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[2]
ROOT_DIR = Path(__file__).resolve().parents[3]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from monitoring.phase1.runtime_monitor import (  # noqa: E402
    article_id_for_url,
    finish_run,
    start_run,
)
from monitoring.phase2.model_monitor import (  # noqa: E402
    PROMPT_VERSIONS,
    finish_evaluation_run,
    get_evaluation_run,
    record_evaluation_result,
    start_evaluation_run,
)


DEFAULT_DATASET_PATH = Path(__file__).with_name("evaluation_cases.json")


def load_evaluation_cases(path: str | Path | None = None) -> list[dict]:
    dataset_path = Path(path) if path else DEFAULT_DATASET_PATH
    cases = json.loads(dataset_path.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases:
        raise ValueError("评测集必须是非空JSON数组")
    required = {"case_id", "level2", "research_core", "content", "expected_relevant"}
    case_ids = []
    for case in cases:
        if not isinstance(case, dict) or not required.issubset(case):
            raise ValueError(f"评测用例缺少字段：{sorted(required)}")
        case_ids.append(str(case["case_id"]))
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("评测集case_id必须唯一")
    return cases


def _event_date_text(value) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def run_evaluation(
    *,
    dataset_path: str | Path | None = None,
    summarizer=None,
    model_name: str | None = None,
    prompt_version: str | None = None,
) -> dict:
    """Evaluate relevance, event-date extraction, and required keyword retention."""
    cases = load_evaluation_cases(dataset_path)
    uses_active_summarizer = summarizer is None
    if uses_active_summarizer:
        from runtime.fetch_data import summarize_v2

        summarizer = summarize_v2

    model = model_name or os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    active_version = PROMPT_VERSIONS["relevance_extract"]
    version = prompt_version or active_version
    if uses_active_summarizer and version != active_version:
        raise ValueError(
            f"只能评测当前代码中的Prompt版本 {active_version}，避免结果被错误标记"
        )
    run_date = dt.date.today().isoformat()
    run_id = start_run(run_date, "evaluation", environment="evaluation")
    eval_run_id = start_evaluation_run(
        run_id=run_id,
        prompt_version=version,
        model_name=model,
    )

    passed_count = 0
    for case in cases:
        article_id = article_id_for_url(f"evaluation://{case['case_id']}")
        expected_relevant = bool(case["expected_relevant"])
        expected_date = case.get("expected_event_date")
        expected_keyword = case.get("expected_keyword")
        actual_relevant = False
        actual_date = None
        keyword_matched = None
        error = None
        try:
            title, summary, event_date, _metadata = summarizer(
                case["content"],
                case["level2"],
                case["research_core"],
                "",
                run_id=run_id,
                article_id=article_id,
            )
            actual_relevant = title != "__NOT_RELEVANT__"
            actual_date = _event_date_text(event_date)
            if expected_keyword:
                keyword_matched = str(expected_keyword).casefold() in (
                    f"{title}\n{summary}".casefold()
                )
        except Exception as exc:
            error = exc

        passed = error is None and actual_relevant == expected_relevant
        if passed and expected_relevant:
            if expected_date:
                passed = actual_date == expected_date
            if passed and expected_keyword:
                passed = bool(keyword_matched)
        passed_count += int(passed)
        record_evaluation_result(
            eval_run_id=eval_run_id,
            case_id=case["case_id"],
            article_id=article_id,
            expected_relevant=expected_relevant,
            actual_relevant=actual_relevant,
            expected_event_date=expected_date,
            actual_event_date=actual_date,
            expected_keyword=expected_keyword,
            keyword_matched=keyword_matched,
            passed=passed,
            error=error,
        )

    status = "success" if passed_count == len(cases) else "regression"
    finish_evaluation_run(
        eval_run_id,
        status=status,
        total_cases=len(cases),
        passed_cases=passed_count,
    )
    finish_run(
        run_id,
        status="success" if status == "success" else "partial_success",
        failed_count=len(cases) - passed_count,
    )
    report = get_evaluation_run(eval_run_id)
    report["run_id"] = run_id
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="运行模型Prompt固定回归评测")
    parser.add_argument("--dataset", help="自定义评测集JSON路径")
    args = parser.parse_args()
    report = run_evaluation(dataset_path=args.dataset)
    print(
        f"评测 {report.get('eval_run_id')}｜{report.get('passed_cases', 0)}/"
        f"{report.get('total_cases', 0)} 通过｜准确率 {report.get('accuracy')}%"
    )
    failed = [item for item in report.get("results", []) if not item.get("passed")]
    if failed:
        print("未通过用例：" + "、".join(item["case_id"] for item in failed))
    return 0 if report.get("status") == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
