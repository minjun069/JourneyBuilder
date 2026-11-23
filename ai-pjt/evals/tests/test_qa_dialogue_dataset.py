import json
import re
import unittest
from collections import Counter
from pathlib import Path


FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "qa-dialogue-dataset-v1.json"
)
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
STEP_REGISTRY_PATH = (
    REPOSITORY_ROOT
    / "contract-fixtures"
    / "ai-lifecycle"
    / "v2"
    / "step-registry.json"
)


class QADialogueDatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        cls.cases = cls.fixture["cases"]

    def test_declares_one_hundred_unique_cases_with_exact_distribution(self) -> None:
        self.assertEqual(self.fixture["fixture_kind"], "qa-dialogue-evaluation-dataset")
        self.assertEqual(self.fixture["schema_version"], "1.0")
        self.assertEqual(self.fixture["language"], "ko-KR")
        self.assertEqual(self.fixture["case_count"], 100)
        self.assertEqual(len(self.cases), 100)
        self.assertEqual(
            Counter(case["category"] for case in self.cases),
            Counter(self.fixture["distribution"]),
        )
        self.assertEqual(
            [case["id"] for case in self.cases],
            [f"qa-{number:03d}" for number in range(1, 101)],
        )

    def test_each_case_has_entry_input_reference_answer_and_semantic_rubric(self) -> None:
        source_ids = set(self.fixture["knowledge_sources"])
        registry = json.loads(STEP_REGISTRY_PATH.read_text(encoding="utf-8"))
        valid_step_ids = {
            *(step["step_id"] for step in registry["steps"]),
            *registry["transport_fallback_step_ids"],
        }
        for case in self.cases:
            with self.subTest(case=case["id"]):
                self.assertRegex(case["id"], re.compile(r"^qa-\d{3}$"))
                self.assertIn(case["step_id"], valid_step_ids)
                self.assertTrue(case["input"]["message"].strip())
                self.assertIsInstance(case["input"]["prior_turns"], list)
                for turn in case["input"]["prior_turns"]:
                    self.assertEqual(set(turn), {"user", "assistant"})
                    self.assertTrue(turn["user"].strip())
                    self.assertTrue(turn["assistant"].strip())

                expected = case["expected"]
                self.assertTrue(expected["assistant_response"].strip())
                self.assertTrue(expected["semantic_requirements"])
                self.assertTrue(expected["grounding_sources"])
                self.assertLessEqual(set(expected["grounding_sources"]), source_ids)

        for relative_path in self.fixture["knowledge_sources"].values():
            with self.subTest(knowledge_source=relative_path):
                self.assertTrue((REPOSITORY_ROOT / relative_path).is_file())

    def test_intermediate_annotations_start_pending_without_fake_ground_truth(self) -> None:
        expected_stage_keys = {
            "planner",
            "plan_policy",
            "router",
            "qa",
            "aggregator",
        }
        self.assertEqual(
            set(self.fixture["pipeline_stage_schema"]),
            expected_stage_keys,
        )
        for case in self.cases:
            with self.subTest(case=case["id"]):
                self.assertEqual(
                    case["expected_pipeline"],
                    {"annotation_status": "pending", "stages": {}},
                )


if __name__ == "__main__":
    unittest.main()
