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
        self.assertIsNone(policy["error_review_false_positive_threshold"])
        self.assertEqual(policy["maximum_total_model_calls"], 200)
        self.assertEqual(load_fixture()["model_call_limit"], 200)

    def test_confidence_fixture_matches_runtime_and_low_is_hidden(self) -> None:
        from agents.error_review_confidence import confidence_level, visible_review_items

        for case in self.contract["error_review_confidence_cases"]:
            with self.subTest(confidence=case["confidence"]):
                item = {
                    "confidence": case["confidence"],
                    "confidence_level": confidence_level(case["confidence"]),
                }
                self.assertEqual(item["confidence_level"], case["expected_level"])
                self.assertEqual(bool(visible_review_items([item])), case["expected_visible"])

    def test_fallback_messages_match_contract_exactly(self) -> None:
        from agents.fallback import MESSAGES

        self.assertEqual(MESSAGES, self.contract["fallback_exact_messages"])

    def test_referenced_shared_fixture_cases_exist(self) -> None:
        fixture_root = CONTRACT_PATH.parent
        for filename, keys in self.contract["required_shared_fixture_cases"].items():
            fixture = json.loads((fixture_root / filename).read_text(encoding="utf-8"))
            if filename == "elicit-policy.json":
                self.assertLessEqual(set(keys), set(fixture))
            else:
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

    def test_chart_decimal_rates_accept_equivalent_percent_lexemes(self) -> None:
        from evals.agent_baseline import _numeric_lexemes

        lexemes = _numeric_lexemes({
            "inflation_rate": 0.026,
            "investment_rate": 0.04,
            "wage_growth_rate": 0.03,
        })

        self.assertTrue({"2.6", "4", "3"}.issubset(lexemes))

    def test_false_positive_is_measured_by_field_and_confidence(self) -> None:
        summary = summarize_false_positives([{
            "evaluated_fields": {"basic_info.age": 1, "basic_info.monthly_income": 1},
            "raw_items": [
                {"section": "basic_info", "path": "age", "confidence_level": "low"},
                {"section": "basic_info", "path": "monthly_income", "confidence_level": "high"},
            ],
        }])
        self.assertEqual(summary["unit"], "field_instance")
        self.assertEqual(summary["totals"]["evaluated"], 2)
        self.assertEqual(summary["totals"]["low"], 1)
        self.assertEqual(summary["totals"]["high"], 1)
        self.assertEqual(summary["totals"]["raw_false_positive_rate"], 1.0)
        self.assertEqual(summary["totals"]["visible_false_positive_rate"], 0.5)
        self.assertIsNone(summary["acceptance_threshold"])


class ModelBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_budget_counts_success_and_blocks_call_over_limit(self) -> None:
        delegate = SimpleNamespace(ainvoke=AsyncMock(return_value={"ok": True}))
        budget = ModelCallBudget(limit=1, timeout_seconds=1)

        self.assertEqual(await budget.invoke("llm:test", delegate, {}), {"ok": True})
        with self.assertRaises(ModelCallLimitError):
            await budget.invoke("llm:test", delegate, {})
        self.assertEqual(budget.used, 1)
        delegate.ainvoke.assert_awaited_once()


class ErrorReviewVisibilityIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_production_review_hides_low_and_returns_derived_high_level(self) -> None:
        from agents import error_review

        result = error_review.ReviewResult(items=[
            error_review.ReviewItem(
                section="basic_info",
                path="monthly_income",
                current_value_json="20000",
                reason="가능성만 있습니다.",
                suggested_value_json=None,
                confidence=0.59,
            ),
            error_review.ReviewItem(
                section="basic_info",
                path="monthly_income",
                current_value_json="20000",
                reason="단위 착오가 명확합니다.",
                suggested_value_json="2000000",
                confidence=0.91,
            ),
        ])
        registry = [{
            "step_id": "basic-info",
            "section": "basic_info",
            "path": "monthly_income",
            "cardinality": "single",
            "writable_by_ai": True,
            "operations": ["set"],
            "value_type": "integer",
            "unit": "KRW",
            "nullable": True,
            "constraints": {"min": 0},
            "sensitivity": "financial_sensitive",
        }]
        with patch.object(
            error_review,
            "_review_chain",
            SimpleNamespace(ainvoke=AsyncMock(return_value=result)),
        ):
            state = await error_review._run({
                "step_id": "basic-info",
                "review_trigger": "save",
                "customer_info": {"current_form": {"monthly_income": 20_000}},
                "field_registry": registry,
            })

        items = state["output"]["review"]["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["confidence_level"], "high")
        self.assertEqual(items[0]["confidence"], 0.91)


if __name__ == "__main__":
    unittest.main()
