"""Regression checks for compact review, chart facts, and conversation memory."""

import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("LLM_PROVIDER", "gemini")
os.environ.setdefault("EMBEDDING_PROVIDER", "gemini")
os.environ.setdefault("GOOGLE_API_KEY", "refactor-test-key")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@postgres/test")
os.environ.setdefault("AI_SERVICE_BEARER_TOKEN", "refactor-test-bearer")
os.environ.setdefault("AI_MASKING_PSEUDONYM_KEY", "refactor-test-masking-key-at-least-32-bytes")

from agents.chart_interpret.context import project_chart_facts
from agents.chart_interpret.policy import unauthorized_numeric_claims
from agents.error_review.model import ReviewItem, ReviewResult
from agents.error_review.policy import build_review_fields, decode_review_item, validated_item
from agents.error_review import service as review_service
from core.context import ContextScope, ContextSummary, ConversationTurn, StructuredConversationState, SummaryJob
from core.context_summary import SummaryCandidate, SummaryClaim, SummaryValidationError, verify_summary_candidate
from core.field_registry import load_field_registry
from core.json_response import json_response_chain


class CompactReviewTests(unittest.IsolatedAsyncioTestCase):
    def test_json_response_keeps_arbitrary_values_without_function_schema(self):
        from langchain_core.language_models.fake_chat_models import FakeListChatModel
        from langchain_core.messages import HumanMessage
        from langchain_core.exceptions import OutputParserException

        for value in (3000000, 0.03, False, "연금형", {"none": False, "count": 3}, [1, "항목"]):
            with self.subTest(value=value):
                model = FakeListChatModel(responses=[json.dumps({"items": [{"field_ref": "f1", "reason": "확인", "suggested_value": value}]})])
                chain = json_response_chain(model, ReviewResult)
                self.assertNotIn("tools", chain.first.kwargs)
                self.assertNotIn("response_schema", chain.first.kwargs)
                result = chain.invoke([HumanMessage(content="JSON으로 응답")])
                self.assertEqual(result.items[0].suggested_value, value)
                self.assertIs(type(result.items[0].suggested_value), type(value))
        invalid = json_response_chain(FakeListChatModel(responses=['{"items":[{"field_ref":"f1"}]}']), ReviewResult)
        with self.assertRaises(OutputParserException):
            invalid.invoke([HumanMessage(content="JSON으로 응답")])

    def test_list_and_nested_references_restore_the_exact_row(self):
        registry = load_field_registry("financial-goals")
        current_form = {"goal_periods": [
            {"id": 11, "from_age": 60, "to_age": 70, "items": [{"id": 21, "name": "생활비", "amount": 100}]},
            {"client_item_id": "new-parent", "from_age": 80, "to_age": 70,
             "items": [{"client_item_id": "new-child", "name": "여행비", "amount": 200}]},
        ]}
        fields, references = build_review_fields(current_form, registry)
        self.assertNotIn("new-parent", str(fields))
        self.assertNotIn("new-child", str(fields))
        self.assertTrue(all(set(field) == {"ref", "name", "unit", "value"} for field in fields))
        target = next(field for field in fields if field["value"] == 200)
        item = decode_review_item({"field_ref": target["ref"], "reason": "확인", "suggested_value": 300}, references)
        self.assertEqual(item["client_item_id"], "new-child")
        self.assertEqual(item["parent_client_item_id"], "new-parent")
        self.assertEqual(item["current_value"], 200)
        result = validated_item(item, step_id="financial-goals", current_form=current_form, field_registry=registry)
        self.assertEqual(result["suggested_value"], 300)
        self.assertNotIn("confidence", result)

    def test_unknown_field_reference_is_rejected(self):
        self.assertIsNone(decode_review_item({"field_ref": "missing", "reason": "확인"}, {}))

    def test_invalid_correction_does_not_remove_the_review_reason(self):
        registry = load_field_registry("basic-info")
        form = {"retirement_age": 90}
        fields, references = build_review_fields(form, registry)
        item = decode_review_item({"field_ref": fields[0]["ref"], "reason": "은퇴 계획을 확인하세요", "suggested_value": -10}, references)
        result = validated_item(item, step_id="basic-info", current_form=form, field_registry=registry)
        self.assertEqual(result["current_value"], 90)
        self.assertEqual(result["reason"], "은퇴 계획을 확인하세요")
        self.assertIsNone(result["suggested_value"])

    def test_numeric_review_drops_amount_token_but_keeps_native_candidates(self):
        registry = load_field_registry("basic-info")
        form = {"monthly_income": 3000000, "job": "직장인"}
        fields, references = build_review_fields(form, registry)
        for current, candidate, expected in (
            (3000000, "[[FINANCIAL_VALUE_aabbccddeeff_1]]", None),
            (3000000, 3200000, 3200000),
            ("직장인", "공무원", "공무원"),
        ):
            with self.subTest(candidate=candidate):
                target = next(field for field in fields if field["value"] == current)
                item = decode_review_item({
                    "field_ref": target["ref"],
                    "reason": "입력한 값을 확인하세요",
                    "suggested_value": candidate,
                }, references)
                result = validated_item(
                    item, step_id="basic-info", current_form=form, field_registry=registry,
                )
                self.assertEqual(result["current_value"], current)
                self.assertEqual(result["reason"], "입력한 값을 확인하세요")
                self.assertEqual(result["suggested_value"], expected)

    async def test_service_passes_only_compact_inputs_and_restores_current_value(self):
        async def fake_review(*, prompt_payload, current_form):
            self.assertEqual(set(prompt_payload), {"fields", "review_rules"})
            target = next(field for field in prompt_payload["fields"] if field["value"] == 95)
            return ReviewResult(items=[ReviewItem(field_ref=target["ref"], reason="종료 나이를 확인하세요")])
        with patch.object(review_service, "review_draft", side_effect=fake_review):
            result = await review_service._run({"step_id": "basic-info", "customer_info": {"current_form": {"retirement_age": 95, "end_age": 90}}})
        item = result["output"]["review"]["items"][0]
        self.assertEqual(item["path"], "retirement_age")
        self.assertEqual(item["current_value"], 95)
        self.assertNotIn("field_ref", item)
        self.assertNotIn("confidence", item)


class ChartFactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[2]
        fixture = json.loads((root / "contract-fixtures/ai-lifecycle/v2/chart-payloads.json").read_text())
        cls.payload = fixture["cases"]["lifetime_cashflow_ready"]["django_ai_request_fragment"]["chart_payload"]

    def test_report_metadata_is_neither_model_context_nor_numeric_evidence(self):
        facts = project_chart_facts(self.payload)
        for key in ("schema_version", "source_report", "input_snapshot_digest", "provenance", "knowledge_scope", "assumptions"):
            self.assertNotIn(f'"{key}"', json.dumps(facts))
        self.assertEqual(unauthorized_numeric_claims("701원입니다", self.payload), {"701"})
        self.assertEqual(unauthorized_numeric_claims("2026년입니다", self.payload), {"2026"})

    def test_percentage_display_has_one_shared_input_and_validation_policy(self):
        facts = project_chart_facts(self.payload)
        self.assertIn({"label": "물가상승률", "display": "3%"}, facts["assumption_details"])
        self.assertEqual(unauthorized_numeric_claims("물가상승률 3%", facts), set())
        self.assertEqual(unauthorized_numeric_claims("물가상승률 0.03", facts), {"0.03"})
        fraction_only = {"status": "ready", "summary": {"ratio": 0.12}}
        self.assertEqual(unauthorized_numeric_claims("12%", fraction_only), {"12"})

    def test_entry_projection_omits_long_series_but_keeps_display_values(self):
        facts = project_chart_facts(self.payload, entry=True)
        self.assertNotIn("series", facts)
        self.assertNotIn("details", facts)
        self.assertLessEqual(len(facts["highlights"]), 3)
        self.assertEqual(unauthorized_numeric_claims("713,000,000원", facts), set())


class BroadConversationMemoryTests(unittest.TestCase):
    def job(self, *, user="", assistant="", previous=None):
        return SummaryJob(
            scope=ContextScope(1, "thread"), base_cursor=0, target_cursor=1, attempts=0,
            existing_summary=previous, existing_structured_state=StructuredConversationState(),
            source_turns=(ConversationTurn("r1", user, assistant),),
        )

    def test_financial_numbers_masked_tokens_and_response_preferences_are_retained(self):
        job = self.job(user="월 소득은 300만원이고 [[customer-1]]의 답변은 3줄로 부탁해")
        candidate = SummaryCandidate(claims=[SummaryClaim(
            kind="context", value="월 소득 300만원, [[customer-1]]은 3줄 답변 선호",
            evidence_quote="월 소득은 300만원이고 [[customer-1]]의 답변은 3줄로 부탁해",
        )])
        summary, state = verify_summary_candidate(job, candidate)
        self.assertIn("300만원", summary.text)
        self.assertIn("[[customer-1]]", state.entities[0])
        self.assertIn("3줄", state.entities[0])

    def test_assistant_proposal_is_remembered_with_its_origin(self):
        job = self.job(assistant="다음에는 연금 일정을 비교해 보면 좋겠습니다")
        candidate = SummaryCandidate(claims=[SummaryClaim(
            kind="context", value="연금 일정 비교를 제안함", evidence_quote="연금 일정을 비교해 보면 좋겠습니다",
        )])
        _summary, state = verify_summary_candidate(job, candidate)
        self.assertEqual(state.entities, ("AI: 연금 일정 비교를 제안함",))
        self.assertEqual(state.decisions, ())

    def test_previous_summary_can_supply_evidence(self):
        job = self.job(previous=ContextSummary("이전에 60세 은퇴를 논의함", 20, 0))
        candidate = SummaryCandidate(claims=[SummaryClaim(
            kind="context", value="60세 은퇴 논의", evidence_quote="60세 은퇴를 논의함",
        )])
        _summary, state = verify_summary_candidate(job, candidate)
        self.assertEqual(state.entities, ("이전 요약: 60세 은퇴 논의",))

    def test_quote_must_still_exist_in_the_scoped_source(self):
        job = self.job(user="이전에 은퇴를 논의했어")
        candidate = SummaryCandidate(claims=[SummaryClaim(
            kind="context", value="없는 대화", evidence_quote="다른 세션의 발화",
        )])
        with self.assertRaises(SummaryValidationError):
            verify_summary_candidate(job, candidate)
