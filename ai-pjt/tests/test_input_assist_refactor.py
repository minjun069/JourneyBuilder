"""Behavioral coverage for batch extraction, local checks, and optional review."""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


os.environ.setdefault("LLM_PROVIDER", "gemini")
os.environ.setdefault("EMBEDDING_PROVIDER", "gemini")
os.environ.setdefault("GOOGLE_API_KEY", "disposable-test-key")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("AI_SERVICE_BEARER_TOKEN", "disposable-test-bearer")
os.environ.setdefault("AI_MASKING_PSEUDONYM_KEY", "disposable-test-masking-key-at-least-32-bytes")

from agents.input_assist import service
from agents.input_assist.builder import build_graph
from agents.input_assist.model import ChangeReview, ExtractedChange, ExtractResult, ReviewResult


def state_for(step_id="basic-info", form=None, message="나이를 40으로 바꿔줘"):
    return {"step_id": step_id, "message": message, "customer_info": {"current_form": form or {"age": 30}}}


def field_ref(state, path):
    fields, _, _ = service._form_context(state)
    return next(ref for ref, field in fields.items() if field["path"] == path)


class InputAssistRefactorTests(unittest.IsolatedAsyncioTestCase):
    async def run_graph(self, state, extracted, reviewed=None):
        results = [extracted]
        if reviewed is not None:
            results.append(reviewed)
        results.append(SimpleNamespace(content="변경안을 확인한 뒤 적용해 주세요."))
        with patch.object(service, "secure_ainvoke", AsyncMock(side_effect=results)) as invoke:
            result = await build_graph().ainvoke(state)
        return result, invoke

    async def test_multiple_literal_changes_use_two_model_calls_and_keep_old_values(self):
        state = state_for(form={"age": 30, "job": "직장인"}, message="나이 40, 직업 공무원으로 바꿔줘")
        extracted = ExtractResult(request="change", questions=[], changes=[
            ExtractedChange(field_ref=field_ref(state, "age"), operation="set", value=40),
            ExtractedChange(field_ref=field_ref(state, "job"), operation="set", value="공무원"),
        ])
        result, invoke = await self.run_graph(state, extracted)
        self.assertEqual(invoke.await_count, 2)
        self.assertEqual([c["value"] for c in result["output"]["action"]["changes"]], [40, "공무원"])
        self.assertEqual(result["candidates"][0]["description"]["current_value"], 30)
        prompt = str(invoke.await_args_list[-1].args[1])
        self.assertIn("나이", prompt)
        self.assertIn("30", prompt)
        self.assertNotIn("confidence", str(result["output"]))

    async def test_invalid_type_range_and_unknown_ref_are_rejected_before_review(self):
        state = state_for()
        extracted = ExtractResult(request="change", questions=[], changes=[
            ExtractedChange(field_ref=field_ref(state, "age"), operation="set", value=140),
            ExtractedChange(field_ref=field_ref(state, "age"), operation="set", value="40"),
            ExtractedChange(field_ref="untrusted", operation="set", value=40),
        ])
        result, invoke = await self.run_graph(state, extracted)
        self.assertEqual(invoke.await_count, 2)
        self.assertIsNone(result["output"]["action"])
        self.assertEqual(len(result["questions"]), 3)

    async def test_normalized_value_receives_one_batch_review(self):
        state = state_for(message="세후 월 소득 320만 원과 은퇴 예정 나이 65세로 바꿔줘")
        extracted = ExtractResult(request="change", questions=[], changes=[
            ExtractedChange(field_ref=field_ref(state, "monthly_income"), operation="set", value=3200000),
            ExtractedChange(field_ref=field_ref(state, "retirement_age"), operation="set", value=65),
        ])
        reviewed = ReviewResult(items=[ChangeReview(change_ref="c0", accepted=True), ChangeReview(change_ref="c1", accepted=True)])
        result, invoke = await self.run_graph(state, extracted, reviewed)
        self.assertEqual(invoke.await_count, 3)
        self.assertEqual(len(result["output"]["action"]["changes"]), 2)

    async def test_rejected_and_missing_review_verdicts_do_not_become_actions(self):
        state = state_for(message="적당한 월 소득과 은퇴 나이로 바꿔줘")
        extracted = ExtractResult(request="change", questions=[], changes=[
            ExtractedChange(field_ref=field_ref(state, "monthly_income"), operation="set", value=5000000),
            ExtractedChange(field_ref=field_ref(state, "retirement_age"), operation="set", value=65),
        ])
        reviewed = ReviewResult(items=[ChangeReview(change_ref="c0", accepted=False, reason="월 소득을 알려 주세요.")])
        result, _ = await self.run_graph(state, extracted, reviewed)
        self.assertIsNone(result["output"]["action"])
        self.assertEqual(len(result["questions"]), 2)

    async def test_value_from_another_field_cannot_skip_meaning_review(self):
        state = state_for(message="나이는 55, 은퇴 예정 나이는 65로 바꿔줘")
        extracted = ExtractResult(request="change", questions=[], changes=[
            ExtractedChange(field_ref=field_ref(state, "age"), operation="set", value=65),
        ])
        result, invoke = await self.run_graph(state, extracted, ReviewResult(items=[
            ChangeReview(change_ref="c0", accepted=False, reason="현재 나이는 55세입니다."),
        ]))
        self.assertEqual(invoke.await_count, 3)
        self.assertIsNone(result["output"]["action"])

    async def test_literal_value_in_explanation_request_still_requires_review(self):
        state = state_for(message="세후 월 소득 3000000원을 설명해줘")
        extracted = ExtractResult(request="change", questions=[], changes=[
            ExtractedChange(field_ref=field_ref(state, "monthly_income"), operation="set", value=3000000),
        ])
        result, invoke = await self.run_graph(state, extracted, ReviewResult(items=[
            ChangeReview(change_ref="c0", accepted=False, reason="입력 변경 요청이 아닙니다."),
        ]))
        self.assertEqual(invoke.await_count, 3)
        self.assertIsNone(result["output"]["action"])

    async def test_delete_restores_existing_id_and_does_not_resolve_or_emit_value(self):
        state = state_for("financial-assets", {"assets": [{"id": 71, "name": "적금", "current_value": 1200000}]}, "적금 삭제해줘")
        extracted = ExtractResult(request="change", questions=[], changes=[
            ExtractedChange(field_ref=field_ref(state, "name"), item_ref="r0", operation="remove", value=999),
        ])
        result, invoke = await self.run_graph(state, extracted)
        self.assertEqual(invoke.await_count, 2)
        change = result["output"]["action"]["changes"][0]
        self.assertEqual(change["item_id"], 71)
        self.assertNotIn("value", change)

    async def test_nested_update_restores_parent_and_child_ids(self):
        state = state_for("financial-goals", {"goal_periods": [{"id": 10, "from_age": 40, "to_age": 60,
            "items": [{"client_item_id": "child-a", "name": "생활비", "amount": 1000000}]}]}, "생활비 기간별 금액을 2000000으로 바꿔줘")
        extracted = ExtractResult(request="change", questions=[], changes=[
            ExtractedChange(field_ref=field_ref(state, "items[].amount"), item_ref="r1", operation="update", value=2000000),
        ])
        result, invoke = await self.run_graph(state, extracted)
        self.assertEqual(invoke.await_count, 2)
        change = result["output"]["action"]["changes"][0]
        self.assertEqual(change["parent_item_id"], 10)
        self.assertEqual(change["client_item_id"], "child-a")
        self.assertEqual(result["candidates"][0]["description"]["current_value"], 1000000)

    async def test_question_cannot_produce_change_even_if_extraction_contains_one(self):
        state = state_for(message="월 소득은 어디에 입력해?")
        extracted = ExtractResult(request="none", questions=[], changes=[
            ExtractedChange(field_ref=field_ref(state, "age"), operation="set", value=40),
        ])
        result, invoke = await self.run_graph(state, extracted)
        self.assertEqual(invoke.await_count, 2)
        self.assertIsNone(result["output"]["action"])

    async def test_approved_defaults_are_code_owned_and_skip_semantic_review(self):
        state = state_for(form={"age": 40}, message="계획 종료 나이를 승인된 기본값으로 채워줘")
        extracted = ExtractResult(request="fill_defaults", questions=[], changes=[])
        with patch.object(service, "build_default_proposals", return_value=[{
            "section": "basic_info", "path": "end_age", "operation": "set", "policy_value": 100,
        }]):
            result, invoke = await self.run_graph(state, extracted)
        self.assertEqual(invoke.await_count, 2)
        self.assertEqual(result["output"]["action"]["changes"][0]["value"], 100)

    async def test_change_request_can_use_verified_reference_without_repeating_number(self):
        state = state_for(message="그 조회값으로 세후 월 소득을 채워줘")
        state["task_input"] = {"bindings": {
            "value": 3200000, "unit": "KRW", "period": "monthly", "population": "approved_population",
            "source_id": "internal-source", "as_of": "2026-09-01", "calculation_policy_version": "internal-version",
        }}
        extracted = ExtractResult(request="change", questions=[], changes=[
            ExtractedChange(field_ref=field_ref(state, "monthly_income"), operation="set", value=3200000),
        ])
        result, invoke = await self.run_graph(state, extracted, ReviewResult(items=[
            ChangeReview(change_ref="c0", accepted=True),
        ]))
        self.assertEqual(invoke.await_count, 3)
        self.assertEqual(result["output"]["action"]["changes"][0]["value"], 3200000)
        for call in invoke.await_args_list[:2]:
            prompt = str(call.args[1])
            self.assertIn("검증된 조회값", prompt)
            self.assertIn("3200000", prompt)
            self.assertNotIn("internal-source", prompt)
            self.assertNotIn("internal-version", prompt)

    def test_bound_change_cannot_change_verified_value_or_unit(self):
        for value, unit in ((3200001, "KRW"), (3200000, "percent"), ("3200000", "KRW")):
            with self.subTest(value=value, unit=unit):
                state = state_for(message="그 조회값으로 세후 월 소득을 채워줘")
                state["task_input"] = {"bindings": {"value": 3200000, "unit": unit}}
                state["field_refs"], state["item_refs"], _ = service._form_context(state)
                state["questions"] = []
                state["extraction"] = {"request": "change", "changes": [{
                    "field_ref": field_ref(state, "monthly_income"), "item_ref": None,
                    "operation": "set", "value": value,
                }]}
                result = service._validate(state)
                self.assertEqual(result["candidates"], [])
                self.assertEqual(result["review_candidates"], [])
                self.assertIn("조회한 기준값", result["questions"][0])

    def test_change_classification_keeps_approved_reference_target_scope(self):
        state = state_for("financial-goals", {"goal_periods": [{"id": 10, "from_age": 40, "to_age": 60,
            "items": [{"id": 11, "name": "주거비", "amount": None}, {"id": 12, "name": "식비", "amount": None}]}]},
            "그 조회값으로 빈 주거비를 채워줘")
        state["task_input"] = {"bindings": {"reference_id": "housing", "value": 361000, "unit": "KRW"}}
        state["field_refs"], state["item_refs"], payload = service._form_context(state)
        self.assertEqual(payload["reference"], {"value": 361000, "unit": "KRW"})
        proposal = {"section": "goal_periods", "path": "items[].amount", "operation": "update",
                    "item_id": 11, "parent_item_id": 10, "policy_value": 361000}
        for item_ref, accepted in (("r1", True), ("r2", False)):
            with self.subTest(item_ref=item_ref):
                state["questions"] = []
                state["extraction"] = {"request": "change", "changes": [{
                    "field_ref": field_ref(state, "items[].amount"), "item_ref": item_ref,
                    "operation": "update", "value": 361000,
                }]}
                with patch.object(service, "build_bound_reference_proposals", return_value=[proposal]) as build:
                    result = service._validate(state)
                build.assert_called_once()
                self.assertEqual(bool(result["candidates"]), accepted)
                if accepted:
                    change = result["candidates"][0]["change"]
                    self.assertEqual((change["parent_item_id"], change["item_id"], change["value"]), (10, 11, 361000))
                else:
                    self.assertIn("승인된 기존 빈 항목", result["questions"][0])

    async def test_add_requires_nonnullable_fields(self):
        state = state_for("financial-goals", {"goal_milestones": []}, "마일스톤을 추가해줘")
        fields, _, _ = service._form_context(state)
        ref = next(ref for ref, field in fields.items() if field["section"] == "goal_milestones" and field["path"] == "content")
        extracted = ExtractResult(request="change", questions=[], changes=[
            ExtractedChange(field_ref=ref, operation="add", value={"content": "주택 구입"}),
        ])
        result, invoke = await self.run_graph(state, extracted)
        self.assertEqual(invoke.await_count, 2)
        self.assertIsNone(result["output"]["action"])
        self.assertIn("필수값", result["questions"][0])

    def test_model_input_omits_raw_ids_and_unused_security_metadata(self):
        state = state_for("financial-assets", {"assets": [{"id": 71, "name": "적금", "current_value": 1200000}]})
        _, _, payload = service._form_context(state)
        rendered = str(payload)
        self.assertNotIn("sensitivity", rendered)
        self.assertNotIn("writable_by_ai", rendered)
        self.assertNotIn("'id'", rendered)
        self.assertIn("item_ref", rendered)


if __name__ == "__main__":
    unittest.main()
