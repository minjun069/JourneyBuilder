from __future__ import annotations

import importlib.util
import os
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


AGENT_RUNTIME_AVAILABLE = all(
    importlib.util.find_spec(module) is not None
    for module in ("langchain_core", "langgraph")
)


def configure_runtime() -> None:
    os.environ.setdefault("LLM_PROVIDER", "gemini")
    os.environ.setdefault("EMBEDDING_PROVIDER", "gemini")
    os.environ.setdefault("GOOGLE_API_KEY", "agent-feature-test-key")
    os.environ.setdefault("DATABASE_URL", "postgresql://test:test@postgres/test")
    os.environ.setdefault("AI_SERVICE_BEARER_TOKEN", "agent-feature-bearer")
    os.environ.setdefault(
        "AI_MASKING_PSEUDONYM_KEY",
        "agent-feature-masking-key-at-least-32-bytes",
    )


class InputAssistDefaultPolicyTests(unittest.TestCase):
    def test_only_empty_existing_household_rows_receive_benchmarks(self) -> None:
        configure_runtime()
        from agents.input_assist.defaults import build_default_proposals

        proposals = build_default_proposals(
            step_id="expenses",
            message="전국 가구 평균으로 채워줘",
            current_form={"assets": [
                {"client_item_id": "keep-zero", "name": "교통비", "amount": 0},
                {"client_item_id": "fill", "name": "식비/생활비", "amount": None},
                {"client_item_id": "unmapped", "name": "기타", "amount": None},
            ]},
            today=date(2026, 9, 12),
        )

        amount_proposals = [item for item in proposals if item["path"] == "amount"]
        self.assertEqual(len(amount_proposals), 1)
        self.assertEqual(amount_proposals[0]["client_item_id"], "fill")
        self.assertEqual(amount_proposals[0]["policy_value"], 449_000)
        self.assertEqual(amount_proposals[0]["policy_metadata"]["assumption_level"], "household_benchmark")

    def test_unmapped_step_and_unconfirmed_birth_year_do_not_generate_values(self) -> None:
        configure_runtime()
        from agents.input_assist.defaults import build_default_proposals

        self.assertEqual(build_default_proposals(
            step_id="income",
            message="평균값으로 채워줘",
            current_form={"assets": [{"client_item_id": "row", "amount": None}]},
            today=date(2026, 9, 10),
        ), [])
        pension = build_default_proposals(
            step_id="pension-assets",
            message="평균값으로 채워줘",
            current_form={"assets": [{
                "client_item_id": "pension-row",
                "type": "국민연금",
                "receive_method": None,
                "pension_period": None,
                "pension_start_age": None,
            }]},
            today=date(2026, 9, 10),
        )
        self.assertEqual({item["path"] for item in pension}, {"receive_method"})

        confirmed = build_default_proposals(
            step_id="pension-assets",
            message="노령연금 수급요건 충족을 확인했고 1969년생입니다",
            current_form={"assets": [{
                "client_item_id": "pension-row",
                "type": "국민연금",
                "receive_method": None,
                "pension_period": None,
                "pension_start_age": None,
            }]},
            today=date(2026, 9, 10),
        )
        self.assertEqual(
            {item["path"] for item in confirmed},
            {"receive_method", "pension_period", "pension_start_age"},
        )

    def test_all_household_references_target_only_the_matching_existing_before_row(self) -> None:
        configure_runtime()
        from agents.input_assist.defaults import (
            build_bound_reference_proposals,
            load_default_policy,
        )
        from tools.financial_policy import lookup_approved_reference_value

        mapping = load_default_policy()["household_expense_reference_ids"]
        for name, reference_id in mapping.items():
            with self.subTest(reference_id=reference_id):
                reference = lookup_approved_reference_value(
                    reference_id=reference_id,
                    age=45,
                    period="monthly",
                    population="kr_general_households_1plus",
                    effective_on=date(2026, 9, 12),
                )
                proposals = build_bound_reference_proposals(
                    step_id="financial-goals",
                    message="전국 가구 평균으로 이 기존 항목을 채워줘",
                    current_form={"goal_periods": [{
                        "id": 51,
                        "phase": "before",
                        "items": [{"id": 501, "name": name, "amount": None}],
                    }]},
                    bindings=reference,
                    user_requested_change=True,
                    today=date(2026, 9, 12),
                )

                self.assertEqual(len(proposals), 1)
                proposal = proposals[0]
                self.assertEqual(proposal["section"], "goal_periods")
                self.assertEqual(proposal["path"], "items[].amount")
                self.assertEqual(proposal["operation"], "update")
                self.assertEqual(proposal["parent_item_id"], 51)
                self.assertEqual(proposal["item_id"], 501)
                self.assertEqual(proposal["policy_value"], reference["value"])
                self.assertEqual(
                    proposal["policy_metadata"]["reference"]["reference_id"],
                    reference_id,
                )

    def test_bound_reference_target_mismatches_fail_closed(self) -> None:
        configure_runtime()
        from agents.input_assist.defaults import (
            ReferenceTargetNotAllowed,
            build_bound_reference_proposals,
        )
        from tools.financial_policy import lookup_approved_reference_value

        reference = lookup_approved_reference_value(
            reference_id="household_monthly_expense_housing_avg",
            age=45,
            period="monthly",
            population="kr_general_households_1plus",
            effective_on=date(2026, 9, 12),
        )

        def form(*, phase="before", parent_id=51, item_id=501, name="주거비", amount=None):
            period = {"phase": phase, "items": [{"name": name, "amount": amount}]}
            if parent_id is not None:
                period["id"] = parent_id
            if item_id is not None:
                period["items"][0]["id"] = item_id
            return {"goal_periods": [period]}

        cases = (
            {"step_id": "expenses"},
            {"user_requested_change": False},
            {"current_form": form(phase="after")},
            {"current_form": form(parent_id=None)},
            {"current_form": form(item_id=None)},
            {"current_form": form(name="식비/생활비")},
            {"current_form": form(amount=0)},
            {"current_form": form(amount=1)},
            {"current_form": {"goal_periods": []}},
            {"current_form": {"goal_periods": form()["goal_periods"] * 2}},
            {"bindings": {**reference, "unit": "USD"}},
        )
        defaults = {
            "step_id": "financial-goals",
            "message": "전국 가구 평균으로 이 기존 항목을 채워줘",
            "current_form": form(),
            "bindings": reference,
            "user_requested_change": True,
            "today": date(2026, 9, 12),
        }
        for overrides in cases:
            with self.subTest(overrides=overrides), self.assertRaises(
                ReferenceTargetNotAllowed
            ):
                build_bound_reference_proposals(**{**defaults, **overrides})

        old_reference = lookup_approved_reference_value(
            reference_id="debt_service_ratio_max",
            age=45,
            period="annual",
            population="retirewise_kr_households",
            effective_on=date(2026, 9, 12),
        )
        with self.assertRaises(ReferenceTargetNotAllowed):
            build_bound_reference_proposals(**{**defaults, "bindings": old_reference})

    def test_expired_benchmark_and_inconsistent_age_range_fail_closed(self) -> None:
        configure_runtime()
        from agents.input_assist.defaults import build_default_proposals

        self.assertEqual(build_default_proposals(
            step_id="expenses",
            message="전국 가구 평균으로 채워줘",
            current_form={"assets": [{
                "client_item_id": "expense-row",
                "name": "교통비",
                "amount": None,
            }]},
            today=date(2028, 1, 1),
        ), [])

        self.assertEqual(build_default_proposals(
            step_id="financial-goals",
            message="기간 기본값으로 채워줘",
            current_form={"goal_periods": [{
                "client_item_id": "period-row",
                "phase": "before",
                "from_age": None,
                "to_age": None,
                "items": [],
            }]},
            saved_data={"basic_info": {
                "age": 65,
                "retirement_age": 60,
                "end_age": 100,
            }},
            today=date(2026, 9, 10),
        ), [])


class ErrorReviewManualTests(unittest.TestCase):
    def test_every_supported_step_has_one_hash_verified_manual(self) -> None:
        configure_runtime()
        from agents.error_review.manuals import SUPPORTED_STEPS, load_error_review_manual

        loaded = [load_error_review_manual(step_id) for step_id in sorted(SUPPORTED_STEPS)]
        self.assertEqual(len(loaded), 10)
        self.assertTrue(all(manual.text and manual.document_id for manual in loaded))

    def test_nested_review_candidate_requires_matching_parent_and_item(self) -> None:
        configure_runtime()
        from agents.error_review import _validated_item

        registry = [{
            "step_id": "financial-goals",
            "section": "goal_periods",
            "path": "items[].amount",
            "cardinality": "nested_list",
            "writable_by_ai": True,
            "operations": ["update"],
            "value_type": "integer",
            "unit": "KRW",
            "nullable": False,
            "constraints": {"min": 0},
        }]
        current_form = {"goal_periods": [{
            "client_item_id": "period-1",
            "items": [{"client_item_id": "item-1", "amount": 20_000}],
        }]}
        candidate = {
            "section": "goal_periods",
            "path": "items[].amount",
            "client_item_id": "item-1",
            "parent_client_item_id": "period-1",
            "current_value": 20_000,
            "reason": "단위 착오가 의심됩니다.",
            "suggested_value": 2_000_000,
        }

        self.assertIsNotNone(_validated_item(
            candidate,
            step_id="financial-goals",
            current_form=current_form,
            field_registry=registry,
        ))
        self.assertIsNone(_validated_item(
            {**candidate, "parent_client_item_id": "other-period"},
            step_id="financial-goals",
            current_form=current_form,
            field_registry=registry,
        ))


@unittest.skipUnless(AGENT_RUNTIME_AVAILABLE, "Agent runtime image에서 실행")
class QARecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_retrieval_records_only_safe_result_metadata(self) -> None:
        configure_runtime()
        from agents.qa import service as qa
        from core.operation_observation import bind_operation_recorder

        records: list[dict] = []
        results = [SimpleNamespace(
            chunk=SimpleNamespace(
                metadata=SimpleNamespace(source_id="approved-pension-guide")
            ),
            score=0.91,
        )]
        retriever = SimpleNamespace(aretrieve=AsyncMock(return_value=results))
        with (
            bind_operation_recorder(
                lambda **event: records.append(dict(event))
            ),
            patch.object(qa, "get_retriever", return_value=retriever),
            patch.object(qa, "format_docs", return_value="승인 문서 context"),
        ):
            result = await qa._search("연금 제도 승인 근거")

        self.assertEqual(result, "승인 문서 context")
        self.assertEqual(len(records), 1)
        event = records[0]
        self.assertEqual(event["operation_name"], "qa.search")
        self.assertEqual(event["resource_ids"], ("approved-pension-guide",))
        self.assertEqual(event["scores"], (0.91,))
        self.assertEqual(event["result_count"], 1)
        self.assertNotIn("query", repr(event).lower())
        self.assertNotIn("연금 제도 승인 근거", repr(event))

    async def test_product_term_question_and_search_are_planned_in_one_model_call(self) -> None:
        configure_runtime()
        from agents.qa import service as qa

        plan_chain = SimpleNamespace(ainvoke=AsyncMock(return_value=qa.PlanResult(
            questions=[qa.QuestionPlan(
                question="유동성 중시에서 유동성이란?", need_retrieval=True,
                query="유동성 중시 유동성 정의",
            )],
        )))
        message = "유동성 중시에서 유동성이 뭐야?"
        with patch.object(qa, "_plan_chain", plan_chain):
            planned = await qa._plan({
                "step_id": "financial-mindset",
                "message": message,
                "feedback": "",
                "task_input": {"description": message, "bindings": {}},
            })

        self.assertEqual(
            planned["questions"],
            [{"question": "유동성 중시에서 유동성이란?", "need_retrieval": True,
              "query": "유동성 중시 유동성 정의"}],
        )
        plan_chain.ainvoke.assert_awaited_once()

    async def test_product_term_explanation_retrieves_then_uses_model(self) -> None:
        configure_runtime()
        from agents.qa import service as qa

        retriever = SimpleNamespace(aretrieve=AsyncMock(return_value=[object()]))
        state = {
            "step_id": "financial-mindset",
            "message": "안정 추구가 정확히 뭐야?",
            "customer_info": {},
            "customer_context": {},
            "task_input": {"description": "용어 설명", "bindings": {}},
            "questions": [{"question": "안정 추구란?", "need_retrieval": True,
                           "query": "안정 추구 정의"}],
            "current_answer": "",
            "feedback": "",
        }
        answer = "손실 가능성과 자산 가치 변동을 낮추는 것을 우선한다는 뜻입니다."
        generated_with_source = (
            f"{answer}\n\n출처:\n"
            "- RetireWise 제품 용어 설명 "
            "(ai-pjt/rag/data/financial-knowledge/product-terms.md)"
        )
        invoke = AsyncMock(
            return_value=SimpleNamespace(content=generated_with_source)
        )
        with (
            patch.object(qa, "get_retriever", return_value=retriever),
            patch.object(qa, "format_docs", return_value="승인된 안정 추구 용어 절"),
            patch.object(qa, "secure_ainvoke", invoke),
        ):
            retrieved = await qa._retrieve(state)
            generated = await qa._generate({**state, **retrieved})

        retriever.aretrieve.assert_awaited_once_with("안정 추구 정의")
        invoke.assert_awaited_once()
        self.assertEqual(retrieved["evidence"], [
            {"question": "안정 추구란?", "reference": "승인된 안정 추구 용어 절"},
        ])
        self.assertEqual(generated["current_answer"], answer)
        self.assertNotIn("출처", generated["current_answer"])
        self.assertNotIn("product-terms.md", generated["current_answer"])

    def test_retrieval_source_disclosure_is_removed_without_losing_answer(
        self,
    ) -> None:
        configure_runtime()
        from agents.qa import service as qa

        answer = qa.strip_retrieval_source_disclosure(
            "핵심 설명입니다. (출처: 제품 용어 문서)\n"
            "[근거: RetireWise 제품 용어 설명 | 발행: S15P21B109]\n"
            "[source_id: retirewise-product-terms-2026-09-17 | "
            "chunk_id: term-liquidity]\n"
            "[원문 위치: ai-pjt/rag/data/financial-knowledge/product-terms.md]\n"
            "\n**참고 문서:**\n"
            "- RetireWise 제품 용어 설명\n"
        )

        self.assertEqual(answer, "핵심 설명입니다.")

    def test_qa_contains_no_fixed_product_term_answer_path(self) -> None:
        configure_runtime()
        from agents.qa import service as qa

        source = Path(qa.__file__).read_text(encoding="utf-8")
        forbidden = (
            "_QUALITATIVE_CHOICE_ANSWERS",
            "_TERM_EXPLANATION_CUES",
            "_qualitative_choice_answer",
            "_is_qualitative_choice_explanation",
            "안정추구",
            "균형추구",
            "위험감수",
            "단기지향",
            "장기지향",
        )
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, source)

@unittest.skipUnless(AGENT_RUNTIME_AVAILABLE, "Agent runtime image에서 실행")
class ChartAndFailureResponderTests(unittest.IsolatedAsyncioTestCase):
    async def test_chart_without_contract_is_treated_as_unsupported_without_model(self) -> None:
        configure_runtime()
        from agents.chart_interpret import service as chart_interpret

        result = await chart_interpret._run({
            "message": "차트를 설명해줘",
            "step_id": "lifetime-cashflow",
            "chart_payload": None,
        })
        self.assertIn("설명 대상이 아닙니다", result["output"]["response"])
        self.assertIsNone(result["output"]["action"])

    async def test_failure_responder_has_four_stable_categories(self) -> None:
        configure_runtime()
        from graph.aggregation import failure_response

        for category in (
            "ambiguous_intent",
            "unsupported_intent",
            "plan_invalid",
            "all_tasks_failed",
        ):
            self.assertTrue(failure_response(category))
        self.assertEqual(
            failure_response("capability_unavailable"),
            "요청을 안전하게 처리하지 못했습니다. 내용을 확인해 다시 시도해 주세요.",
        )

@unittest.skipUnless(AGENT_RUNTIME_AVAILABLE, "Agent runtime image에서 실행")
class ElicitStateTests(unittest.IsolatedAsyncioTestCase):
    async def test_plain_agent_answers_from_conversation_context(self) -> None:
        configure_runtime()
        from agents.elicit import model as elicit_model
        from agents.elicit import service as elicit
        from langchain_core.messages import AIMessage

        context = {"summary": {"text": "안정적인 생활을 중요하게 생각한다."}}
        model = SimpleNamespace(
            ainvoke=AsyncMock(return_value=AIMessage(content="그 기준을 이어서 살펴볼게요."))
        )
        with patch.object(elicit_model, "llm", model):
            result = await elicit._run({
                "request_id": "turn-chat",
                "message": "앞서 말한 기준으로 이야기해 줘.",
                "step_id": "financial-mindset",
                "customer_info": {"saved_data": {}, "current_form": {}},
                "conversation_context": context,
            })

        self.assertEqual(result["output"]["response"], "그 기준을 이어서 살펴볼게요.")
        self.assertIsNone(result["output"]["action"])
        self.assertNotIn("elicit_capture", result["output"])


if __name__ == "__main__":
    unittest.main()
