"""Topic-only PostgreSQL vector retrieval for pending counseling evidence.

Only fixed public topic queries reach Gemini. The active index remains isolated
under ``counseling-candidate`` and every startup revalidates its pending source
snapshot, embedding contract, and projected counseling cards.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from enum import Enum
from functools import lru_cache
from math import isfinite
from pathlib import Path
from types import MappingProxyType

from guardrails.boundary_policy import evaluate_rag_content
from rag.counseling_corpus import (
    DEFAULT_MANIFEST,
    REPOSITORY_ROOT,
    CandidateCorpus,
    load_candidate,
)
from rag.pipeline.embeddings import EmbeddingAdapter, LlamaIndexEmbeddingAdapter
from rag.pipeline.indexing import (
    COUNSELING_CANDIDATE_COLLECTION,
    IndexedRetriever,
    corpus_digest,
)
from rag.pipeline.models import Chunk, IndexManifest, SourceDocument
from rag.pipeline.store import IndexStore, PostgresIndexStore


class CounselingTopic(str, Enum):
    NONE = "none"
    CASHFLOW = "cashflow"
    DEBT = "debt"
    ASSETS = "assets"
    PENSION = "pension"
    RETIREMENT = "retirement"
    GOALS = "goals"
    WELLBEING = "wellbeing"
    COMPARISON = "comparison"
    UNDERSTANDING = "understanding"


# These are fixed public concepts, never interpolated with a request or profile.
TOPIC_QUERIES = MappingProxyType({
    CounselingTopic.CASHFLOW: "현금흐름 입금일 청구일 수입 소득 중단 비상금 생활비",
    CounselingTopic.DEBT: "대출 부채 상환 납부 부담 원금 만기 이자",
    CounselingTopic.ASSETS: "자산 유동성 현금화 집중 처분 사용 시점",
    CounselingTopic.PENSION: "연금 예상액 수령 개시 잔액 같은 재원 불확실성",
    CounselingTopic.RETIREMENT: "은퇴자금 부족 은퇴진단 보고서 가정 강점 결과",
    CounselingTopic.GOALS: "목표 우선순위 배우자 부모 지원 노후 목돈 기간",
    CounselingTopic.WELLBEING: "불안 걱정 통제감 자책 생활비 재정 충격",
    CounselingTopic.COMPARISON: "또래 비교 평균 통계 물가 금리 사회 경제 기준시점",
    CounselingTopic.UNDERSTANDING: "고객 이해 정정 사실 가설 추정 이전 해석 갱신",
})

MAX_EVIDENCE = 3
_H2_SECTION = re.compile(r"(?m)^## ([^\n]+)\n")
_ADMIN_HEADINGS = frozenset({
    "기존 금융지식 재사용", "출처와 이용 범위", "갱신·폐기 기준", "갱신·폐기 조건",
})
_REUSE_SENTENCE = re.compile(r"재사용:\s*[^\n]*?(?:\.(?=\s|$)|$)")
_INTERNAL_CONTENT = re.compile(
    r"https?://|www\.|`|(?:retirewise|cfpb|nps|moel|hf)-[a-z0-9-]+"
    r"|\b(?:source_id|review_status|approved_at|catalog_version)\b"
    r"|\b[\w.-]+\.(?:md|json|py)\b|(?:/home/|/tmp/|[A-Z]:\\)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CounselingEvidence:
    evidence_id: str
    text: str
    source_id: str
    score: float


def _project_sections(document: SourceDocument) -> list[SourceDocument]:
    """Keep complete counseling cards while removing administrative provenance."""

    headings = list(_H2_SECTION.finditer(document.text))
    projected: list[SourceDocument] = []
    for index, match in enumerate(headings):
        heading = match.group(1).strip()
        if heading in _ADMIN_HEADINGS:
            continue
        end = headings[index + 1].start() if index + 1 < len(headings) else len(document.text)
        body = document.text[match.end():end].strip()
        if not body.startswith("적용 상황:"):
            raise ValueError("unexpected counseling section structure")
        body = _REUSE_SENTENCE.sub("", body).strip()
        text = f"## {heading}\n\n{body}"
        if _INTERNAL_CONTENT.search(text):
            raise ValueError("counseling projection contains internal provenance")
        if not evaluate_rag_content(text).allowed:
            continue
        projected.append(SourceDocument(metadata=document.metadata, text=text))
    return projected


class CounselingCardChunker:
    """Project each reviewed card as one deterministic candidate-index chunk."""

    @property
    def chunker_id(self) -> str:
        return "counseling-card-v1"

    def chunk_documents(self, documents: list[SourceDocument]) -> list[Chunk]:
        chunks: list[Chunk] = []
        for document in sorted(
            documents,
            key=lambda item: (item.metadata.source_id, item.metadata.version),
        ):
            chunks.extend(self.chunk_document(document))
        return chunks

    def chunk_document(self, document: SourceDocument) -> list[Chunk]:
        indexed = SourceDocument(
            metadata=replace(
                document.metadata,
                collection=COUNSELING_CANDIDATE_COLLECTION,
            ),
            text=document.text,
        )
        chunks: list[Chunk] = []
        for ordinal, section in enumerate(_project_sections(indexed)):
            content_hash = hashlib.sha256(section.text.encode("utf-8")).hexdigest()
            identity = "\x1f".join((
                section.metadata.source_id,
                section.metadata.version,
                self.chunker_id,
                str(ordinal),
                "0",
                str(len(section.text)),
                content_hash,
            ))
            chunks.append(Chunk(
                chunk_id=hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32],
                text=section.text,
                metadata=section.metadata,
                ordinal=ordinal,
                start_offset=0,
                end_offset=len(section.text),
                content_hash=content_hash,
                chunker_id=self.chunker_id,
            ))
        return chunks


def _validate_active_index(
    *,
    store: IndexStore,
    embedding_model: str,
    embedding_dimensions: int,
    manifest_path: Path,
    repository_root: Path,
) -> tuple[IndexManifest, CandidateCorpus]:
    corpus = load_candidate(manifest_path, repository_root=repository_root)
    physical = store.active_collection(COUNSELING_CANDIDATE_COLLECTION)
    if physical is None:
        raise RuntimeError("counseling candidate has no active index")
    manifest = store.get_manifest(physical)
    if manifest is None or manifest.physical_collection != physical:
        raise RuntimeError("active counseling candidate manifest is missing")
    expected_sources = tuple(sorted(
        (document.metadata.source_id, document.metadata.version)
        for document in corpus.documents
    ))
    expected_chunks = CounselingCardChunker().chunk_documents(list(corpus.documents))
    expected_by_id = {chunk.chunk_id: chunk for chunk in expected_chunks}
    if (
        manifest.collection != COUNSELING_CANDIDATE_COLLECTION
        or manifest.artifact_kind != "experiment"
        or manifest.corpus_digest != corpus_digest(list(corpus.documents))
        or manifest.document_count != len(corpus.documents)
        or manifest.chunk_count != len(expected_chunks)
        or manifest.chunker_id != CounselingCardChunker().chunker_id
        or manifest.embedding_model != embedding_model
        or manifest.embedding_dimensions != embedding_dimensions
        or manifest.source_versions != expected_sources
    ):
        raise RuntimeError("active counseling candidate manifest is incompatible")

    allowed = dict(expected_sources)
    chunks = store.list_chunks(physical)
    if len(chunks) != manifest.chunk_count:
        raise RuntimeError("active counseling candidate chunk count is inconsistent")
    for embedded in chunks:
        metadata = embedded.chunk.metadata
        expected = expected_by_id.get(embedded.chunk.chunk_id)
        if (
            expected is None
            or embedded.chunk != expected
            or metadata.collection != COUNSELING_CANDIDATE_COLLECTION
            or metadata.review_status != "pending"
            or metadata.sensitivity != "public"
            or allowed.get(metadata.source_id) != metadata.version
            or embedded.chunk.chunker_id != manifest.chunker_id
            or len(embedded.vector) != embedding_dimensions
            or _INTERNAL_CONTENT.search(embedded.chunk.text)
            or not evaluate_rag_content(embedded.chunk.text).allowed
        ):
            raise RuntimeError("active counseling candidate contains an unsafe chunk")
    return manifest, corpus


class CounselingRetriever:
    """Read the validated active candidate index using Gemini dense vectors."""

    def __init__(
        self,
        *,
        store: IndexStore | None = None,
        embedding: EmbeddingAdapter | None = None,
        manifest_path: Path = DEFAULT_MANIFEST,
        repository_root: Path = REPOSITORY_ROOT,
        fetch_k: int | None = None,
        minimum_dense_score: float | None = None,
    ) -> None:
        if (store is None) != (embedding is None):
            raise ValueError("store and embedding must be provided together")
        if store is None or embedding is None:
            from core.config import settings
            from core.llm import embed_model

            store = PostgresIndexStore(settings.db_url)
            embedding = LlamaIndexEmbeddingAdapter(
                embed_model,
                model_id=settings.embedding_model,
                dimensions=settings.embed_dim,
            )
            fetch_k = settings.rag_fetch_k if fetch_k is None else fetch_k
            minimum_dense_score = (
                settings.rag_counseling_min_dense_score
                if minimum_dense_score is None
                else minimum_dense_score
            )
        self._store = store
        self._embedding = embedding
        self._manifest, corpus = _validate_active_index(
            store=store,
            embedding_model=embedding.model_id,
            embedding_dimensions=embedding.dimensions,
            manifest_path=manifest_path,
            repository_root=repository_root,
        )
        self._allowed_sources = frozenset(
            document.metadata.source_id for document in corpus.documents
        )
        self.candidate_version = corpus.version
        self.manifest_sha256 = corpus.manifest_sha256
        self._retriever = IndexedRetriever(
            store=store,
            embedding=embedding,
            hybrid=False,
            fetch_k=fetch_k or 10,
            minimum_dense_score=(
                0.54 if minimum_dense_score is None else minimum_dense_score
            ),
        )

    @property
    def physical_collection(self) -> str:
        return self._manifest.physical_collection

    def retrieve(self, topic: CounselingTopic) -> list[CounselingEvidence]:
        if type(topic) is not CounselingTopic:
            raise TypeError("counseling retrieval requires a CounselingTopic enum")
        if topic is CounselingTopic.NONE:
            return []
        results = self._retriever.retrieve(
            COUNSELING_CANDIDATE_COLLECTION,
            TOPIC_QUERIES[topic],
            top_k=MAX_EVIDENCE,
        )
        evidence: list[CounselingEvidence] = []
        for result in results:
            metadata = result.chunk.metadata
            _, separator, body = result.chunk.text.partition("\n\n")
            if (
                metadata.source_id not in self._allowed_sources
                or metadata.collection != COUNSELING_CANDIDATE_COLLECTION
                or metadata.review_status != "pending"
                or not separator
                or not body.startswith("적용 상황:")
                or _INTERNAL_CONTENT.search(body)
                or not evaluate_rag_content(body).allowed
                or not isfinite(result.score)
            ):
                raise RuntimeError("invalid counseling candidate search result")
            evidence.append(CounselingEvidence(
                evidence_id=f"E{len(evidence) + 1}",
                text=body,
                source_id=metadata.source_id,
                score=float(result.score),
            ))
        return evidence


@lru_cache(maxsize=1)
def get_counseling_retriever() -> CounselingRetriever:
    """Create one validated runtime adapter; failed startup checks are not cached."""

    return CounselingRetriever()
