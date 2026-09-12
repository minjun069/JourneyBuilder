from __future__ import annotations

import copy
import json
import unittest

from f14_rollback_evidence import DEFAULT_REPORT, load_and_validate, validate


class F14RollbackEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.report = json.loads(DEFAULT_REPORT.read_text(encoding="utf-8"))

    def test_committed_production_evidence_passes(self) -> None:
        result = load_and_validate()
        self.assertEqual(result["result"], "PASS")
        self.assertEqual(result["restored_services"], 4)

    def test_configuration_source_cannot_be_embedded(self) -> None:
        report = copy.deepcopy(self.report)
        report["config_snapshot"]["app_env_contents"] = "must-never-be-stored"
        with self.assertRaisesRegex(ValueError, "extra=.*app_env_contents"):
            validate(report)

    def test_non_immutable_snapshot_is_rejected(self) -> None:
        report = copy.deepcopy(self.report)
        report["config_snapshot"]["filesystem_immutable"] = False
        with self.assertRaisesRegex(ValueError, "filesystem_immutable"):
            validate(report)

    def test_policy_drift_is_rejected(self) -> None:
        report = copy.deepcopy(self.report)
        report["production_environment"]["calculation_policy_version"] = "different"
        with self.assertRaisesRegex(ValueError, "policy versions differ"):
            validate(report)


if __name__ == "__main__":
    unittest.main()
