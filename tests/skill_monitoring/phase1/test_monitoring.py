import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT_DIR / "scripts"))

import monitoring  # noqa: E402


class MonitoringTest(unittest.TestCase):
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
            },
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp_dir.cleanup()

    def test_funnel_is_rebuilt_from_distinct_article_events(self):
        run_id = monitoring.start_run("2026-09-07", "manual_test")
        urls = [f"https://example.com/article/{index}" for index in range(4)]
        article_ids = [monitoring.article_id_for_url(url) for url in urls]

        for article_id in article_ids:
            monitoring.track_article_event(
                run_id, article_id, "article_collected", status="collected"
            )
        # A duplicate event must not inflate the funnel count.
        monitoring.track_article_event(
            run_id, article_ids[0], "article_collected", status="collected"
        )
        for article_id in article_ids[:3]:
            monitoring.track_article_event(
                run_id, article_id, "article_time_passed", status="passed"
            )
        monitoring.track_article_event(
            run_id,
            article_ids[3],
            "article_time_blocked",
            status="blocked",
            reason_code="before_cutoff",
        )
        for article_id in article_ids[:2]:
            monitoring.track_article_event(
                run_id, article_id, "content_parse_completed", status="success"
            )
        monitoring.track_article_event(
            run_id,
            article_ids[2],
            "content_parse_failed",
            status="failed",
            reason_code="content_too_short",
        )
        monitoring.track_article_event(
            run_id, article_ids[0], "relevance_passed", status="passed"
        )
        monitoring.track_article_event(
            run_id,
            article_ids[1],
            "relevance_blocked",
            status="blocked",
            reason_code="llm_not_relevant",
        )
        for event_name in ("event_date_passed", "dedupe_passed", "record_written"):
            monitoring.track_article_event(
                run_id, article_ids[0], event_name, status="success"
            )

        # Two feeds on one domain must remain two independently monitored sources.
        monitoring.record_source_run(
            run_id,
            "https://news.example.com/rss/topic-a",
            status="success",
            entry_count=3,
            fresh_entry_count=2,
            parse_success_count=2,
        )
        monitoring.record_source_run(
            run_id,
            "https://news.example.com/rss/topic-b",
            status="failed",
            failure_reason="timeout",
        )

        monitoring.finish_run(
            run_id,
            status="partial_success",
            feishu_push_status="success",
            web_publish_status="success",
        )
        run = monitoring.get_run(run_id)
        self.assertEqual(run["environment"], "test")
        self.assertEqual(run["candidate_count"], 4)
        self.assertEqual(run["time_pass_count"], 3)
        self.assertEqual(run["parse_success_count"], 2)
        self.assertEqual(run["relevance_pass_count"], 1)
        self.assertEqual(run["event_date_pass_count"], 1)
        self.assertEqual(run["dedupe_pass_count"], 1)
        self.assertEqual(run["written_count"], 1)
        self.assertEqual(run["failed_count"], 1)

        sources = monitoring.get_source_summary(run_id)
        self.assertEqual(sources["total_count"], 2)
        self.assertEqual(sources["success_count"], 1)
        self.assertEqual(sources["failed_count"], 1)

        report = monitoring.build_run_report(run_id)
        self.assertTrue(report["checks"]["summaryMatchesEvents"])
        self.assertEqual(report["losses"]["time_blocked"], 1)
        self.assertEqual(report["losses"]["parse_failed"], 1)
        self.assertEqual(report["losses"]["relevance_blocked"], 1)

        published = monitoring.publish_run_report(run_id)
        self.assertEqual(published["status"], "success")
        self.assertEqual(published["webhookStatus"], "not_configured")
        self.assertTrue(Path(published["jsonPath"]).is_file())
        self.assertTrue(Path(published["markdownPath"]).is_file())

    def test_monitoring_storage_failure_never_raises(self):
        bad_path = Path(self.temp_dir.name) / "database-is-a-directory"
        bad_path.mkdir()
        with mock.patch.dict(os.environ, {"MONITORING_DB_PATH": str(bad_path)}):
            run_id = monitoring.start_run("2026-09-07", "manual_test")
            monitoring.track_article_event(run_id, "article", "article_collected")
            monitoring.record_source_run(
                run_id, "https://example.com/rss", status="failed"
            )
            monitoring.finish_run(run_id, status="failed")
            self.assertIsNone(monitoring.get_run(run_id))

    def test_url_ids_ignore_tracking_parameters_and_fragments(self):
        canonical = "https://Example.com/news/item?a=1&b=2"
        tracked = "https://example.com/news/item/?b=2&utm_source=feed&a=1#section"
        self.assertEqual(
            monitoring.article_id_for_url(canonical),
            monitoring.article_id_for_url(tracked),
        )


if __name__ == "__main__":
    unittest.main()
