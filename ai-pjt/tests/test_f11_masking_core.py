"""Production masking-core tests for F11-T01 / W1-L12."""

from __future__ import annotations

from copy import deepcopy
import json
import unittest

from core.masking import (
    MaskingPolicy,
    MaskingPolicyError,
    MaskingSink,
    mask_payload,
)
from tests.security.masking_contract import (
    MASKING_SENTINELS,
    Sink as ContractSink,
    assert_no_raw_sentinels,
)


TEST_PSEUDONYM_KEY = b"f11-t01-deterministic-test-key!!"


def _policy(*allowed_model_paths: tuple[str | int, ...]) -> MaskingPolicy:
    return MaskingPolicy(
        pseudonym_key=TEST_PSEUDONYM_KEY,
        allowed_model_paths=frozenset(allowed_model_paths),
    )


def _sentinel_payload() -> dict[str, object]:
    return {
        "plan_id": "P13-PLAN-RAW-0001",
        "tasks": [
            {
                "id": "task-1",
                "type": "answer_question",
                "description": "사용자 입력을 처리합니다.",
                "depends_on": [],
                "bindings": {},
            }
        ],
        "nested": {
            case.field_name: case.raw_value
            for case in MASKING_SENTINELS
        },
    }


class MaskingPolicyTests(unittest.TestCase):
    def test_key_is_required_and_must_be_long_enough(self) -> None:
        for invalid in (b"", b"short", "not-bytes"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(MaskingPolicyError):
                    MaskingPolicy(pseudonym_key=invalid)  # type: ignore[arg-type]

    def test_model_allowlist_accepts_only_non_empty_exact_paths(self) -> None:
        with self.assertRaises(MaskingPolicyError):
            MaskingPolicy(
                pseudonym_key=TEST_PSEUDONYM_KEY,
                allowed_model_paths=frozenset({()}),
            )

    def test_key_material_is_not_exposed_by_policy_repr(self) -> None:
        policy = _policy()
        self.assertNotIn(TEST_PSEUDONYM_KEY.decode(), repr(policy))


class RecursiveMaskingTests(unittest.TestCase):
    def test_p13_sentinels_are_removed_at_every_existing_sink(self) -> None:
        sink_pairs = (
            (MaskingSink.MODEL_INPUT, ContractSink.MODEL_INPUT),
            (MaskingSink.MODEL_OUTPUT, ContractSink.MODEL_OUTPUT),
            (MaskingSink.CALLBACK, ContractSink.CALLBACK),
            (MaskingSink.APPLICATION_LOG, ContractSink.APPLICATION_LOG),
            (MaskingSink.TRACE_EXPORT, ContractSink.TRACE_EXPORT),
        )
        for production_sink, contract_sink in sink_pairs:
            masked = mask_payload(
                _sentinel_payload(),
                sink=production_sink,
                policy=_policy(),
            )
            rendered = json.dumps(masked, ensure_ascii=False, sort_keys=True)
            with self.subTest(sink=production_sink.value):
                assert_no_raw_sentinels(rendered, contract_sink)

    def test_callback_preserves_only_root_transport_correlation_ids(self) -> None:
        payload = {
            "request_id": "callback-request-0001",
            "session_id": 731,
            "thread_id": "callback-thread-0001",
            "output": {
                "request_id": "nested-request-raw-0001",
                "task_id": "task-1",
                "email": "p13-user@example.invalid",
                "artifact": {
                    "type": "dashboard_metric",
                    "value": 248_600_000,
                },
            },
        }

        masked = mask_payload(
            payload,
            sink=MaskingSink.CALLBACK,
            policy=_policy(),
        )

        self.assertEqual(masked["request_id"], payload["request_id"])
        self.assertEqual(masked["session_id"], payload["session_id"])
        self.assertEqual(masked["thread_id"], payload["thread_id"])
        self.assertRegex(masked["output"]["request_id"], r"^request_[0-9a-f]{24}$")
        self.assertRegex(masked["output"]["task_id"], r"^task_[0-9a-f]{24}$")
        self.assertEqual(masked["output"]["email"], "[DIRECT_IDENTIFIER]")
        self.assertEqual(
            masked["output"]["artifact"]["value"],
            "[FINANCIAL_VALUE_MASKED]",
        )

    def test_callback_removes_every_p13_sentinel_from_nested_output(self) -> None:
        masked = mask_payload(
            {
                "request_id": "callback-request-0001",
                "session_id": 731,
                "thread_id": "callback-thread-0001",
                "output": _sentinel_payload(),
            },
            sink=MaskingSink.CALLBACK,
            policy=_policy(),
        )
        rendered = json.dumps(masked, ensure_ascii=False, sort_keys=True)
        for case in MASKING_SENTINELS:
            for fragment in case.forbidden_fragments:
                with self.subTest(case_id=case.case_id, fragment=fragment):
                    self.assertNotIn(fragment, rendered)

    def test_route_plan_and_task_result_references_keep_correlation(self) -> None:
        payload = {
            "plan_id": "plan-raw-0001",
            "tasks": [
                {
                    "id": "task-1",
                    "type": "lookup_reference_value",
                    "description": "기준값 조회",
                    "depends_on": [],
                    "bindings": {},
                },
                {
                    "id": "task-2",
                    "type": "answer_question",
                    "description": "조회값 설명",
                    "depends_on": ["task-1"],
                    "bindings": {
                        "value": {"task_id": "task-1", "path": "value"}
                    },
                },
            ],
            "task_results": {
                "task-1": {
                    "task_id": "task-1",
                    "status": "completed",
                    "artifact": {
                        "type": "reference_value",
                        "producer_task_id": "task-1",
                        "value": 7_310_000,
                    },
                },
                "task-2": {
                    "task_id": "task-2",
                    "status": "completed",
                    "artifact": {
                        "type": "form_change",
                        "changes": [
                            {
                                "section": "incomes",
                                "path": "monthly_income",
                                "operation": "set",
                                "value": 7_310_000,
                            }
                        ],
                    },
                },
            },
            "bound_inputs": {"value": 7_310_000},
        }

        first = mask_payload(
            payload,
            sink=MaskingSink.TRACE_EXPORT,
            policy=_policy(),
        )
        second = mask_payload(
            payload,
            sink=MaskingSink.TRACE_EXPORT,
            policy=_policy(),
        )

        source_id = first["tasks"][0]["id"]
        result_key = next(iter(first["task_results"]))
        self.assertEqual(first, second)
        self.assertRegex(first["plan_id"], r"^plan_[0-9a-f]{24}$")
        self.assertRegex(source_id, r"^task_[0-9a-f]{24}$")
        self.assertEqual(first["tasks"][1]["depends_on"], [source_id])
        self.assertEqual(
            first["tasks"][1]["bindings"]["value"]["task_id"],
            source_id,
        )
        self.assertEqual(result_key, source_id)
        self.assertEqual(first["task_results"][result_key]["task_id"], source_id)
        self.assertEqual(
            first["task_results"][result_key]["artifact"]["producer_task_id"],
            source_id,
        )
        self.assertEqual(
            first["task_results"][result_key]["artifact"]["value"],
            "[FINANCIAL_VALUE_MASKED]",
        )
        self.assertEqual(first["bound_inputs"]["value"], "[FINANCIAL_VALUE_MASKED]")
        second_task_id = first["tasks"][1]["id"]
        self.assertEqual(
            first["task_results"][second_task_id]["artifact"]["changes"][0]["value"],
            "[FINANCIAL_VALUE_MASKED]",
        )
        rendered = json.dumps(first, ensure_ascii=False, sort_keys=True)
        self.assertNotIn("plan-raw-0001", rendered)
        self.assertNotIn("task-1", rendered)

    def test_model_financial_and_quasi_values_require_exact_allowlist_paths(self) -> None:
        payload = {
            "customer_info": {
                "current_form": {
                    "monthly_income": 7_310_000,
                    "age": 47,
                    "email": "p13-user@example.invalid",
                }
            }
        }
        default_masked = mask_payload(
            payload,
            sink=MaskingSink.MODEL_INPUT,
            policy=_policy(),
        )
        self.assertEqual(
            default_masked["customer_info"]["current_form"]["monthly_income"],
            "[FINANCIAL_VALUE_MASKED]",
        )
        self.assertEqual(
            default_masked["customer_info"]["current_form"]["age"],
            "[QUASI_IDENTIFIER_BUCKETED]",
        )
        for sink in (MaskingSink.MODEL_INPUT, MaskingSink.MODEL_OUTPUT):
            allowed = mask_payload(
                payload,
                sink=sink,
                policy=_policy(
                    ("customer_info", "current_form", "monthly_income"),
                    ("customer_info", "current_form", "age"),
                    ("customer_info", "current_form", "email"),
                ),
            )
            with self.subTest(sink=sink.value):
                self.assertEqual(
                    allowed["customer_info"]["current_form"]["monthly_income"],
                    7_310_000,
                )
                self.assertEqual(allowed["customer_info"]["current_form"]["age"], 47)
                self.assertEqual(
                    allowed["customer_info"]["current_form"]["email"],
                    "[DIRECT_IDENTIFIER]",
                )

    def test_allowed_model_value_is_not_hidden_by_same_masked_value_elsewhere(self) -> None:
        payload = {
            "customer_info": {"current_form": {"monthly_income": "7310000"}},
            "total_assets": "7310000",
        }
        masked = mask_payload(
            payload,
            sink=MaskingSink.MODEL_INPUT,
            policy=_policy(
                ("customer_info", "current_form", "monthly_income"),
            ),
        )

        self.assertEqual(
            masked["customer_info"]["current_form"]["monthly_income"],
            "7310000",
        )
        self.assertEqual(masked["total_assets"], "[FINANCIAL_VALUE_MASKED]")

    def test_observability_preserves_safe_text_and_masks_sensitive_fragments(self) -> None:
        payload = {
            "request_id": "request-raw-0001",
            "description": (
                "request-raw-0001 담당자 p13-user@example.invalid "
                "연락처 01000000000"
            ),
            "safe_label": "p13-user@example.invalid / 010-0000-0000",
            "authorization": "Bearer secret-token-value",
        }

        masked = mask_payload(
            payload,
            sink=MaskingSink.APPLICATION_LOG,
            policy=_policy(),
        )
        rendered = json.dumps(masked, ensure_ascii=False, sort_keys=True)

        self.assertIn("담당자", masked["description"])
        self.assertIn("[DIRECT_IDENTIFIER]", masked["description"])
        self.assertNotEqual(masked["description"], "[FREE_TEXT_REDACTED]")
        self.assertEqual(masked["authorization"], "[SECRET]")
        self.assertNotIn("request-raw-0001", rendered)
        self.assertNotIn("p13-user@example.invalid", rendered)
        self.assertNotIn("01000000000", rendered)
        self.assertNotIn("010-0000-0000", rendered)

    def test_known_financial_literals_are_removed_from_other_log_fields(self) -> None:
        payload = {
            "monthly_income": 7_310_000,
            "unclassified_label": "월 소득은 7,310,000원 또는 7310000원",
        }

        masked = mask_payload(
            payload,
            sink=MaskingSink.TRACE_EXPORT,
            policy=_policy(),
        )
        rendered = json.dumps(masked, ensure_ascii=False, sort_keys=True)

        self.assertNotIn("7,310,000", rendered)
        self.assertNotIn("7310000", rendered)

    def test_nested_observability_content_preserves_non_sensitive_text(self) -> None:
        payload = {
            "content": [
                "단기 지향의 뜻을 설명해 줘",
                {"message": "월급 350만 원과 010-1234-5678은 숨겨줘"},
            ]
        }
        masked = mask_payload(
            payload,
            sink=MaskingSink.APPLICATION_LOG,
            policy=_policy(),
        )
        self.assertEqual(masked["content"][0], "단기 지향의 뜻을 설명해 줘")
        self.assertEqual(
            masked["content"][1]["message"],
            "월급 [FINANCIAL_VALUE_MASKED]과 [DIRECT_IDENTIFIER]은 숨겨줘",
        )

    def test_input_is_not_mutated_and_tuple_shape_is_preserved(self) -> None:
        payload = {
            "depends_on": ("task-1", "task-2"),
            "nested": [{"total_debt": 73_400_000}],
        }
        original = deepcopy(payload)

        masked = mask_payload(
            payload,
            sink=MaskingSink.TRACE_EXPORT,
            policy=_policy(),
        )

        self.assertEqual(payload, original)
        self.assertIsInstance(masked["depends_on"], tuple)
        self.assertEqual(
            masked["nested"][0]["total_debt"],
            "[FINANCIAL_VALUE_MASKED]",
        )

    def test_different_keys_produce_different_pseudonyms(self) -> None:
        payload = {"task_id": "task-1"}
        first = mask_payload(
            payload,
            sink=MaskingSink.TRACE_EXPORT,
            policy=_policy(),
        )
        second = mask_payload(
            payload,
            sink=MaskingSink.TRACE_EXPORT,
            policy=MaskingPolicy(pseudonym_key=b"another-f11-test-pseudonym-key!!"),
        )
        self.assertNotEqual(first["task_id"], second["task_id"])

    def test_short_task_result_mapping_key_is_still_pseudonymized(self) -> None:
        masked = mask_payload(
            {"task_results": {"x": {"task_id": "x", "status": "failed"}}},
            sink=MaskingSink.TRACE_EXPORT,
            policy=_policy(),
        )
        result_key = next(iter(masked["task_results"]))
        self.assertRegex(result_key, r"^task_[0-9a-f]{24}$")
        self.assertEqual(masked["task_results"][result_key]["task_id"], result_key)

    def test_unsupported_values_and_non_string_keys_fail_closed(self) -> None:
        for payload in ({1: "value"}, {"unsafe": object()}, {"unsafe": float("nan")}):
            with self.subTest(payload=payload):
                with self.assertRaises(MaskingPolicyError):
                    mask_payload(
                        payload,
                        sink=MaskingSink.TRACE_EXPORT,
                        policy=_policy(),
                    )


if __name__ == "__main__":
    unittest.main()
