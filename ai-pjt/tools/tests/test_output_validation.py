import json
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
from tools.output_validation import validate_form_update, validate_form_update_contract


FIELD_SPEC = {
    "step_id": "basic-info",
    "section": "basic_info",
    "path": "monthly_income",
    "value_type": "integer",
    "unit": "KRW",
    "constraints": {"min": 0, "period": "month"},
    "cardinality": "single",
    "writable_by_ai": True,
    "nullable": False,
    "operations": ["set", "update"],
}

LIST_FIELD_SPEC = {
    "step_id": "financial-assets",
    "section": "financial_assets",
    "path": "current_value",
    "value_type": "integer",
    "unit": "KRW",
    "constraints": {"min": 0},
    "cardinality": "list",
    "writable_by_ai": True,
    "nullable": False,
    "operations": ["add", "update", "remove"],
}


def state_with_change(change, *, step_id="basic-info"):
    return {
        "step_id": step_id,
        "output": {
            "response": "입력 제안을 만들었습니다.",
            "step_id": step_id,
            "action": {
                "type": "form_change",
                "target": "draft_form",
                "user_requested": True,
                "changes": [change],
            },
        },
    }


class OutputValidationTests(unittest.TestCase):
    def test_all_d1_form_action_fixtures_pass_the_common_validator(self):
        fixture = json.loads((
            REPOSITORY_ROOT
            / "contract-fixtures/ai-lifecycle/v2/form-actions.json"
        ).read_text(encoding="utf-8"))

        for case_name, case in fixture["cases"].items():
            with self.subTest(case=case_name):
                step_id = case["step_id"]
                result = validate_form_update_contract({
                    "step_id": step_id,
                    "output": {
                        "response": "fixture response",
                        "step_id": step_id,
                        "action": case["ai_output_action"],
                    },
                })
                self.assertTrue(
                    result.valid,
                    [issue.to_dict() for issue in result.issues],
                )

    def test_plain_answer_passes_mandatory_node(self):
        state = {
            "step_id": "intro",
            "output": {"response": "안녕하세요.", "update": {}},
        }
        result = validate_form_update_contract(state)
        self.assertTrue(result.valid)
        self.assertEqual(result.output["response"], "안녕하세요.")

        node_result = validate_form_update(state)
        self.assertEqual(set(node_result), {"output"})
        self.assertEqual(node_result["output"]["response"], "안녕하세요.")

    def test_valid_form_change_passes(self):
        result = validate_form_update_contract(state_with_change({
            "section": "basic_info",
            "path": "monthly_income",
            "operation": "set",
            "value": 3_000_000,
        }))
        self.assertTrue(result.valid)
        self.assertIsNotNone(result.output["action"])

    def test_policy_provenance_is_validated(self):
        change = {
            "section": "basic_info",
            "path": "monthly_income",
            "operation": "set",
            "value": 3_000_000,
            "policy_version": "2026-09-09",
            "source": {
                "source_id": "approved-source",
                "title": "승인 자료",
                "reference_period": "2025",
            },
            "retrieved_at": "2026-09-09",
            "transform": "원 단위 직접 매핑",
            "assumption_level": "household_benchmark",
        }
        self.assertTrue(validate_form_update_contract(state_with_change(change)).valid)

        invalid = validate_form_update_contract(state_with_change({
            **change,
            "source": {"title": "출처가 불완전함"},
        }))
        self.assertFalse(invalid.valid)
        self.assertIn("INVALID_CHANGE_SOURCE", {issue.code for issue in invalid.issues})

    def test_unknown_field_fails_closed_and_removes_action(self):
        result = validate_form_update_contract(state_with_change({
            "section": "basic_info",
            "path": "unknown",
            "operation": "set",
            "value": 1,
        }))
        self.assertFalse(result.valid)
        self.assertIsNone(result.output["action"])
        self.assertEqual(result.issues[0].code, "UNKNOWN_CANONICAL_FIELD")

    def test_numeric_string_is_not_accepted_as_integer(self):
        result = validate_form_update_contract(state_with_change({
            "section": "basic_info",
            "path": "monthly_income",
            "operation": "set",
            "value": "3000000",
        }))
        self.assertFalse(result.valid)
        self.assertIn("VALUE_TYPE_MISMATCH", {issue.code for issue in result.issues})

    def test_unit_mismatch_fails_closed(self):
        result = validate_form_update_contract(state_with_change({
            "section": "basic_info",
            "path": "monthly_income",
            "operation": "set",
            "value": 300,
            "unit": "manwon",
        }))
        self.assertFalse(result.valid)
        self.assertIn("UNIT_MISMATCH", {issue.code for issue in result.issues})

    def test_list_update_requires_stable_item_id(self):
        result = validate_form_update_contract(state_with_change(
            {
                "section": "financial_assets",
                "path": "current_value",
                "operation": "update",
                "value": 5_000_000,
            },
            step_id="financial-assets",
        ))
        self.assertFalse(result.valid)
        self.assertIn("ITEM_ID_REQUIRED", {issue.code for issue in result.issues})

    def test_list_add_rejects_unknown_row_field(self):
        row_name_spec = {
            **LIST_FIELD_SPEC,
            "path": "name",
            "value_type": "string",
            "unit": "none",
        }
        result = validate_form_update_contract(state_with_change(
            {
                "section": "financial_assets",
                "path": "name",
                "operation": "add",
                "value": {"name": "새 자산", "unregistered": 1},
            },
            step_id="financial-assets",
        ))
        self.assertFalse(result.valid)
        self.assertIn(
            "UNKNOWN_ADDED_ITEM_FIELD",
            {issue.code for issue in result.issues},
        )

    def test_non_empty_legacy_update_is_rejected(self):
        result = validate_form_update_contract({
            "step_id": "basic-info",
            "output": {"response": "변경했습니다.", "update": {"monthly_income": 1}},
        })
        self.assertFalse(result.valid)
        self.assertIn("LEGACY_UPDATE_NOT_ALLOWED", {issue.code for issue in result.issues})

    def test_model_supplied_mode_is_rejected(self):
        state = state_with_change({
            "section": "basic_info",
            "path": "monthly_income",
            "operation": "set",
            "value": 3_000_000,
        })
        state["output"]["action"]["mode"] = "direct"
        result = validate_form_update_contract(state)
        self.assertFalse(result.valid)
        self.assertIn("MODEL_MODE_NOT_ALLOWED", {issue.code for issue in result.issues})

    def test_missing_runtime_step_fails_closed(self):
        result = validate_form_update_contract({
            "output": {"response": "답변"},
        })
        self.assertFalse(result.valid)
        self.assertIn("INVALID_CURRENT_STEP", {issue.code for issue in result.issues})

    def test_error_review_requires_a_reason_without_scores(self):
        item = {
            "section": "basic_info",
            "path": "monthly_income",
            "current_value": 20_000,
            "reason": "단위 착오가 의심됩니다.",
            "suggested_value": 2_000_000,
        }
        state = {
            "step_id": "basic-info",
            "output": {
                "response": "확인이 필요합니다.",
                "review": {
                    "type": "error_review",
                    "manual": {"document_id": "interface-basic-info", "version": "1.0.0"},
                    "items": [item],
                },
            },
        }
        self.assertTrue(validate_form_update_contract(state).valid)

        low = {
            **state,
            "output": {
                **state["output"],
                "review": {
                    **state["output"]["review"],
                    "items": [{**item, "reason": ""}],
                },
            },
        }
        result = validate_form_update_contract(low)
        self.assertFalse(result.valid)
        self.assertIn(
            "INVALID_REVIEW_ITEM",
            {issue.code for issue in result.issues},
        )


if __name__ == "__main__":
    unittest.main()
