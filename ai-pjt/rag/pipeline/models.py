from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from typing import Any


DOCUMENT_KINDS = frozenset({"financial-knowledge", "interface-guide"})
REVIEW_STATUSES = frozenset({"approved", "rejected", "pending", "synthetic"})
ARTIFACT_KINDS = frozenset({"official", "experiment"})


def _require_text(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


@dataclass(frozen=True)
class DocumentMetadata:
    source_id: str
    title: str
    publisher: str
    source_locators: tuple[str, ...]
    published_at: str | None
    as_of_date: str
    version: str
    scope: str
    review_status: str
    reviewed_by: str
    document_kind: str
    collection: str
    license: str
    sensitivity: str
    sha256: str
    catalog_document_id: str
    file_path: str
    reviewed_at: str | None = None
    refresh_cadence_days: int | None = None
    retire_when: str = ""

    def __post_init__(self) -> None:
        required_text = {
            "source_id": self.source_id,
            "title": self.title,
            "publisher": self.publisher,
            "as_of_date": self.as_of_date,
            "version": self.version,
            "scope": self.scope,
            "review_status": self.review_status,
            "reviewed_by": self.reviewed_by,
            "document_kind": self.document_kind,
            "collection": self.collection,
            "license": self.license,
            "sensitivity": self.sensitivity,
        }
        for name, value in required_text.items():
            _require_text(name, value)
        if not self.source_locators:
            raise ValueError("source_locators must not be empty")
        for locator in self.source_locators:
            _require_text("source_locator", locator)
        if self.published_at is not None:
            _require_text("published_at", self.published_at)
        if self.reviewed_at is not None:
            _require_text("reviewed_at", self.reviewed_at)
        if self.refresh_cadence_days is not None and self.refresh_cadence_days < 1:
            raise ValueError("refresh_cadence_days must be positive")
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
        values = dict(payload)
        if "issuer" in values:
            values["publisher"] = values.pop("issuer")
        if "source_uri" in values:
            values["source_locators"] = (values.pop("source_uri"),)
        if "as_of" in values:
            values["as_of_date"] = values.pop("as_of")
        legacy_kind = values.get("document_kind")
        if legacy_kind in {"financial_knowledge", "interface_guide"}:
            values["document_kind"] = legacy_kind.replace("_", "-")
        if values.get("review_status") == "synthetic":
            kind = values.get("document_kind", "financial-knowledge")
            values.setdefault("collection", f"synthetic_{kind.replace('-', '_')}")
            values.setdefault("license", "synthetic only")
            values.setdefault("sensitivity", "public")
            values.setdefault("sha256", "")
            values.setdefault("catalog_document_id", "")
            values.setdefault("file_path", "")
        expected = set(cls.__dataclass_fields__)
        optional = {"reviewed_at", "refresh_cadence_days", "retire_when"}
        required = expected - optional
        missing = required - values.keys()
        extra = values.keys() - expected
        if missing or extra:
            raise ValueError(
                "invalid metadata fields: "
                f"missing={sorted(missing)}, extra={sorted(extra)}"
            )
        locators = values.get("source_locators")
        if isinstance(locators, list):
            values["source_locators"] = tuple(locators)
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["source_locators"] = list(self.source_locators)
        return payload


@dataclass(frozen=True)
class SourceDocument:
    metadata: DocumentMetadata
    text: str

    def __post_init__(self) -> None:
        _require_text("text", self.text)
        if self.metadata.review_status == "approved":
            actual = hashlib.sha256(self.text.encode("utf-8")).hexdigest()
            if actual != self.metadata.sha256:
                raise ValueError(
                    f"approved source content hash differs from metadata: "
                    f"{self.metadata.source_id}"
                )


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
    artifact_kind: str = "experiment"

    def __post_init__(self) -> None:
        for name, value in (
            ("collection", self.collection),
            ("index_version", self.index_version),
        ):
            _require_text(name, value)
            if not all(char.isalnum() or char in "_-" for char in value):
                raise ValueError(f"{name} contains an unsafe character: {value!r}")
        if self.artifact_kind not in ARTIFACT_KINDS:
            raise ValueError(
                f"artifact_kind must be one of {sorted(ARTIFACT_KINDS)}"
            )
        if not self.operational and self.artifact_kind != "experiment":
            raise ValueError("non-operational indexes must be experiments")

    @property
    def physical_collection(self) -> str:
        qualifier = f"{self.artifact_kind}-" if self.operational else ""
        return f"{self.collection}__{qualifier}{self.index_version}"


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
    artifact_kind: str = "legacy"

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
        values.setdefault("artifact_kind", "legacy")
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
    dense_score: float | None = None
    sparse_score: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk.chunk_id,
            "source_id": self.chunk.metadata.source_id,
            "catalog_document_id": self.chunk.metadata.catalog_document_id,
            "document_version": self.chunk.metadata.version,
            "title": self.chunk.metadata.title,
            "publisher": self.chunk.metadata.publisher,
            "source_locators": list(self.chunk.metadata.source_locators),
            "as_of_date": self.chunk.metadata.as_of_date,
            "collection": self.chunk.metadata.collection,
            "sha256": self.chunk.metadata.sha256,
            "start_offset": self.chunk.start_offset,
            "end_offset": self.chunk.end_offset,
            "score": self.score,
            "dense_score": self.dense_score,
            "sparse_score": self.sparse_score,
            "rank": self.rank,
            "text": self.chunk.text,
        }


@dataclass(frozen=True)
class GoldQuery:
    query_id: str
    query: str
    relevant_source_ids: tuple[str, ...]
    category: str = "positive"

    def __post_init__(self) -> None:
        _require_text("query_id", self.query_id)
        _require_text("query", self.query)
        if self.category not in {"positive", "boundary", "no_result"}:
            raise ValueError("category must be positive, boundary, or no_result")
        if self.category == "no_result" and self.relevant_source_ids:
            raise ValueError("no_result queries must not name relevant sources")
        if self.category != "no_result" and not self.relevant_source_ids:
            raise ValueError("non-negative queries must name relevant sources")
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
    category: str = "positive"
    no_result_passed: bool | None = None

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
    positive_query_count: int
    no_result_query_count: int
    recall_at_k: float
    mrr_at_k: float
    context_precision_at_k: float
    provenance_completeness: float
    collection_leakage_count: int
    false_evidence_count: int
    minimum_recall: float
    minimum_mrr: float
    minimum_provenance: float
    maximum_collection_leakage: int
    maximum_false_evidence: int
    passed: bool
    queries: tuple[QueryEvaluation, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["queries"] = [query.to_dict() for query in self.queries]
        return payload
