from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from core.config import settings
from rag.pipeline.embeddings import LlamaIndexEmbeddingAdapter
from rag.pipeline.indexing import IndexedRetriever
from rag.pipeline.models import SearchResult
from rag.pipeline.store import PostgresIndexStore


class OperationalRetriever:
    """Read-only runtime adapter for an already promoted logical collection."""

    def __init__(self, collection: str) -> None:
        from core.llm import embed_model

        store = PostgresIndexStore(settings.db_url)
        embedding = LlamaIndexEmbeddingAdapter(
            embed_model,
            model_id=settings.embedding_model,
            dimensions=settings.embed_dim,
        )
        self.collection = collection
        self._retriever = IndexedRetriever(
            store=store,
            embedding=embedding,
            hybrid=True,
            fetch_k=settings.rag_fetch_k,
            minimum_dense_score=settings.rag_min_dense_score,
        )

    def retrieve(self, query: str, *, top_k: int | None = None) -> list[SearchResult]:
        try:
            return self._retriever.retrieve(
                self.collection,
                query,
                top_k=top_k or settings.rag_top_k,
            )
        except Exception as error:
            raise RuntimeError("approved knowledge index is unavailable") from error

    async def aretrieve(
        self, query: str, *, top_k: int | None = None
    ) -> list[SearchResult]:
        return await asyncio.to_thread(self.retrieve, query, top_k=top_k)


_retrievers: dict[str, Any] = {}


def get_retriever(collection: str | None = None) -> Any:
    logical_collection = collection or settings.rag_financial_collection
    retriever = _retrievers.get(logical_collection)
    if retriever is None:
        retriever = OperationalRetriever(logical_collection)
        _retrievers[logical_collection] = retriever
    return retriever


async def search_financial_knowledge(
    query: str, top_k: int = 5
) -> list[dict[str, Any]]:
    results = await get_retriever(settings.rag_financial_collection).aretrieve(
        query,
        top_k=top_k,
    )
    from guardrails.boundary_policy import filter_rag_results

    return [result.to_dict() for result in filter_rag_results(results)]


def initialize_test_retriever(documents: Sequence[Any]) -> None:
    """Keep the legacy experiment entry point isolated from operational storage."""

    from core.llm import embed_model
    from llama_index.core import VectorStoreIndex
    from llama_index.core.retrievers import QueryFusionRetriever
    from llama_index.retrievers.bm25 import BM25Retriever

    fetch_k = settings.rag_fetch_k
    index = VectorStoreIndex.from_documents(list(documents), embed_model=embed_model)
    dense = index.as_retriever(similarity_top_k=fetch_k)
    sparse = BM25Retriever.from_defaults(index=index, similarity_top_k=fetch_k)
    _retrievers[settings.rag_financial_collection] = QueryFusionRetriever(
        retrievers=[dense, sparse],
        similarity_top_k=settings.rag_top_k,
        num_queries=1,
        mode="reciprocal_rerank",
        use_async=False,
    )


def reset_retrievers() -> None:
    """Clear cached adapters between isolated tests or configuration changes."""

    _retrievers.clear()


def format_docs(results: Sequence[Any]) -> str:
    from guardrails.boundary_policy import filter_rag_results

    results = filter_rag_results(results)
    if not results:
        return "검색된 승인 근거가 없습니다. 현재 자료만으로는 답변할 수 없습니다."

    formatted: list[str] = []
    for result in results:
        if isinstance(result, SearchResult):
            metadata = result.chunk.metadata
            formatted.append(
                "\n".join(
                    (
                        f"[자료: {metadata.title} | 기준일: {metadata.as_of_date}]",
                        result.chunk.text,
                    )
                )
            )
            continue

        node = result.node
        formatted.append(node.get_content())
    return "\n\n---\n\n".join(formatted)
