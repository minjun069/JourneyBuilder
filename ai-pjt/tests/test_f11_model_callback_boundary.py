from __future__ import annotations

import ast
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Literal
import unittest

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from core.callback_masking import CallbackPayloadMasker
from core.config import settings
from core.model_boundary import (
    DATA_HANDLING_PROMPT,
    ModelOutputBlockedError,
    ModelPurpose,
    bind_model_call_recorder,
    mask_model_payload,
    prompt_messages,
    prompt_policy_version,
    secure_invoke,
    secure_structured_invoke,
    secure_system_prompt,
)
from core.prompt_registry import bind_prompt_trace_sink
from prompts import get_prompt
from transport.contracts import CompletedCallback


TEST_MASKING_KEY = b"f11-t02-model-callback-test-key-32bytes"
AI_ROOT = Path(__file__).resolve().parents[1]


class FakeModel:
    def __init__(self, content: str) -> None:
        self.content = content
        self.messages: list[object] = []

    def invoke(self, messages: list[object]) -> AIMessage:
        self.messages = messages
        return AIMessage(content=self.content)


class StructuredAnswer(BaseModel):
    status: Literal["matched"]
    agent: Literal["qa"]


class FakeStructuredRunnable:
    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, _messages: list[object]) -> dict:
        self.calls += 1
        if self.calls == 1:
            return {"status": "invalid", "agent": "qa"}
        return {"status": "matched", "agent": "qa"}


class FakeStructuredProvider:
    def __init__(self) -> None:
        self.runnable = FakeStructuredRunnable()
        self.schema: type[BaseModel] | None = None

    def with_structured_output(
        self,
        schema: type[BaseModel],
    ) -> FakeStructuredRunnable:
        self.schema = schema
        return self.runnable


class ModelBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_key = settings.masking_pseudonym_key
        settings.masking_pseudonym_key = TEST_MASKING_KEY.decode()

    def tearDown(self) -> None:
        settings.masking_pseudonym_key = self.previous_key

    def test_common_prompt_has_explicit_version(self) -> None:
        self.assertEqual(prompt_policy_version, "1.1")
        self.assertIn("v1.1", DATA_HANDLING_PROMPT)
        self.assertIn("참조 token", DATA_HANDLING_PROMPT)

    def test_registered_prompt_keeps_runtime_payload_out_of_system_message(self) -> None:
        prompt = get_prompt("qa.specify")
        messages = prompt_messages(prompt, {"message": "사용자 런타임 입력"})

        self.assertNotIn("사용자 런타임 입력", messages[0].content)
        self.assertIn("사용자 런타임 입력", messages[1].content)

    def test_prompt_invocation_emits_complete_version_metadata(self) -> None:
        prompt = get_prompt("qa.specify")
        records = []
        with bind_prompt_trace_sink(records.append):
            secure_invoke(
                FakeModel("정상 응답"),
                prompt_messages(prompt, {"message": "질문"}),
                purpose=ModelPurpose.QA,
                prompt=prompt,
            )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].prompt_id, "qa.specify")
        self.assertEqual(records[0].prompt_version, "1.0.0")
        self.assertEqual(len(records[0].prompt_hash), 64)
        self.assertEqual(records[0].prompt_policy_version, "1.1")

    def test_combined_recorders_emit_one_content_and_metadata_event(self) -> None:
        prompt = get_prompt("qa.specify")
        model_events: list[dict] = []
        prompt_events = []

        with bind_model_call_recorder(
            lambda **event: model_events.append(dict(event))
        ):
            with bind_prompt_trace_sink(prompt_events.append):
                secure_invoke(
                    FakeModel("정상 응답"),
                    prompt_messages(prompt, {"message": "질문"}),
                    purpose=ModelPurpose.QA,
                    operation_name="qa.specify",
                    prompt=prompt,
                )

        self.assertEqual(len(model_events), 1)
        self.assertEqual(prompt_events, [])
        event = model_events[0]
        self.assertEqual(event["prompt_id"], "qa.specify")
        self.assertEqual(event["prompt_version"], "1.0.0")
        self.assertEqual(len(event["prompt_hash"]), 64)
        self.assertTrue(event["prompt_release"])
        self.assertEqual(event["prompt_policy_version"], "1.1")
        self.assertEqual(event["input"][0]["role"], "system")
        self.assertEqual(
            event["output"],
            {"role": "assistant", "content": "정상 응답"},
        )

    def test_structured_output_is_schema_validated_and_retried_once(self) -> None:
        provider = FakeStructuredProvider()
        result = secure_structured_invoke(
            provider,
            StructuredAnswer,
            [SystemMessage(content=secure_system_prompt("Router 규칙"))],
            purpose=ModelPurpose.ROUTER,
        )

        self.assertIs(provider.schema, StructuredAnswer)
        self.assertEqual(provider.runnable.calls, 2)
        self.assertEqual(
            result.model_dump(),
            {"status": "matched", "agent": "qa"},
        )

    def test_direct_identifiers_never_reach_fake_model_prompt(self) -> None:
        raw = {
            "customer_info": {
                "current_form": {
                    "customer_name": "홍길동",
                    "phone_number": "010-1234-5678",
                    "email": "gildong@example.com",
                    "monthly_income": 3_000_000,
                }
            }
        }
        model = FakeModel("정상 응답")
        secure_invoke(
            model,
            [
                SystemMessage(content=secure_system_prompt("입력 보조 규칙")),
                HumanMessage(content=json.dumps(raw, ensure_ascii=False)),
            ],
            purpose=ModelPurpose.INPUT_ASSIST,
            sensitive_payload=raw,
        )

        rendered = repr([message.content for message in model.messages])
        for forbidden in ("홍길동", "010-1234-5678", "gildong@example.com"):
            self.assertNotIn(forbidden, rendered)
        self.assertIn("3000000", rendered)
        self.assertIn(DATA_HANDLING_PROMPT, model.messages[0].content)

    def test_actual_provider_prompt_and_response_are_reported_after_model_masking(self) -> None:
        raw = {
            "customer_info": {
                "customer_name": "홍길동",
                "phone_number": "010-1234-5678",
                "monthly_income": 3_000_000,
            }
        }
        records: list[dict] = []

        def record(**event: object) -> None:
            records.append(dict(event))

        with bind_model_call_recorder(record):
            result = secure_invoke(
                FakeModel("월 소득 기준으로 안내할게요."),
                [
                    SystemMessage(content=secure_system_prompt("QA 실제 시스템 규칙")),
                    HumanMessage(content=json.dumps(raw, ensure_ascii=False)),
                ],
                purpose=ModelPurpose.QA,
                operation_name="qa.specify",
                sensitive_payload=raw,
            )

        self.assertEqual(result.content, "월 소득 기준으로 안내할게요.")
        self.assertEqual(len(records), 1)
        event = records[0]
        self.assertEqual(event["purpose"], "qa")
        self.assertEqual(event["operation_name"], "qa.specify")
        self.assertEqual(event["status"], "succeeded")
        self.assertEqual(event["input"][0]["role"], "system")
        self.assertEqual(event["input"][1]["role"], "user")
        rendered_input = repr(event["input"])
        self.assertIn("QA 실제 시스템 규칙", rendered_input)
        self.assertIn("[FINANCIAL_VALUE_MASKED]", rendered_input)
        self.assertNotIn("3000000", rendered_input)
        self.assertNotIn("홍길동", rendered_input)
        self.assertNotIn("010-1234-5678", rendered_input)
        self.assertEqual(
            event["output"],
            {"role": "assistant", "content": "월 소득 기준으로 안내할게요."},
        )

    def test_observation_failure_does_not_fail_model_call(self) -> None:
        def fail_to_record(**_event: object) -> None:
            raise RuntimeError("simulated observability outage")

        with bind_model_call_recorder(fail_to_record):
            result = secure_invoke(
                FakeModel("정상 응답"),
                [SystemMessage(content=secure_system_prompt("QA 규칙"))],
                purpose=ModelPurpose.QA,
            )

        self.assertEqual(result.content, "정상 응답")

    def test_malicious_phone_or_email_output_is_blocked(self) -> None:
        for content in (
            "담당자 전화번호는 010-9876-5432입니다.",
            "결과를 retire@example.com으로 보냈습니다.",
        ):
            with self.subTest(content=content):
                with self.assertRaises(ModelOutputBlockedError):
                    secure_invoke(
                        FakeModel(content),
                        [SystemMessage(content=secure_system_prompt("QA 규칙"))],
                        purpose=ModelPurpose.QA,
                    )

    def test_issued_reference_token_survives_model_input_and_output_checks(self) -> None:
        token = "[[USER_NAME_a1b2c3d4e5f6_1]]"
        payload = {"customer_info": {"current_form": {"customer_name": token}}}
        model = FakeModel(f"네, {token} 님.")

        result = secure_invoke(
            model,
            [
                SystemMessage(content=secure_system_prompt("입력 보조 규칙")),
                HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
            ],
            purpose=ModelPurpose.INPUT_ASSIST,
            sensitive_payload=payload,
        )

        self.assertIn(token, model.messages[1].content)
        self.assertEqual(result.content, f"네, {token} 님.")

    def test_purpose_allowlist_is_exact_and_never_allows_email(self) -> None:
        payload = {
            "customer_info": {
                "current_form": {
                    "monthly_income": 3_000_000,
                    "email": "user@example.com",
                }
            }
        }
        qa = mask_model_payload(
            payload,
            purpose=ModelPurpose.QA,
            pseudonym_key=TEST_MASKING_KEY,
        )
        planner = mask_model_payload(
            payload,
            purpose=ModelPurpose.PLANNER,
            pseudonym_key=TEST_MASKING_KEY,
        )

        self.assertEqual(
            qa["customer_info"]["current_form"]["monthly_income"],
            3_000_000,
        )
        self.assertEqual(
            planner["customer_info"]["current_form"]["monthly_income"],
            "[FINANCIAL_VALUE_MASKED]",
        )
        self.assertEqual(
            qa["customer_info"]["current_form"]["email"],
            "[DIRECT_IDENTIFIER]",
        )

    def test_nested_task_result_and_artifact_are_masked_before_model_use(self) -> None:
        masked = mask_model_payload(
            {
                "task_results": {
                    "task-1": {
                        "task_id": "task-1",
                        "status": "completed",
                        "artifact": {
                            "type": "reference_value",
                            "producer_task_id": "task-1",
                            "value": 7_310_000,
                            "email": "result-owner@example.com",
                        },
                    }
                }
            },
            purpose=ModelPurpose.PLANNER,
            pseudonym_key=TEST_MASKING_KEY,
        )
        result_key = next(iter(masked["task_results"]))
        artifact = masked["task_results"][result_key]["artifact"]
        self.assertRegex(result_key, r"^task_[0-9a-f]{24}$")
        self.assertEqual(artifact["value"], "[FINANCIAL_VALUE_MASKED]")
        self.assertEqual(artifact["email"], "[DIRECT_IDENTIFIER]")


class CallbackBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_key = settings.masking_pseudonym_key
        settings.masking_pseudonym_key = TEST_MASKING_KEY.decode()

    def tearDown(self) -> None:
        settings.masking_pseudonym_key = self.previous_key

    def test_normal_financial_output_preserves_completed_callback_contract(self) -> None:
        model_result = secure_invoke(
            FakeModel("월 소득 3,000,000원 변경안을 만들었습니다."),
            [SystemMessage(content=secure_system_prompt("입력 보조 응답 규칙"))],
            purpose=ModelPurpose.INPUT_ASSIST,
        )
        raw = CompletedCallback(
            request_id="request-test",
            session_id=731,
            thread_id="thread-test",
            completed_at=datetime(2026, 9, 11, tzinfo=timezone.utc),
            output={
                "response": model_result.content,
                "step_id": "basic-info",
                "action": {
                    "type": "form_change",
                    "target": "draft_form",
                    "user_requested": True,
                    "confidence": 1.0,
                    "changes": [{
                        "section": "incomes",
                        "path": "monthly_income",
                        "operation": "set",
                        "value": 3_000_000,
                        "reason": "사용자 요청",
                    }],
                },
            },
        ).model_dump(mode="json")

        masked = CallbackPayloadMasker(
            pseudonym_key=TEST_MASKING_KEY,
        ).mask(raw)

        self.assertEqual(masked["request_id"], raw["request_id"])
        self.assertEqual(masked["session_id"], raw["session_id"])
        self.assertEqual(masked["thread_id"], raw["thread_id"])
        self.assertEqual(
            masked["output"]["action"]["changes"][0]["value"],
            3_000_000,
        )
        self.assertEqual(
            CompletedCallback.model_validate(masked).model_dump(mode="json"),
            masked,
        )

    def test_callback_masks_direct_identifier_but_keeps_business_shape(self) -> None:
        raw = {
            "contract_version": "2.0",
            "request_id": "request-test",
            "session_id": 731,
            "thread_id": "thread-test",
            "status": "completed",
            "completed_at": "2026-09-11T00:00:00Z",
            "output": {
                "response": "연락처 010-9876-5432로 안내했습니다.",
                "step_id": "basic-info",
                "action": None,
            },
        }
        masked = CallbackPayloadMasker(
            pseudonym_key=TEST_MASKING_KEY,
        ).mask(raw)
        self.assertNotIn("010-9876-5432", masked["output"]["response"])
        self.assertEqual(masked["output"]["action"], None)

    def test_callback_preserves_reference_tokens_for_django_validation(self) -> None:
        token = "[[EMAIL_ADDRESS_a1b2c3d4e5f6_1]]"
        raw = {
            "contract_version": "2.0",
            "request_id": "request-test",
            "session_id": 731,
            "thread_id": "thread-test",
            "status": "completed",
            "completed_at": "2026-09-11T00:00:00Z",
            "output": {
                "response": f"확인 주소는 {token}입니다.",
                "step_id": "basic-info",
                "email": token,
                "action": None,
            },
        }

        masked = CallbackPayloadMasker(
            pseudonym_key=TEST_MASKING_KEY,
        ).mask(raw)

        self.assertEqual(masked["output"]["email"], token)
        self.assertIn(token, masked["output"]["response"])

    def test_callback_preserves_only_form_target_identities_needed_by_backend(self) -> None:
        raw = CompletedCallback(
            request_id="request-test",
            session_id=731,
            thread_id="thread-test",
            completed_at=datetime(2026, 9, 11, tzinfo=timezone.utc),
            output={
                "response": "기존 항목 변경안을 만들었습니다.",
                "step_id": "financial-goals",
                "action": {
                    "type": "form_change",
                    "target": "draft_form",
                    "user_requested": True,
                    "confidence": 1.0,
                    "changes": [{
                        "section": "goal_periods",
                        "path": "items[].amount",
                        "operation": "update",
                        "parent_item_id": 51,
                        "item_id": 501,
                        "value": 361_000,
                        "unit": "KRW",
                        "reason": "승인 기준값",
                    }],
                },
            },
        ).model_dump(mode="json")

        masker = CallbackPayloadMasker(pseudonym_key=TEST_MASKING_KEY)
        first = masker.mask(raw)
        replay = masker.mask(first)

        change = first["output"]["action"]["changes"][0]
        self.assertEqual(change["parent_item_id"], 51)
        self.assertEqual(change["item_id"], 501)
        self.assertEqual(first, replay)


class ProductionModelCallAuditTests(unittest.TestCase):
    TARGETS = (
        "graph/planner.py",
        "graph/router.py",
        "agents/qa.py",
        "agents/input_assist.py",
        "agents/error_review.py",
        "agents/chart_interpret.py",
        "agents/elicit.py",
        "core/context_summary.py",
    )

    def test_no_production_model_call_bypasses_secure_wrapper(self) -> None:
        bypasses: list[str] = []
        for relative in self.TARGETS:
            tree = ast.parse((AI_ROOT / relative).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"invoke", "ainvoke"}
                ):
                    bypasses.append(f"{relative}:{node.lineno}")
        self.assertEqual(bypasses, [])

    def test_all_model_roles_register_versioned_prompt_specs(self) -> None:
        expected = {
            "graph/planner.py": {"PLANNER_PROMPT"},
            "graph/router.py": {"ROUTER_PROMPT"},
            "agents/qa.py": {
                "_SPECIFY_PROMPT",
                "_DECIDE_PROMPT",
                "_ANSWER_PROMPT",
                "_DIRECT_ANSWER_PROMPT",
                "_MERGE_PROMPT",
                "_JUDGE_PROMPT",
                "_REFINE_PROMPT",
            },
            "agents/input_assist.py": {
                "_CLASSIFY_PROMPT",
                "_IDENTIFY_PROMPT",
                "_RESOLVE_PROMPT",
                "_ERROR_CHECK_2_PROMPT",
                "_MERGE_PROMPT",
            },
            "agents/error_review.py": {"REVIEW_PROMPT"},
            "agents/chart_interpret.py": {"CHART_PROMPT"},
            "agents/elicit.py": {"ANALYZE_PROMPT"},
            "core/context_summary.py": {"SUMMARY_PROMPT"},
        }
        for relative, required_names in expected.items():
            tree = ast.parse((AI_ROOT / relative).read_text(encoding="utf-8"))
            registered = {
                target.id
                for node in tree.body
                if isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == "get_prompt"
                for target in node.targets
                if isinstance(target, ast.Name)
            }
            with self.subTest(relative=relative):
                self.assertTrue(required_names.issubset(registered))


if __name__ == "__main__":
    unittest.main()
