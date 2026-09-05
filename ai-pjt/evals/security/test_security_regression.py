from __future__ import annotations

import copy
import unittest

from security_regression import (
    SUPPORTED_CATEGORIES,
    load_suite,
    run_suite,
    validate_suite,
)


class SecurityRegressionSuiteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.suite = load_suite()

    def test_suite_structure_and_policy_coverage_are_valid(self) -> None:
        self.assertEqual([], validate_suite(self.suite))

        coverage = {
            category: {"allow": 0, "block": 0}
            for category in SUPPORTED_CATEGORIES
        }
        for case in self.suite["cases"]:
            coverage[case["category"]][case["expected"]["decision"]] += 1

        self.assertTrue(
            all(count > 0 for decisions in coverage.values() for count in decisions.values())
        )

    def test_mock_oracle_matches_every_expected_decision(self) -> None:
        results = run_suite(self.suite)

        failures = {
            result.case_id: result.failures
            for result in results
            if not result.passed
        }
        self.assertEqual({}, failures)

    def test_critical_cases_are_fail_closed(self) -> None:
        critical_cases = [
            case for case in self.suite["cases"] if case["severity"] == "critical"
        ]

        self.assertTrue(critical_cases)
        self.assertTrue(
            all(case["expected"]["decision"] == "block" for case in critical_cases)
        )

    def test_validator_rejects_missing_policy_category(self) -> None:
        mutated = copy.deepcopy(self.suite)
        mutated["cases"] = [
            case
            for case in mutated["cases"]
            if case["category"] != "callback_integrity"
        ]

        errors = validate_suite(mutated)

        self.assertIn("category 'callback_integrity' has no 'allow' case", errors)
        self.assertIn("category 'callback_integrity' has no 'block' case", errors)

    def test_validator_rejects_duplicate_case_id(self) -> None:
        mutated = copy.deepcopy(self.suite)
        mutated["cases"][1]["id"] = mutated["cases"][0]["id"]

        errors = validate_suite(mutated)

        self.assertIn(f"duplicate case id: {mutated['cases'][0]['id']}", errors)

    def test_runner_detects_expected_decision_drift(self) -> None:
        mutated = copy.deepcopy(self.suite)
        mutated["cases"][0]["expected"]["decision"] = "allow"

        result = run_suite(mutated)[0]

        self.assertFalse(result.passed)
        self.assertTrue(
            any(failure.startswith("decision:") for failure in result.failures)
        )


if __name__ == "__main__":
    unittest.main()
