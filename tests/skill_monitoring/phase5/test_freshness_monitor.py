import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo


ROOT_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT_DIR / "scripts"))

from monitoring.phase5 import freshness_monitor  # noqa: E402


NOW = dt.datetime(2026, 9, 8, 9, tzinfo=ZoneInfo("Asia/Shanghai"))
PAYLOAD = {
    "sampleDate": "2026-09-08",
    "records": [{"id": "intel-1"}, {"id": "intel-2"}],
    "meta": {
        "freshness": "fresh",
        "source": "D1 Daily Snapshot",
        "generatedAt": "2026-09-08T00:15:00Z",
    },
}


class FreshnessMonitorTest(unittest.TestCase):
    def test_fetch_parses_javascript_payload_and_refreshes(self):
        response = mock.Mock()
        response.text = f"window.SHU_SIGNAL_DATA = {json.dumps(PAYLOAD)};\n"
        with mock.patch.object(freshness_monitor.requests, "get", return_value=response) as get:
            result = freshness_monitor.fetch_site_snapshot("https://example.com", refresh=True)
        response.raise_for_status.assert_called_once()
        self.assertEqual(result["sampleDate"], "2026-09-08")
        self.assertEqual(get.call_args.kwargs["params"], {"refresh": "1"})

    def test_matching_date_and_count_are_healthy(self):
        result = freshness_monitor.assess_freshness(
            PAYLOAD,
            expected_date="2026-09-08",
            expected_count=2,
            now=NOW,
        )
        self.assertEqual(result["status"], "healthy")
        self.assertEqual(result["alerts"], [])

    def test_unknown_freshness_is_alerted(self):
        payload = {**PAYLOAD, "meta": {**PAYLOAD["meta"], "freshness": "unknown"}}
        result = freshness_monitor.assess_freshness(
            payload,
            expected_date="2026-09-08",
            expected_count=2,
            now=NOW,
        )
        self.assertEqual(result["status"], "alert")
        self.assertEqual(result["alerts"][0]["code"], "site_freshness_unknown")
        self.assertEqual(result["alerts"][0]["level"], "P1")

    def test_stale_date_and_count_mismatch_raise_alerts(self):
        payload = {**PAYLOAD, "sampleDate": "2026-09-07", "meta": {**PAYLOAD["meta"], "freshness": "stale"}}
        result = freshness_monitor.assess_freshness(
            payload,
            expected_date="2026-09-08",
            expected_count=30,
            now=NOW,
        )
        self.assertEqual([item["code"] for item in result["alerts"]], [
            "site_snapshot_stale",
            "site_record_count_mismatch",
        ])

    def test_empty_snapshot_after_eight_is_alerted(self):
        payload = {**PAYLOAD, "records": []}
        result = freshness_monitor.assess_freshness(
            payload,
            expected_date="2026-09-08",
            now=NOW,
        )
        self.assertEqual(result["alerts"][0]["code"], "site_daily_snapshot_empty")

    def test_run_writes_report_and_sends_alert_card(self):
        stale = {**PAYLOAD, "sampleDate": "2026-09-07", "meta": {**PAYLOAD["meta"], "freshness": "stale"}}
        response = mock.Mock()
        response.json.return_value = {"code": 0}
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.object(
            freshness_monitor, "fetch_site_snapshot", return_value=stale
        ), mock.patch.object(
            freshness_monitor.requests, "post", return_value=response
        ) as post:
            result = freshness_monitor.run_freshness_monitor(
                "https://example.com",
                expected_date="2026-09-08",
                report_dir=Path(temp_dir),
                webhook_url="https://open.feishu.cn/webhook/test",
                now=NOW,
            )
            self.assertTrue((Path(temp_dir) / "latest_freshness.md").exists())
            self.assertEqual(result["webhookStatus"], "success")
            post.assert_called_once()


if __name__ == "__main__":
    unittest.main()
