"""Reproducible document indexing and retrieval evaluation primitives."""

from .chunking import DeterministicChunker
from .embeddings import DeterministicHashEmbedding, LlamaIndexEmbeddingAdapter
from .evaluation import EvaluationThresholds, evaluate_retriever
from .indexing import IndexPipeline, IndexedRetriever
from .models import DocumentMetadata, IndexTarget, SearchResult, SourceDocument
from .store import IndexStore, JsonVectorStore

__all__ = [
    "DeterministicChunker",
    "DeterministicHashEmbedding",
    "DocumentMetadata",
    "EvaluationThresholds",
    "IndexPipeline",
    "IndexStore",
    "IndexTarget",
    "IndexedRetriever",
    "JsonVectorStore",
    "LlamaIndexEmbeddingAdapter",
    "SearchResult",
    "SourceDocument",
    "evaluate_retriever",
]
