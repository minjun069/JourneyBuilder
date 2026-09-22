from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
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
COUNSELING_CANDIDATE_COLLECTION = "counseling-candidate"
_TOKEN_PATTERN = re.compile(r"[0-9A-Za-z가-힣_]+")
_MARKDOWN_HEADING = re.compile(r"(?m)^#{1,6}\s+(.+?)\s*$")
_PRODUCT_TERM_HEADING = re.compile(r"(?m)^##\s+(.+?)\s*$")
_PRODUCT_TERM_ALIASES = re.compile(r"(?m)^- \*\*별칭:\*\*\s*(.+?)\s*$")
_KOREAN_PARTICLES = tuple(sorted(
    {
        "에게서는", "으로서는", "으로부터", "에서는", "으로써", "이라고",
        "이라면", "로서는", "에게서", "한테서", "까지는", "부터는", "이라도",
        "이라고", "이란", "에는", "에도", "에서", "에게", "한테", "께서",
        "으로", "처럼", "보다", "만큼", "조차", "마저", "이랑", "하고",
        "이라", "라도", "이나", "든지", "부터", "까지", "은", "는", "이",
        "가", "을", "를", "의", "와", "과", "로", "에", "도", "만", "랑",
    },
    key=len,
    reverse=True,
))
_BM25_FIELD_WEIGHTS = {"title": 4.0, "aliases": 2.5, "body": 1.0}
_EXACT_TITLE_BONUS = 1_000.0
_EXACT_ALIAS_BONUS = 500.0
_ONE_CHARACTER_TERM_STEMS = frozenset({"연", "월", "주"})
_TERM_LOOKUP_CUES = ("뜻", "의미", "정의", "설명", "뭐", "무엇", "차이", "용어")


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
        pending_candidate_sources: dict[str, tuple[str, str]] | None = None,
    ) -> None:
        self.store = store
        self.chunker = chunker
        self.embedding = embedding
        self.approved_sources = (
            dict(approved_sources) if approved_sources is not None else None
        )
        self.pending_candidate_sources = (
            dict(pending_candidate_sources)
            if pending_candidate_sources is not None
            else None
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
        if any(chunk.metadata.collection != target.collection for chunk in chunks):
            raise ValueError("indexed chunk collection differs from target")
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
            if target.collection == COUNSELING_CANDIDATE_COLLECTION:
                self._validate_pending_candidate(documents, target)
                return
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

    def _validate_pending_candidate(
        self,
        documents: list[SourceDocument],
        target: IndexTarget,
    ) -> None:
        """Allow only the named, hash-pinned pending counseling experiment."""

        if target.artifact_kind != "experiment":
            raise ValueError("pending counseling candidates must remain experiments")
        if self.pending_candidate_sources is None:
            raise ValueError(
                "counseling candidate indexing requires a pending source allowlist"
            )
        invalid_versions = [
            f"{document.metadata.source_id}@{document.metadata.version}"
            for document in documents
            if self.pending_candidate_sources.get(document.metadata.source_id)
            != (document.metadata.version, document.metadata.sha256)
        ]
        if invalid_versions or len(documents) != len(self.pending_candidate_sources):
            raise ValueError(
                "counseling candidate contains an unregistered source id/version/hash: "
                f"{sorted(invalid_versions)}"
            )
        invalid = [
            document.metadata.source_id
            for document in documents
            if (
                document.metadata.review_status != "pending"
                or document.metadata.collection != "financial-knowledge"
                or document.metadata.document_kind != "financial-knowledge"
                or document.metadata.sensitivity != "public"
                or len(document.metadata.sha256) != 64
            )
        ]
        if invalid:
            raise ValueError(
                "counseling candidate contains a non-pending or unsafe source: "
                f"{sorted(invalid)}"
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
        sparse_rrf_weight: float = 1.0,
    ) -> None:
        if fetch_k < 1:
            raise ValueError("fetch_k must be positive")
        if not 0.0 <= minimum_dense_score <= 1.0:
            raise ValueError("minimum_dense_score must be between 0 and 1")
        if rrf_constant < 1:
            raise ValueError("rrf_constant must be positive")
        if sparse_rrf_weight <= 0:
            raise ValueError("sparse_rrf_weight must be positive")
        self.store = store
        self.embedding = embedding
        self.hybrid = hybrid
        self.fetch_k = fetch_k
        self.minimum_dense_score = minimum_dense_score
        self.rrf_constant = rrf_constant
        self.sparse_rrf_weight = sparse_rrf_weight

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
            entry["fusion"] = float(entry["fusion"]) + self.sparse_rrf_weight / (
                self.rrf_constant + rank
            )
            if score >= _EXACT_ALIAS_BONUS:
                entry["fusion"] = float(entry["fusion"]) + 1.0 / (
                    self.rrf_constant + 1
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
    def _tokens(text: str, *, strip_particles: bool = True) -> list[str]:
        tokens: list[str] = []
        for token in _TOKEN_PATTERN.findall(
            unicodedata.normalize("NFKC", text).casefold()
        ):
            normalized = (
                IndexedRetriever._normalize_token(token)
                if strip_particles
                else token
            )
            if normalized:
                tokens.append(normalized)
        return tokens

    @staticmethod
    def _normalize_token(token: str) -> str:
        normalized = unicodedata.normalize("NFKC", token).casefold()
        for particle in _KOREAN_PARTICLES:
            if not normalized.endswith(particle):
                continue
            stem = normalized[:-len(particle)]
            if len(stem) >= 2 or stem in _ONE_CHARACTER_TERM_STEMS:
                return stem
        return normalized

    @classmethod
    def _search_fields(cls, embedded: object) -> dict[str, str]:
        text = embedded.chunk.text
        product_title = _PRODUCT_TERM_HEADING.search(text)
        headings = _MARKDOWN_HEADING.findall(text)
        title = (
            product_title.group(1).strip()
            if product_title is not None
            else " ".join(heading.strip() for heading in headings)
        )
        if not title:
            title = embedded.chunk.metadata.title

        aliases_match = _PRODUCT_TERM_ALIASES.search(text)
        aliases = ""
        if aliases_match is not None and aliases_match.group(1).strip() != "없음":
            aliases = aliases_match.group(1).strip()

        body = _MARKDOWN_HEADING.sub(" ", text)
        body = _PRODUCT_TERM_ALIASES.sub(" ", body)
        return {"title": title, "aliases": aliases, "body": body}

    @classmethod
    def _contains_surface(cls, query_tokens: list[str], surface: str) -> bool:
        surface_tokens = cls._tokens(surface)
        if not surface_tokens or len(surface_tokens) > len(query_tokens):
            return False
        width = len(surface_tokens)
        return any(
            query_tokens[index:index + width] == surface_tokens
            for index in range(len(query_tokens) - width + 1)
        )

    @classmethod
    def _bm25(
        cls, chunks: list, query: str, top_k: int
    ) -> list[tuple[object, float]]:
        raw_query_tokens = cls._tokens(query, strip_particles=False)
        is_term_lookup = (
            len(raw_query_tokens) <= 3
            or any(
                cue in token
                for token in raw_query_tokens
                for cue in _TERM_LOOKUP_CUES
            )
        )
        query_tokens = cls._tokens(query, strip_particles=is_term_lookup)
        query_terms = Counter(query_tokens)
        if not query_terms or not chunks:
            return []
        fields = [cls._search_fields(item) for item in chunks]
        documents = [
            {
                field: Counter(cls._tokens(value))
                for field, value in document_fields.items()
            }
            for document_fields in fields
        ]
        lengths = {
            field: [sum(document[field].values()) for document in documents]
            for field in _BM25_FIELD_WEIGHTS
        }
        average_lengths = {
            field: (sum(values) / len(values) if values else 0.0)
            for field, values in lengths.items()
        }
        document_frequency = {
            term: sum(
                any(term in document[field] for field in _BM25_FIELD_WEIGHTS)
                for document in documents
            )
            for term in query_terms
        }
        k1 = 1.5
        b = 0.75
        scored: list[tuple[float, object]] = []
        for index, (embedded, frequencies) in enumerate(zip(chunks, documents)):
            score = 0.0
            for term, query_frequency in query_terms.items():
                frequency_in_documents = document_frequency[term]
                inverse_frequency = math.log(
                    1.0
                    + (len(documents) - frequency_in_documents + 0.5)
                    / (frequency_in_documents + 0.5)
                )
                for field, weight in _BM25_FIELD_WEIGHTS.items():
                    frequency = frequencies[field].get(term, 0)
                    average_length = average_lengths[field]
                    if frequency == 0 or average_length == 0.0:
                        continue
                    normalization = frequency + k1 * (
                        1.0 - b + b * lengths[field][index] / average_length
                    )
                    score += (
                        weight
                        * inverse_frequency
                        * frequency
                        * (k1 + 1.0)
                        / normalization
                        * query_frequency
                    )

            if is_term_lookup and cls._contains_surface(
                query_tokens,
                fields[index]["title"],
            ):
                score += _EXACT_TITLE_BONUS + 10.0 * len(
                    cls._tokens(fields[index]["title"])
                )
            alias_values = [
                alias.strip()
                for alias in fields[index]["aliases"].split(",")
                if alias.strip()
            ]
            if is_term_lookup and any(
                cls._contains_surface(query_tokens, alias)
                for alias in alias_values
            ):
                score += _EXACT_ALIAS_BONUS
            if not is_term_lookup and "- **용어 ID:**" in embedded.chunk.text:
                score *= 0.25
            if score > 0.0:
                scored.append((score, embedded))
        scored.sort(key=lambda item: (-item[0], item[1].chunk.chunk_id))
        return [(embedded, score) for score, embedded in scored[:top_k]]
