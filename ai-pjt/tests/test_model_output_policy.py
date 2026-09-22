from __future__ import annotations

import unittest

from core.model_contracts import ModelOutputBlockedError, ModelPurpose
from core.model_output_policy import block_forbidden_output


class ModelOutputPolicyTests(unittest.TestCase):
    def test_input_assist_allows_business_name_in_extracted_change_value(self) -> None:
        block_forbidden_output(
            {
                "request": "change",
                "changes": [{
                    "field_ref": "f0",
                    "item_ref": None,
                    "operation": "add",
                    "value": {
                        "name": "주택청약",
                        "principal": 3_000_000,
                    },
                }],
                "questions": [],
            },
            purpose=ModelPurpose.INPUT_ASSIST,
            operation_name="input_assist.extract",
        )

    def test_name_allowance_is_limited_to_input_assist_change_values(self) -> None:
        payloads = (
            (
                ModelPurpose.QA,
                "qa.answer",
                {"changes": [{"value": {"name": "주택청약"}}]},
            ),
            (
                ModelPurpose.INPUT_ASSIST,
                "input_assist.review",
                {"changes": [{"value": {"name": "주택청약"}}]},
            ),
            (ModelPurpose.INPUT_ASSIST, "input_assist.extract", {"name": "홍길동"}),
            (
                ModelPurpose.INPUT_ASSIST,
                "input_assist.extract",
                {"changes": [{"name": "홍길동"}]},
            ),
        )
        for purpose, operation_name, payload in payloads:
            with self.subTest(
                purpose=purpose,
                operation_name=operation_name,
                payload=payload,
            ):
                with self.assertRaises(ModelOutputBlockedError):
                    block_forbidden_output(
                        payload,
                        purpose=purpose,
                        operation_name=operation_name,
                    )

    def test_identity_fields_remain_blocked_inside_allowed_value(self) -> None:
        for field in ("customer_name", "full_name", "email"):
            with self.subTest(field=field):
                with self.assertRaises(ModelOutputBlockedError):
                    block_forbidden_output(
                        {"changes": [{"value": {field: "홍길동"}}]},
                        purpose=ModelPurpose.INPUT_ASSIST,
                        operation_name="input_assist.extract",
                    )

    def test_business_name_still_rejects_embedded_direct_identifiers(self) -> None:
        for value in (
            "문의 owner@example.com",
            "문의 010-1234-5678",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ModelOutputBlockedError):
                    block_forbidden_output(
                        {"changes": [{"value": {"name": value}}]},
                        purpose=ModelPurpose.INPUT_ASSIST,
                        operation_name="input_assist.extract",
                    )


if __name__ == "__main__":
    unittest.main()
