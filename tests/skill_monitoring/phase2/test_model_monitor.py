import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT_DIR / "scripts"))

from monitoring.phase1 import runtime_monitor  # noqa: E402
from monitoring.phase2 import model_monitor  # noqa: E402


class ModelMonitoringTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        temp_path = Path(self.temp_dir.name)
        self.db_path = temp_path / "monitoring.sqlite3"
        self.report_dir = temp_path / "reports"
        self.env = mock.patch.dict(
            os.environ,
            {
                "MONITORING_DB_PATH": str(self.db_path),
                "MONITORING_REPORT_DIR": str(self.report_dir),
                "MONITORING_FEISHU_WEBHOOK_URL": "",
                "MODEL_INPUT_COST_PER_MILLION": "1.0",
                "MODEL_OUTPUT_COST_PER_MILLION": "2.0",
                "SKILL_ENVIRONMENT": "test",
            },
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp_dir.cleanup()

    def test_model_quality_summary_and_prompt_comparison(self):
        run_id = runtime_monitor.start_run("2026-09-07", "manual_test")
        article_a = runtime_monitor.article_id_for_url("https://example.com/a")
        article_b = runtime_monitor.article_id_for_url("https://example.com/b")

        runtime_monitor.track_article_event(
            run_id,
            article_a,
            "event_date_passed",
            status="passed",
            properties={"event_date": "2026-09-07"},
        )
        runtime_monitor.track_article_event(
            run_id,
            article_b,
            "event_date_passed",
            status="passed",
            properties={"event_date": None},
        )
        runtime_monitor.track_article_event(
            run_id, article_a, "record_written", status="success"
        )

        model_monitor.record_model_call(
            run_id=run_id,
            article_id=article_a,
            call_type="relevance_extract",
            model_name="deepseek-chat",
            prompt_version=model_monitor.PROMPT_VERSIONS["relevance_extract"],
            result_status="success",
            input_tokens=100,
            output_tokens=20,
            latency_ms=100,
            json_valid=True,
        )
        model_monitor.record_model_call(
            run_id=run_id,
            article_id=article_b,
            call_type="relevance_extract",
            model_name="deepseek-chat",
            prompt_version=model_monitor.PROMPT_VERSIONS["relevance_extract"],
            result_status="invalid_output",
            input_tokens=80,
            output_tokens=10,
            latency_ms=200,
            json_valid=False,
        )
        model_monitor.record_model_call(
            run_id=run_id,
            article_id=article_b,
            call_type="json_repair",
            model_name="deepseek-chat",
            prompt_version=model_monitor.PROMPT_VERSIONS["json_repair"],
            result_status="success",
            input_tokens=20,
            output_tokens=10,
            latency_ms=300,
            json_valid=True,
            retry_count=1,
        )
        model_monitor.record_model_call(
            run_id=run_id,
            article_id=article_a,
            call_type="summary_fallback",
            model_name="deepseek-chat",
            prompt_version=model_monitor.PROMPT_VERSIONS["summary_fallback"],
            result_status="success",
            input_tokens=50,
            output_tokens=10,
            latency_ms=400,
            fallback_used=True,
        )
        model_monitor.record_model_call(
            run_id=run_id,
            article_id=None,
            call_type="daily_summary",
            model_name="deepseek-chat",
            prompt_version=model_monitor.PROMPT_VERSIONS["daily_summary"],
            result_status="failed",
            latency_ms=500,
            error=RuntimeError("temporary failure"),
        )
        runtime_monitor.finish_run(run_id, status="partial_success")

        summary = model_monitor.get_model_summary(run_id)
        self.assertEqual(summary["total_calls"], 5)
        self.assertEqual(summary["model_success_rate"], 80.0)
        self.assertEqual(summary["valid_result_calls"], 3)
        self.assertEqual(summary["invalid_output_calls"], 1)
        self.assertEqual(summary["first_pass_json_valid_rate"], 50.0)
        self.assertEqual(summary["json_repair_count"], 1)
        self.assertEqual(summary["json_repair_rate"], 50.0)
        self.assertEqual(summary["fallback_count"], 1)
        self.assertEqual(summary["fallback_rate"], 50.0)
        self.assertEqual(summary["event_date_extraction_rate"], 50.0)
        self.assertEqual(summary["total_tokens"], 300)
        self.assertEqual(summary["tokens_per_written_record"], 300.0)
        self.assertEqual(summary["average_latency_ms"], 300.0)
        self.assertEqual(summary["p95_latency_ms"], 500)
        self.assertEqual(summary["estimated_cost_usd"], 0.00035)
        self.assertEqual(len(summary["prompt_breakdown"]), 4)

        comparison = model_monitor.get_prompt_comparison(days=7)
        self.assertEqual(len(comparison), 4)
        self.assertEqual(sum(item["call_count"] for item in comparison), 5)

        evaluation_run_id = runtime_monitor.start_run(
            "2026-09-07", "evaluation", environment="evaluation"
        )
        model_monitor.record_model_call(
            run_id=evaluation_run_id,
            article_id="evaluation-case",
            call_type="relevance_extract",
            model_name="deepseek-chat",
            prompt_version=model_monitor.PROMPT_VERSIONS["relevance_extract"],
            result_status="success",
            json_valid=True,
        )
        production_only = model_monitor.get_prompt_comparison(days=7)
        with_evaluation = model_monitor.get_prompt_comparison(
            days=7, include_evaluation=True
        )
        self.assertEqual(sum(item["call_count"] for item in production_only), 5)
        self.assertEqual(sum(item["call_count"] for item in with_evaluation), 6)

        with sqlite3.connect(self.db_path) as conn:
            prompt_count = conn.execute("SELECT COUNT(*) FROM prompt_versions").fetchone()[0]
        self.assertEqual(prompt_count, 4)

        report = runtime_monitor.build_run_report(run_id)
        self.assertEqual(report["modelQuality"]["total_calls"], 5)
        published = runtime_monitor.publish_run_report(run_id)
        report_text = Path(published["markdownPath"]).read_text(encoding="utf-8")
        self.assertIn("模型与Prompt质量", report_text)

    def test_update_model_call_records_json_outcome(self):
        run_id = runtime_monitor.start_run("2026-09-07", "manual_test")
        call_id = model_monitor.record_model_call(
            run_id=run_id,
            article_id="article",
            call_type="relevance_extract",
            model_name="deepseek-chat",
            prompt_version=model_monitor.PROMPT_VERSIONS["relevance_extract"],
            result_status="success",
        )
        model_monitor.update_model_call(
            call_id, json_valid=False, result_status="invalid_output"
        )
        summary = model_monitor.get_model_summary(run_id)
        self.assertEqual(summary["first_pass_json_valid_rate"], 0.0)
        self.assertEqual(summary["failed_calls"], 0)
        self.assertEqual(summary["invalid_output_calls"], 1)

    def test_model_monitoring_failure_never_raises(self):
        bad_path = Path(self.temp_dir.name) / "database-is-a-directory"
        bad_path.mkdir()
        with mock.patch.dict(os.environ, {"MONITORING_DB_PATH": str(bad_path)}):
            call_id = model_monitor.record_model_call(
                run_id="run",
                article_id="article",
                call_type="relevance_extract",
                model_name="deepseek-chat",
                prompt_version=model_monitor.PROMPT_VERSIONS["relevance_extract"],
                result_status="failed",
                error=RuntimeError("failure"),
            )
            model_monitor.update_model_call(call_id, json_valid=False)
            self.assertEqual(model_monitor.get_model_summary("run")["total_calls"], 0)


if __name__ == "__main__":
    unittest.main()
