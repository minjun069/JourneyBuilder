from __future__ import annotations

import unittest

from graph.task_contracts import (
    BindingResolutionError,
    blocked_task_result,
    resolve_task_bindings,
    validate_task_result,
)


FIELD_SPEC = {
    "step_id": "financial-goals",
    "section": "financial_goals",
    "path": "monthly_target_expense",
    "value_type": "integer",
    "unit": "KRW",
    "constraints": {"min": 0, "period": "month"},
    "cardinality": "single",
    "writable_by_ai": True,
    "nullable": False,
    "operations": ["set", "update"],
}


def reference_task() -> dict:
    return {
        "id": "task-1",
        "type": "lookup_reference_value",
        "depends_on": [],
        "produces": "reference_value",
    }


def reference_result() -> dict:
    return {
        "task_id": "task-1",
        "status": "completed",
        "artifact": {
            "type": "reference_value",
            "schema_version": "1.0",
            "producer_task_id": "task-1",
            "reference_id": "monthly_target_expense",
            "age": 45,
            "value": 2_500_000,
            "unit": "KRW",
            "period": "monthly",
            "population": "40대",
            "comparator": "eq",
            "source_id": "approved-statistics-2026",
            "source_version": "1.0.0",
            "as_of": "2026-06-30",
            "calculation_policy_version": "2026-09-07.v1",
        },
    }


class TaskResultContractTests(unittest.TestCase):
    def test_reference_value_requires_provenance_and_unit(self) -> None:
        validation = validate_task_result(
            task=reference_task(),
            result=reference_result(),
            step_id="financial-goals",
        )
        self.assertTrue(validation.valid)

        invalid = reference_result()
        del invalid["artifact"]["source_id"]
        invalid["artifact"]["value"] = "2500000"
        validation = validate_task_result(
            task=reference_task(),
            result=invalid,
            step_id="financial-goals",
        )
        self.assertFalse(validation.valid)
        self.assertEqual(validation.result["reason_code"], "output_schema_invalid")
        self.assertEqual(
            {issue.code for issue in validation.issues},
            {
                "INVALID_REFERENCE_VALUE",
                "MISSING_REFERENCE_METADATA",
                "MISSING_TOOL_ARTIFACT_FIELD",
            },
        )

    def test_completed_task_must_match_declared_artifact(self) -> None:
        validation = validate_task_result(
            task={**reference_task(), "produces": "text_response"},
            result=reference_result(),
            step_id="financial-goals",
        )
        self.assertFalse(validation.valid)
        self.assertIn(
            "ARTIFACT_TYPE_MISMATCH",
            {issue.code for issue in validation.issues},
        )

    def test_failed_task_cannot_publish_artifact(self) -> None:
        validation = validate_task_result(
            task=reference_task(),
            result={
                "task_id": "task-1",
                "status": "failed",
                "reason_code": "provider_timeout",
                "artifact": reference_result()["artifact"],
            },
            step_id="financial-goals",
        )
        self.assertFalse(validation.valid)
        self.assertIn(
            "FAILED_TASK_HAS_ARTIFACT",
            {issue.code for issue in validation.issues},
        )

    def test_blocked_task_must_name_declared_dependency(self) -> None:
        task = {
            "id": "task-2",
            "depends_on": ["task-1"],
            "produces": "form_change",
        }
        valid = validate_task_result(
            task=task,
            result=blocked_task_result("task-2", "task-1"),
            step_id="financial-goals",
        )
        self.assertTrue(valid.valid)

        invalid = validate_task_result(
            task=task,
            result=blocked_task_result("task-2", "task-9"),
            step_id="financial-goals",
        )
        self.assertFalse(invalid.valid)
        self.assertIn("INVALID_BLOCKED_BY", {issue.code for issue in invalid.issues})

    def test_form_change_artifact_reuses_common_validator(self) -> None:
        task = {
            "id": "task-2",
            "depends_on": ["task-1"],
            "produces": "form_change",
        }
        result = {
            "task_id": "task-2",
            "status": "completed",
            "response": "목표 생활비 입력 제안을 만들었습니다.",
            "artifact": {
                "type": "form_change",
                "target": "draft_form",
                "user_requested": True,
                "confidence": 1.0,
                "changes": [
                    {
                        "section": "financial_goals",
                        "path": "monthly_target_expense",
                        "operation": "set",
                        "value": 2_500_000,
                        "unit": "KRW",
                        "reason": "승인된 40대 월평균 비용 조회 결과",
                    }
                ],
            },
        }
        validation = validate_task_result(
            task=task,
            result=result,
            step_id="financial-goals",
            field_registry=[FIELD_SPEC],
        )
        self.assertTrue(
            validation.valid,
            [issue.to_dict() for issue in validation.issues],
        )


class BindingContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.task = {
            "id": "task-2",
            "depends_on": ["task-1"],
            "bindings": {
                "value": {"task_id": "task-1", "path": "value"},
            },
        }
        self.expectations = {
            "value": {
                "artifact_type": "reference_value",
                "path": "value",
                "value_type": "number",
                "unit": "KRW",
            }
        }

    def test_binding_reads_only_validated_typed_artifact_field(self) -> None:
        resolved = resolve_task_bindings(
            task=self.task,
            task_results={"task-1": reference_result()},
            expectations=self.expectations,
        )
        self.assertEqual(resolved, {"value": 2_500_000})

    def test_failed_dependency_cannot_be_bound(self) -> None:
        with self.assertRaises(BindingResolutionError) as raised:
            resolve_task_bindings(
                task=self.task,
                task_results={
                    "task-1": {
                        "task_id": "task-1",
                        "status": "failed",
                        "reason_code": "provider_timeout",
                    }
                },
                expectations=self.expectations,
            )
        self.assertEqual(raised.exception.code, "DEPENDENCY_FAILED")

    def test_arbitrary_artifact_path_is_rejected(self) -> None:
        self.task["bindings"]["value"]["path"] = "response.guessed_number"
        with self.assertRaises(BindingResolutionError) as raised:
            resolve_task_bindings(
                task=self.task,
                task_results={"task-1": reference_result()},
                expectations=self.expectations,
            )
        self.assertEqual(raised.exception.code, "ARTIFACT_PATH_NOT_ALLOWED")

    def test_unit_mismatch_is_rejected(self) -> None:
        source = reference_result()
        source["artifact"]["unit"] = "USD"
        with self.assertRaises(BindingResolutionError) as raised:
            resolve_task_bindings(
                task=self.task,
                task_results={"task-1": source},
                expectations=self.expectations,
            )
        self.assertEqual(raised.exception.code, "ARTIFACT_UNIT_MISMATCH")


if __name__ == "__main__":
    unittest.main()
