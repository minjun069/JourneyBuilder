from __future__ import annotations

import importlib.util
import os
import unittest
from datetime import date
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
        from agents.input_assist_defaults import build_default_proposals

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
        from agents.input_assist_defaults import build_default_proposals

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
        from agents.input_assist_defaults import (
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
                    bound_inputs=reference,
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
        from agents.input_assist_defaults import (
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
            {"bound_inputs": {**reference, "unit": "USD"}},
        )
        defaults = {
            "step_id": "financial-goals",
            "message": "전국 가구 평균으로 이 기존 항목을 채워줘",
            "current_form": form(),
            "bound_inputs": reference,
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
            build_bound_reference_proposals(**{**defaults, "bound_inputs": old_reference})

    def test_expired_benchmark_and_inconsistent_age_range_fail_closed(self) -> None:
        configure_runtime()
        from agents.input_assist_defaults import build_default_proposals

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
    def test_confidence_bands_are_exact_and_low_is_hidden(self) -> None:
        from agents.error_review_confidence import confidence_level, visible_review_items

        self.assertEqual(confidence_level(0.59), "low")
        self.assertEqual(confidence_level(0.60), "medium")
        self.assertEqual(confidence_level(0.849), "medium")
        self.assertEqual(confidence_level(0.85), "high")
        self.assertEqual(
            [item["confidence_level"] for item in visible_review_items([
                {"confidence_level": "low"},
                {"confidence_level": "medium"},
                {"confidence_level": "high"},
            ])],
            ["medium", "high"],
        )

    def test_every_supported_step_has_one_hash_verified_manual(self) -> None:
        configure_runtime()
        from agents.error_review_manuals import SUPPORTED_STEPS, load_error_review_manual

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
            "confidence": 0.9,
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
    async def test_new_official_value_fails_closed_even_with_unrelated_binding(self) -> None:
        configure_runtime()
        from agents import qa

        decision_chain = SimpleNamespace(ainvoke=AsyncMock(return_value=qa.RetrievalDecision(
            need_retrieval=True,
            requests_new_official_value=True,
        )))
        with patch.object(qa, "_decision_chain", decision_chain):
            decision = await qa._decide({
                "message": "연금저축 공식 한도는 얼마인가요?",
                "current_query": "연금저축 공식 한도",
                "customer_info": {},
                "bound_inputs": {
                    "retirement_gap": {"value": 100_000, "unit": "KRW"},
                },
            })

        self.assertFalse(decision["need_retrieval"])
        self.assertTrue(decision["official_value_unavailable"])

    async def test_evidence_shortage_refreshes_query_once_but_quality_retry_keeps_context(self) -> None:
        configure_runtime()
        from agents import qa

        base = {
            "message": "기준의 의미를 설명해줘",
            "bound_inputs": {"value": 100, "unit": "KRW", "source_id": "approved"},
            "current_answer": "답변",
            "current_context": "cached-context",
            "current_query": "원래 검색어",
            "partial_answers": [],
            "query_index": 0,
            "retries": 0,
            "retrieval_refreshes": 0,
            "need_retrieval": True,
        }
        evidence_chain = SimpleNamespace(ainvoke=AsyncMock(return_value=qa.JudgeResult(
            verdict="bad", failure_kind="evidence_insufficient", reason="근거 부족"
        )))
        refine_chain = SimpleNamespace(ainvoke=AsyncMock(return_value=qa.RefineResult(query="보정 검색어")))
        with patch.object(qa, "_judge_chain", evidence_chain), patch.object(qa, "_refine_chain", refine_chain):
            refreshed = await qa._judge(base)
        self.assertEqual(refreshed["current_context"], "")
        self.assertEqual(refreshed["current_query"], "보정 검색어")
        self.assertEqual(refreshed["retrieval_refreshes"], 1)

        quality_chain = SimpleNamespace(ainvoke=AsyncMock(return_value=qa.JudgeResult(
            verdict="bad", failure_kind="answer_quality", reason="표현 부족"
        )))
        with patch.object(qa, "_judge_chain", quality_chain):
            quality_retry = await qa._judge(base)
        self.assertNotIn("current_context", quality_retry)
        self.assertEqual(quality_retry["retry_hint"], "표현 부족")


@unittest.skipUnless(AGENT_RUNTIME_AVAILABLE, "Agent runtime image에서 실행")
class ChartAndFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_chart_without_verified_payload_explains_readiness_without_model(self) -> None:
        configure_runtime()
        from agents import chart_interpret

        result = await chart_interpret._run({
            "message": "차트를 설명해줘",
            "step_id": "lifetime-cashflow",
            "chart_payload": None,
        })
        self.assertIn("준비되지", result["output"]["response"])
        self.assertIsNone(result["output"]["action"])

    async def test_fallback_accepts_only_four_categories(self) -> None:
        configure_runtime()
        from agents import fallback

        for category in fallback.MESSAGES:
            result = fallback._run({"step_id": "intro", "fallback_category": category})
            self.assertTrue(result["output"]["response"])
        with self.assertRaises(ValueError):
            fallback._run({"step_id": "intro", "fallback_category": "capability_unavailable"})

    async def test_active_elicit_qa_pause_gets_only_resume_ui_contract(self) -> None:
        configure_runtime()
        from graph.builder import _pause_elicit_state, _with_elicit_resume_action

        paused = _pause_elicit_state({"phase": "confirming", "pending_value": "안정"})
        self.assertEqual(paused["phase"], "paused")
        self.assertEqual(paused["paused_from"], "confirming")
        self.assertEqual(_with_elicit_resume_action({
            "response": "국민연금 설명",
            "action": None,
        }, paused)["ui_action"], {
            "type": "resume_elicit",
            "label": "이어서 계속",
        })


@unittest.skipUnless(AGENT_RUNTIME_AVAILABLE, "Agent runtime image에서 실행")
class ElicitStateTests(unittest.IsolatedAsyncioTestCase):
    def state(self, request_id: str, message: str, **extra) -> dict:
        return {
            "request_id": request_id,
            "message": message,
            "step_id": "financial-mindset",
            "customer_info": {"saved_data": {}, "current_form": {}},
            "context_scope": {
                "schema_version": "1.0",
                "session_id": 12,
                "thread_id": "user-3-session-12",
            },
            **extra,
        }

    async def test_ui_start_and_one_field_confirmation_create_deterministic_capture(self) -> None:
        configure_runtime()
        from agents import elicit

        started = await elicit._run(self.state("turn-start", "동의", elicit_action="start"))
        self.assertEqual(started["elicit_state"]["phase"], "asking")
        self.assertEqual(started["output"]["response"].count("?"), 1)

        analysis = SimpleNamespace(ainvoke=AsyncMock(return_value=elicit.ElicitAnalysis(
            response_class="substantive",
            captured_text="안정적인 생활을 우선한다",
        )))
        with patch.object(elicit, "_analysis_chain", analysis):
            pending = await elicit._run(self.state(
                "turn-answer",
                "안정적인 생활이 제일 중요해요",
                elicit_state=started["elicit_state"],
            ))
        self.assertEqual(pending["elicit_state"]["phase"], "confirming")
        self.assertIn("가치관 항목", pending["output"]["response"])
        self.assertNotIn("elicit_capture", pending["output"])

        confirmed = await elicit._run(self.state(
            "turn-confirm",
            "네",
            elicit_state=pending["elicit_state"],
        ))
        capture = confirmed["output"]["elicit_capture"]["changes"][0]
        self.assertEqual(capture["path"], "values")
        self.assertEqual(capture["value"], "안정적인 생활을 우선한다")
        self.assertEqual(capture["source_turn_ids"], ["turn-answer"])
        self.assertEqual(confirmed["elicit_state"]["field_index"], 1)

    async def test_corrupted_pause_state_is_not_resumed(self) -> None:
        configure_runtime()
        from agents import elicit

        started = await elicit._run(self.state("turn-start", "동의", elicit_action="start"))
        corrupted = {
            **started["elicit_state"],
            "phase": "paused",
            "paused_from": "unknown",
        }
        result = await elicit._run(self.state(
            "turn-resume",
            "이어서 계속",
            elicit_action="resume",
            elicit_state=corrupted,
        ))

        self.assertNotIn("elicit_state", result)
        self.assertIn("동의", result["output"]["response"])

    async def test_chat_request_without_ui_consent_does_not_start_collection(self) -> None:
        configure_runtime()
        from agents import elicit

        result = await elicit._run(self.state("turn-chat", "질문을 시작해줘"))

        self.assertNotIn("elicit_state", result)
        self.assertIn("동의", result["output"]["response"])
        self.assertIsNone(result["output"]["action"])


if __name__ == "__main__":
    unittest.main()
