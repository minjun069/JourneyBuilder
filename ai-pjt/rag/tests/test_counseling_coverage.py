from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core.step_registry import load_step_registry
from rag.evaluation.counseling_coverage import (
    evaluate_coverage,
    main,
    render_markdown,
    validate_coverage,
)
from rag.pipeline.models import DocumentMetadata, SourceDocument


def document(source_id: str, text: str, *, candidate: bool = False) -> SourceDocument:
    return SourceDocument(
        metadata=DocumentMetadata(
            source_id=source_id, title=source_id, publisher="fixture",
            source_locators=("https://example.org/fixture",), published_at=None,
            as_of_date="2026-09-21", version="fixture-v1", scope="fixture",
            review_status="pending" if candidate else "approved",
            reviewed_by="unreviewed" if candidate else "fixture-reviewer",
            document_kind="financial-knowledge", collection="financial-knowledge",
            license="fixture", sensitivity="public", sha256=hashlib.sha256(text.encode()).hexdigest(),
            catalog_document_id=source_id, file_path=f"fixture/{source_id}.md",
        ),
        text=text,
    )


class CounselingCoverageValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = load_step_registry()
        steps = [step["step_id"] for step in self.registry["steps"]]
        self.existing = [document("existing-income", "# 소득\n\n## 소득 확인\n\n공식 자료 확인\n")]
        self.candidates = [document(
            "counseling-income",
            "# 상담\n\n## 소득 공백\n\n확인할 사항\n\n### 하위 설명\n\n```markdown\n## 가짜 제목\n```\n",
            candidate=True,
        )]
        case = {
            "id": "no-retrieval", "area": "기본 안내", "step_ids": [*steps, "general"],
            "question": "저장됐나요?", "concern": "현재 저장 상태", "primary_action": "read_verified_data",
            "knowledge_mode": "none", "retrieval_timing": "never",
            "reason": "실제 저장 결과를 확인해야 한다.", "required_context": ["현재 저장 결과"],
            "existing_refs": [], "candidate_refs": [], "before": "not_applicable", "gap": "",
            "improvement": "data_or_tool", "after": "data_dependency",
            "ax_observation": ["저장되지 않은 상태를 완료로 말하지 않는다."], "evaluation_ids": [],
        }
        combined = copy.deepcopy(case)
        combined.update({
            "id": "data-and-knowledge", "area": "소득 공백", "step_ids": ["income"],
            "question": "소득이 끊기면 어떻게 봐야 하나요?", "concern": "현재 지급 여력",
            "knowledge_mode": "combined", "retrieval_timing": "after_verified_data",
            "reason": "현재 수입을 확인한 뒤 원리로 설명한다.", "required_context": ["현재 수입"],
            "existing_refs": [{"source_id": "existing-income", "section": "소득 확인"}],
            "candidate_refs": [{"source_id": "counseling-income", "section": "소득 공백"}],
            "before": "partial", "gap": "소득 중단의 설명 부족", "improvement": "expanded_document",
            "evaluation_ids": ["known-query"],
        })
        self.payload = {
            "schema_version": "counseling-coverage/v1", "baseline_revision": "278bd53",
            "assessment_kind": "document-coverage-not-AX", "runtime_implemented": False,
            "supported_steps": steps, "fallback_step": "general", "cases": [case, combined],
        }

    def validate(self) -> dict:
        return validate_coverage(
            self.payload, registry=self.registry, existing_documents=self.existing,
            candidate_documents=self.candidates, evaluation_ids={"known-query"},
        )

    def test_data_first_is_orthogonal_to_knowledge_and_input_is_unchanged(self) -> None:
        before = copy.deepcopy(self.payload)
        report = self.validate()
        self.assertTrue(report["validation_passed"])
        self.assertEqual(report["counts"]["knowledge_mode"]["combined"], 1)
        self.assertEqual(report["counts"]["after"]["data_dependency"], 2)
        self.assertEqual(report["by_step"]["income"]["case_count"], 2)
        self.assertEqual(self.payload, before)
        for field in ("runtime_implemented", "retrieval_executed", "llm_evaluated", "ax_gain_measured"):
            self.assertFalse(report[field])

    def test_declared_steps_must_match_real_registry(self) -> None:
        self.payload["supported_steps"].remove("required-savings")
        with self.assertRaisesRegex(ValueError, "exactly match"):
            self.validate()

    def test_cases_must_cover_every_supported_step_and_fallback(self) -> None:
        for missing in ("required-savings", "general"):
            with self.subTest(missing=missing):
                original = self.payload["cases"][0]["step_ids"][:]
                self.payload["cases"][0]["step_ids"].remove(missing)
                with self.assertRaisesRegex(ValueError, "omit registered stages"):
                    self.validate()
                self.payload["cases"][0]["step_ids"] = original

    def test_unknown_case_step_and_wrong_fallback_fail(self) -> None:
        self.payload["cases"][0]["step_ids"].append("invented-step")
        with self.assertRaisesRegex(ValueError, "unregistered step_ids"):
            self.validate()
        self.payload["cases"][0]["step_ids"].pop()
        self.payload["fallback_step"] = "fallback"
        with self.assertRaisesRegex(ValueError, "registered general"):
            self.validate()

    def test_only_exact_h2_outside_code_is_a_valid_reference(self) -> None:
        for section in ("소득", "소득 공백 오타", "하위 설명", "가짜 제목", "## 소득 공백"):
            with self.subTest(section=section):
                self.payload["cases"][1]["candidate_refs"][0]["section"] = section
                with self.assertRaisesRegex(ValueError, "unknown H2 section"):
                    self.validate()

    def test_source_reference_cannot_cross_existing_and_candidate_namespaces(self) -> None:
        self.payload["cases"][1]["candidate_refs"] = [{"source_id": "existing-income", "section": "소득 확인"}]
        with self.assertRaisesRegex(ValueError, "unknown source_id"):
            self.validate()

    def test_no_retrieval_requires_both_empty_references_and_never(self) -> None:
        case = self.payload["cases"][0]
        case["retrieval_timing"] = "now"
        with self.assertRaisesRegex(ValueError, "no-retrieval contract"):
            self.validate()
        case["retrieval_timing"] = "never"
        case["existing_refs"] = [{"source_id": "existing-income", "section": "소득 확인"}]
        with self.assertRaisesRegex(ValueError, "knowledge_mode conflicts"):
            self.validate()
        case["knowledge_mode"] = "existing"
        with self.assertRaisesRegex(ValueError, "no-retrieval contract"):
            self.validate()

    def test_combined_mode_needs_both_types_and_unknown_evaluation_ids_fail(self) -> None:
        case = self.payload["cases"][1]
        original = case["existing_refs"]
        case["existing_refs"] = []
        with self.assertRaisesRegex(ValueError, "knowledge_mode conflicts"):
            self.validate()
        case["existing_refs"] = original
        case["evaluation_ids"] = ["not-evaluated-id"]
        with self.assertRaisesRegex(ValueError, "unknown evaluation_ids"):
            self.validate()

    def test_duplicate_case_ids_and_unexplained_gaps_fail(self) -> None:
        self.payload["cases"][1]["id"] = "no-retrieval"
        with self.assertRaisesRegex(ValueError, "duplicate case id"):
            self.validate()
        self.payload["cases"][1]["id"] = "data-and-knowledge"
        self.payload["cases"][1]["gap"] = ""
        with self.assertRaisesRegex(ValueError, "gap explanation"):
            self.validate()

    def test_runtime_or_ax_claims_are_rejected(self) -> None:
        self.payload["runtime_implemented"] = True
        with self.assertRaisesRegex(ValueError, "document-coverage-not-AX"):
            self.validate()

    def test_markdown_escapes_table_cells_and_cli_writes_requested_reports(self) -> None:
        self.payload["cases"][0]["question"] = "저장 | 완료\n맞나요?"
        self.payload["interpretation_notes"] = [
            "before는 기준 커밋의 문서 대응 범위다.",
            "after_verified_data는 필요한 데이터의 확인이 끝난 뒤 검색한다는 뜻이다.",
        ]
        report = self.validate()
        report["repository_root"] = "/review workspace"
        markdown = render_markdown(report)
        self.assertIn("저장 \\| 완료 맞나요?", markdown)
        self.assertIn("실제 AX 개선", markdown)
        self.assertIn("| ID | 단계 | 질문 | 지식·검색 시점 | 이전 → 이후 |", markdown)
        self.assertIn("### data-and-knowledge — 소득 공백", markdown)
        self.assertIn("**부족했던 설명:** 소득 중단의 설명 부족", markdown)
        self.assertIn("**확인할 맥락**\n\n- 현재 수입", markdown)
        self.assertIn("**후속 AX 평가에서 관찰할 행동**\n\n- ", markdown)
        self.assertIn("**평가 ID:** `known-query`", markdown)
        self.assertIn("[counseling-income](</review workspace/fixture/counseling-income.md>) — 소득 공백", markdown)
        for note in self.payload["interpretation_notes"]:
            self.assertIn(note, markdown)
        with tempfile.TemporaryDirectory() as directory:
            json_path = Path(directory) / "report.json"
            markdown_path = Path(directory) / "report.md"
            with patch("rag.evaluation.counseling_coverage.evaluate_coverage", return_value=report), contextlib.redirect_stdout(io.StringIO()):
                result = main(["--report", str(json_path), "--markdown", str(markdown_path)])
            self.assertEqual(result, 0)
            self.assertEqual(json.loads(json_path.read_text())["total_cases"], 2)
            self.assertEqual(markdown_path.read_text(), markdown)


class CounselingCoverageRealFixtureTests(unittest.TestCase):
    def test_real_fixture_resolves_steps_catalog_candidates_and_evaluation_ids(self) -> None:
        root = Path(__file__).resolve().parents[3]
        before = {
            path: path.read_bytes()
            for path in (
                root / "ai-pjt/rag/data/catalog.json",
                root / "ai-pjt/rag/data/counseling-candidate.json",
                root / "ai-pjt/rag/evaluation/counseling-coverage-v1.json",
            )
        }
        report = evaluate_coverage(repository_root=root)
        self.assertTrue(report["validation_passed"])
        self.assertEqual(len(report["supported_steps"]), 24)
        self.assertEqual(len(report["by_step"]), 25)
        self.assertEqual(sum(report["counts"]["before"].values()), report["total_cases"])
        self.assertEqual(report["source_counts"]["existing"], 26)
        self.assertEqual(report["repository_root"], str(root.resolve()))
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)


if __name__ == "__main__":
    unittest.main()
