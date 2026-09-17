from __future__ import annotations

import os
import hashlib
import unittest
from pathlib import Path

from rag.pipeline.chunking import MarkdownStructureChunker
from rag.pipeline.corpus import load_approved_catalog, load_gold_queries
from rag.pipeline.embeddings import DeterministicHashEmbedding
from rag.pipeline.evaluation import evaluate_retriever
from rag.pipeline.indexing import IndexPipeline, IndexedRetriever
from rag.pipeline.models import DocumentMetadata, GoldQuery, IndexTarget, SourceDocument
from rag.pipeline.store import PostgresIndexStore


DATABASE_URL = os.environ.get("RAG_TEST_DATABASE_URL", "").strip()


def make_document(version: str, text: str) -> SourceDocument:
    return SourceDocument(
        metadata=DocumentMetadata(
            source_id="postgres-smoke-source",
            title="PostgreSQL 저장소 검사 문서",
            publisher="S15P21B109",
            source_locators=("test://postgres-smoke",),
            published_at="2026-09-07",
            as_of_date="2026-09-07",
            version=version,
            scope="isolated integration test",
            review_status="approved",
            reviewed_by="test",
            reviewed_at="2026-09-07",
            document_kind="financial-knowledge",
            collection="financial-knowledge",
            license="test only",
            sensitivity="public",
            sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            catalog_document_id="postgres-smoke",
            file_path="test/postgres-smoke.md",
        ),
        text=text,
    )


@unittest.skipUnless(DATABASE_URL, "RAG_TEST_DATABASE_URL is not configured")
class PostgresStoreIntegrationTests(unittest.TestCase):
    def test_approved_catalog_and_66_queries_pass_in_pgvector(self) -> None:
        repository_root = Path(__file__).resolve().parents[3]
        rag_root = repository_root / "ai-pjt" / "rag"
        catalog = load_approved_catalog(
            rag_root / "data" / "catalog.json",
            repository_root=repository_root,
        )
        approved_sources = {
            document.metadata.source_id: (
                document.metadata.version,
                document.metadata.sha256,
            )
            for document in catalog.documents
        }
        store = PostgresIndexStore(DATABASE_URL)
        store.ensure_schema()
        embedding = DeterministicHashEmbedding(2048)
        pipeline = IndexPipeline(
            store=store,
            chunker=MarkdownStructureChunker(),
            embedding=embedding,
            approved_sources=approved_sources,
        )
        retriever = IndexedRetriever(
            store=store,
            embedding=embedding,
            fetch_k=10,
            minimum_dense_score=0.30,
        )

        query_count = 0
        candidates = []
        for collection in ("interface-guides", "financial-knowledge"):
            manifest = pipeline.build(
                catalog.for_collection(collection),
                IndexTarget(collection, "postgres-catalog-2-2-0", operational=True),
            )
            report = evaluate_retriever(
                retriever=retriever,
                manifest=manifest,
                gold_queries=load_gold_queries(
                    rag_root / "evaluation" / f"{collection}-gold-v1.json"
                ),
                evaluation_set_id=f"postgres-{collection}",
                top_k=5,
            )
            self.assertTrue(report.passed)
            candidates.append((manifest, report))
            query_count += report.query_count

        self.assertEqual(query_count, 66)
        pipeline.promote_many(candidates)
        for manifest, _ in candidates:
            self.assertEqual(
                store.active_collection(manifest.collection),
                manifest.physical_collection,
            )

    def test_build_search_and_atomic_version_switch(self) -> None:
        store = PostgresIndexStore(DATABASE_URL)
        store.ensure_schema()
        embedding = DeterministicHashEmbedding(64)
        retriever = IndexedRetriever(
            store=store,
            embedding=embedding,
            minimum_dense_score=0.0,
        )

        v1_document = make_document("1.0.0", "퇴직연금 DB형 DC형 IRP 안내")
        v1_pipeline = IndexPipeline(
            store=store,
            chunker=MarkdownStructureChunker(),
            embedding=embedding,
            approved_sources={
                "postgres-smoke-source": (
                    "1.0.0",
                    v1_document.metadata.sha256,
                )
            },
        )
        v1 = v1_pipeline.build(
            [v1_document],
            IndexTarget("financial-knowledge", "postgres-smoke-v1", operational=True),
        )
        v1_report = evaluate_retriever(
            retriever=retriever,
            manifest=v1,
            gold_queries=[
                GoldQuery("postgres-smoke", "퇴직연금 DB형", ("postgres-smoke-source",))
            ],
            top_k=1,
        )
        v1_pipeline.promote(v1, v1_report)
        self.assertEqual(
            store.active_collection("financial-knowledge"),
            v1.physical_collection,
        )
        self.assertEqual(
            retriever.retrieve("financial-knowledge", "퇴직연금", top_k=1)[0]
            .chunk.metadata.version,
            "1.0.0",
        )

        v2_document = make_document("2.0.0", "퇴직연금 DB형 DC형 IRP 최신 안내")
        v2_pipeline = IndexPipeline(
            store=store,
            chunker=MarkdownStructureChunker(),
            embedding=embedding,
            approved_sources={
                "postgres-smoke-source": (
                    "2.0.0",
                    v2_document.metadata.sha256,
                )
            },
        )
        v2 = v2_pipeline.build(
            [v2_document],
            IndexTarget("financial-knowledge", "postgres-smoke-v2", operational=True),
        )
        v2_report = evaluate_retriever(
            retriever=retriever,
            manifest=v2,
            gold_queries=[
                GoldQuery("postgres-smoke", "퇴직연금 최신", ("postgres-smoke-source",))
            ],
            top_k=1,
        )
        v2_pipeline.promote(v2, v2_report)

        self.assertEqual(
            store.active_collection("financial-knowledge"),
            v2.physical_collection,
        )
        self.assertTrue(store.list_chunks(v1.physical_collection))


if __name__ == "__main__":
    unittest.main()
