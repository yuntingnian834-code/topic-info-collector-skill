import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


ROOT_DIR = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT_DIR / "scripts"
RUNTIME_DIR = SCRIPTS_DIR / "runtime"
sys.path.insert(0, str(SCRIPTS_DIR))

from monitoring.phase1 import runtime_monitor  # noqa: E402
from monitoring.phase2 import model_monitor  # noqa: E402


def load_runtime_module(name: str):
    dependency_stubs = {
        "feedparser": types.SimpleNamespace(parse=lambda _text: {"entries": []}),
        "trafilatura": types.SimpleNamespace(extract=lambda *_args, **_kwargs: ""),
        "dotenv": types.SimpleNamespace(load_dotenv=lambda: None),
        "feishu": types.SimpleNamespace(
            get_info_points=lambda: [],
            append_daily_record=lambda _fields: "record-id",
            get_today_records=lambda _date: [],
            DAILY_BASE_TOKEN="base-token",
        ),
    }
    spec = importlib.util.spec_from_file_location(
        f"{name}_under_phase2_test", RUNTIME_DIR / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, dependency_stubs):
        spec.loader.exec_module(module)
    module.DEEPSEEK_API_KEY = "test-key"
    return module


fetch_data = load_runtime_module("fetch_data")
reporter = load_runtime_module("reporter")


class FakeResponse:
    def __init__(self, content: str, prompt_tokens: int, completion_tokens: int):
        self._payload = {
            "model": "deepseek-chat-test",
            "choices": [{"message": {"content": content}}],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            },
        }

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class ModelCallIntegrationTest(unittest.TestCase):
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

    def test_main_extraction_records_tokens_prompt_and_json_validity(self):
        run_id = runtime_monitor.start_run(fetch_data.TODAY_STR, "manual_test")
        article_id = runtime_monitor.article_id_for_url("https://example.com/copper")
        model_output = json.dumps(
            {
                "is_target_related": True,
                "rejection_reason": None,
                "date_of_event": fetch_data.TODAY_STR,
                "commodity_type": "LME铜",
                "quantitative_metrics": "库存下降100吨",
                "market_or_policy_status": None,
                "analytical_summary": "库存下降，供应边际收紧。",
            },
            ensure_ascii=False,
        )
        response = FakeResponse(model_output, 120, 30)
        with mock.patch.object(fetch_data.requests, "post", return_value=response):
            title, _summary, _event_date, metadata = fetch_data.summarize_v2(
                "测试正文" * 100,
                "铜库存",
                "关注库存变化",
                "",
                run_id=run_id,
                article_id=article_id,
            )

        self.assertEqual(title, "LME铜最新动态")
        self.assertEqual(metadata["relevance_status"], "passed")
        quality = model_monitor.get_model_summary(run_id)
        self.assertEqual(quality["total_calls"], 1)
        self.assertEqual(quality["total_tokens"], 150)
        self.assertEqual(quality["first_pass_json_valid_rate"], 100.0)
        self.assertEqual(
            quality["prompt_breakdown"][0]["prompt_version"],
            model_monitor.PROMPT_VERSIONS["relevance_extract"],
        )

    def test_invalid_main_output_triggers_a_monitored_json_repair(self):
        run_id = runtime_monitor.start_run(fetch_data.TODAY_STR, "manual_test")
        article_id = runtime_monitor.article_id_for_url("https://example.com/repair")
        repaired_output = json.dumps(
            {
                "is_target_related": True,
                "rejection_reason": None,
                "date_of_event": None,
                "commodity_type": "原油",
                "quantitative_metrics": "价格上涨2%",
                "market_or_policy_status": None,
                "analytical_summary": "油价受到供应扰动支撑。",
            },
            ensure_ascii=False,
        )
        responses = [
            FakeResponse("这不是合法JSON", 100, 10),
            FakeResponse(repaired_output, 40, 20),
        ]
        with mock.patch.object(fetch_data.requests, "post", side_effect=responses):
            title, _summary, _event_date, _metadata = fetch_data.summarize_v2(
                "测试正文" * 100,
                "原油价格",
                "关注价格变化",
                "",
                run_id=run_id,
                article_id=article_id,
            )

        self.assertEqual(title, "原油最新动态")
        quality = model_monitor.get_model_summary(run_id)
        self.assertEqual(quality["total_calls"], 2)
        self.assertEqual(quality["first_pass_json_valid_rate"], 0.0)
        self.assertEqual(quality["json_repair_count"], 1)
        self.assertEqual(quality["json_repair_rate"], 100.0)
        self.assertEqual(quality["invalid_output_calls"], 1)

    def test_daily_summary_uses_its_own_prompt_version(self):
        run_id = runtime_monitor.start_run(fetch_data.TODAY_STR, "manual_test")
        response = FakeResponse("今日重点关注铜库存下降。", 50, 10)
        with mock.patch.object(reporter.requests, "post", return_value=response):
            result = reporter._deepseek_executive_summary("铜库存下降", run_id=run_id)

        self.assertEqual(result, "今日重点关注铜库存下降。")
        quality = model_monitor.get_model_summary(run_id)
        self.assertEqual(quality["total_calls"], 1)
        self.assertEqual(
            quality["prompt_breakdown"][0]["prompt_version"],
            model_monitor.PROMPT_VERSIONS["daily_summary"],
        )


if __name__ == "__main__":
    unittest.main()
