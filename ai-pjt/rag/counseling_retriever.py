"""Topic-only, in-memory sparse retrieval of pending public counseling prose.

This intentionally does not use an approved index, database, embedding provider,
or customer query. Scores are raw BM25 lexical scores, not confidence values or
the cosine scores in the older hash-embedding candidate evaluation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path
import re
from types import MappingProxyType

from guardrails.boundary_policy import evaluate_rag_content
from rag.counseling_corpus import DEFAULT_MANIFEST, REPOSITORY_ROOT, load_candidate
from rag.pipeline.chunking import MarkdownStructureChunker
from rag.pipeline.indexing import IndexedRetriever
from rag.pipeline.models import EmbeddedChunk, SourceDocument


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
MIN_BM25_SCORE = 1.0
MIN_QUERY_TERM_MATCHES = 2
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
    """Keep complete counseling cards, excluding administrative provenance.

The original bytes have already passed the manifest hash check. Projected chunks
retain original source metadata internally and get their own chunk content hash.
Inline '재사용:' sentences name internal references, not customer-facing advice.
Unknown metadata is rejected instead of accidentally exposing it in a prompt.
"""
    headings = list(_H2_SECTION.finditer(document.text))
    projected = []
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
        # Reject embedded instructions before retrieval; public source status
        # does not turn source prose into trusted model/tool instructions.
        if not evaluate_rag_content(text).allowed:
            continue
        projected.append(SourceDocument(metadata=document.metadata, text=text))
    return projected


class CounselingRetriever:
    """An immutable corpus snapshot; no request or session data is cached."""

    def __init__(
        self, *, manifest_path: Path = DEFAULT_MANIFEST,
        repository_root: Path = REPOSITORY_ROOT,
    ) -> None:
        corpus = load_candidate(manifest_path, repository_root=repository_root)
        chunker = MarkdownStructureChunker(900, 100)
        chunks = []
        for document in corpus.documents:
            for section in _project_sections(document):
                section_chunks = chunker.chunk_document(section)
                # The application conditions, example and limits must travel
                # together. A future oversized card needs explicit revision.
                if len(section_chunks) != 1 or section_chunks[0].text != section.text:
                    raise ValueError("counseling card no longer fits one complete chunk")
                chunks.append(EmbeddedChunk(chunk=section_chunks[0], vector=()))
        self._chunks = tuple(chunks)
        self.candidate_version = corpus.version
        self.manifest_sha256 = corpus.manifest_sha256

    def retrieve(self, topic: CounselingTopic) -> list[CounselingEvidence]:
        if type(topic) is not CounselingTopic:
            raise TypeError("counseling retrieval requires a CounselingTopic enum")
        if topic is CounselingTopic.NONE:
            return []
        query = TOPIC_QUERIES[topic]
        query_terms = set(IndexedRetriever._tokens(query))
        ranked = IndexedRetriever._bm25(list(self._chunks), query, len(self._chunks))
        evidence = []
        for embedded, score in ranked:
            if score < MIN_BM25_SCORE:
                continue
            matched = query_terms.intersection(IndexedRetriever._tokens(embedded.chunk.text))
            if len(matched) < MIN_QUERY_TERM_MATCHES:
                continue
            # The heading is useful for ranking, but only the full counseling
            # body crosses the model boundary. Source IDs remain separate.
            _, _, body = embedded.chunk.text.partition("\n\n")
            evidence.append(CounselingEvidence(
                evidence_id=f"E{len(evidence) + 1}", text=body,
                source_id=embedded.chunk.metadata.source_id, score=float(score),
            ))
            if len(evidence) == MAX_EVIDENCE:
                break
        return evidence


@lru_cache(maxsize=1)
def get_counseling_retriever() -> CounselingRetriever:
    """Lazily load only the checked public corpus; failed loads are not cached."""
    return CounselingRetriever()
