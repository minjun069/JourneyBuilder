from __future__ import annotations

import copy
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
                "artifact_mutation",
            },
            {case["scenario"] for case in self.suite["cases"]},
        )

    async def test_current_ai_anomalies_pass_as_one_report(self) -> None:
        results = await run_suite(self.suite)
        report = build_report(self.suite, results)

        self.assertTrue(all(result.passed for result in results))
        self.assertEqual(3, report["summary"]["total"])
        self.assertEqual(3, report["summary"]["passed"])
        self.assertEqual(0, report["summary"]["failed"])

    def test_validator_rejects_duplicate_and_unknown_scenario(self) -> None:
        mutated = copy.deepcopy(self.suite)
        mutated["cases"][1]["id"] = mutated["cases"][0]["id"]
        mutated["cases"][2]["scenario"] = "sleep_and_hope"

        errors = validate_suite(mutated)

        self.assertIn(f"duplicate case id: {mutated['cases'][0]['id']}", errors)
        self.assertIn("cases[2].scenario is unsupported: 'sleep_and_hope'", errors)


if __name__ == "__main__":
    unittest.main()
