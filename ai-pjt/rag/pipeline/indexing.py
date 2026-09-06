from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from .chunking import DeterministicChunker
from .embeddings import EmbeddingAdapter
from .models import (
    EmbeddedChunk,
    EvaluationReport,
    IndexManifest,
    IndexTarget,
    SearchResult,
    SourceDocument,
)
from .store import IndexStore


def corpus_digest(documents: list[SourceDocument]) -> str:
    records = [
        {"metadata": document.metadata.to_dict(), "text": document.text}
        for document in sorted(
            documents,
            key=lambda item: (item.metadata.source_id, item.metadata.version),
        )
    ]
    canonical = json.dumps(
        records, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class IndexPipeline:
    def __init__(
        self,
        *,
        store: IndexStore,
        chunker: DeterministicChunker,
        embedding: EmbeddingAdapter,
    ) -> None:
        self.store = store
        self.chunker = chunker
        self.embedding = embedding

    def build(
        self, documents: list[SourceDocument], target: IndexTarget
    ) -> IndexManifest:
        self._validate_documents(documents, target)
        digest = corpus_digest(documents)
        physical = target.physical_collection
        existing = self.store.get_manifest(physical)
        active = self.store.active_collection(target.collection)

        if active == physical:
            if existing is not None and self._same_build(existing, digest):
                return existing
            raise ValueError(
                "active index versions are immutable; build a new index_version"
            )

        chunks = self.chunker.chunk_documents(documents)
        vectors = self.embedding.embed_texts([chunk.text for chunk in chunks])
        if len(vectors) != len(chunks):
            raise ValueError("embedding adapter returned an unexpected batch size")
        if any(len(vector) != self.embedding.dimensions for vector in vectors):
            raise ValueError("embedding adapter returned an unexpected dimension")
        embedded = [
            EmbeddedChunk(chunk=chunk, vector=vector)
            for chunk, vector in zip(chunks, vectors)
        ]

        self.store.clear_collection(physical)
        self.store.upsert(physical, embedded)
        manifest = IndexManifest(
            collection=target.collection,
            physical_collection=physical,
            index_version=target.index_version,
            corpus_digest=digest,
            document_kind=documents[0].metadata.document_kind,
            document_count=len(documents),
            chunk_count=len(chunks),
            chunker_id=self.chunker.chunker_id,
            embedding_model=self.embedding.model_id,
            embedding_dimensions=self.embedding.dimensions,
            source_versions=tuple(
                sorted(
                    (document.metadata.source_id, document.metadata.version)
                    for document in documents
                )
            ),
            built_at=datetime.now(timezone.utc).isoformat(),
        )
        self.store.write_manifest(physical, manifest)
        return manifest

    def promote(
        self, manifest: IndexManifest, evaluation: EvaluationReport
    ) -> None:
        stored_manifest = self.store.get_manifest(manifest.physical_collection)
        if stored_manifest != manifest:
            raise ValueError("stored manifest differs from the evaluated manifest")
        if evaluation.physical_collection != manifest.physical_collection:
            raise ValueError("evaluation belongs to a different physical collection")
        if evaluation.corpus_digest != manifest.corpus_digest:
            raise ValueError("evaluation belongs to a different corpus")
        if not evaluation.passed:
            raise ValueError("evaluation thresholds did not pass")
        if len(self.store.list_chunks(manifest.physical_collection)) != manifest.chunk_count:
            raise ValueError("stored chunk count differs from manifest")
        self.store.activate(manifest.collection, manifest.physical_collection)

    def _same_build(self, manifest: IndexManifest, digest: str) -> bool:
        return (
            manifest.corpus_digest == digest
            and manifest.chunker_id == self.chunker.chunker_id
            and manifest.embedding_model == self.embedding.model_id
            and manifest.embedding_dimensions == self.embedding.dimensions
        )

    @staticmethod
    def _validate_documents(
        documents: list[SourceDocument], target: IndexTarget
    ) -> None:
        if not documents:
            raise ValueError("cannot build an empty corpus")
        source_ids = [document.metadata.source_id for document in documents]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("a corpus may contain only one version of each source_id")
        kinds = {document.metadata.document_kind for document in documents}
        if len(kinds) != 1:
            raise ValueError("document kinds must use separate collections")

        if target.operational:
            invalid = [
                document.metadata.source_id
                for document in documents
                if document.metadata.review_status != "approved"
            ]
            if invalid:
                raise ValueError(
                    f"operational target contains unapproved sources: {sorted(invalid)}"
                )
            return

        if not target.collection.startswith("synthetic_"):
            raise ValueError("non-operational targets must use a synthetic_ collection")
        invalid = [
            document.metadata.source_id
            for document in documents
            if document.metadata.review_status != "synthetic"
        ]
        if invalid:
            raise ValueError(
                f"synthetic target contains non-synthetic sources: {sorted(invalid)}"
            )


class IndexedRetriever:
    """F06-facing retrieval boundary with versioned provenance in every result."""

    def __init__(self, *, store: IndexStore, embedding: EmbeddingAdapter) -> None:
        self.store = store
        self.embedding = embedding

    def retrieve(
        self, logical_collection: str, query: str, *, top_k: int = 5
    ) -> list[SearchResult]:
        physical = self.store.active_collection(logical_collection)
        if physical is None:
            raise RuntimeError(f"collection has no active index: {logical_collection}")
        return self.retrieve_physical(physical, query, top_k=top_k)

    def retrieve_physical(
        self, physical_collection: str, query: str, *, top_k: int = 5
    ) -> list[SearchResult]:
        vectors = self.embedding.embed_texts([query])
        if len(vectors) != 1 or len(vectors[0]) != self.embedding.dimensions:
            raise ValueError("embedding adapter returned an invalid query vector")
        return self.store.search(physical_collection, vectors[0], top_k)
