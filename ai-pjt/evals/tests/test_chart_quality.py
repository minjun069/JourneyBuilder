from __future__ import annotations

import unittest

from evals.chart_quality import (
    build_evaluation_payload,
    evaluate_chart_response,
    evaluation_cases,
    load_chart_evaluation_fixture,
    summarize_chart_quality,
)


class ChartQualityGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = load_chart_evaluation_fixture()

    def test_covers_ten_normal_steps_and_required_edge_cases(self) -> None:
        self.assertEqual(
            {case["step_id"] for case in self.fixture["normal_cases"]},
            {
                "balance-sheet", "cash-flow", "financial-indicators",
                "financial-goal-result", "retirement-fund",
                "retirement-readiness", "retirement-simulation",
                "pension-schedule", "lifetime-cashflow", "required-savings",
            },
        )
        edge_ids = {case["id"] for case in self.fixture["edge_cases"]}
        self.assertLessEqual({
            "edge_report_not_ready", "edge_report_stale", "edge_policy_mismatch",
            "edge_zero", "edge_negative", "edge_deficit_transition",
            "edge_asset_depletion", "edge_multiple_turning_points",
            "edge_long_horizon", "edge_cause_question",
            "edge_personalization_boundary",
        }, edge_ids)

    def test_safe_payload_only_responses_pass_every_quality_threshold(self) -> None:
        from agents.chart_interpret import _fallback_response

        results = []
        for case in evaluation_cases(self.fixture):
            payload = build_evaluation_payload(case)
            response = _fallback_response(
                payload,
                entry=case.get("request_mode") == "presentation_entry",
            )
            observations = evaluate_chart_response(
                case=case,
                payload=payload,
                response=response,
                action=None,
            )
            results.append({
                "id": case["id"],
                "status": "completed",
                "observations": observations,
            })

        summary = summarize_chart_quality(results, self.fixture)
        self.assertTrue(summary["passed"])
        self.assertEqual(summary["metrics"]["normal_case_pass_rate"], 1.0)
        self.assertEqual(summary["metrics"]["unsupported_numeric_claim_count"], 0)
        self.assertEqual(summary["metrics"]["recalculation_claim_count"], 0)
        self.assertEqual(summary["metrics"]["unsupported_causal_claim_count"], 0)

    def test_invented_number_and_cause_fail_the_gate(self) -> None:
        case = self.fixture["edge_cases"][-2]
        payload = build_evaluation_payload(case)
        observations = evaluate_chart_response(
            case=case,
            payload=payload,
            response="시장 폭락 때문에 999원 부족합니다.",
            action=None,
        )

        self.assertFalse(observations["passed"])
        self.assertTrue(observations["unsupported_numeric_claims"])
        self.assertTrue(observations["unsupported_causal_claims"])

    def test_verified_number_without_its_payload_unit_fails_exact_match(self) -> None:
        case = self.fixture["normal_cases"][0]
        payload = build_evaluation_payload(case)
        observations = evaluate_chart_response(
            case=case,
            payload=payload,
            response="순자산은 320,000,000입니다.",
            action=None,
        )

        self.assertFalse(observations["numeric_unit_period_exact_match"])
        self.assertEqual(observations["numeric_unit_period_mismatches"], ["320,000,000"])

    def test_period_value_requires_the_backend_declared_age_unit(self) -> None:
        case = self.fixture["normal_cases"][5]
        payload = build_evaluation_payload(case)
        observations = evaluate_chart_response(
            case=case,
            payload=payload,
            response="분석 기간은 45부터 60까지입니다.",
            action=None,
        )

        self.assertFalse(observations["numeric_unit_period_exact_match"])
        self.assertEqual(
            observations["numeric_unit_period_mismatches"],
            ["45", "60"],
        )


if __name__ == "__main__":
    unittest.main()
