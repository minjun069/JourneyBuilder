import unittest

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


def state_with_change(change, *, step_id="basic-info", registry=None):
    return {
        "step_id": step_id,
        "field_registry": registry or [FIELD_SPEC],
        "output": {
            "response": "입력 제안을 만들었습니다.",
            "step_id": step_id,
            "action": {
                "type": "form_change",
                "target": "draft_form",
                "user_requested": True,
                "confidence": 0.95,
                "changes": [change],
            },
        },
    }


class OutputValidationTests(unittest.TestCase):
    def test_plain_answer_passes_mandatory_node(self):
        state = {
            "step_id": "intro",
            "field_registry": [],
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
            registry=[LIST_FIELD_SPEC],
        ))
        self.assertFalse(result.valid)
        self.assertIn("ITEM_ID_REQUIRED", {issue.code for issue in result.issues})

    def test_non_empty_legacy_update_is_rejected(self):
        result = validate_form_update_contract({
            "step_id": "basic-info",
            "field_registry": [FIELD_SPEC],
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
            "field_registry": [],
            "output": {"response": "답변"},
        })
        self.assertFalse(result.valid)
        self.assertIn("INVALID_CURRENT_STEP", {issue.code for issue in result.issues})


if __name__ == "__main__":
    unittest.main()
