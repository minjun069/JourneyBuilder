from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest


AI_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(AI_ROOT))

from tools.catalog import default_tool_definitions  # noqa: E402

from security_regression import (
    SUPPORTED_CATEGORIES,
    load_suite,
    run_suite,
    validate_suite,
)


P14_CASE_IDS = {
    "SEC-DPI-001",
    "SEC-DPI-002",
    "SEC-CTX-001",
    "SEC-CTX-002",
    "SEC-RAG-001",
    "SEC-RAG-002",
    "SEC-TOL-001",
    "SEC-TOL-002",
    "SEC-TOL-003",
    "SEC-TOL-004",
    "SEC-APR-001",
    "SEC-APR-002",
    "SEC-CBK-001",
    "SEC-CBK-002",
    "SEC-CBK-003",
    "SEC-CBK-004",
    "SEC-CBK-005",
    "SEC-RES-001",
    "SEC-RES-002",
    "SEC-FIN-001",
    "SEC-FIN-002",
    "SEC-OBF-001",
    "SEC-OBF-002",
    "SEC-OBF-003",
    "SEC-OBF-004",
}
P14_ID_INPUT_EXPECTED_SHA256 = (
    "83e39291a031b49c2661dc0f1968ab20a52d60288647dc297d4c7557c95038b7"
)


class SecurityRegressionSuiteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.suite = load_suite()

    def test_suite_structure_and_policy_coverage_are_valid(self) -> None:
        self.assertEqual([], validate_suite(self.suite))

        self.assertTrue(self.suite["policy_source"].startswith("shared-docs:"))
        self.assertTrue(self.suite["requirements_source"].startswith("shared-docs:"))

        coverage = {
            category: {"allow": 0, "block": 0}
            for category in SUPPORTED_CATEGORIES
        }
        for case in self.suite["cases"]:
            coverage[case["category"]][case["expected"]["decision"]] += 1

        self.assertTrue(
            all(count > 0 for decisions in coverage.values() for count in decisions.values())
        )

    def test_promoted_p14_cases_and_route_plan_dispatch_are_explicit(self) -> None:
        foundation_cases = {
            case["id"]: case
            for case in self.suite["cases"]
            if case["id"] in P14_CASE_IDS
        }
        self.assertEqual(set(foundation_cases), P14_CASE_IDS)
        self.assertTrue(all(
            case["input_kind"] == "security_signals"
            and case["evaluator"] == "mock_policy"
            for case in foundation_cases.values()
        ))
        preserved_contract = [
            {
                "id": case["id"],
                "input": case["input"],
                "expected": case["expected"],
            }
            for case in self.suite["cases"]
            if case["id"] in P14_CASE_IDS
        ]
        digest = hashlib.sha256(json.dumps(
            preserved_contract,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()).hexdigest()
        self.assertEqual(digest, P14_ID_INPUT_EXPECTED_SHA256)

        route_cases = [
            case
            for case in self.suite["cases"]
            if case["input_kind"] == "route_plan"
        ]
        self.assertTrue(route_cases)
        self.assertTrue(all(
            case["evaluator"] == "plan_guardrail"
            for case in route_cases
        ))

    def test_declared_evaluators_match_every_expected_decision(self) -> None:
        results = run_suite(self.suite)

        failures = {
            result.case_id: result.failures
            for result in results
            if not result.passed
        }
        self.assertEqual({}, failures)

    def test_allowlisted_tool_cases_match_the_p10_registry_and_agent_scope(self) -> None:
        definitions = {
            definition.name: definition
            for definition in default_tool_definitions()
        }

        for case in self.suite["cases"]:
            if case["category"] != "tool_manipulation":
                continue
            payload = case["input"]["payload"]
            signals = case["input"]["signals"]
            tool_name = payload["tool_name"]
            with self.subTest(case=case["id"]):
                if signals["tool_allowlisted"]:
                    self.assertIn(tool_name, definitions)
                else:
                    self.assertNotIn(tool_name, definitions)

                agent = payload.get("agent")
                if signals["tool_allowlisted"] and agent is not None:
                    self.assertEqual(
                        signals["scope_allowed"],
                        agent in definitions[tool_name].allowed_agents,
                    )

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

    def test_validator_rejects_evaluator_input_kind_mismatch(self) -> None:
        mutated = copy.deepcopy(self.suite)
        mutated["cases"][0]["evaluator"] = "plan_guardrail"

        errors = validate_suite(mutated)

        self.assertIn(
            "cases[0].evaluator 'plan_guardrail' does not accept "
            "input_kind 'security_signals'",
            errors,
        )

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
