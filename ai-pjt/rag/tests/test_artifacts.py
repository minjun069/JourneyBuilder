from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rag.artifacts import export_artifact, import_artifact, inspect_artifact
from rag.pipeline.chunking import DeterministicChunker
from rag.pipeline.embeddings import DeterministicHashEmbedding
from rag.pipeline.indexing import IndexPipeline
from rag.pipeline.models import IndexTarget
from rag.pipeline.store import PostgresIndexStore
from rag.tests.test_pipeline import make_document


DATABASE_URL = os.environ.get("RAG_TEST_DATABASE_URL", "").strip()


def qualification(*, passed: bool = True) -> dict:
    collections = []
    active = {}
    for logical in ("interface-guides", "financial-knowledge"):
        physical = f"{logical}__official-catalog-2-1-0"
        manifest = {
            "collection": logical,
            "physical_collection": physical,
            "index_version": "catalog-2-1-0",
            "corpus_digest": f"digest-{logical}",
            "document_kind": (
                "interface-guide" if logical == "interface-guides" else "financial-knowledge"
            ),
            "document_count": 1,
            "chunk_count": 0,
            "chunker_id": "markdown-v1",
            "embedding_model": "gemini-embedding-2",
            "embedding_dimensions": 3072,
            "source_versions": [],
            "built_at": "2026-09-10T00:00:00+00:00",
            "artifact_kind": "official",
        }
        collections.append(
            {
                "logical_collection": logical,
                "physical_collection": physical,
                "decision": "PASS" if passed else "FAIL",
                "manifest": manifest,
                "evaluation": {
                    "query_count": 20 if logical == "interface-guides" else 38,
                    "passed": passed,
                },
            }
        )
        active[logical] = physical
    return {
        "schema_version": "rag-qualification/v1",
        "qualification_id": "official-catalog-2-1-0",
        "artifact_kind": "official",
        "environment_id": "owner-local",
        "operator_id": "rag-owner",
        "source_revision": "a" * 40,
        "catalog": {"version": "2.1.0", "sha256": "b" * 64},
        "calculation_policy": {"version": "2026-09-07.v1", "sha256": "c" * 64},
        "embedding": {
            "provider": "gemini",
            "model": "gemini-embedding-2",
            "dimensions": 3072,
            "endpoint": "gms",
        },
        "collections": collections,
        "overall_decision": "PASS" if passed else "FAIL",
        "activatable": passed,
        "deployable": passed,
        "activation": {
            "requested": True,
            "performed": passed,
            "before": {logical: None for logical in active},
            "after": active,
        },
    }


class ArtifactTestCase(unittest.TestCase):
    def test_export_contains_only_qualified_rows_and_verifiable_checksums(self) -> None:
        payload = qualification()
        manifests = [
            {
                "physical_collection": result["physical_collection"],
                "logical_collection": result["logical_collection"],
                "manifest": result["manifest"],
                "created_at": "2026-09-10 00:00:00+00",
                "retired_at": None,
            }
            for result in payload["collections"]
        ]
        active = [
            {
                "logical_collection": result["logical_collection"],
                "physical_collection": result["physical_collection"],
                "activated_at": "2026-09-10 00:01:00+00",
            }
            for result in payload["collections"]
        ]
        rows = {
            "rag_index_manifests": manifests,
            "rag_index_chunks": [],
            "rag_active_indexes": active,
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_path = root / "qualification.json"
            report_path.write_text(json.dumps(payload), encoding="utf-8")
            with patch("rag.artifacts._export_rows", return_value=rows):
                artifact_path, result = export_artifact(
                    database_url="postgresql://unused/test",
                    qualification_path=report_path,
                    output_dir=root,
                )
            inspected = inspect_artifact(artifact_path)

        self.assertTrue(result["git_tracking_candidate"])
        self.assertEqual(inspected["manifest"]["artifact_kind"], "official")
        self.assertEqual(
            inspected["manifest"]["database_scope"]["tables"],
            ["rag_index_manifests", "rag_index_chunks", "rag_active_indexes"],
        )
        self.assertTrue(
            inspected["manifest"]["producer_smoke"]["active_pointer_match"]
        )

    def test_failed_qualification_is_recorded_but_not_exportable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_path = root / "qualification.json"
            report_path.write_text(json.dumps(qualification(passed=False)), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "fully passing"):
                export_artifact(
                    database_url="postgresql://unused/test",
                    qualification_path=report_path,
                    output_dir=root,
                )


@unittest.skipUnless(DATABASE_URL, "RAG_TEST_DATABASE_URL is not configured")
class ArtifactIntegrationTestCase(unittest.TestCase):
    def test_existing_database_reuses_an_identical_logical_backup(self) -> None:
        store = PostgresIndexStore(DATABASE_URL)
        store.ensure_schema()
        embedding = DeterministicHashEmbedding(8)
        documents = [
            make_document(
                "artifact-interface",
                "기본 정보 입력 안내",
                kind="interface-guide",
                review_status="approved",
                collection="interface-guides",
            ),
            make_document(
                "artifact-financial",
                "은퇴 준비 비상자금 안내",
                review_status="approved",
                collection="financial-knowledge",
            ),
        ]
        pipeline = IndexPipeline(
            store=store,
            chunker=DeterministicChunker(80, 10),
            embedding=embedding,
            approved_sources={
                document.metadata.source_id: (
                    document.metadata.version,
                    document.metadata.sha256,
                )
                for document in documents
            },
        )
        manifests = []
        for document in documents:
            target = IndexTarget(
                document.metadata.collection,
                "artifact-integration",
                operational=True,
                artifact_kind="experiment",
            )
            manifests.append(pipeline.build([document], target))
        payload = {
            "schema_version": "rag-qualification/v1",
            "qualification_id": "experiment-artifact-integration",
            "artifact_kind": "experiment",
            "environment_id": "integration-test",
            "operator_id": None,
            "source_revision": "integration-test",
            "catalog": {"version": "test", "sha256": "test"},
            "calculation_policy": {"version": "test", "sha256": "test"},
            "embedding": {
                "provider": "deterministic",
                "model": embedding.model_id,
                "dimensions": embedding.dimensions,
            },
            "collections": [
                {
                    "logical_collection": manifest.collection,
                    "physical_collection": manifest.physical_collection,
                    "decision": "PASS",
                    "manifest": manifest.to_dict(),
                    "evaluation": {"query_count": 1, "passed": True},
                }
                for manifest in manifests
            ],
            "overall_decision": "PASS",
            "activatable": True,
            "deployable": False,
            "activation": {
                "requested": False,
                "performed": False,
                "before": {},
                "after": {},
            },
        }
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                report_path = root / "qualification.json"
                report_path.write_text(json.dumps(payload), encoding="utf-8")
                artifact_path, _ = export_artifact(
                    database_url=DATABASE_URL,
                    qualification_path=report_path,
                    output_dir=root,
                )
                result = import_artifact(
                    database_url=DATABASE_URL,
                    artifact_path=artifact_path,
                )
            self.assertEqual(result["inserted_physical_collections"], [])
            self.assertEqual(
                set(result["reused_physical_collections"]),
                {manifest.physical_collection for manifest in manifests},
            )
        finally:
            for manifest in manifests:
                store.clear_collection(manifest.physical_collection)


if __name__ == "__main__":
    unittest.main()
