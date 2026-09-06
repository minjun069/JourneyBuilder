from __future__ import annotations

import math
import unittest
from collections import Counter

from router_evaluation import (
    load_contract_step_candidates,
    load_evaluation_fixture,
    load_frontend_step_ids,
    passes_accuracy_gate,
    route_accuracy,
)


EXPECTED_AGENTS = {
    "qa",
    "input_assist",
    "error_review",
    "chart_interpret",
    "elicit",
    "fallback",
}
EXPECTED_REASON_CODES = {
    "intent_match",
    "ambiguous_intent",
    "unsupported_intent",
    "unsupported_step",
    "agent_not_allowed",
    "classifier_error",
}
EXPECTED_STEP_IDS = {
    "intro",
    "collection",
    "basic-info",
    "financial-mindset",
    "financial-satisfaction",
    "financial-info",
    "financial-assets",
    "pension-assets",
    "real-estate",
    "liabilities",
    "income",
    "expenses",
    "financial-goals",
    "presentation",
    "balance-sheet",
    "cash-flow",
    "financial-indicators",
    "financial-goal-result",
    "retirement-fund",
    "retirement-readiness",
    "pension-schedule",
    "lifetime-cashflow",
    "required-savings",
}


class RouterEvaluationFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = load_evaluation_fixture()
        cls.cases = cls.fixture["cases"]
        cls.step_candidates = load_contract_step_candidates()

    def test_metadata_and_case_count_are_frozen(self) -> None:
        self.assertEqual(self.fixture["schema_version"], "1.0")
        self.assertEqual(self.fixture["activation_gate"], "M2-L02")
        self.assertEqual(self.fixture["accuracy_threshold"], 0.95)
        self.assertEqual(len(self.cases), 60)
        self.assertEqual(self.fixture["minimum_correct"], 57)
        self.assertEqual(
            self.fixture["minimum_correct"],
            math.ceil(len(self.cases) * self.fixture["accuracy_threshold"]),
        )

    def test_step_registry_has_all_23_unique_steps(self) -> None:
        self.assertEqual(set(self.step_candidates), EXPECTED_STEP_IDS)
        for step_id, candidates in self.step_candidates.items():
            with self.subTest(step_id=step_id):
                self.assertEqual(len(candidates), len(set(candidates)))
                self.assertTrue(set(candidates) <= EXPECTED_AGENTS)
                self.assertIn("fallback", candidates)

    def test_machine_registry_matches_frontend_step_registry(self) -> None:
        frontend_step_ids = load_frontend_step_ids()
        self.assertEqual(len(frontend_step_ids), len(set(frontend_step_ids)))
        self.assertEqual(set(frontend_step_ids), set(self.step_candidates))

    def test_case_ids_are_unique_and_required_values_are_known(self) -> None:
        case_ids = [case["id"] for case in self.cases]
        self.assertEqual(len(case_ids), len(set(case_ids)))

        for case in self.cases:
            with self.subTest(case=case["id"]):
                self.assertTrue(case["message"].strip())
                self.assertIn(case["classifier"]["status"], {
                    "matched",
                    "ambiguous",
                    "unsupported",
                    "error",
                })
                self.assertIn(case["expected"]["agent"], EXPECTED_AGENTS)
                self.assertIn(
                    case["expected"]["reason_code"], EXPECTED_REASON_CODES
                )

    def test_all_roles_have_representative_expected_routes(self) -> None:
        route_counts = Counter(case["expected"]["agent"] for case in self.cases)
        self.assertEqual(set(route_counts), EXPECTED_AGENTS)
        for agent in EXPECTED_AGENTS:
            with self.subTest(agent=agent):
                self.assertGreater(route_counts[agent], 0)

    def test_success_routes_obey_step_allowlist(self) -> None:
        for case in self.cases:
            if case["expected"]["reason_code"] != "intent_match":
                continue
            with self.subTest(case=case["id"]):
                candidates = self.step_candidates[case["step_id"]]
                self.assertIn(case["expected"]["agent"], candidates)
                self.assertEqual(
                    case["classifier"]["agent"], case["expected"]["agent"]
                )

    def test_fallback_labels_match_their_policy_condition(self) -> None:
        for case in self.cases:
            reason_code = case["expected"]["reason_code"]
            if reason_code == "intent_match":
                continue

            with self.subTest(case=case["id"]):
                self.assertEqual(case["expected"]["agent"], "fallback")

                if reason_code == "ambiguous_intent":
                    self.assertEqual(case["classifier"]["status"], "ambiguous")
                elif reason_code == "unsupported_intent":
                    self.assertEqual(case["classifier"]["status"], "unsupported")
                elif reason_code == "unsupported_step":
                    self.assertNotIn(case["step_id"], self.step_candidates)
                    self.assertEqual(case.get("expected_classifier_calls"), 0)
                elif reason_code == "agent_not_allowed":
                    self.assertEqual(case["classifier"]["status"], "matched")
                    self.assertNotIn(
                        case["classifier"]["agent"],
                        self.step_candidates[case["step_id"]],
                    )
                elif reason_code == "classifier_error":
                    self.assertEqual(case["classifier"]["status"], "error")

    def test_elicit_means_explicit_qualitative_collection(self) -> None:
        elicit_cases = [
            case for case in self.cases if case["expected"]["agent"] == "elicit"
        ]
        self.assertGreaterEqual(len(elicit_cases), 3)
        for case in elicit_cases:
            with self.subTest(case=case["id"]):
                self.assertEqual(case["step_id"], "financial-mindset")
                self.assertEqual(case["kind"], "qualitative_collection")
                self.assertEqual(case["classifier"]["status"], "matched")
                self.assertEqual(case["expected"]["reason_code"], "intent_match")

    def test_every_ambiguous_intent_falls_back(self) -> None:
        ambiguous_cases = [
            case
            for case in self.cases
            if case["classifier"]["status"] == "ambiguous"
        ]
        self.assertGreaterEqual(len(ambiguous_cases), 4)
        for case in ambiguous_cases:
            with self.subTest(case=case["id"]):
                self.assertEqual(case["expected"]["agent"], "fallback")
                self.assertEqual(
                    case["expected"]["reason_code"], "ambiguous_intent"
                )

    def test_fallback_reason_categories_are_covered(self) -> None:
        fallback_reasons = {
            case["expected"]["reason_code"]
            for case in self.cases
            if case["expected"]["agent"] == "fallback"
        }
        self.assertEqual(
            fallback_reasons,
            EXPECTED_REASON_CODES - {"intent_match"},
        )

    def test_scoring_uses_95_percent_gate(self) -> None:
        predictions = {
            case["id"]: case["expected"]["agent"] for case in self.cases
        }
        self.assertEqual(route_accuracy(self.cases, predictions), 1.0)

        for case in self.cases[:3]:
            predictions[case["id"]] = "fallback"
        self.assertTrue(
            passes_accuracy_gate(
                self.cases,
                predictions,
                self.fixture["accuracy_threshold"],
            )
        )

        predictions[self.cases[3]["id"]] = "fallback"
        self.assertFalse(
            passes_accuracy_gate(
                self.cases,
                predictions,
                self.fixture["accuracy_threshold"],
            )
        )

    def test_scoring_rejects_missing_predictions(self) -> None:
        with self.assertRaisesRegex(ValueError, "예측이 누락된 case"):
            route_accuracy(self.cases, {})


if __name__ == "__main__":
    unittest.main()
