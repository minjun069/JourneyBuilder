from __future__ import annotations

from llama_index.core import Document, VectorStoreIndex
from llama_index.core.base.base_retriever import BaseRetriever
from llama_index.core.retrievers import QueryFusionRetriever
from llama_index.core.schema import NodeWithScore
from llama_index.retrievers.bm25 import BM25Retriever
from llama_index.vector_stores.postgres import PGVectorStore

# ⚠️ 주의: core.llm.embed_model은 LlamaIndex의 BaseEmbedding 구현체여야 합니다.
# 예: from llama_index.embeddings.openai import OpenAIEmbedding
from core.llm import embed_model
from core.config import settings



def _build_dense_retriever(k: int | None = None) -> BaseRetriever:
    """PGVector를 사용한 Dense Retriever 빌드"""
    # LlamaIndex의 PGVectorStore는 표준 postgresql:// URL 스키마를 잘 지원합니다.
    vector_store = PGVectorStore.from_params(
        connection_string=settings.db_url,
        table_name=settings.rag_collection,
        embed_dim=settings.embed_dim,
    )
    
    # 저장된 벡터 저장소를 기반으로 인덱스 로드
    index = VectorStoreIndex.from_vector_store(
        vector_store=vector_store, 
        embed_model=embed_model
    )
    
    return index.as_retriever(
        similarity_top_k=k or settings.rag_top_k
    )


def _build_hybrid_retriever(docs: list[Document]) -> BaseRetriever:
    """Dense(PGVector) + Sparse(BM25) 하이브리드 Retriever 빌드"""
    fetch_k = settings.rag_top_k * 2

    dense = _build_dense_retriever(k=fetch_k)
    
    # LlamaIndex의 BM25Retriever는 Document 객체 리스트를 받아 초기화합니다.
    sparse = BM25Retriever.from_defaults(
        documents=docs, 
        similarity_top_k=fetch_k
    )

    # LangChain의 EnsembleRetriever 대신 QueryFusionRetriever를 사용합니다.
    # mode="reciprocal_rerank" 옵션으로 RRF(Reciprocal Rank Fusion) 알고리즘을 적용하여 점수를 통합합니다.
    return QueryFusionRetriever(
        retrievers=[dense, sparse],
        similarity_top_k=settings.rag_top_k,
        num_queries=1,  # 쿼리 변형(Rewriting)을 하지 않고 원본 쿼리만 사용하도록 1로 설정
        mode="reciprocal_rerank",
        use_async=False,
    )


# ---------------------------------------------------------------------------
# 싱글턴 — 앱 시작 시 initialize_retriever()로 BM25 활성화
# ---------------------------------------------------------------------------

_retriever: BaseRetriever | None = None


def initialize_retriever(docs: list[Document]) -> None:
    """
    앱 시작 시 문서를 로드한 후 호출.
    호출 전에는 dense-only 폴백으로 동작.
    """
    global _retriever
    _retriever = _build_hybrid_retriever(docs)


# ---------------------------------------------------------------------------
# ⚠️  EXPERIMENTS ONLY — 인메모리 테스트용 (pgvector 불필요)
# 프로덕션에서 절대 사용 금지. experiments/test_qa.py 에서만 호출.
# ---------------------------------------------------------------------------

def initialize_test_retriever(docs: list[Document]) -> None:
    """
    ⚠️ EXPERIMENTS ONLY
    pgvector 없이 기본 인메모리 스토리지 + BM25 로 동작.
    """
    global _retriever
    fetch_k = settings.rag_top_k * 2

    # 1. 기본 InMemory 구조의 벡터 인덱스 생성
    index = VectorStoreIndex.from_documents(docs, embed_model=embed_model)
    
    # 2. Dense 검색기 (Index에서 추출)
    dense = index.as_retriever(similarity_top_k=fetch_k)
    
    # 3. Sparse 검색기 (BM25)
    # 💡 수정됨: documents=docs (LangChain 방식) 대신, 
    # 위에서 만든 index 객체를 그대로 주입하는 것이 LlamaIndex의 정석입니다.
    sparse = BM25Retriever.from_defaults(
        index=index, 
        similarity_top_k=fetch_k
    )

    # 4. 하이브리드 결합
    _retriever = QueryFusionRetriever(
        retrievers=[dense, sparse],
        similarity_top_k=settings.rag_top_k,
        num_queries=1,
        mode="reciprocal_rerank",
        use_async=False,
    )

def get_retriever() -> BaseRetriever:
    global _retriever
    if _retriever is None:
        _retriever = _build_dense_retriever()
    return _retriever


def format_docs(nodes: list[NodeWithScore]) -> str:
    """LlamaIndex의 Retriever는 NodeWithScore 객체 리스트를 반환합니다."""
    if not nodes:
        return "검색된 문서 없음"
    
    return "\n\n---\n\n".join(
        f"[출처: {node.node.metadata.get('source', '알 수 없음')}]\n{node.node.get_content()}"
        for node in nodes
    )