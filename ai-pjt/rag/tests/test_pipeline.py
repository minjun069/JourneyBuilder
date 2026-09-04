from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from rag.pipeline.chunking import DeterministicChunker
from rag.pipeline.cli import main as cli_main
from rag.pipeline.corpus import load_corpus, load_gold_queries
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
    kind: str = "financial_knowledge",
    review_status: str = "synthetic",
) -> SourceDocument:
    return SourceDocument(
        metadata=DocumentMetadata(
            source_id=source_id,
            title=f"{source_id} title",
            issuer="Synthetic Authority",
            source_uri=f"synthetic://{source_id}",
            published_at="2026-01-01",
            as_of="2026-01-01",
            version=version,
            scope="test only",
            review_status=review_status,
            reviewed_by="synthetic-test",
            document_kind=kind,
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
            make_document("guide", "필드 가이드", kind="interface_guide"),
        ]
        with self.assertRaisesRegex(ValueError, "separate collections"):
            self.pipeline.build(mixed, IndexTarget("synthetic_mixed", "v1"))

        pending = [
            make_document("pending", "검토 전 문서", review_status="pending")
        ]
        with self.assertRaisesRegex(ValueError, "non-synthetic"):
            self.pipeline.build(pending, IndexTarget("synthetic_financial", "v1"))
        with self.assertRaisesRegex(ValueError, "unapproved"):
            self.pipeline.build(
                pending,
                IndexTarget("financial_knowledge", "v1", operational=True),
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
        retriever = IndexedRetriever(store=self.store, embedding=self.embedding)
        failing = evaluate_retriever(
            retriever=retriever,
            manifest=manifest,
            gold_queries=[GoldQuery("missing", "없는 근거", ("not-indexed",))],
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


if __name__ == "__main__":
    unittest.main()
