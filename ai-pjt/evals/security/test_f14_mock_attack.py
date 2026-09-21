from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest


AI_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(AI_ROOT))

from f14_mock_attack import (  # noqa: E402
    SCHEMA_VERSION,
    build_report,
    load_suite,
    run_suite,
    validate_suite,
)


class F14MockAttackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.suite = load_suite()

    def test_suite_is_reproducible_and_contains_no_risk_approval_schema(self) -> None:
        self.assertEqual([], validate_suite(self.suite))
        rendered = json.dumps(self.suite, ensure_ascii=False)
        self.assertNotIn("accepted_risk", rendered)
        self.assertNotIn("approver", rendered)
        self.assertNotIn("expires", rendered)
        self.assertEqual(
            {
                "task_identity",
                "dependency_integrity",
                "binding_integrity",
                "artifact_integrity",
            },
            {case["category"] for case in self.suite["cases"]},
        )

    async def test_integrated_attack_suite_has_no_findings(self) -> None:
        results = await run_suite(self.suite)
        report = build_report(self.suite, results)

        self.assertEqual(len(self.suite["cases"]), report["summary"]["total"])
        self.assertEqual(report["summary"]["total"], report["summary"]["passed"])
        self.assertEqual(0, report["summary"]["findings"])
        self.assertEqual([], report["findings"])
        self.assertTrue(all(result.passed for result in results))

    async def test_each_case_can_be_reproduced_by_id(self) -> None:
        for case in self.suite["cases"]:
            with self.subTest(case=case["id"]):
                results = await run_suite(self.suite, case_id=case["id"])
                self.assertEqual([case["id"]], [result.case_id for result in results])

    def test_validator_rejects_duplicate_id_and_unknown_scenario(self) -> None:
        mutated = copy.deepcopy(self.suite)
        mutated["cases"][1]["id"] = mutated["cases"][0]["id"]
        mutated["cases"][2]["scenario"] = "mock_only"

        errors = validate_suite(mutated)

        self.assertIn(f"duplicate case id: {mutated['cases'][0]['id']}", errors)
        self.assertIn("cases[2].scenario is unsupported: 'mock_only'", errors)


if __name__ == "__main__":
    unittest.main()
