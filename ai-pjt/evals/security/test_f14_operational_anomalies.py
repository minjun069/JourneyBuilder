from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest


AI_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(AI_ROOT))

from f14_operational_anomalies import (  # noqa: E402
    build_report,
    load_suite,
    run_suite,
    validate_suite,
)


class F14OperationalAnomalyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.suite = load_suite()

    def test_suite_declares_all_required_operational_boundaries(self) -> None:
        self.assertEqual([], validate_suite(self.suite))
        self.assertEqual(
            {
                "dependency_failure",
                "request_timeout",
                "callback_retry",
                "concurrent_accept",
                "artifact_mutation",
            },
            {case["scenario"] for case in self.suite["cases"]},
        )

    async def test_ai_anomalies_and_backend_evidence_pass_as_one_report(self) -> None:
        results = await run_suite(self.suite)
        report = build_report(
            self.suite,
            results,
            backend_concurrency_passed=True,
        )

        self.assertTrue(all(result.passed for result in results))
        self.assertEqual(6, report["summary"]["total"])
        self.assertEqual(6, report["summary"]["passed"])
        self.assertEqual(0, report["summary"]["failed"])

    async def test_missing_backend_concurrency_evidence_fails_report(self) -> None:
        results = await run_suite(self.suite)
        report = build_report(
            self.suite,
            results,
            backend_concurrency_passed=False,
        )

        self.assertEqual(1, report["summary"]["failed"])
        self.assertFalse(report["backend_concurrency_check"]["passed"])

    async def test_checked_report_matches_fresh_execution(self) -> None:
        checked = json.loads(
            (AI_ROOT / "evals" / "artifacts" / "f14-operational-anomalies.json")
            .read_text(encoding="utf-8")
        )
        fresh = build_report(
            self.suite,
            await run_suite(self.suite),
            backend_concurrency_passed=True,
        )

        self.assertEqual(fresh, checked)

    def test_validator_rejects_duplicate_and_unknown_scenario(self) -> None:
        mutated = copy.deepcopy(self.suite)
        mutated["cases"][1]["id"] = mutated["cases"][0]["id"]
        mutated["cases"][2]["scenario"] = "sleep_and_hope"

        errors = validate_suite(mutated)

        self.assertIn(f"duplicate case id: {mutated['cases'][0]['id']}", errors)
        self.assertIn("cases[2].scenario is unsupported: 'sleep_and_hope'", errors)


if __name__ == "__main__":
    unittest.main()
