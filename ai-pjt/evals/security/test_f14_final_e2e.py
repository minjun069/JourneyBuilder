from __future__ import annotations

import copy
import unittest

from f14_final_e2e import load_report, validate


class F14FinalE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        self.report = load_report()

    def test_checked_final_evidence_passes_g7_contract(self) -> None:
        self.assertEqual([], validate(self.report))
        self.assertEqual(0, self.report["summary"]["failed"])

    def test_hidden_suite_failure_is_rejected(self) -> None:
        report = copy.deepcopy(self.report)
        report["executions"][0]["failed"] = 1
        report["executions"][0]["passed"] -= 1
        report["summary"]["failed"] = 1
        report["summary"]["passed"] -= 1

        self.assertTrue(any("test failures" in error for error in validate(report)))

    def test_current_policy_digest_drift_is_rejected(self) -> None:
        report = copy.deepcopy(self.report)
        report["calculation_policy"]["sha256"] = "0" * 64

        self.assertTrue(any("policy version or digest" in error for error in validate(report)))

    def test_approval_save_or_rag_side_effect_drift_is_rejected(self) -> None:
        cases = []
        approval = copy.deepcopy(self.report)
        approval["reference_value_e2e"]["success"]["database_value_after_approval"] = 361000
        cases.append(approval)
        rag = copy.deepcopy(self.report)
        rag["rag_reuse"]["index_writes"] = 1
        cases.append(rag)

        for report in cases:
            with self.subTest(report=report["reference_value_e2e"]["success"]):
                self.assertNotEqual([], validate(report))


if __name__ == "__main__":
    unittest.main()
