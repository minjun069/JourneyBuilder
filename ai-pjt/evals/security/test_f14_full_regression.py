from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest


AI_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(AI_ROOT))

from f14_full_regression import load_report, validate_report  # noqa: E402


class F14FullRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.report = load_report()

    def test_checked_release_evidence_passes_g6_contract(self) -> None:
        self.assertEqual([], validate_report(self.report))
        self.assertEqual(0, self.report["summary"]["failed"])
        self.assertEqual(
            self.report["summary"]["tests"],
            self.report["summary"]["passed"] + self.report["summary"]["skipped"],
        )

    def test_gate_fails_if_a_suite_failure_is_hidden_in_summary(self) -> None:
        mutated = copy.deepcopy(self.report)
        mutated["executions"][0]["failed"] = 1
        mutated["executions"][0]["passed"] -= 1
        mutated["summary"]["failed"] = 1
        mutated["summary"]["passed"] -= 1

        errors = validate_report(mutated)

        self.assertTrue(any("has test failures" in error for error in errors))

    def test_gate_fails_if_source_artifact_digest_drifts(self) -> None:
        mutated = copy.deepcopy(self.report)
        mutated["source_artifacts"][0]["sha256"] = "0" * 64

        errors = validate_report(mutated)

        self.assertTrue(any("sha256 mismatch" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
