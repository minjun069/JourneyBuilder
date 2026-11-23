from __future__ import annotations

import os
import unittest

os.environ.setdefault("LLM_PROVIDER", "gemini")
os.environ.setdefault("EMBEDDING_PROVIDER", "gemini")
os.environ.setdefault("GOOGLE_API_KEY", "prompt-masking-test-key")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("AI_SERVICE_BEARER_TOKEN", "prompt-masking-bearer")
os.environ.setdefault("AI_MASKING_PSEUDONYM_KEY", "prompt-masking-key-at-least-32-bytes")

from agents.input_assist import service as input_assist
from core.masking import MaskingPolicy, MaskingSink, mask_payload
from core.model_boundary import (
    ModelPurpose, _prepare_call, mask_model_payload, prompt_messages,
)


class PromptInputMaskingTests(unittest.TestCase):
    def prepare_extraction(self, message, current_form, *, step_id="financial-assets", **customer):
        state = {
            "message": message, "step_id": step_id,
            "customer_info": {"current_form": current_form, **customer},
        }
        _, _, payload = input_assist._form_context(state)
        messages, _ = _prepare_call(
            prompt_messages(input_assist.EXTRACT_PROMPT, payload),
            purpose=ModelPurpose.INPUT_ASSIST,
            sensitive_payload=input_assist._sensitive_payload(state),
            prompt=input_assist.EXTRACT_PROMPT,
        )
        return messages[1].content

    def test_actual_extraction_prompt_keeps_amount_and_distinct_product_names(self) -> None:
        text = self.prepare_extraction(
            "주택청약 원금을 5000000원으로 바꿔줘",
            {"assets": [
                {"client_item_id": "first-row", "name": "주택청약", "principal": 3000000},
                {"client_item_id": "second-row", "name": "국민적금", "principal": 5000000},
            ]},
        )
        self.assertIn("주택청약 원금을 5000000원으로 바꿔줘", text)
        self.assertIn("name: 주택청약", text)
        self.assertIn("name: 국민적금", text)
        self.assertIn("f2: 3000000", text)
        self.assertIn("f2: 5000000", text)
        self.assertNotIn("FINANCIAL_VALUE_MASKED", text)
        self.assertNotIn("first-row", text)

    def test_actual_nested_goal_prompt_keeps_named_item_and_amount(self) -> None:
        text = self.prepare_extraction(
            "여행자금 금액을 2500000원으로 바꿔줘",
            {"goal_periods": [{
                "client_item_id": "period-one", "from_age": 60, "to_age": 70,
                "items": [{"client_item_id": "goal-one", "name": "여행자금", "amount": 2000000}],
            }]},
            step_id="financial-goals",
        )
        self.assertIn("여행자금 금액을 2500000원으로 바꿔줘", text)
        self.assertIn("name: 여행자금", text)
        self.assertIn("2000000", text)
        self.assertNotIn("period-one", text)

    def test_allowed_large_amount_survives_rendering_and_direct_identifier_filter(self) -> None:
        text = self.prepare_extraction(
            "주택청약 원금을 1500000000원으로 바꿔줘",
            {"assets": [{"client_item_id": "row-one", "name": "주택청약", "principal": 1500000000}]},
        )
        self.assertIn("주택청약 원금을 1500000000원으로 바꿔줘", text)
        self.assertIn("f2: 1500000000", text)
        self.assertNotIn("RWALLOWEDNUMBER", text)

    def test_same_digits_in_account_context_are_not_unmasked(self) -> None:
        text = self.prepare_extraction(
            "주택청약 원금을 1500000000원으로 바꿔줘\n계좌번호는 1500000000",
            {"assets": [{"client_item_id": "row-one", "name": "주택청약", "principal": 1500000000}]},
            account_number="1500000000",
        )
        self.assertIn("원금을 1500000000원으로", text)
        self.assertIn("계좌번호는 [DIRECT_IDENTIFIER]", text)
        self.assertNotIn("계좌번호는 1500000000", text)

    def test_money_unit_does_not_override_explicit_identifier_context(self) -> None:
        text = self.prepare_extraction(
            "계좌번호 1500000000원이라고 표시해줘",
            {"assets": [{"client_item_id": "row-one", "name": "주택청약", "principal": 1500000000}]},
        )
        self.assertIn("계좌번호 [DIRECT_IDENTIFIER]원", text)
        self.assertIn("f2: 1500000000", text)

    def test_structured_phone_with_same_numeric_value_remains_hidden(self) -> None:
        from prompts import get_prompt

        amount = 1012345678
        payload = {
            "question": "현재 정보를 알려줘", "feedback": "", "bindings": {}, "evidence": [],
            "user_message": "현재 정보를 알려줘", "conversation": {}, "report": {},
            "customer_info": {"current_form": {"monthly_income": amount, "phone": amount}},
        }
        prompt = get_prompt("qa.answer")
        messages, _ = _prepare_call(
            prompt_messages(prompt, payload), purpose=ModelPurpose.QA,
            sensitive_payload=payload, prompt=prompt,
        )
        self.assertIn(f"monthly_income: {amount}", messages[1].content)
        self.assertIn("phone: [DIRECT_IDENTIFIER]", messages[1].content)

    def test_qa_followup_context_masks_contact_details(self) -> None:
        from prompts import get_prompt

        phone = "010-9876-5432"
        email = "qa-followup@example.com"
        payload = {
            "question": "앞서 말한 항목 설명", "feedback": "", "bindings": {},
            "evidence": [], "customer_info": {},
            "user_message": f"그게 뭔가요? {phone}",
            "conversation": {"recent_turns": [{"user": f"연락처는 {email}"}]},
            "report": {"status": "ready", "warnings": [{"message": f"문의: {phone}"}]},
        }
        prompt = get_prompt("qa.answer")
        messages, _ = _prepare_call(
            prompt_messages(prompt, payload), purpose=ModelPurpose.QA,
            sensitive_payload=payload, prompt=prompt,
        )
        self.assertNotIn(phone, messages[1].content)
        self.assertNotIn(email, messages[1].content)
        self.assertIn("그게 뭔가요?", messages[1].content)

    def test_user_text_cannot_collide_with_number_protection_markers(self) -> None:
        marker = "\x00RWALLOWEDNUMBER0\x00"
        text = self.prepare_extraction(
            f"주택청약 원금을 1500000000원으로 바꿔줘\n표식 {marker}",
            {"assets": [{"client_item_id": "row-one", "name": "주택청약", "principal": 1500000000}]},
        )
        self.assertIn(f"표식 {marker}", text)
        self.assertIn("f2: 1500000000", text)

    def test_runtime_payload_metadata_is_not_copied_to_provider_messages(self) -> None:
        from prompts import get_prompt

        prompt = get_prompt("qa.answer")
        payload = {"question": "제 소득", "customer_info": {"monthly_income": 1500000000},
                   "user_message": "제 소득", "conversation": {}, "report": {},
                   "bindings": {}, "evidence": [], "feedback": ""}
        pending = prompt_messages(prompt, payload)
        messages, _ = _prepare_call(
            pending, purpose=ModelPurpose.QA, sensitive_payload=payload, prompt=prompt,
        )
        self.assertIs(type(messages), list)
        self.assertFalse(hasattr(messages, "payload"))
        self.assertFalse(any(hasattr(message, "payload") for message in messages))
        self.assertTrue(all(message.additional_kwargs == {} for message in messages))

    def test_customer_name_remains_hidden_while_product_name_is_available(self) -> None:
        text = self.prepare_extraction(
            "홍길동의 주택청약 원금을 5000000원으로 바꿔줘",
            {"assets": [{"client_item_id": "row-one", "name": "주택청약", "principal": 3000000}]},
            name="홍길동",
        )
        self.assertNotIn("홍길동", text)
        self.assertIn("[DIRECT_IDENTIFIER]의 주택청약", text)

    def test_name_exception_is_limited_to_canonical_business_rows(self) -> None:
        payload = {"customer_info": {
            "name": "홍길동",
            "current_form": {
                "name": "김사용자", "contact": {"name": "박연락자"},
                "assets": [{"name": "주택청약"}],
                "arbitrary_rows": [{"name": "이비밀값"}],
            },
            "saved_data": {"assets": [{"name": "정과거명"}]},
        }}
        result = mask_model_payload(payload, purpose=ModelPurpose.INPUT_ASSIST)
        self.assertEqual(result["customer_info"]["current_form"]["assets"][0]["name"], "주택청약")
        for value in ("홍길동", "김사용자", "박연락자", "이비밀값", "정과거명"):
            self.assertNotIn(value, repr(result))

    def test_product_label_still_scrubs_embedded_direct_identifiers(self) -> None:
        payload = {"customer_info": {"current_form": {"assets": [{
            "name": "연락처 owner@example.test 010-1234-5678",
        }]}}}
        result = mask_model_payload(payload, purpose=ModelPurpose.INPUT_ASSIST)
        self.assertNotIn("owner@example.test", repr(result))
        self.assertNotIn("010-1234-5678", repr(result))

    def test_reference_tokens_survive_new_input_fields_and_masking(self) -> None:
        token = "[[FINANCIAL_VALUE_abcdef123456_1]]"
        text = self.prepare_extraction(
            f"주택청약 원금을 {token}원으로 바꿔줘",
            {"assets": [{"client_item_id": "row-one", "name": "주택청약", "principal": token}]},
        )
        self.assertGreaterEqual(text.count(token), 2)

    def test_business_names_and_amounts_are_still_masked_in_traces(self) -> None:
        raw = {"message": "주택청약 원금은 5000000원", "customer_info": {"current_form": {
            "assets": [{"name": "주택청약", "principal": 5000000}],
        }}}
        trace = mask_payload(
            raw, sink=MaskingSink.TRACE_EXPORT,
            policy=MaskingPolicy(pseudonym_key=b"prompt-masking-test-key-32-bytes-long"),
        )
        self.assertNotIn("주택청약", repr(trace))
        self.assertNotIn("5000000", repr(trace))

    def test_unallowed_numeric_leaf_is_still_scrubbed_from_free_text(self) -> None:
        result = mask_model_payload({
            "message": "입력 금액 5000000원",
            "customer_info": {"current_form": {"assets": [{"principal": 5000000}]}},
        }, purpose=ModelPurpose.PLANNER)
        self.assertNotIn("5000000", repr(result))


if __name__ == "__main__":
    unittest.main()
