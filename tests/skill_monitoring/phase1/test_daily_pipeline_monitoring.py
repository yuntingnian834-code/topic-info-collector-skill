import datetime as dt
import importlib.util
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo


ROOT_DIR = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT_DIR / "scripts"
SCRIPT_DIR = SCRIPTS_DIR / "runtime"
sys.path.insert(0, str(SCRIPTS_DIR))

import monitoring  # noqa: E402


def load_daily_pipeline_module():
    dependency_stubs = {
        "export_web_snapshot": types.SimpleNamespace(export=lambda _date: {}),
        "feishu": types.SimpleNamespace(get_today_records=lambda _date: []),
        "fetch_data": types.SimpleNamespace(
            fetch_and_write=lambda **_kwargs: (0, 0),
            collection_source_health=lambda: {
                "total_count": 0,
                "success_count": 0,
                "failed_count": 0,
            },
        ),
        "reporter": types.SimpleNamespace(send_daily_report=lambda _date: 0),
    }
    spec = importlib.util.spec_from_file_location(
        "daily_pipeline_under_test", SCRIPT_DIR / "daily_pipeline.py"
    )
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, dependency_stubs):
        spec.loader.exec_module(module)
    return module


pipeline = load_daily_pipeline_module()


class DailyPipelineMonitoringTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        temp_path = Path(self.temp_dir.name)
        self.env = mock.patch.dict(
            os.environ,
            {
                "MONITORING_DB_PATH": str(temp_path / "monitoring.sqlite3"),
                "MONITORING_REPORT_DIR": str(temp_path / "reports"),
                "MONITORING_FEISHU_WEBHOOK_URL": "",
                "SKILL_ENVIRONMENT": "test",
                "SKILL_TRIGGER_TYPE": "manual_test_pipeline",
            },
        )
        self.env.start()
        self.today = dt.datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()

    def tearDown(self):
        self.env.stop()
        self.temp_dir.cleanup()

    @staticmethod
    def _successful_fetch(*, run_id):
        article_id = monitoring.article_id_for_url("https://example.com/article")
        for event_name in (
            "article_collected",
            "article_time_passed",
            "content_parse_completed",
            "relevance_passed",
            "event_date_passed",
            "dedupe_passed",
            "record_written",
        ):
            monitoring.track_article_event(run_id, article_id, event_name, status="success")
        monitoring.record_source_run(
            run_id,
            "https://example.com/rss/topic",
            status="success",
            entry_count=1,
            fresh_entry_count=1,
            parse_success_count=1,
        )
        return 1, 0

    def test_complete_pipeline_finishes_one_successful_monitored_run(self):
        with mock.patch.object(
            pipeline, "fetch_and_write", side_effect=self._successful_fetch
        ), mock.patch.object(
            pipeline, "get_today_records", return_value=[{"id": "row"}]
        ), mock.patch.object(
            pipeline, "send_daily_report", return_value=1
        ), mock.patch.object(
                pipeline,
                "export",
                return_value={
                    "recordCount": 1,
                    "generatedAt": "2026-09-07T05:00:00+08:00",
                    "sampleDate": self.today,
                },
        ), mock.patch.object(
            pipeline, "current_website_date", return_value=None
        ):
            result = pipeline.run_daily_pipeline(self.today)

        self.assertEqual(result["status"], "success")
        self.assertTrue(result["deliveryConsistent"])
        self.assertEqual(result["monitoring"]["written_count"], 1)
        self.assertEqual(result["monitoring"]["trigger_type"], "manual_test_pipeline")
        self.assertEqual(result["monitoringReport"]["status"], "success")

    def test_all_source_failures_stop_delivery_and_are_not_empty_success(self):
        readback = mock.Mock(return_value=[])

        def failed_fetch(*, run_id):
            monitoring.record_source_run(
                run_id,
                "https://example.com/rss/topic",
                status="failed",
                failure_reason="timeout",
            )
            return 0, 0

        with mock.patch.object(
            pipeline, "fetch_and_write", side_effect=failed_fetch
        ), mock.patch.object(pipeline, "get_today_records", readback):
            with self.assertRaises(pipeline.PipelineStageError):
                pipeline.run_daily_pipeline(self.today)

        readback.assert_not_called()
        run = monitoring.latest_run()
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["error_stage"], "source_health")
        self.assertEqual(run["feishu_push_status"], "skipped_upstream_failure")
        self.assertEqual(run["web_publish_status"], "skipped_upstream_failure")

    def test_delivery_mismatch_is_recorded_as_partial_success(self):
        with mock.patch.object(
            pipeline, "get_today_records", return_value=[{"id": "row"}]
        ), mock.patch.object(
                pipeline,
                "export",
                return_value={
                    "recordCount": 0,
                    "generatedAt": "2026-09-07T05:00:00+08:00",
                    "sampleDate": self.today,
                },
        ):
            with self.assertRaises(pipeline.PipelineStageError):
                pipeline.run_daily_pipeline(
                    self.today,
                    skip_collect=True,
                    skip_push=True,
                )

        run = monitoring.latest_run()
        self.assertEqual(run["status"], "partial_success")
        self.assertEqual(run["error_stage"], "delivery_consistency")
        self.assertEqual(run["web_publish_status"], "inconsistent")


if __name__ == "__main__":
    unittest.main()
