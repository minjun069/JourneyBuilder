"""Validate automatic-input data without models, scoring rules, or databases."""

from copy import deepcopy
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator


EVALS = Path(__file__).resolve().parents[1]
REPO = EVALS.parents[1]
SCHEMA = json.loads((EVALS / "schemas/ai-evaluation-scenario-v1.schema.json").read_text())
SOURCE = json.loads((EVALS / "data/community-retirement-case-v1.json").read_text())


class ScenarioSchemaTests(unittest.TestCase):
    def setUp(self):
        self.data = deepcopy(SOURCE)
        self.validator = Draft202012Validator(SCHEMA)

    def assert_invalid(self):
        self.assertTrue(list(self.validator.iter_errors(self.data)))

    def test_schema_and_original_data(self):
        Draft202012Validator.check_schema(SCHEMA)
        self.validator.validate(self.data)

    def test_example_preserves_first_stage(self):
        example = json.loads((EVALS / "data/community-retirement-eval-v1.example.json").read_text())
        self.assertEqual(example, SOURCE[:1])
        self.validator.validate(example)

    def test_empty_input_and_partial_update_are_allowed(self):
        for values in ({}, {"분석 종료 나이": 95}, {"국민연금": None}, {"자녀": []}):
            with self.subTest(values=values):
                self.data[0]["대화"][0]["입력값"] = values
                self.validator.validate(self.data)

    def test_non_object_input_is_rejected(self):
        for values in (None, [], "", 0):
            with self.subTest(values=values):
                self.data[0]["대화"][0]["입력값"] = values
                self.assert_invalid()

    def test_required_turn_fields(self):
        for key in ("사용자", "입력값"):
            with self.subTest(key=key):
                self.data = deepcopy(SOURCE)
                del self.data[0]["대화"][0][key]
                self.assert_invalid()

    def test_blank_message_and_empty_conversation(self):
        self.data[0]["대화"][0]["사용자"] = "  "
        self.assert_invalid()
        self.data[0]["대화"] = []
        self.assert_invalid()

    def test_unrelated_turn_metadata_is_rejected(self):
        self.data[0]["대화"][0]["evaluation"] = {}
        self.assert_invalid()

    def test_unknown_step_is_rejected(self):
        self.data[0]["step_id"] = "unknown"
        self.assert_invalid()

    def test_step_ids_match_product_registry(self):
        registry = json.loads((REPO / "contract-fixtures/ai-lifecycle/v2/step-registry.json").read_text())
        expected = {row["step_id"] for row in registry["steps"]} | set(registry["transport_fallback_step_ids"])
        self.assertEqual(expected, set(SCHEMA["items"]["properties"]["step_id"]["enum"]))


if __name__ == "__main__":
    unittest.main()
