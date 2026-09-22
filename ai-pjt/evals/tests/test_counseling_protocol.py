"""Boundary checks for the design-only counseling review protocol."""

from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from evals.counseling_protocol import (
    BEHAVIOR_PATH, COVERAGE_PATH, PLAN_PATH, REGISTRY_PATH, REPOSITORY_ROOT,
    build_review_template, evaluate_protocol, main, render_markdown,
    validate_protocol, validate_registry,
)


class CounselingProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.paths = [REPOSITORY_ROOT / path for path in (REGISTRY_PATH, PLAN_PATH, COVERAGE_PATH, BEHAVIOR_PATH)]
        cls.inputs = [json.loads(path.read_text(encoding="utf-8")) for path in cls.paths]

    def setUp(self):
        self.registry, self.plan, self.coverage, self.behavior = deepcopy(self.inputs)

    def validate(self):
        return validate_protocol(self.registry, self.plan, self.coverage, self.behavior)

    def test_real_design_is_complete_but_has_no_observed_quality(self):
        report = evaluate_protocol()
        expected = {(kind, case["id"]) for kind, source in (("coverage", self.coverage), ("behavior", self.behavior)) for case in source["cases"]}
        self.assertEqual(expected, {(case["kind"], case["case_id"]) for case in report["cases"]})
        self.assertEqual({"coverage": 54, "behavior": 26}, report["case_counts"])
        self.assertTrue(report["document_validation"]["references_validated"])
        self.assertFalse(report["document_validation"]["response_quality_assessed"])
        self.assertFalse(report["llm_evaluated"])
        self.assertFalse(report["ax_gain_measured"])
        self.assertIsNone(report["official_scores"])
        self.assertIsNone(report["measured_outcomes"])
        self.assertNotIn("quality_passed", report)
        self.assertNotIn("total_score", report)
        for by_kind in report["axis_applicability_counts"].values():
            for kind, statuses in by_kind.items():
                self.assertEqual(report["case_counts"][kind], sum(statuses.values()))

    def test_missing_duplicate_and_unknown_case_mappings_are_rejected(self):
        for mutation, message in (
            (lambda plans: plans.pop(), "missing case mappings"),
            (lambda plans: plans.append(deepcopy(plans[0])), "duplicate case mapping"),
            (lambda plans: plans[0].update(case_id="invented-case"), "unknown case"),
        ):
            with self.subTest(message=message):
                self.plan = deepcopy(self.inputs[1])
                mutation(self.plan["plans"])
                with self.assertRaisesRegex(ValueError, message):
                    self.validate()

    def test_registry_references_cannot_cross_framework_or_axis_boundaries(self):
        mutations = (
            lambda r: r["frameworks"][0].update(source_ids=["missing-source"]),
            lambda r: r["axes"][0].update(framework_ids=["cfpboard-seven-step"]),
            lambda r: r["criteria"][0].update(framework_id="miti-4.2.1"),
            lambda r: r["criteria"][0].update(axis_id="conversation"),
            lambda r: r["criteria"][0].update(kind="official-scale-item"),
            lambda r: r["sources"][0].update(raw_content_ingested=True),
            lambda r: r["sources"].append(deepcopy(r["sources"][0])),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                registry = deepcopy(self.inputs[0])
                mutate(registry)
                with self.assertRaises(ValueError):
                    validate_registry(registry)

    def test_required_source_provenance_and_structure_are_checked(self):
        for mutate in (
            lambda r: r["sources"][0].pop("rights_url"),
            lambda r: r["sources"][0].update(locator=" "),
            lambda r: r["sources"][0].update(url="not-a-url"),
            lambda r: r["sources"][0].update(verified_at="2026-99-99"),
            lambda r: r["frameworks"][4]["structure"].update(verified_item_count=10),
        ):
            with self.subTest(mutation=mutate):
                registry = deepcopy(self.inputs[0])
                mutate(registry)
                with self.assertRaises(ValueError):
                    validate_registry(registry)

    def test_stage_four_and_missing_or_boolean_process_stage_are_rejected(self):
        target = next(index for index, plan in enumerate(self.plan["plans"]) if plan["process_stage_ids"])
        for stages in ([4], [], [True]):
            with self.subTest(stages=stages):
                self.plan = deepcopy(self.inputs[1])
                self.plan["plans"][target]["process_stage_ids"] = stages
                with self.assertRaisesRegex(ValueError, "process_stage_ids|process stages"):
                    self.validate()
        self.registry["scope"]["included_process_stages"] = [1, 2, 3, 4]
        with self.assertRaisesRegex(ValueError, "included process stages"):
            validate_registry(self.registry)

    def test_unknown_criterion_and_false_applicability_are_rejected(self):
        self.plan["plans"][0]["criterion_ids"].append("official-miti-empathy")
        with self.assertRaisesRegex(ValueError, "unknown criterion"):
            self.validate()
        for axis, invalid in (("customer-experience", "supported"), ("financial-wellbeing", "estimated"), ("process", "officially-compliant")):
            with self.subTest(axis=axis):
                self.plan = deepcopy(self.inputs[1])
                self.plan["plans"][0]["axis_status"][axis] = invalid
                with self.assertRaisesRegex(ValueError, "axis status"):
                    self.validate()

    def test_formal_miti_and_fabricated_results_are_rejected(self):
        self.plan["plans"][0]["formal_miti"]["status"] = "scored"
        with self.assertRaisesRegex(ValueError, "formal MITI"):
            self.validate()
        for mutate in (
            lambda r: r.update(official_certification=True),
            lambda r: r.update(no_total_score=False),
            lambda r: r.update(measured_outcomes={"satisfaction": 5}),
            lambda r: r.update(total_score=90),
            lambda r: r["frameworks"][3].update(official_scoring_in_this_project=True),
            lambda r: r["frameworks"][3]["structure"].update(official_scores_implemented=True),
            lambda r: r["frameworks"][3]["structure"].update(miti_score=4),
        ):
            with self.subTest(mutation=mutate):
                registry = deepcopy(self.inputs[0])
                mutate(registry)
                with self.assertRaises(ValueError):
                    validate_registry(registry)

    def test_document_presence_does_not_allow_claiming_an_executed_behavior_review(self):
        for mutate in (
            lambda b: b.update(status="executed"),
            lambda b: b.update(llm_evaluated=True),
            lambda b: b["cases"][0].update(actual_response="fabricated result"),
            lambda b: b["project_checks"].update(aggregation="sum"),
        ):
            with self.subTest(mutation=mutate):
                self.behavior = deepcopy(self.inputs[3])
                mutate(self.behavior)
                with self.assertRaises(ValueError):
                    self.validate()

    def test_all_four_variants_and_evidence_requirements_are_preserved(self):
        self.behavior["variants"].pop(0)
        with self.assertRaisesRegex(ValueError, "variants"):
            self.validate()
        self.behavior = deepcopy(self.inputs[3])
        self.plan["plans"][0]["needed_evidence"] = []
        with self.assertRaisesRegex(ValueError, "needed_evidence"):
            self.validate()

    def test_failed_document_reference_check_prevents_report(self):
        with patch("evals.counseling_protocol.evaluate_coverage", side_effect=ValueError("missing source section")):
            with self.assertRaisesRegex(ValueError, "missing source section"):
                evaluate_protocol()

    def test_template_is_a_complete_empty_cross_product_without_expected_answers(self):
        report = self.validate()
        template = build_review_template(report)
        expected = {(case["id"], variant["id"]) for case in self.behavior["cases"] for variant in self.behavior["variants"]}
        actual = {(review["behavior_case_id"], review["variant_id"]) for review in template["reviews"]}
        self.assertEqual(expected, actual)
        self.assertEqual(len(expected), len(template["reviews"]))
        self.assertEqual(104, len(template["reviews"]))
        plan_by_id = {case["case_id"]: case for case in self.plan["plans"] if case["kind"] == "behavior"}
        for review in template["reviews"]:
            for field in ("actual_response", "execution", "reviewer", "customer_experience", "financial_wellbeing"):
                self.assertIsNone(review[field])
            self.assertEqual("not_applicable", review["formal_miti"]["status"])
            self.assertEqual(plan_by_id[review["behavior_case_id"]]["criterion_ids"], [item["criterion_id"] for item in review["criteria"]])
            for criterion in review["criteria"]:
                self.assertEqual("not-observed", criterion["status"])
                self.assertEqual([], criterion["evidence"])
                self.assertIsNone(criterion["reason"])
        serialized = json.dumps(template, ensure_ascii=False)
        # Evidence instructions may name an expected field to warn against using
        # it as a result; neither that field nor its answer belongs in a slot.
        self.assertTrue('"example_understanding":' not in serialized)
        self.assertTrue('"expected_understanding":' not in serialized)
        for case in self.behavior["cases"]:
            if case.get("example_understanding"):
                self.assertTrue(case["example_understanding"] not in serialized,
                                f"expected understanding copied into template: {case['id']}")

    def test_markdown_explains_sources_units_and_nonmeasurement(self):
        report = self.validate()
        markdown = render_markdown(report)
        self.assertIn("지식 지도 54개와 합성 대화 26개", markdown)
        self.assertIn("104개 후속 검토 칸은 모두 미실행", markdown)
        self.assertIn("공식 점수: 없음", markdown)
        self.assertIn("관찰 성공·실패 수가 아닙니다", markdown)
        for source in self.registry["sources"]:
            self.assertIn(source["url"], markdown)
            self.assertIn(source["locator"], markdown)
        for criterion in self.registry["criteria"]:
            self.assertIn(criterion["id"], markdown)
        for note in self.plan["notes"]:
            self.assertIn(note, markdown)

    def test_cli_writes_only_requested_artifacts_and_preserves_inputs(self):
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in self.paths}
        with TemporaryDirectory() as folder, patch("sys.stdout", new_callable=io.StringIO) as stdout:
            report_path, markdown_path, template_path = [Path(folder) / name for name in ("report.json", "report.md", "template.json")]
            self.assertEqual(0, main(["--report", str(report_path), "--markdown", str(markdown_path), "--template", str(template_path)]))
            self.assertEqual("design-validated-not-executed", json.loads(stdout.getvalue())["status"])
            self.assertEqual("not-executed", json.loads(template_path.read_text())["status"])
            self.assertIn("검증의 경계", markdown_path.read_text())
            self.assertFalse(json.loads(report_path.read_text())["llm_evaluated"])
        self.assertEqual(before, {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in self.paths})

    def test_cli_rejects_colliding_outputs_before_writing(self):
        with TemporaryDirectory() as folder, patch("sys.stderr", new_callable=io.StringIO):
            destination = Path(folder) / "same.json"
            with self.assertRaises(SystemExit) as error:
                main(["--report", str(destination), "--template", str(destination)])
            self.assertEqual(1, error.exception.code)
            self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
