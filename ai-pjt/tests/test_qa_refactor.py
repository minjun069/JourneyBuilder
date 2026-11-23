from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError


os.environ.setdefault("LLM_PROVIDER", "gemini")
os.environ.setdefault("EMBEDDING_PROVIDER", "gemini")
os.environ.setdefault("GOOGLE_API_KEY", "qa-refactor-test-key")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("AI_SERVICE_BEARER_TOKEN", "qa-refactor-bearer")
os.environ.setdefault("AI_MASKING_PSEUDONYM_KEY", "qa-refactor-masking-key-at-least-32-bytes")

from agents.qa import service as qa
from agents.qa.builder import build_graph
from agents.qa.policy import relevant_customer_info


def question(text: str, query: str | None = None) -> qa.QuestionPlan:
    return qa.QuestionPlan(question=text, need_retrieval=query is not None, query=query)


class QAPlanContractTests(unittest.TestCase):
    def test_plan_rejects_empty_or_unbounded_question_lists(self) -> None:
        for questions in ([], [question("질문")] * 4):
            with self.subTest(count=len(questions)), self.assertRaises(ValidationError):
                qa.PlanResult(questions=questions)

    def test_search_query_is_required_only_when_searching(self) -> None:
        with self.assertRaises(ValidationError):
            qa.QuestionPlan(question="용어 설명", need_retrieval=True, query=" ")
        direct = qa.QuestionPlan(question="내 나이", need_retrieval=False, query="불필요")
        self.assertIsNone(direct.query)

    def test_failed_review_requires_a_reason_without_duplicate_verdict(self) -> None:
        with self.assertRaises(ValidationError):
            qa.JudgeResult(status="answer_quality", reason=" ")
        with self.assertRaises(ValidationError):
            qa.JudgeResult(status="good", verdict="bad")
        self.assertEqual(qa.JudgeResult(status="good", reason="군더더기").reason, "")


class QACustomerContextTests(unittest.TestCase):
    def test_general_term_question_has_no_customer_information(self) -> None:
        self.assertEqual(relevant_customer_info(
            "유동성의 뜻을 설명해 줘",
            {"current_form": {"balance": 900}, "saved_data": {"basic_info": {"age": 55}}},
            "liabilities",
        ), {})

    def test_unclear_question_keeps_current_form_but_not_whole_saved_snapshot(self) -> None:
        context = relevant_customer_info(
            "이 내용을 알려 줘",
            {"current_form": {"age": 55, "client_item_id": "internal"},
             "saved_data": {"basic_info": {"age": 40}, "financial_assets": [{"value": 999}]}},
            "basic-info",
        )
        self.assertEqual(context, {"current_form": {"age": 55}})

    def test_explicit_saved_field_is_selected_without_unrelated_information(self) -> None:
        context = relevant_customer_info(
            "제 나이는 얼마인가요?",
            {"current_form": {"assets": []},
             "saved_data": {"basic_info": {"age": 55, "monthly_income": 999999}}},
            "expenses",
        )
        self.assertEqual(context["saved_data"], {"basic_info": {"age": 55}})

    def test_current_null_value_is_not_replaced_by_old_saved_value(self) -> None:
        context = relevant_customer_info(
            "제 나이를 알려 줘",
            {"current_form": {"age": None}, "saved_data": {"basic_info": {"age": 55}}},
            "basic-info",
        )
        self.assertEqual(context, {"current_form": {"age": None}})


class QAFlowTests(unittest.IsolatedAsyncioTestCase):
    async def run_flow(self, *, plans, reviews=None, answers=None, state=None):
        remaining_plans = list(plans)
        remaining_reviews = list(reviews or [qa.JudgeResult(status="good")])
        remaining_answers = list(answers or ["확인된 답변입니다."])
        calls = []
        events = []

        async def invoke(_chain, payload, **kwargs):
            operation = kwargs["operation_name"]
            events.append(operation)
            calls.append((operation, payload))
            if operation == "qa.plan":
                return remaining_plans.pop(0)
            if operation == "qa.generate":
                return SimpleNamespace(content=remaining_answers.pop(0))
            if operation == "qa.judge":
                return remaining_reviews.pop(0)
            self.fail(f"unexpected model call: {operation}")

        async def search(query):
            events.append(f"search:{query}")
            return [query]

        def payload(prompt, value, *, step_id=None):
            step_id = step_id if step_id is not None else value.get("step_id")
            if step_id is not None:
                from prompts.screens import screen_context
                value = {**value, "screen_context": screen_context(step_id)}
            prompt.validate_payload(value)
            return value

        retriever = SimpleNamespace(aretrieve=AsyncMock(side_effect=search))
        initial = {
            "message": "A와 B를 설명해 줘", "step_id": "basic-info",
            "customer_info": {"current_form": {"age": 55}},
            **(state or {}),
        }
        with (
            patch.object(qa, "secure_ainvoke", side_effect=invoke),
            patch.object(qa, "prompt_messages", side_effect=payload),
            patch.object(qa, "get_retriever", return_value=retriever),
            patch.object(qa, "format_docs", side_effect=lambda nodes: f"자료:{nodes[0]}"),
        ):
            result = await build_graph().ainvoke(initial)
        return result, calls, events, retriever

    async def test_multiple_questions_search_first_then_answer_and_review_once(self) -> None:
        plan = qa.PlanResult(questions=[question("A란 무엇인가", "A"), question("B란 무엇인가", "B")])
        result, calls, events, _ = await self.run_flow(plans=[plan])
        self.assertEqual(events, ["qa.plan", "search:A", "search:B", "qa.generate", "qa.judge"])
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[1][1]["question"], "A와 B를 설명해 줘")
        self.assertEqual(calls[1][1]["evidence"], [
            {"question": "A란 무엇인가", "reference": "자료:A"},
            {"question": "B란 무엇인가", "reference": "자료:B"},
        ])
        self.assertEqual(result["output"]["response"], "확인된 답변입니다.")

    async def test_followup_keeps_original_question_history_and_report_through_graph(self) -> None:
        warning = "연금 수령액은 계획 종료 연령까지만 반영했습니다."
        answer = f"앞서 말씀드린 항목은 연금 수령 기간입니다. {warning}"
        state = {
            "message": "앞서 확인할 항목을 구체적으로 설명한다.",
            "user_message": "하나라면서요. 그게 뭔데요?",
            "conversation_context": {
                "summary": {"text": "시뮬레이션을 설명하는 대화", "version": 3},
                "turns": [{"request_id": "storage-request", "user_message": "계획대로 살면 되나요?",
                           "assistant_message": "확인할 연금 수령 기간 항목이 있습니다."}],
                "retrieved_memories": [{"score": 0.9, "turn": {
                    "user_message": "연금 기간도 확인해 주세요.", "assistant_message": "확인하겠습니다.",
                }}],
            },
            "chart_payload": {
                "status": "ready", "title": "은퇴 시뮬레이션", "schema_version": "2.1",
                "warnings": [{"code": "pension_horizon_limited", "field_name": "pension_assets[0]",
                              "message": warning}],
            },
        }
        result, calls, _, _ = await self.run_flow(
            plans=[qa.PlanResult(questions=[question("확인할 연금 수령 기간")])],
            answers=[answer], state=state,
        )
        for _, payload in calls:
            self.assertEqual(payload["user_message"], state["user_message"])
            self.assertEqual(payload["question"], state["message"])
            self.assertEqual(payload["conversation"]["recent_messages"][0]["assistant_message"],
                             "확인할 연금 수령 기간 항목이 있습니다.")
            self.assertEqual(payload["report"]["warnings"], [{"message": warning}])
            self.assertNotIn("storage-request", str(payload))
            self.assertNotIn("schema_version", str(payload))
            self.assertNotIn("pension_assets[0]", str(payload))
        self.assertEqual(result["output"]["response"], answer)

    async def test_unready_report_does_not_authorize_old_numeric_facts(self) -> None:
        _, calls, _, _ = await self.run_flow(
            plans=[qa.PlanResult(questions=[question("확인할 항목")])],
            state={"chart_payload": {"status": "guidance_only", "title": "은퇴 시뮬레이션",
                                     "highlights": [{"label": "오래된 값", "display": "99999원"}],
                                     "warnings": [{"message": "보고서를 다시 생성해 주세요."}]}},
        )
        for _, payload in calls:
            self.assertNotIn("99999", str(payload))
            self.assertIn("보고서를 다시 생성", str(payload["report"]))

    async def test_direct_answer_and_review_receive_identical_selected_facts(self) -> None:
        state = {
            "message": "제 나이는 얼마인가요?",
            "customer_info": {"current_form": {"age": 55}, "saved_data": {"unrelated": 999999}},
        }
        _, calls, _, retriever = await self.run_flow(
            plans=[qa.PlanResult(questions=[question("제 나이")])], state=state,
        )
        self.assertEqual(len(calls), 3)
        self.assertEqual(set(calls[0][1]), {
            "question", "user_message", "conversation", "report", "step_id", "feedback",
            "reference_options", "screen_context",
        })
        generated, reviewed = calls[1][1], calls[2][1]
        self.assertEqual(generated["customer_info"], {"current_form": {"age": 55}})
        for key in ("question", "user_message", "conversation", "report", "customer_info", "bindings", "evidence"):
            self.assertEqual(generated[key], reviewed[key])
        retriever.aretrieve.assert_not_awaited()

    async def test_same_search_query_is_fetched_once_and_linked_to_both_questions(self) -> None:
        plan = qa.PlanResult(questions=[question("A 정의", "A"), question("A 주의점", "A")])
        _, calls, _, retriever = await self.run_flow(plans=[plan])
        retriever.aretrieve.assert_awaited_once_with("A")
        self.assertEqual(len(calls[1][1]["evidence"]), 2)

    async def test_quality_retry_reuses_evidence_and_never_replans(self) -> None:
        _, calls, events, retriever = await self.run_flow(
            plans=[qa.PlanResult(questions=[question("A 정의", "A")])],
            reviews=[qa.JudgeResult(status="answer_quality", reason="B 설명도 포함"), qa.JudgeResult(status="good")],
            answers=["첫 답변", "보완 답변"],
        )
        self.assertEqual(len(calls), 5)
        self.assertEqual(events.count("qa.plan"), 1)
        retriever.aretrieve.assert_awaited_once_with("A")
        self.assertEqual(calls[1][1]["evidence"], calls[3][1]["evidence"])
        self.assertEqual(calls[3][1]["feedback"], "B 설명도 포함")

    async def test_evidence_retry_replans_once_and_stops_after_second_failed_review(self) -> None:
        result, calls, events, _ = await self.run_flow(
            plans=[qa.PlanResult(questions=[question("A 정의", "A")]),
                   qa.PlanResult(questions=[question("A 정의", "A 공식 명칭")])],
            reviews=[qa.JudgeResult(status="evidence_insufficient", reason="정의가 없음")] * 2,
            answers=["검증 전 답변", "여전히 근거 없는 답변"],
        )
        self.assertEqual(len(calls), 6)
        self.assertEqual(events.count("qa.plan"), 2)
        self.assertEqual(calls[3][1]["feedback"], "정의가 없음")
        self.assertNotIn("여전히 근거 없는", result["output"]["response"])

    async def test_unrelated_calculation_does_not_authorize_official_value_search(self) -> None:
        result, calls, _, retriever = await self.run_flow(
            plans=[qa.PlanResult(questions=[question("연금저축 공식 한도는 얼마인가요?", "연금저축 한도")])],
            state={"message": "연금저축 공식 한도는 얼마인가요?",
                   "task_input": {"bindings": {"retirement_gap": {"value": 100000, "unit": "KRW"}}}},
        )
        self.assertEqual(len(calls), 1)
        retriever.aretrieve.assert_not_awaited()
        self.assertEqual(result["output"]["response"], qa.OFFICIAL_VALUE_UNAVAILABLE)
        self.assertNotIn("RAG", result["output"]["response"])

    async def test_bound_value_is_preserved_but_internal_source_id_is_not_in_model_input(self) -> None:
        _, calls, _, _ = await self.run_flow(
            plans=[qa.PlanResult(questions=[question("기준 30%의 뜻", "부채 비율")])],
            state={"task_input": {"bindings": {"value": 30, "unit": "percent", "source_id": "internal-policy"}}},
        )
        self.assertEqual(calls[1][1]["bindings"], {"value": 30, "unit": "percent"})
        self.assertNotIn("internal-policy", repr(calls))


if __name__ == "__main__":
    unittest.main()
