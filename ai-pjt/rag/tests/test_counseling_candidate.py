from __future__ import annotations

import copy
import hashlib
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rag.evaluation.counseling_candidate import evaluate_candidate, load_candidate
from rag.pipeline.corpus import load_approved_catalog
from rag.pipeline.chunking import MarkdownStructureChunker
from rag.pipeline.store import JsonVectorStore


class CounselingCandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        relative = Path("ai-pjt/rag/data/financial-knowledge/counseling-cash-flow.md")
        self.document = self.root / relative
        self.document.parent.mkdir(parents=True)
        self.document.write_text(
            "# 정기 생활비 현금흐름\n\n정기 생활비 현금흐름을 확인합니다.\n",
            encoding="utf-8",
        )
        self.manifest = self.root / "candidate.json"
        self.gold = self.root / "gold.json"
        self.payload = {
            "schema_version": "counseling-candidate/v1",
            "catalog_version": "counseling-1.0.0",
            "as_of_date": "2026-09-21",
            "hash_algorithm": "sha256",
            "acceptance": {
                "status": "pending", "approved_at": None, "reviewed_by": None,
                "service_boundary": "재무진단과 설명까지", "license": "프로젝트 작성 요약",
                "sensitivity": "public",
            },
            "sources": [{
                "id": "public-education", "publisher": "Public Agency",
                "title": "Public education", "url": "https://example.org/education",
                "locator": "Chapter on understanding cash flow",
                "verified_at": "2026-09-21", "published_at": None,
                "usage": "adapted-concepts",
                "rights": {"status": "public-domain", "url": "https://example.org/rights", "note": "Fixture only"},
            }],
            "documents": [{
                "id": "counseling-cash-flow", "source_id": "candidate-cash-flow",
                "path": relative.as_posix(), "title": "정기 생활비 현금흐름",
                "document_type": "financial-knowledge", "collection": "financial-knowledge",
                "publisher": "S15P21B109", "published_at": "2026-09-21",
                "as_of_date": "2026-09-21", "version": "1.0.0",
                "source_refs": ["public-education"],
                "applies_to": {"country": "KR", "topic": "cash-flow"},
                "refresh": {"cadence_days": 180, "triggers": []},
                "retire_when": "A reviewed replacement is available",
                "sha256": hashlib.sha256(self.document.read_bytes()).hexdigest(),
            }],
        }
        self._write_manifest()
        self.gold.write_text(json.dumps({"queries": [
            {"query_id": "cash-flow", "query": "정기 생활비 현금흐름", "relevant_source_ids": ["candidate-cash-flow"]},
            {"query_id": "unrelated", "query": "알파센타우리 테라포밍", "relevant_source_ids": [], "category": "no_result"},
        ]}), encoding="utf-8")

    def _write_manifest(self) -> None:
        self.manifest.write_text(json.dumps(self.payload, ensure_ascii=False), encoding="utf-8")

    def test_pending_sources_remain_unapproved_and_cannot_use_operational_loader(self) -> None:
        before = self.manifest.read_bytes()
        corpus = load_candidate(self.manifest, repository_root=self.root)
        self.assertEqual(corpus.documents[0].metadata.review_status, "pending")
        self.assertEqual(corpus.documents[0].metadata.reviewed_by, "unreviewed")
        self.assertIsNone(corpus.documents[0].metadata.reviewed_at)
        with self.assertRaisesRegex(ValueError, "domain-approved"):
            load_approved_catalog(self.manifest, repository_root=self.root)
        self.assertEqual(self.manifest.read_bytes(), before)

    def test_evaluation_uses_temporary_physical_index_without_activation(self) -> None:
        before = self.manifest.read_bytes()
        with (
            patch.object(JsonVectorStore, "activate", side_effect=AssertionError("must not activate")),
            patch.object(JsonVectorStore, "activate_many", side_effect=AssertionError("must not activate")),
        ):
            report = evaluate_candidate(self.manifest, self.gold, repository_root=self.root)
        self.assertFalse(report["deployable"])
        self.assertFalse(report["llm_evaluated"])
        self.assertEqual(report["acceptance"], "pending")
        self.assertTrue(report["retrieval_checks_passed"])
        evaluation = report["candidate_only"]["evaluation"]
        self.assertEqual(evaluation["false_evidence_count"], 0)
        self.assertEqual(evaluation["provenance_completeness"], 1.0)
        self.assertEqual(self.manifest.read_bytes(), before)
        self.assertEqual(list(self.root.rglob("active.json")), [])

    def test_claimed_approval_or_reviewer_is_rejected(self) -> None:
        original = copy.deepcopy(self.payload)
        for changes in (
            {"status": "domain-approved"}, {"approved_at": "2026-09-21"},
            {"reviewed_by": "someone"},
        ):
            with self.subTest(changes=changes):
                self.payload = copy.deepcopy(original)
                self.payload["acceptance"].update(changes)
                self._write_manifest()
                with self.assertRaises(ValueError):
                    load_candidate(self.manifest, repository_root=self.root)

    def test_modified_document_fails_hash_validation(self) -> None:
        self.document.write_text("changed after cataloging", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "sha256 mismatch"):
            load_candidate(self.manifest, repository_root=self.root)

    def test_unknown_document_source_reference_is_rejected(self) -> None:
        self.payload["documents"][0]["source_refs"] = ["missing-source"]
        self._write_manifest()
        with self.assertRaisesRegex(ValueError, "unknown source_refs"):
            load_candidate(self.manifest, repository_root=self.root)

    def test_unknown_gold_reference_is_rejected(self) -> None:
        self.gold.write_text(json.dumps({"queries": [{
            "query_id": "unknown", "query": "현금흐름",
            "relevant_source_ids": ["not-in-corpus"],
        }]}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "outside the manifest"):
            evaluate_candidate(self.manifest, self.gold, repository_root=self.root)

    def test_outside_path_and_document_symlink_are_rejected(self) -> None:
        original_path = self.payload["documents"][0]["path"]
        self.payload["documents"][0]["path"] = "../counseling-private.md"
        self._write_manifest()
        with self.assertRaisesRegex(ValueError, "candidate path"):
            load_candidate(self.manifest, repository_root=self.root)
        self.payload["documents"][0]["path"] = original_path
        self._write_manifest()
        target = self.root / "outside.md"
        self.document.rename(target)
        self.document.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "symlink"):
            load_candidate(self.manifest, repository_root=self.root)

    def test_duplicate_registry_id_and_incomplete_rights_are_rejected(self) -> None:
        original = copy.deepcopy(self.payload)
        self.payload["sources"].append(copy.deepcopy(self.payload["sources"][0]))
        self._write_manifest()
        with self.assertRaisesRegex(ValueError, "duplicate source registry"):
            load_candidate(self.manifest, repository_root=self.root)
        self.payload = original
        self.payload["sources"][0]["rights"].pop("url")
        self._write_manifest()
        with self.assertRaisesRegex(ValueError, "rights.url"):
            load_candidate(self.manifest, repository_root=self.root)

    def test_mixed_comparison_preserves_each_sources_review_status(self) -> None:
        existing = self.document.parent / "existing-deposits.md"
        existing.write_text("# 예금보호 합산한도\n\n예금보호 합산한도 예금기관.\n", encoding="utf-8")
        approved_document = copy.deepcopy(self.payload["documents"][0])
        approved_document.update({
            "id": "existing-deposits", "source_id": "existing-deposits",
            "path": existing.relative_to(self.root).as_posix(),
            "title": "예금보호 합산한도", "source_locator": "https://example.org/deposits",
            "sha256": hashlib.sha256(existing.read_bytes()).hexdigest(),
        })
        approved = copy.deepcopy(self.payload)
        approved["catalog_version"] = "1.0.0"
        approved["acceptance"].update({
            "status": "domain-approved", "reviewed_by": "fixture-reviewer", "approved_at": "2026-09-21",
        })
        approved["collections"] = [{
            "id": "financial-knowledge", "document_type": "financial-knowledge",
            "path_pattern": "ai-pjt/rag/data/financial-knowledge/*.md",
        }]
        approved["documents"] = [approved_document]
        catalog = self.root / "approved.json"
        catalog.write_text(json.dumps(approved), encoding="utf-8")
        baseline_gold = self.root / "baseline-gold.json"
        baseline_gold.write_text(json.dumps({"queries": [{
            "query_id": "deposits", "query": "예금보호 합산한도",
            "relevant_source_ids": ["existing-deposits"],
        }]}), encoding="utf-8")
        observed: set[tuple[str, str]] = set()
        original_upsert = JsonVectorStore.upsert

        def record_status(store, collection, chunks):
            observed.update((item.chunk.metadata.source_id, item.chunk.metadata.review_status) for item in chunks)
            return original_upsert(store, collection, chunks)

        with patch.object(JsonVectorStore, "upsert", record_status):
            report = evaluate_candidate(
                self.manifest, self.gold, repository_root=self.root,
                approved_catalog_path=catalog, baseline_gold_path=baseline_gold,
            )
        self.assertEqual(observed, {("candidate-cash-flow", "pending"), ("existing-deposits", "approved")})
        self.assertIn("mixed_candidate_queries", report)
        self.assertEqual(report["existing_query_delta"]["recall_at_k"], 0.0)
        self.assertFalse(report["deployable"])


class CounselingCorpusContentTests(unittest.TestCase):
    def test_real_cards_keep_applicability_and_examples_in_one_chunk(self) -> None:
        root = Path(__file__).resolve().parents[3]
        candidate = load_candidate(
            root / "ai-pjt/rag/data/counseling-candidate.json", repository_root=root
        )
        chunker = MarkdownStructureChunker(900, 100)
        checked = 0
        for document in candidate.documents:
            chunks = chunker.chunk_document(document)
            for section in re.split(r"(?m)^## ", document.text)[1:]:
                if "적용 상황:" not in section:
                    continue
                title, body = section.split("\n", 1)
                body = body.strip()
                with self.subTest(source=document.metadata.source_id, section=title):
                    self.assertTrue(
                        any(body in chunk.text for chunk in chunks),
                        "Counseling conditions and examples must remain in the same chunk",
                    )
                checked += 1
        self.assertGreater(checked, 0)

    def test_behavior_cases_are_unexecuted_synthetic_and_resolve_sources(self) -> None:
        root = Path(__file__).resolve().parents[3]
        candidate = load_candidate(
            root / "ai-pjt/rag/data/counseling-candidate.json", repository_root=root
        )
        path = root / "ai-pjt/rag/evaluation/counseling-behavior-cases-v1.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["status"], "not-executed")
        self.assertEqual(payload["data_classification"], "synthetic-only")
        cases = payload["cases"]
        self.assertTrue(cases)
        self.assertEqual(len(cases), len({case["id"] for case in cases}))
        sources = {document.metadata.source_id for document in candidate.documents}
        for case in cases:
            with self.subTest(case=case["id"]):
                self.assertTrue(case["session_id"].startswith("synthetic-session-"))
                self.assertTrue(set(case["relevant_source_ids"]).issubset(sources))
                self.assertTrue(case["turns"])
                self.assertTrue(case["must_preserve"])
                self.assertTrue(case["must_not_infer"])
                self.assertTrue(case["answer_expectations"])


if __name__ == "__main__":
    unittest.main()
