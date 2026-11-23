"""Only canonical KRW amount leaves may defer opaque reference validation."""

import unittest

from tools.output_policy import _validate_value, validate_form_update_contract


TOKEN = "[[FINANCIAL_VALUE_aabbccddeeff_1]]"


def validate_change(change, step_id="basic-info"):
    return validate_form_update_contract({
        "step_id": step_id,
        "output": {
            "response": "변경안을 확인해 주세요.", "step_id": step_id,
            "action": {"type": "form_change", "target": "draft_form", "user_requested": True, "changes": [change]},
        },
    })


class FinancialReferenceValueTests(unittest.TestCase):
    def test_exact_token_is_preserved_for_canonical_krw_numeric_types(self):
        for value_type in ("integer", "decimal", "number"):
            with self.subTest(value_type=value_type):
                spec = {"value_type": value_type, "unit": "KRW", "nullable": False, "constraints": {"min": 0, "max": 10}}
                self.assertEqual(_validate_value(TOKEN, spec, "value"), [])
        result = validate_change({"section": "basic_info", "path": "monthly_income", "operation": "set", "value": TOKEN})
        self.assertTrue(result.valid)
        self.assertEqual(result.output["action"]["changes"][0]["value"], TOKEN)

    def test_other_kinds_malformed_or_embedded_tokens_do_not_bypass_numeric_checks(self):
        values = (
            "[[USER_NAME_aabbccddeeff_1]]", "[[DIRECT_IDENTIFIER_aabbccddeeff_1]]",
            "[[FINANCIAL_VALUE_AABBCCDDEEFF_1]]", "[[FINANCIAL_VALUE_aabbccddee_1]]",
            "[[FINANCIAL_VALUE_aabbccddeeff_0]]", "[[FINANCIAL_VALUE_aabbccddeeff_01]]",
            TOKEN + "원", "-" + TOKEN, TOKEN + "+100", TOKEN + TOKEN, " " + TOKEN, TOKEN + "\n",
        )
        for value in values:
            with self.subTest(value=value):
                result = validate_change({"section": "basic_info", "path": "monthly_income", "operation": "set", "value": value})
                self.assertFalse(result.valid)
                self.assertIn("VALUE_TYPE_MISMATCH", {issue.code for issue in result.issues})

    def test_token_cannot_stand_in_for_age_percent_or_boolean(self):
        cases = (
            ("basic-info", {"section": "basic_info", "path": "age", "operation": "set", "value": TOKEN}),
            ("pension-assets", {"section": "pension_assets", "path": "expected_return_rate", "operation": "update", "item_id": 7, "value": TOKEN}),
            ("income", {"section": "incomes", "path": "is_fixed", "operation": "update", "item_id": 7, "value": TOKEN}),
        )
        for step_id, change in cases:
            with self.subTest(step=step_id):
                self.assertFalse(validate_change(change, step_id).valid)

    def test_list_add_uses_numeric_child_spec_instead_of_string_anchor(self):
        change = {"section": "financial_assets", "path": "name", "operation": "add",
                  "value": {"name": "적금", "principal": TOKEN}}
        result = validate_change(change, "financial-assets")
        self.assertTrue(result.valid, result.issues)
        self.assertEqual(result.output["action"]["changes"][0]["value"]["principal"], TOKEN)
        for key in ("id", "unregistered_amount"):
            with self.subTest(key=key):
                rejected = {**change, "value": {"name": "적금", key: TOKEN}}
                self.assertFalse(validate_change(rejected, "financial-assets").valid)

    def test_nested_add_and_update_follow_amount_leaf_contract(self):
        additions = {"section": "goal_periods", "path": "items", "operation": "add", "parent_item_id": 10,
                     "value": {"name": "주거비", "amount": TOKEN, "is_fixed": True}}
        update = {"section": "goal_periods", "path": "items[].amount", "operation": "update",
                  "parent_item_id": 10, "item_id": 11, "value": TOKEN}
        for change in (additions, update):
            with self.subTest(operation=change["operation"]):
                result = validate_change(change, "financial-goals")
                self.assertTrue(result.valid, result.issues)
        wrong_type = {**additions, "value": {"name": "주거비", "amount": TOKEN, "is_fixed": TOKEN}}
        self.assertFalse(validate_change(wrong_type, "financial-goals").valid)

    def test_nonfinite_numbers_are_rejected_even_when_range_comparisons_are_false(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            for value_type in ("decimal", "number"):
                with self.subTest(value=value, value_type=value_type):
                    spec = {"value_type": value_type, "unit": "KRW", "constraints": {"min": 0}}
                    issues = _validate_value(value, spec, "value")
                    self.assertEqual([issue.code for issue in issues], ["NON_FINITE_NUMBER"])
            result = validate_change({
                "section": "pension_assets", "path": "expected_return_rate", "operation": "update", "item_id": 7, "value": value,
            }, "pension-assets")
            self.assertFalse(result.valid)
            addition = {"section": "pension_assets", "path": "name", "operation": "add",
                        "value": {"name": "연금", "expected_return_rate": value}}
            self.assertFalse(validate_change(addition, "pension-assets").valid)

    def test_nullable_boolean_negative_and_fractional_amount_rules_still_apply(self):
        spec = {"value_type": "integer", "unit": "KRW", "nullable": False, "constraints": {"min": 0}}
        self.assertEqual([issue.code for issue in _validate_value(None, spec, "value")], ["NULL_NOT_ALLOWED"])
        self.assertEqual(_validate_value(None, {**spec, "nullable": True}, "value"), [])
        self.assertEqual([issue.code for issue in _validate_value(True, spec, "value")], ["VALUE_TYPE_MISMATCH"])
        self.assertEqual([issue.code for issue in _validate_value(-1, spec, "value")], ["VALUE_BELOW_MIN"])
        self.assertEqual([issue.code for issue in _validate_value(0.5, spec, "value")], ["VALUE_TYPE_MISMATCH"])
        self.assertEqual(_validate_value(0, spec, "value"), [])


if __name__ == "__main__":
    unittest.main()
