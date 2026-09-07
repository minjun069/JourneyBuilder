"""Reproducible document indexing and retrieval evaluation primitives."""

from .chunking import DeterministicChunker, MarkdownStructureChunker
from .corpus import ApprovedCatalog, load_approved_catalog
from .embeddings import DeterministicHashEmbedding, LlamaIndexEmbeddingAdapter
from .evaluation import EvaluationThresholds, evaluate_retriever
from .indexing import IndexPipeline, IndexedRetriever
from .models import DocumentMetadata, IndexTarget, SearchResult, SourceDocument
from .store import IndexStore, JsonVectorStore, PostgresIndexStore

__all__ = [
    "ApprovedCatalog",
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
    "MarkdownStructureChunker",
    "PostgresIndexStore",
    "SearchResult",
    "SourceDocument",
    "evaluate_retriever",
    "load_approved_catalog",
]
