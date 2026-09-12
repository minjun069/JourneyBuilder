from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest


AI_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(AI_ROOT))

from f14_finding_triage import (  # noqa: E402
    DEFAULT_SOURCE,
    build_triage,
    validate_source,
    validate_triage,
)


class F14FindingTriageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.report = json.loads(DEFAULT_SOURCE.read_text(encoding="utf-8"))

    def test_checked_report_closes_with_no_dynamic_tasks(self) -> None:
        self.assertEqual([], validate_source(self.report))
        triage = build_triage(self.report, source_path=DEFAULT_SOURCE)

        self.assertEqual([], validate_triage(triage))
        self.assertEqual("no_findings", triage["decision"])
        self.assertEqual([], triage["triage"])
        self.assertEqual([], triage["dynamic_tasks"])

    def test_finding_is_deduplicated_and_registered_from_t20(self) -> None:
        mutated = copy.deepcopy(self.report)
        finding = copy.deepcopy(mutated["results"][0])
        finding["finding_id"] = "FIND-F14-ATK-001"
        finding["mismatches"] = ["decision drift"]
        duplicate = copy.deepcopy(finding)
        duplicate["finding_id"] = "FIND-F14-ATK-099"
        mutated["findings"] = [finding, duplicate]

        triage = build_triage(mutated, source_path=DEFAULT_SOURCE)

        self.assertEqual(1, triage["deduplication"]["unique_findings"])
        self.assertEqual("F14-T20", triage["dynamic_tasks"][0]["task_id"])
        self.assertEqual("W8-L01", triage["dynamic_tasks"][0]["lane_id"])
        self.assertEqual(
            ["FIND-F14-ATK-099"],
            triage["triage"][0]["duplicate_finding_ids"],
        )

    def test_accepted_risk_uses_rationale_only(self) -> None:
        mutated = copy.deepcopy(self.report)
        finding = copy.deepcopy(mutated["results"][0])
        finding["finding_id"] = "FIND-F14-ATK-001"
        finding["mismatches"] = ["decision drift"]
        mutated["findings"] = [finding]

        triage = build_triage(
            mutated,
            source_path=DEFAULT_SOURCE,
            decisions={finding["finding_id"]: {
                "disposition": "accepted_risk",
                "rationale": "합성 fixture에서만 발생하며 production 입력으로 도달하지 않는다.",
            }},
        )
        self.assertEqual([], validate_triage(triage))
        self.assertEqual([], triage["dynamic_tasks"])
        self.assertEqual(
            {
                "deduplication_key",
                "primary_finding_id",
                "duplicate_finding_ids",
                "disposition",
                "rationale",
                "severity",
                "impact_boundary",
                "owner_paths",
                "reproduction",
            },
            set(triage["triage"][0]),
        )

    def test_non_remediation_disposition_requires_rationale_only(self) -> None:
        mutated = copy.deepcopy(self.report)
        finding = copy.deepcopy(mutated["results"][0])
        finding["finding_id"] = "FIND-F14-ATK-001"
        mutated["findings"] = [finding]

        with self.assertRaisesRegex(ValueError, "requires rationale"):
            build_triage(
                mutated,
                source_path=DEFAULT_SOURCE,
                decisions={finding["finding_id"]: {"disposition": "false_positive"}},
            )


if __name__ == "__main__":
    unittest.main()
