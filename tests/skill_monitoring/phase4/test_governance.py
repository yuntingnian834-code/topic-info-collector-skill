import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT_DIR = Path(__file__).resolve().parents[3]
import sys

sys.path.insert(0, str(ROOT_DIR / "scripts"))

from monitoring.phase1 import runtime_monitor  # noqa: E402
from monitoring.phase4 import governance  # noqa: E402


class GovernanceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        temp_path = Path(self.temp_dir.name)
        self.env = mock.patch.dict(
            os.environ,
            {
                "MONITORING_DB_PATH": str(temp_path / "monitoring.sqlite3"),
                "MONITORING_REPORT_DIR": str(temp_path / "reports"),
                "MONITORING_FEISHU_WEBHOOK_URL": "",
                "SKILL_ENVIRONMENT": "production",
            },
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp_dir.cleanup()

    def test_monday_review_queue_samples_retained_and_blocked_idempotently(self):
        run_id = runtime_monitor.start_run("2026-09-07", "schedule")
        retained = runtime_monitor.article_id_for_url("https://example.com/retained")
        blocked = runtime_monitor.article_id_for_url("https://example.com/blocked")
        runtime_monitor.track_article_event(
            run_id,
            retained,
            "article_collected",
            status="collected",
            properties={"title": "保留样本"},
        )
        for event_name in (
            "article_time_passed",
            "content_parse_completed",
            "relevance_passed",
            "event_date_passed",
            "dedupe_passed",
            "record_written",
        ):
            runtime_monitor.track_article_event(run_id, retained, event_name, status="success")
        runtime_monitor.track_article_event(
            run_id,
            blocked,
            "article_collected",
            status="collected",
            properties={"title": "拦截样本"},
        )
        runtime_monitor.track_article_event(
            run_id,
            blocked,
            "relevance_blocked",
            status="blocked",
            reason_code="unrelated",
        )
        runtime_monitor.finish_run(run_id, status="success")

        first = runtime_monitor.build_run_report(run_id)["governance"]
        second = runtime_monitor.build_run_report(run_id)["governance"]
        self.assertEqual(first["reviewQueueCreated"], {"retained": 1, "blocked": 1})
        self.assertEqual(second["reviewQueueCreated"], {"retained": 0, "blocked": 0})
        queue = governance.get_review_queue("pending")
        self.assertEqual({item["sample_type"] for item in queue}, {"retained", "blocked"})
        self.assertEqual({item["article_title"] for item in queue}, {"保留样本", "拦截样本"})
        self.assertTrue(
            governance.update_review(
                queue[0]["review_id"],
                status="reviewed",
                expected_relevance=True,
                metrics_correct=True,
                summary_faithful=True,
                reviewer="tester",
            )
        )

    def test_failed_run_generates_p0_and_source_alerts(self):
        run_id = runtime_monitor.start_run("2026-09-08", "schedule")
        for index in range(3):
            runtime_monitor.record_source_run(
                run_id,
                f"https://example.com/feed-{index}",
                status="failed",
                failure_reason="timeout",
            )
        runtime_monitor.finish_run(run_id, status="failed", error_stage="source_health")
        governance_result = runtime_monitor.build_run_report(run_id)["governance"]
        rules = {item["ruleKey"] for item in governance_result["alerts"]}
        self.assertIn("run_failed", rules)
        self.assertIn("all_sources_failed", rules)
        self.assertIn("source_failure_ratio", rules)
        self.assertEqual(governance_result["alertCounts"]["P0"], 2)

    def test_candidate_volume_uses_three_run_baseline(self):
        for day in ("2026-09-01", "2026-09-02", "2026-09-03"):
            run_id = runtime_monitor.start_run(day, "schedule")
            for index in range(10):
                runtime_monitor.track_article_event(
                    run_id,
                    f"{day}-{index}",
                    "article_collected",
                    status="collected",
                )
            runtime_monitor.finish_run(run_id, status="success")

        current_id = runtime_monitor.start_run("2026-09-07", "schedule")
        for index in range(2):
            runtime_monitor.track_article_event(
                current_id,
                f"current-{index}",
                "article_collected",
                status="collected",
            )
        runtime_monitor.finish_run(current_id, status="empty_success")
        rules = {
            item["ruleKey"]
            for item in runtime_monitor.build_run_report(current_id)["governance"]["alerts"]
        }
        self.assertIn("candidate_volume_low", rules)

    def test_governance_storage_failure_never_raises(self):
        bad_path = Path(self.temp_dir.name) / "database-is-a-directory"
        bad_path.mkdir()
        with mock.patch.dict(os.environ, {"MONITORING_DB_PATH": str(bad_path)}):
            result = governance.apply_governance({"run": {"run_id": "missing"}})
        self.assertEqual(result["alerts"], [])
        self.assertEqual(governance.get_review_queue(), [])


if __name__ == "__main__":
    unittest.main()
