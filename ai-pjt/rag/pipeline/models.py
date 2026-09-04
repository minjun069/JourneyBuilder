from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


DOCUMENT_KINDS = frozenset({"financial_knowledge", "interface_guide"})
REVIEW_STATUSES = frozenset({"approved", "rejected", "pending", "synthetic"})


def _require_text(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


@dataclass(frozen=True)
class DocumentMetadata:
    source_id: str
    title: str
    issuer: str
    source_uri: str
    published_at: str
    as_of: str
    version: str
    scope: str
    review_status: str
    reviewed_by: str
    document_kind: str

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            _require_text(name, value)
        if self.document_kind not in DOCUMENT_KINDS:
            raise ValueError(
                f"document_kind must be one of {sorted(DOCUMENT_KINDS)}"
            )
        if self.review_status not in REVIEW_STATUSES:
            raise ValueError(
                f"review_status must be one of {sorted(REVIEW_STATUSES)}"
            )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> DocumentMetadata:
        expected = set(cls.__dataclass_fields__)
        missing = expected - payload.keys()
        extra = payload.keys() - expected
        if missing or extra:
            raise ValueError(
                "invalid metadata fields: "
                f"missing={sorted(missing)}, extra={sorted(extra)}"
            )
        return cls(**payload)

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class SourceDocument:
    metadata: DocumentMetadata
    text: str

    def __post_init__(self) -> None:
        _require_text("text", self.text)


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    text: str
    metadata: DocumentMetadata
    ordinal: int
    start_offset: int
    end_offset: int
    content_hash: str
    chunker_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "text": self.text,
            "metadata": self.metadata.to_dict(),
            "ordinal": self.ordinal,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "content_hash": self.content_hash,
            "chunker_id": self.chunker_id,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Chunk:
        values = dict(payload)
        values["metadata"] = DocumentMetadata.from_dict(values["metadata"])
        return cls(**values)


@dataclass(frozen=True)
class EmbeddedChunk:
    chunk: Chunk
    vector: tuple[float, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"chunk": self.chunk.to_dict(), "vector": list(self.vector)}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> EmbeddedChunk:
        return cls(
            chunk=Chunk.from_dict(payload["chunk"]),
            vector=tuple(float(value) for value in payload["vector"]),
        )


@dataclass(frozen=True)
class IndexTarget:
    collection: str
    index_version: str
    operational: bool = False

    def __post_init__(self) -> None:
        for name, value in (
            ("collection", self.collection),
            ("index_version", self.index_version),
        ):
            _require_text(name, value)
            if not all(char.isalnum() or char in "_-" for char in value):
                raise ValueError(f"{name} contains an unsafe character: {value!r}")

    @property
    def physical_collection(self) -> str:
        return f"{self.collection}__{self.index_version}"


@dataclass(frozen=True)
class IndexManifest:
    collection: str
    physical_collection: str
    index_version: str
    corpus_digest: str
    document_kind: str
    document_count: int
    chunk_count: int
    chunker_id: str
    embedding_model: str
    embedding_dimensions: int
    source_versions: tuple[tuple[str, str], ...]
    built_at: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["source_versions"] = [
            {"source_id": source_id, "version": version}
            for source_id, version in self.source_versions
        ]
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> IndexManifest:
        values = dict(payload)
        values["source_versions"] = tuple(
            (item["source_id"], item["version"])
            for item in values["source_versions"]
        )
        return cls(**values)


@dataclass(frozen=True)
class SearchResult:
    chunk: Chunk
    score: float
    rank: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk.chunk_id,
            "source_id": self.chunk.metadata.source_id,
            "document_version": self.chunk.metadata.version,
            "title": self.chunk.metadata.title,
            "start_offset": self.chunk.start_offset,
            "end_offset": self.chunk.end_offset,
            "score": self.score,
            "rank": self.rank,
            "text": self.chunk.text,
        }


@dataclass(frozen=True)
class GoldQuery:
    query_id: str
    query: str
    relevant_source_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_text("query_id", self.query_id)
        _require_text("query", self.query)
        if not self.relevant_source_ids:
            raise ValueError("relevant_source_ids must not be empty")
        for source_id in self.relevant_source_ids:
            _require_text("relevant_source_id", source_id)


@dataclass(frozen=True)
class QueryEvaluation:
    query_id: str
    relevant_source_ids: tuple[str, ...]
    recall_at_k: float
    reciprocal_rank_at_k: float
    context_precision_at_k: float
    retrieved_source_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EvaluationReport:
    physical_collection: str
    corpus_digest: str
    evaluation_set_id: str
    evaluation_set_digest: str
    top_k: int
    query_count: int
    recall_at_k: float
    mrr_at_k: float
    context_precision_at_k: float
    minimum_recall: float
    minimum_mrr: float
    passed: bool
    queries: tuple[QueryEvaluation, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["queries"] = [query.to_dict() for query in self.queries]
        return payload
