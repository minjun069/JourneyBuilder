from __future__ import annotations

import io
import hashlib
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from rag.pipeline.chunking import DeterministicChunker, MarkdownStructureChunker
from rag.pipeline.cli import main as cli_main
from rag.pipeline.corpus import load_approved_catalog, load_corpus, load_gold_queries
from rag.pipeline.embeddings import (
    DeterministicHashEmbedding,
    LlamaIndexEmbeddingAdapter,
)
from rag.pipeline.evaluation import EvaluationThresholds, evaluate_retriever
from rag.pipeline.indexing import IndexPipeline, IndexedRetriever
from rag.pipeline.models import (
    DocumentMetadata,
    GoldQuery,
    IndexTarget,
    SourceDocument,
)
from rag.pipeline.store import JsonVectorStore


RAG_DIR = Path(__file__).resolve().parents[1]
SYNTHETIC_DIR = RAG_DIR / "fixtures" / "synthetic"


def make_document(
    source_id: str,
    text: str,
    *,
    version: str = "1.0",
    kind: str = "financial-knowledge",
    review_status: str = "synthetic",
    collection: str = "synthetic_financial",
) -> SourceDocument:
    content_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return SourceDocument(
        metadata=DocumentMetadata(
            source_id=source_id,
            title=f"{source_id} title",
            publisher="Synthetic Authority",
            source_locators=(f"synthetic://{source_id}",),
            published_at="2026-01-01",
            as_of_date="2026-01-01",
            version=version,
            scope="test only",
            review_status=review_status,
            reviewed_by="synthetic-test",
            document_kind=kind,
            collection=collection,
            license="synthetic only",
            sensitivity="public",
            sha256=(content_sha256 if review_status != "synthetic" else ""),
            catalog_document_id=f"catalog-{source_id}",
            file_path=f"synthetic/{source_id}.md",
        ),
        text=text,
    )


class PipelineTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = JsonVectorStore(Path(self.temporary.name))
        self.embedding = DeterministicHashEmbedding(64)
        self.chunker = DeterministicChunker(chunk_size=80, overlap=10)
        self.pipeline = IndexPipeline(
            store=self.store,
            chunker=self.chunker,
            embedding=self.embedding,
        )

    def test_chunk_ids_and_offsets_are_deterministic(self) -> None:
        document = make_document("source-a", "가" * 150)

        first = self.chunker.chunk_document(document)
        second = self.chunker.chunk_document(document)

        self.assertEqual(first, second)
        self.assertEqual([chunk.start_offset for chunk in first], [0, 70])
        self.assertEqual([chunk.end_offset for chunk in first], [80, 150])
        self.assertTrue(all(chunk.text == document.text[chunk.start_offset:chunk.end_offset] for chunk in first))

    def test_markdown_chunker_preserves_heading_and_complete_table(self) -> None:
        text = "# 지표\n\n설명입니다.\n\n| 연령 | 기준 |\n|---|---|\n| 30대 | 70% |\n\n## 해석\n\n후속 설명"
        document = make_document("markdown", text)
        chunks = MarkdownStructureChunker(chunk_size=45, overlap=8).chunk_document(
            document
        )

        table = "| 연령 | 기준 |\n|---|---|\n| 30대 | 70% |"
        self.assertTrue(any(table in chunk.text for chunk in chunks))
        self.assertTrue(
            all(chunk.text == text[chunk.start_offset:chunk.end_offset] for chunk in chunks)
        )

    def test_build_uses_idempotent_upsert(self) -> None:
        documents = [make_document("source-a", "연금저축 세액공제 안내")]
        target = IndexTarget("synthetic_financial", "v1")

        manifest = self.pipeline.build(documents, target)
        chunks = self.store.list_chunks(target.physical_collection)
        inserted, updated = self.store.upsert(target.physical_collection, chunks)

        self.assertEqual(inserted, 0)
        self.assertEqual(updated, manifest.chunk_count)
        self.assertEqual(
            len(self.store.list_chunks(target.physical_collection)),
            manifest.chunk_count,
        )

    def test_collection_policy_rejects_mixed_or_unapproved_documents(self) -> None:
        mixed = [
            make_document("finance", "금융 지식"),
            make_document("guide", "필드 가이드", kind="interface-guide"),
        ]
        with self.assertRaisesRegex(ValueError, "separate collections"):
            self.pipeline.build(mixed, IndexTarget("synthetic_mixed", "v1"))

        pending = [
            make_document("pending", "검토 전 문서", review_status="pending")
        ]
        with self.assertRaisesRegex(ValueError, "non-synthetic"):
            self.pipeline.build(pending, IndexTarget("synthetic_financial", "v1"))
        operational = IndexPipeline(
            store=self.store,
            chunker=self.chunker,
            embedding=self.embedding,
            approved_sources={
                "pending": (
                    "1.0",
                    hashlib.sha256("검토 전 문서".encode("utf-8")).hexdigest(),
                )
            },
        )
        with self.assertRaisesRegex(ValueError, "unapproved"):
            operational.build(
                pending,
                IndexTarget("financial-knowledge", "v1", operational=True),
            )

        approved = make_document(
            "approved",
            "승인 문서",
            review_status="approved",
            collection="financial-knowledge",
        )
        wrong_version = IndexPipeline(
            store=self.store,
            chunker=self.chunker,
            embedding=self.embedding,
            approved_sources={
                "approved": (
                    "2.0",
                    hashlib.sha256("승인 문서".encode("utf-8")).hexdigest(),
                )
            },
        )
        with self.assertRaisesRegex(ValueError, "source id/version/hash"):
            wrong_version.build(
                [approved],
                IndexTarget("financial-knowledge", "v1", operational=True),
            )

        duplicate_versions = [
            make_document("same-source", "구버전", version="1.0"),
            make_document("same-source", "신버전", version="2.0"),
        ]
        with self.assertRaisesRegex(ValueError, "only one version"):
            self.pipeline.build(
                duplicate_versions, IndexTarget("synthetic_financial", "v1")
            )

    def test_evaluation_must_pass_before_version_is_promoted(self) -> None:
        documents = [
            make_document("pension", "퇴직연금 DB형 DC형 IRP 안내"),
            make_document("emergency", "생활비 현금흐름 비상자금 안내"),
        ]
        manifest = self.pipeline.build(
            documents, IndexTarget("synthetic_financial", "v1")
        )
        retriever = IndexedRetriever(
            store=self.store, embedding=self.embedding, minimum_dense_score=0.0
        )
        failing = evaluate_retriever(
            retriever=retriever,
            manifest=manifest,
            gold_queries=[GoldQuery("missing", "없는 근거", ("pension",))],
            top_k=2,
            thresholds=EvaluationThresholds(0.85, 0.70),
        )

        self.assertFalse(failing.passed)
        with self.assertRaisesRegex(ValueError, "did not pass"):
            self.pipeline.promote(manifest, failing)
        self.assertIsNone(self.store.active_collection("synthetic_financial"))

    def test_reindex_keeps_old_version_active_until_new_version_passes(self) -> None:
        documents = [make_document("pension", "퇴직연금 DB형 DC형 IRP 안내")]
        gold = [GoldQuery("pension", "퇴직연금 DB형 DC형", ("pension",))]
        retriever = IndexedRetriever(store=self.store, embedding=self.embedding)

        v1 = self.pipeline.build(
            documents, IndexTarget("synthetic_financial", "v1")
        )
        v1_report = evaluate_retriever(
            retriever=retriever, manifest=v1, gold_queries=gold, top_k=1
        )
        self.pipeline.promote(v1, v1_report)
        result = retriever.retrieve("synthetic_financial", "퇴직연금", top_k=1)[0]
        self.assertEqual(v1.source_versions, (("pension", "1.0"),))
        self.assertEqual(result.chunk.metadata.source_id, "pension")
        self.assertEqual(result.chunk.metadata.version, "1.0")
        self.assertEqual(result.chunk.text, documents[0].text)
        self.assertGreaterEqual(result.score, 0.0)

        v2 = self.pipeline.build(
            documents, IndexTarget("synthetic_financial", "v2")
        )
        self.assertEqual(
            self.store.active_collection("synthetic_financial"),
            v1.physical_collection,
        )
        self.assertTrue(self.store.list_chunks(v1.physical_collection))
        v2_report = evaluate_retriever(
            retriever=retriever, manifest=v2, gold_queries=gold, top_k=1
        )
        self.pipeline.promote(v2, v2_report)

        self.assertEqual(
            self.store.active_collection("synthetic_financial"),
            v2.physical_collection,
        )
        self.assertTrue(self.store.list_chunks(v1.physical_collection))

    def test_multi_collection_promotion_is_all_or_nothing(self) -> None:
        first_manifest = self.pipeline.build(
            [
                make_document(
                    "first", "첫 번째 승인 근거", collection="synthetic_first"
                )
            ],
            IndexTarget("synthetic_first", "v1"),
        )
        second_manifest = self.pipeline.build(
            [
                make_document(
                    "second", "두 번째 승인 근거", collection="synthetic_second"
                )
            ],
            IndexTarget("synthetic_second", "v1"),
        )
        retriever = IndexedRetriever(
            store=self.store, embedding=self.embedding, minimum_dense_score=0.0
        )
        first_report = evaluate_retriever(
            retriever=retriever,
            manifest=first_manifest,
            gold_queries=[GoldQuery("first", "첫 번째 근거", ("first",))],
            top_k=1,
        )
        second_report = replace(
            evaluate_retriever(
                retriever=retriever,
                manifest=second_manifest,
                gold_queries=[GoldQuery("second", "두 번째 근거", ("second",))],
                top_k=1,
            ),
            passed=False,
        )

        self.assertTrue(first_report.passed)
        self.assertFalse(second_report.passed)
        with self.assertRaisesRegex(ValueError, "did not pass"):
            self.pipeline.promote_many(
                [
                    (first_manifest, first_report),
                    (second_manifest, second_report),
                ]
            )
        self.assertIsNone(self.store.active_collection("synthetic_first"))
        self.assertIsNone(self.store.active_collection("synthetic_second"))

    def test_active_version_is_immutable_but_identical_rebuild_is_noop(self) -> None:
        documents = [make_document("pension", "퇴직연금 안내")]
        target = IndexTarget("synthetic_financial", "v1")
        manifest = self.pipeline.build(documents, target)
        report = evaluate_retriever(
            retriever=IndexedRetriever(store=self.store, embedding=self.embedding),
            manifest=manifest,
            gold_queries=[GoldQuery("pension", "퇴직연금", ("pension",))],
            top_k=1,
        )
        self.pipeline.promote(manifest, report)

        self.assertEqual(self.pipeline.build(documents, target), manifest)
        changed = [make_document("pension", "변경된 퇴직연금 안내")]
        with self.assertRaisesRegex(ValueError, "immutable"):
            self.pipeline.build(changed, target)


class FixtureAndAdapterTestCase(unittest.TestCase):
    def test_domain_approved_catalog_maps_all_files_and_provenance(self) -> None:
        repository_root = RAG_DIR.parents[1]
        catalog = load_approved_catalog(
            RAG_DIR / "data" / "catalog.json",
            repository_root=repository_root,
        )

        self.assertEqual(catalog.catalog_version, "2.1.0")
        self.assertEqual(len(catalog.for_collection("interface-guides")), 10)
        self.assertEqual(len(catalog.for_collection("financial-knowledge")), 14)
        self.assertTrue(
            all(document.metadata.review_status == "approved" for document in catalog.documents)
        )
        self.assertTrue(
            all(len(document.metadata.sha256) == 64 for document in catalog.documents)
        )

    def test_catalog_rejects_unapproved_acceptance_before_reading_files(self) -> None:
        payload = json.loads(
            (RAG_DIR / "data" / "catalog.json").read_text(encoding="utf-8")
        )
        payload["acceptance"]["status"] = "pending"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "domain-approved"):
                load_approved_catalog(path, repository_root=RAG_DIR.parents[1])

    def test_synthetic_fixtures_are_valid_and_evaluable(self) -> None:
        documents = load_corpus(SYNTHETIC_DIR / "corpus.json")
        gold = load_gold_queries(SYNTHETIC_DIR / "gold.json")

        self.assertEqual(len(documents), 4)
        self.assertEqual(len(gold), 4)
        self.assertTrue(
            all(document.metadata.review_status == "synthetic" for document in documents)
        )

    def test_corpus_loader_rejects_missing_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            path.write_text(
                json.dumps({"documents": [{"metadata": {}, "text": "content"}]}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "missing"):
                load_corpus(path)

    def test_llama_index_adapter_is_injected_and_dimension_checked(self) -> None:
        class FakeEmbedding:
            def get_text_embedding_batch(self, texts: list[str]) -> list[list[float]]:
                return [[float(len(text)), 1.0] for text in texts]

        adapter = LlamaIndexEmbeddingAdapter(
            FakeEmbedding(), model_id="fake-v1", dimensions=2
        )
        self.assertEqual(adapter.embed_texts(["abc"]), [(3.0, 1.0)])

        wrong = LlamaIndexEmbeddingAdapter(
            FakeEmbedding(), model_id="fake-v1", dimensions=3
        )
        with self.assertRaisesRegex(ValueError, "dimension"):
            wrong.embed_texts(["abc"])

    def test_cli_run_is_reproducible_and_promotes_only_passing_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            arguments = ["run-synthetic", "--workspace", directory]
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                first = cli_main(arguments)
                second = cli_main(arguments)

            self.assertEqual(first, 0)
            self.assertEqual(second, 0)
            report_path = (
                Path(directory)
                / "synthetic_financial_knowledge__v1-report.json"
            )
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertTrue(payload["evaluation"]["passed"])
            self.assertEqual(
                payload["active_collection"],
                "synthetic_financial_knowledge__v1",
            )

    def test_catalog_dry_run_evaluates_all_58_queries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            arguments = ["run-catalog-dry-run", "--workspace", directory]
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                result = cli_main(arguments)

            self.assertEqual(result, 0)
            interface = json.loads(
                (
                    Path(directory)
                    / "interface-guides__experiment-catalog-2-1-0-report.json"
                ).read_text(encoding="utf-8")
            )
            financial = json.loads(
                (
                    Path(directory)
                    / "financial-knowledge__experiment-catalog-2-1-0-report.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(interface["evaluation"]["query_count"], 20)
            self.assertEqual(financial["evaluation"]["query_count"], 38)
            self.assertTrue(interface["evaluation"]["passed"])
            self.assertTrue(financial["evaluation"]["passed"])
            self.assertEqual(financial["evaluation"]["false_evidence_count"], 0)

    def test_official_qualification_requires_rag_owner_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            errors = io.StringIO()
            with patch.dict(
                "os.environ",
                {"DATABASE_URL": "postgresql://unused/test"},
                clear=True,
            ), redirect_stdout(io.StringIO()), redirect_stderr(errors):
                result = cli_main(
                    [
                        "run-operational",
                        "--report-dir",
                        directory,
                        "--artifact-kind",
                        "official",
                        "--operator-id",
                        "rag-owner",
                    ]
                )

            self.assertEqual(result, 2)
            self.assertIn("confirm-rag-owner", errors.getvalue())

    def test_official_run_records_one_qualification_and_activates_both(self) -> None:
        class OperationalJsonStore(JsonVectorStore):
            def ensure_schema(self) -> None:
                return None

            def prune_retired(
                self, logical_collection: str, *, retention_days: int = 30
            ) -> int:
                return 0

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = OperationalJsonStore(root / "store")
            embedding = DeterministicHashEmbedding(2048)
            with patch.dict(
                "os.environ",
                {"DATABASE_URL": "postgresql://unused/test"},
                clear=True,
            ), patch(
                "rag.pipeline.cli.PostgresIndexStore", return_value=store
            ), patch(
                "rag.pipeline.cli._operational_embedding", return_value=embedding
            ), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                result = cli_main(
                    [
                        "run-operational",
                        "--report-dir",
                        str(root / "reports"),
                        "--artifact-kind",
                        "official",
                        "--environment-id",
                        "test-owner-db",
                        "--operator-id",
                        "rag-owner",
                        "--confirm-rag-owner",
                        "--index-version",
                        "catalog-2-1-0-test",
                        "--promote",
                    ]
                )

            payload = json.loads(
                (
                    root
                    / "reports"
                    / "official__catalog-2-1-0-test__qualification.json"
                ).read_text(encoding="utf-8")
            )

        self.assertEqual(result, 0)
        self.assertEqual(payload["query_count"], 58)
        self.assertEqual(payload["overall_decision"], "PASS")
        self.assertTrue(payload["activatable"])
        self.assertTrue(payload["activation"]["performed"])
        self.assertEqual(
            payload["retrieval"],
            {
                "strategy": "dense-bm25-rrf",
                "top_k": 5,
                "fetch_k": 10,
                "minimum_dense_score": 0.52,
            },
        )
        self.assertEqual(
            set(payload["activation"]["after"].values()),
            {
                "interface-guides__official-catalog-2-1-0-test",
                "financial-knowledge__official-catalog-2-1-0-test",
            },
        )

    def test_negative_query_fails_when_retriever_returns_false_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = JsonVectorStore(Path(directory))
            embedding = DeterministicHashEmbedding(64)
            pipeline = IndexPipeline(
                store=store,
                chunker=DeterministicChunker(80, 10),
                embedding=embedding,
            )
            manifest = pipeline.build(
                [make_document("pension", "퇴직연금 제도 안내")],
                IndexTarget("synthetic_financial", "v1"),
            )
            report = evaluate_retriever(
                retriever=IndexedRetriever(
                    store=store,
                    embedding=embedding,
                    minimum_dense_score=0.0,
                ),
                manifest=manifest,
                gold_queries=[
                    GoldQuery("positive", "퇴직연금", ("pension",)),
                    GoldQuery("negative", "오늘 날씨", (), category="no_result"),
                ],
                top_k=1,
            )

            self.assertEqual(report.false_evidence_count, 1)
            self.assertFalse(report.passed)


if __name__ == "__main__":
    unittest.main()
