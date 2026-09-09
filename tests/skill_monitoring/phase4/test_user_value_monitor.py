import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo


ROOT_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT_DIR / "scripts"))

from monitoring.phase4 import user_value_monitor  # noqa: E402


SUMMARY = {
    "generatedAt": "2026-09-07T01:00:00Z",
    "days": 7,
    "metrics": {
        "visitSessions": 20,
        "weeklyUsefulIntelligence": 4,
        "usefulRate": 75.0,
        "libraryToDetailRate": 60.0,
        "detailToSourceRate": 30.0,
        "searchZeroResultRate": 10.0,
    },
    "channels": [{"channel": "feishu", "visitSessions": 12, "feedbackUsers": 3}],
    "badCases": [{"reviewId": "case-1"}],
    "alerts": [],
}


class UserValueMonitorTest(unittest.TestCase):
    def test_fetch_summary_validates_payload(self):
        response = mock.Mock()
        response.json.return_value = {"ok": True, "summary": SUMMARY}
        with mock.patch.object(user_value_monitor.requests, "get", return_value=response) as get:
            result = user_value_monitor.fetch_summary("https://example.com/base", 7)
        response.raise_for_status.assert_called_once()
        self.assertEqual(result["metrics"]["visitSessions"], 20)
        self.assertEqual(get.call_args.kwargs["params"], {"days": 7})

    def test_auto_notification_runs_on_monday_or_alert(self):
        monday = dt.datetime(2026, 9, 7, 9, tzinfo=ZoneInfo("Asia/Shanghai"))
        tuesday = monday + dt.timedelta(days=1)
        self.assertTrue(user_value_monitor.should_notify("auto", SUMMARY, monday))
        self.assertFalse(user_value_monitor.should_notify("auto", SUMMARY, tuesday))
        alerted = {**SUMMARY, "alerts": [{"level": "P1"}]}
        self.assertTrue(user_value_monitor.should_notify("auto", alerted, tuesday))

    def test_report_is_saved_and_optional_webhook_is_sent(self):
        response = mock.Mock()
        response.json.return_value = {"code": 0}
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.object(
            user_value_monitor, "fetch_summary", return_value=SUMMARY
        ), mock.patch.object(
            user_value_monitor.requests, "post", return_value=response
        ) as post:
            result = user_value_monitor.publish_user_value_report(
                "https://example.com",
                notification_mode="always",
                report_dir=Path(temp_dir),
                webhook_url="https://open.feishu.cn/webhook/test",
            )
            markdown = (Path(temp_dir) / "latest_user_value.md").read_text(encoding="utf-8")
            self.assertIn("每周有效情报：4", markdown)
            self.assertEqual(result["webhookStatus"], "success")
            post.assert_called_once()


if __name__ == "__main__":
    unittest.main()
