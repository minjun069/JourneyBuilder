from __future__ import annotations

import re
from pathlib import Path

from llama_index.core import Document, VectorStoreIndex
from llama_index.core.base.base_retriever import BaseRetriever
from llama_index.core.node_parser import MarkdownNodeParser
from llama_index.core.retrievers import QueryFusionRetriever
from llama_index.core.schema import NodeWithScore
from llama_index.retrievers.bm25 import BM25Retriever
from llama_index.vector_stores.postgres import PGVectorStore

from core.config import settings
from core.llm import embed_model

_markdown_retriever: BaseRetriever | None = None

_MD_TABLE = "markdown_nodes"


def _parse_nodes(docs: list[Document]):
    return MarkdownNodeParser().get_nodes_from_documents(docs)


def _build_dense_retriever(k: int) -> BaseRetriever:
    vector_store = PGVectorStore.from_params(
        connection_string=settings.db_url,
        table_name=_MD_TABLE,
        embed_dim=settings.embed_dim,
    )
    index = VectorStoreIndex.from_vector_store(
        vector_store=vector_store,
        embed_model=embed_model,
    )
    return index.as_retriever(similarity_top_k=k)


def _build_hybrid_retriever(nodes) -> BaseRetriever:
    fetch_k = settings.rag_top_k * 2
    dense = _build_dense_retriever(k=fetch_k)
    sparse = BM25Retriever.from_defaults(nodes=nodes, similarity_top_k=fetch_k)
    return QueryFusionRetriever(
        retrievers=[dense, sparse],
        similarity_top_k=settings.rag_top_k,
        num_queries=1,
        mode="reciprocal_rerank",
        use_async=False,
    )


def initialize_markdown_retriever(docs: list[Document]) -> None:
    """앱 시작 시 rag/data/*.md 파일들을 로드한 후 호출."""
    global _markdown_retriever
    _markdown_retriever = _build_hybrid_retriever(_parse_nodes(docs))


# ---------------------------------------------------------------------------
# ⚠️  EXPERIMENTS ONLY — 인메모리 테스트용 (pgvector 불필요)
# ---------------------------------------------------------------------------

def initialize_test_markdown_retriever(docs: list[Document]) -> None:
    """⚠️ EXPERIMENTS ONLY — experiments/test_input_assist.py 에서만 호출."""
    global _markdown_retriever
    fetch_k = settings.rag_top_k * 2

    nodes = _parse_nodes(docs)
    index = VectorStoreIndex(nodes, embed_model=embed_model)
    dense = index.as_retriever(similarity_top_k=fetch_k)
    sparse = BM25Retriever.from_defaults(nodes=nodes, similarity_top_k=fetch_k)

    _markdown_retriever = QueryFusionRetriever(
        retrievers=[dense, sparse],
        similarity_top_k=settings.rag_top_k,
        num_queries=1,
        mode="reciprocal_rerank",
        use_async=False,
    )


def get_markdown_retriever() -> BaseRetriever:
    global _markdown_retriever
    if _markdown_retriever is None:
        raise RuntimeError(
            "markdown_retriever가 초기화되지 않았습니다. "
            "initialize_markdown_retriever() 또는 initialize_test_markdown_retriever()를 먼저 호출하세요."
        )
    return _markdown_retriever


def format_docs(nodes: list[NodeWithScore]) -> str:
    if not nodes:
        return "검색된 문서 없음"

    parts = []
    for node in nodes:
        meta = node.node.metadata
        header_path = " > ".join(filter(None, [
            meta.get("Header 1"),
            meta.get("Header 2"),
        ]))
        source = header_path or meta.get("source", "알 수 없음")
        parts.append(f"[{source}]\n{node.node.get_content()}")

    return "\n\n---\n\n".join(parts)


# ---------------------------------------------------------------------------
# 파일 직접 파싱 — 정확한 섹션 추출 (유사도 검색 없음)
# ---------------------------------------------------------------------------

_DATA_DIR = Path(__file__).parent / "data"


def get_step_guideline(step_id: str) -> str:
    """# 섹션(인트로 + 필드 테이블)만 반환. ## 섹션은 제외."""
    text = (_DATA_DIR / f"{step_id}.md").read_text(encoding="utf-8")
    idx = text.find("\n## ")
    return text[:idx].strip() if idx != -1 else text.strip()


def get_field_spec(step_id: str, field_name: str) -> str:
    """field_name에 해당하는 ## 섹션만 반환."""
    text = (_DATA_DIR / f"{step_id}.md").read_text(encoding="utf-8")
    lines = text.splitlines()

    start = None
    for i, line in enumerate(lines):
        if not line.startswith("## "):
            continue
        m = re.match(r"^## (.+?):", line)
        if not m:
            continue
        # "## fromAge / toAge: ..." 같은 복합 헤더도 처리
        parts = [p.strip() for p in m.group(1).split("/")]
        if field_name in parts:
            start = i
            break

    if start is None:
        return f"{field_name} 명세서를 찾을 수 없습니다."

    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("## "):
            end = i
            break

    return "\n".join(lines[start:end]).strip()
