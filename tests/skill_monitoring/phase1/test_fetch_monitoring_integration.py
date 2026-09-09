import importlib.util
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


ROOT_DIR = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT_DIR / "scripts"
SCRIPT_DIR = SCRIPTS_DIR / "runtime"
sys.path.insert(0, str(SCRIPTS_DIR))

import monitoring  # noqa: E402


def load_fetch_module():
    dependency_stubs = {
        "feedparser": types.SimpleNamespace(parse=lambda _text: {"entries": []}),
        "trafilatura": types.SimpleNamespace(extract=lambda *_args, **_kwargs: ""),
        "dotenv": types.SimpleNamespace(load_dotenv=lambda: None),
        "feishu": types.SimpleNamespace(
            get_info_points=lambda: [],
            append_daily_record=lambda _fields: "record-id",
            get_today_records=lambda _date: [],
        ),
    }
    spec = importlib.util.spec_from_file_location(
        "fetch_data_under_test", SCRIPT_DIR / "fetch_data.py"
    )
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(
        os.environ,
        {
            "DEEPSEEK_API_KEY": "",
            "BAIDU_TRANSLATE_APPID": "",
            "BAIDU_TRANSLATE_KEY": "",
        },
    ), mock.patch.dict(sys.modules, dependency_stubs):
        spec.loader.exec_module(module)
    return module


fetch_data = load_fetch_module()


class FetchMonitoringIntegrationTest(unittest.TestCase):
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

    def test_relevance_rejection_reason_is_persisted(self):
        run_id = monitoring.start_run(fetch_data.TODAY_STR, "manual_test")
        with mock.patch.object(
            fetch_data,
            "summarize_v2",
            return_value=(
                "__NOT_RELEVANT__",
                "",
                None,
                {
                    "relevance_status": "blocked",
                    "rejection_code": "llm_not_relevant",
                    "rejection_reason": "体育赛事-赞助新闻",
                },
            ),
        ):
            record = fetch_data._build_record(
                "宏观政策",
                "商品供需",
                "研究核心",
                "足够长的测试正文" * 20,
                "https://example.com/rejected",
                "example.com",
                fetch_data.TODAY,
                run_id=run_id,
                source_id="feed-source",
            )

        self.assertIsNone(record)
        reasons = monitoring.get_blocked_reasons(run_id)
        self.assertEqual(reasons[0]["event_name"], "relevance_blocked")
        self.assertEqual(reasons[0]["reason_code"], "llm_not_relevant")

    def test_internal_monitoring_field_is_removed_before_feishu_write(self):
        run_id = monitoring.start_run(fetch_data.TODAY_STR, "manual_test")
        captured_fields = []
        proposed_record = {
            fetch_data.FIELD_COLLECT_TIME: fetch_data.TODAY_MS,
            fetch_data.FIELD_LEVEL1: "市场基本面",
            fetch_data.FIELD_LEVEL2: "铜库存",
            fetch_data.FIELD_RESEARCH_CORE: "库存变化",
            fetch_data.FIELD_TITLE: "LME铜最新动态",
            fetch_data.FIELD_SUMMARY: "关键指标：库存下降",
            fetch_data.FIELD_SOURCE_URL: "https://example.com/copper",
            fetch_data.FIELD_SITE_NAME: "example.com",
            fetch_data._MONITOR_SOURCE_ID: "feed-source",
        }

        def append_record(fields):
            captured_fields.append(dict(fields))
            return "record-id"

        with mock.patch.object(
            fetch_data,
            "get_info_points",
            return_value=[
                {
                    "level1": "市场基本面",
                    "level2": "铜库存",
                    "research_core": "库存变化",
                    "source_urls": ["https://example.com/rss"],
                }
            ],
        ), mock.patch.object(
            fetch_data, "_load_existing_fingerprints", return_value=(set(), set())
        ), mock.patch.object(
            fetch_data, "_collect_one", return_value=[dict(proposed_record)]
        ), mock.patch.object(
            fetch_data, "append_daily_record", side_effect=append_record
        ):
            written, failed = fetch_data.fetch_and_write(run_id=run_id)

        self.assertEqual((written, failed), (1, 0))
        self.assertEqual(len(captured_fields), 1)
        self.assertNotIn(fetch_data._MONITOR_SOURCE_ID, captured_fields[0])
        self.assertEqual(monitoring.run_funnel(run_id)["written_count"], 1)

    def test_writes_all_valid_records_without_daily_cap(self):
        run_id = monitoring.start_run(fetch_data.TODAY_STR, "manual_test")
        proposed_records = []
        for index in range(35):
            proposed_records.append(
                {
                    fetch_data.FIELD_COLLECT_TIME: fetch_data.TODAY_MS,
                    fetch_data.FIELD_LEVEL1: "市场基本面",
                    fetch_data.FIELD_LEVEL2: "铜库存",
                    fetch_data.FIELD_RESEARCH_CORE: "库存变化",
                    fetch_data.FIELD_TITLE: f"铜情报{index}",
                    fetch_data.FIELD_SUMMARY: f"关键指标：库存变化{index}",
                    fetch_data.FIELD_SOURCE_URL: f"https://example.com/copper-{index}",
                    fetch_data.FIELD_SITE_NAME: "example.com",
                    fetch_data._MONITOR_SOURCE_ID: "feed-source",
                }
            )

        with mock.patch.object(
            fetch_data,
            "get_info_points",
            return_value=[
                {
                    "level1": "市场基本面",
                    "level2": "铜库存",
                    "research_core": "库存变化",
                    "source_urls": ["https://example.com/rss"],
                }
            ],
        ), mock.patch.object(
            fetch_data, "_load_existing_fingerprints", return_value=(set(), set())
        ), mock.patch.object(
            fetch_data, "_collect_one", return_value=proposed_records
        ), mock.patch.object(
            fetch_data, "append_daily_record", return_value="record-id"
        ) as append_record:
            written, failed = fetch_data.fetch_and_write(run_id=run_id)

        self.assertEqual((written, failed), (35, 0))
        self.assertEqual(append_record.call_count, 35)
        self.assertEqual(monitoring.run_funnel(run_id)["written_count"], 35)


if __name__ == "__main__":
    unittest.main()
