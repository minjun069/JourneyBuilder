from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest


AI_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(AI_ROOT))

from tools.catalog import default_tool_definitions  # noqa: E402

from security_regression import (
    SUPPORTED_CATEGORIES,
    build_report,
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

    def test_promoted_p14_cases_use_only_production_payloads(self) -> None:
        foundation_cases = {
            case["id"]: case
            for case in self.suite["cases"]
            if case["id"] in P14_CASE_IDS
        }
        self.assertEqual(set(foundation_cases), P14_CASE_IDS)
        self.assertTrue(all(
            case["input_kind"] == "production_payload"
            and case["evaluator"] == "production_policy"
            and "signals" not in case["input"]
            for case in foundation_cases.values()
        ))
        self.assertTrue(all(
            case["evaluator"] == "production_policy"
            and case["input_kind"] == "production_payload"
            and "signals" not in case["input"]
            for case in self.suite["cases"]
        ))

    def test_declared_evaluators_match_every_expected_decision(self) -> None:
        results = run_suite(self.suite)

        failures = {
            result.case_id: result.failures
            for result in results
            if not result.passed
        }
        self.assertEqual({}, failures)

    def test_checked_in_report_records_a_clean_production_run(self) -> None:
        report_path = (
            AI_ROOT
            / "evals"
            / "artifacts"
            / "f13-production-security-regression.json"
        )
        report = json.loads(report_path.read_text(encoding="utf-8"))
        fresh_report = build_report(self.suite, run_suite(self.suite))

        self.assertEqual(fresh_report, report)
        self.assertEqual("security-regression-v3", report["schema_version"])
        self.assertEqual("AI-F13-T03", report["suite_id"])
        self.assertEqual("production_policy", report["evaluator"])
        self.assertEqual(len(self.suite["cases"]), report["summary"]["total"])
        self.assertEqual(report["summary"]["total"], report["summary"]["passed"])
        self.assertEqual(0, report["summary"]["failed"])
        self.assertTrue(all(result["passed"] for result in report["results"]))

    def test_allowlisted_tool_cases_match_the_p10_registry_and_agent_scope(self) -> None:
        definitions = {
            definition.name: definition
            for definition in default_tool_definitions()
        }

        for case in self.suite["cases"]:
            if case["category"] != "tool_manipulation":
                continue
            payload = case["input"]["payload"]
            tool_name = payload["tool_name"]
            with self.subTest(case=case["id"]):
                if case["expected"]["reason_code"] == "TOOL_NOT_ALLOWLISTED":
                    self.assertNotIn(tool_name, definitions)
                else:
                    self.assertIn(tool_name, definitions)

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
        mutated["cases"][0]["evaluator"] = "unknown_evaluator"

        errors = validate_suite(mutated)

        self.assertIn(
            "cases[0].evaluator is unsupported: 'unknown_evaluator'",
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
