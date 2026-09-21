from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from evals.agent_baseline import (
    ModelCallBudget,
    ModelCallLimitError,
    load_fixture,
    present_field_counts,
    review_field_counts,
    summarize_false_positives,
)


AI_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = AI_ROOT.parent
CONTRACT_PATH = (
    REPOSITORY_ROOT
    / "contract-fixtures"
    / "ai-lifecycle"
    / "v2"
    / "agent-evaluations.json"
)


class AgentEvaluationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

    def test_only_exact_contract_cases_gate_and_model_quality_is_baseline(self) -> None:
        policy = self.contract["acceptance_policy"]
        self.assertTrue(policy["contract_fixture_exact_match_required"])
        self.assertEqual(policy["production_model_quality"], "baseline_only")
        self.assertIsNone(policy["production_model_quality_thresholds"])
        self.assertEqual(policy["chart_agent_quality"], "quality_gate")
        self.assertEqual(
            policy["chart_agent_quality_thresholds"]["maximum_unsupported_numeric_claims"],
            0,
        )
        self.assertIsNone(policy["error_review_false_positive_threshold"])
        self.assertEqual(policy["maximum_total_model_calls"], 200)
        self.assertEqual(load_fixture()["model_call_limit"], 200)
        self.assertEqual(load_fixture()["chart_quality_disposition"], "quality_gate")

    def test_failure_responder_messages_match_contract_exactly(self) -> None:
        from graph.aggregation import failure_response

        expected = self.contract["failure_responder_exact_messages"]
        self.assertEqual(
            {reason: failure_response(reason) for reason in expected},
            expected,
        )

    def test_referenced_shared_fixture_cases_exist(self) -> None:
        fixture_root = CONTRACT_PATH.parent
        for filename, keys in self.contract["required_shared_fixture_cases"].items():
            fixture = json.loads((fixture_root / filename).read_text(encoding="utf-8"))
            self.assertLessEqual(set(keys), set(fixture["cases"]))

    def test_present_fields_count_nested_and_list_instances(self) -> None:
        registry = [
            {"section": "goal_periods", "path": "from_age", "cardinality": "nested_list", "sensitivity": "financial_sensitive"},
            {"section": "goal_periods", "path": "items[].amount", "cardinality": "nested_list", "sensitivity": "financial_sensitive"},
            {"section": "goal_periods", "path": "id", "cardinality": "nested_list", "sensitivity": "operational"},
        ]
        counts = present_field_counts({
            "goal_periods": [
                {"from_age": 45, "items": [{"amount": 100}, {"amount": 200}]},
                {"from_age": 60, "items": [{"amount": 300}]},
            ]
        }, registry)
        self.assertEqual(counts, {
            "goal_periods.from_age": 2,
            "goal_periods.items[].amount": 3,
        })

    def test_false_positive_is_measured_once_per_field_instance(self) -> None:
        summary = summarize_false_positives([{
            "evaluated_fields": {"basic_info.age": 1, "basic_info.monthly_income": 1},
            "items": [
                {"section": "basic_info", "path": "age"},
                {"section": "basic_info", "path": "monthly_income"},
                {"section": "basic_info", "path": "monthly_income"},
            ],
        }])
        self.assertEqual(summary["unit"], "field_instance")
        self.assertEqual(summary["totals"]["evaluated"], 2)
        self.assertEqual(summary["totals"]["false_positives"], 2)
        self.assertEqual(summary["totals"]["false_positive_rate"], 1.0)
        self.assertIsNone(summary["acceptance_threshold"])

    def test_failed_reviews_do_not_dilute_false_positive_rate(self) -> None:
        summary = summarize_false_positives([
            {"status": "error", "evaluated_fields": {"basic_info.age": 100}, "items": []},
            {"status": "completed", "evaluated_fields": {"basic_info.age": 1},
             "items": [{"section": "basic_info", "path": "age"}]},
        ])
        self.assertEqual(summary["totals"]["evaluated"], 1)
        self.assertEqual(summary["totals"]["false_positive_rate"], 1.0)

    def test_review_denominator_excludes_duplicate_container_fields(self) -> None:
        from core.field_registry import load_field_registry

        counts = review_field_counts({"goal_periods": [{
            "id": 1, "from_age": 60, "to_age": 70,
            "items": [{"id": 2, "name": "생활비", "amount": 100}],
        }]}, load_field_registry("financial-goals"))
        self.assertNotIn("goal_periods.items", counts)
        self.assertEqual(counts["goal_periods.items[].amount"], 1)
        self.assertEqual(counts["goal_periods.from_age"], 1)


class ModelBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_budget_counts_success_and_blocks_call_over_limit(self) -> None:
        delegate = SimpleNamespace(ainvoke=AsyncMock(return_value={"ok": True}))
        budget = ModelCallBudget(limit=1, timeout_seconds=1)

        self.assertEqual(await budget.invoke("llm:test", delegate, {}), {"ok": True})
        with self.assertRaises(ModelCallLimitError):
            await budget.invoke("llm:test", delegate, {})
        self.assertEqual(budget.used, 1)
        delegate.ainvoke.assert_awaited_once()

    async def test_current_qa_graph_runs_with_the_three_model_call_budget(self) -> None:
        from agents.qa import service as qa
        from agents.qa.model import JudgeResult, PlanResult, QuestionPlan
        from evals.agent_baseline import _run_qa_cases, instrument_agent_models
        from langchain_core.messages import AIMessage

        budget = ModelCallBudget(limit=3, timeout_seconds=1)
        with (
            patch.object(qa, "_plan_chain", SimpleNamespace(ainvoke=AsyncMock(return_value=PlanResult(
                questions=[QuestionPlan(question="안녕", need_retrieval=False)],
            )))),
            patch.object(qa, "_judge_chain", SimpleNamespace(ainvoke=AsyncMock(return_value=JudgeResult(status="good")))),
            patch.object(qa, "llm", SimpleNamespace(ainvoke=AsyncMock(return_value=AIMessage(content="안녕하세요")))),
            patch("rag.retriever.get_retriever", return_value=SimpleNamespace(aretrieve=AsyncMock(return_value=[]))),
            instrument_agent_models(budget),
        ):
            report = await _run_qa_cases([{
                "id": "greeting", "message": "안녕", "step_id": "general",
                "customer_info": {"saved_data": {}, "current_form": {}},
            }], budget)
        case = report["cases"][0]
        self.assertEqual(case["status"], "completed", case)
        self.assertEqual(case["response"], "안녕하세요")
        self.assertEqual(case["model_calls"], 3)
        self.assertEqual(budget.used, 3)


class ErrorReviewIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_production_review_restores_reference_and_keeps_numeric_correction(self) -> None:
        from agents.error_review import service as error_review
        from agents.error_review import model as error_review_model

        result = error_review.ReviewResult(items=[
            error_review.ReviewItem(
                field_ref="f1",
                reason="단위 착오가 명확합니다.",
                suggested_value=2000000,
            ),
        ])
        with patch.object(
            error_review_model,
            "_review_chain",
            SimpleNamespace(ainvoke=AsyncMock(return_value=result)),
        ):
            state = await error_review._run({
                "step_id": "basic-info",
                "customer_info": {"current_form": {"monthly_income": 20_000}},
            })

        items = state["output"]["review"]["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["path"], "monthly_income")
        self.assertEqual(items[0]["current_value"], 20000)
        self.assertEqual(items[0]["suggested_value"], 2000000)
        self.assertNotIn("field_ref", items[0])


if __name__ == "__main__":
    unittest.main()
