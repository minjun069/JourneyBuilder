from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
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


OPERATIONAL_COLLECTIONS = {
    "financial-knowledge": "financial-knowledge",
    "interface-guides": "interface-guide",
}
_TOKEN_PATTERN = re.compile(r"[0-9A-Za-z가-힣_]+")


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
        approved_sources: dict[str, tuple[str, str]] | None = None,
    ) -> None:
        self.store = store
        self.chunker = chunker
        self.embedding = embedding
        self.approved_sources = (
            dict(approved_sources) if approved_sources is not None else None
        )

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
            artifact_kind=target.artifact_kind,
        )
        self.store.write_manifest(physical, manifest)
        return manifest

    def promote(
        self, manifest: IndexManifest, evaluation: EvaluationReport
    ) -> None:
        self.promote_many([(manifest, evaluation)])

    def promote_many(
        self,
        candidates: list[tuple[IndexManifest, EvaluationReport]],
    ) -> None:
        if not candidates:
            raise ValueError("at least one evaluated index is required")
        activations: dict[str, str] = {}
        for manifest, evaluation in candidates:
            self._validate_promotion(manifest, evaluation)
            if manifest.collection in activations:
                raise ValueError("each logical collection may be promoted only once")
            activations[manifest.collection] = manifest.physical_collection
        self.store.activate_many(activations)

    def _validate_promotion(
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

    def _same_build(self, manifest: IndexManifest, digest: str) -> bool:
        return (
            manifest.corpus_digest == digest
            and manifest.chunker_id == self.chunker.chunker_id
            and manifest.embedding_model == self.embedding.model_id
            and manifest.embedding_dimensions == self.embedding.dimensions
        )

    def _validate_documents(
        self, documents: list[SourceDocument], target: IndexTarget
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
            if self.approved_sources is None:
                raise ValueError("operational indexing requires an approved source allowlist")
            expected_kind = OPERATIONAL_COLLECTIONS.get(target.collection)
            if expected_kind is None:
                raise ValueError(
                    f"unsupported operational collection: {target.collection}"
                )
            invalid_versions = [
                f"{document.metadata.source_id}@{document.metadata.version}"
                for document in documents
                if self.approved_sources.get(document.metadata.source_id)
                != (document.metadata.version, document.metadata.sha256)
            ]
            if invalid_versions:
                raise ValueError(
                    "operational target contains an unapproved source id/version/hash: "
                    f"{sorted(invalid_versions)}"
                )
            invalid = [
                document.metadata.source_id
                for document in documents
                if (
                    document.metadata.review_status != "approved"
                    or document.metadata.collection != target.collection
                    or document.metadata.document_kind != expected_kind
                    or document.metadata.sensitivity != "public"
                    or len(document.metadata.sha256) != 64
                )
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
    """F06 retrieval boundary using dense plus local BM25 reciprocal-rank fusion."""

    def __init__(
        self,
        *,
        store: IndexStore,
        embedding: EmbeddingAdapter,
        hybrid: bool = True,
        fetch_k: int = 10,
        minimum_dense_score: float = 0.30,
        rrf_constant: int = 60,
    ) -> None:
        if fetch_k < 1:
            raise ValueError("fetch_k must be positive")
        if not 0.0 <= minimum_dense_score <= 1.0:
            raise ValueError("minimum_dense_score must be between 0 and 1")
        if rrf_constant < 1:
            raise ValueError("rrf_constant must be positive")
        self.store = store
        self.embedding = embedding
        self.hybrid = hybrid
        self.fetch_k = fetch_k
        self.minimum_dense_score = minimum_dense_score
        self.rrf_constant = rrf_constant

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
        fetch_k = max(top_k, self.fetch_k)
        dense = [
            result
            for result in self.store.search(physical_collection, vectors[0], fetch_k)
            if result.score >= self.minimum_dense_score
        ]
        if not self.hybrid:
            return [
                SearchResult(
                    chunk=result.chunk,
                    score=result.score,
                    rank=rank,
                    dense_score=result.score,
                )
                for rank, result in enumerate(dense[:top_k], start=1)
            ]

        sparse = self._bm25(self.store.list_chunks(physical_collection), query, fetch_k)
        fused: dict[str, dict[str, object]] = {}
        for rank, result in enumerate(dense, start=1):
            fused[result.chunk.chunk_id] = {
                "chunk": result.chunk,
                "fusion": 1.0 / (self.rrf_constant + rank),
                "dense": result.score,
                "sparse": None,
            }
        for rank, (embedded, score) in enumerate(sparse, start=1):
            entry = fused.setdefault(
                embedded.chunk.chunk_id,
                {
                    "chunk": embedded.chunk,
                    "fusion": 0.0,
                    "dense": None,
                    "sparse": None,
                },
            )
            entry["fusion"] = float(entry["fusion"]) + 1.0 / (
                self.rrf_constant + rank
            )
            entry["sparse"] = score

        ordered = sorted(
            fused.values(),
            key=lambda item: (
                -float(item["fusion"]),
                -float(item["dense"] or 0.0),
                item["chunk"].chunk_id,
            ),
        )
        return [
            SearchResult(
                chunk=item["chunk"],
                score=float(item["fusion"]),
                rank=rank,
                dense_score=(
                    float(item["dense"]) if item["dense"] is not None else None
                ),
                sparse_score=(
                    float(item["sparse"]) if item["sparse"] is not None else None
                ),
            )
            for rank, item in enumerate(ordered[:top_k], start=1)
        ]

    @staticmethod
    def _tokens(text: str) -> list[str]:
        return _TOKEN_PATTERN.findall(text.lower())

    @classmethod
    def _bm25(
        cls, chunks: list, query: str, top_k: int
    ) -> list[tuple[object, float]]:
        query_terms = Counter(cls._tokens(query))
        if not query_terms or not chunks:
            return []
        documents = [Counter(cls._tokens(item.chunk.text)) for item in chunks]
        lengths = [sum(document.values()) for document in documents]
        average_length = sum(lengths) / len(lengths) if lengths else 0.0
        document_frequency = {
            term: sum(term in document for document in documents)
            for term in query_terms
        }
        k1 = 1.5
        b = 0.75
        scored: list[tuple[float, object]] = []
        for embedded, frequencies, length in zip(chunks, documents, lengths):
            score = 0.0
            for term, query_frequency in query_terms.items():
                frequency = frequencies.get(term, 0)
                if frequency == 0:
                    continue
                frequency_in_documents = document_frequency[term]
                inverse_frequency = math.log(
                    1.0
                    + (len(documents) - frequency_in_documents + 0.5)
                    / (frequency_in_documents + 0.5)
                )
                normalization = frequency + k1 * (
                    1.0 - b + b * length / average_length
                )
                score += (
                    inverse_frequency
                    * frequency
                    * (k1 + 1.0)
                    / normalization
                    * query_frequency
                )
            if score > 0.0:
                scored.append((score, embedded))
        scored.sort(key=lambda item: (-item[0], item[1].chunk.chunk_id))
        return [(embedded, score) for score, embedded in scored[:top_k]]
