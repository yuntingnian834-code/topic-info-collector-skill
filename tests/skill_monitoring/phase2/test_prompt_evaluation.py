import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT_DIR / "scripts"))

from monitoring.phase2.evaluate_prompts import run_evaluation  # noqa: E402
from monitoring.phase1.runtime_monitor import latest_run  # noqa: E402


class PromptEvaluationTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        temp_path = Path(self.temp_dir.name)
        self.dataset_path = temp_path / "cases.json"
        self.dataset_path.write_text(
            json.dumps(
                [
                    {
                        "case_id": "relevant",
                        "level2": "铜库存",
                        "research_core": "关注铜库存",
                        "content": "铜库存于2026年9月5日下降。",
                        "expected_relevant": True,
                        "expected_event_date": "2026-09-05",
                        "expected_keyword": "铜",
                    },
                    {
                        "case_id": "irrelevant",
                        "level2": "宏观风险",
                        "research_core": "关注宏观事件",
                        "content": "足球俱乐部发布新球衣。",
                        "expected_relevant": False,
                        "expected_event_date": None,
                        "expected_keyword": None,
                    },
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.env = mock.patch.dict(
            os.environ,
            {
                "MONITORING_DB_PATH": str(temp_path / "monitoring.sqlite3"),
                "MONITORING_REPORT_DIR": str(temp_path / "reports"),
                "MONITORING_FEISHU_WEBHOOK_URL": "",
            },
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp_dir.cleanup()

    @staticmethod
    def fake_summarizer(content, _level2, _core, _date_label, **_kwargs):
        if "足球" in content:
            return "__NOT_RELEVANT__", "", None, {"relevance_status": "blocked"}
        return "LME铜最新动态", "铜库存下降。", "2026-09-05", {
            "relevance_status": "passed"
        }

    def test_fixed_evaluation_records_case_level_results(self):
        report = run_evaluation(
            dataset_path=self.dataset_path,
            summarizer=self.fake_summarizer,
            model_name="fake-model",
        )
        self.assertEqual(report["status"], "success")
        self.assertEqual(report["total_cases"], 2)
        self.assertEqual(report["passed_cases"], 2)
        self.assertEqual(report["accuracy"], 100.0)
        self.assertEqual(len(report["results"]), 2)
        self.assertTrue(all(row["passed"] for row in report["results"]))
        self.assertIsNone(latest_run())
        self.assertEqual(latest_run(include_evaluation=True)["trigger_type"], "evaluation")

    def test_fixed_evaluation_exposes_regression(self):
        def always_relevant(*_args, **_kwargs):
            return "LME铜最新动态", "铜库存下降。", "2026-09-05", {}

        report = run_evaluation(
            dataset_path=self.dataset_path,
            summarizer=always_relevant,
            model_name="fake-model",
        )
        self.assertEqual(report["status"], "regression")
        self.assertEqual(report["passed_cases"], 1)
        self.assertEqual(report["accuracy"], 50.0)
        failed = [row for row in report["results"] if not row["passed"]]
        self.assertEqual(failed[0]["case_id"], "irrelevant")


if __name__ == "__main__":
    unittest.main()
