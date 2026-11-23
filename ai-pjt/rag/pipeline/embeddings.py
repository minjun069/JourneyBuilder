from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from typing import Protocol


_TOKEN_PATTERN = re.compile(r"[0-9A-Za-z가-힣_]+")


class EmbeddingAdapter(Protocol):
    model_id: str
    dimensions: int

    def embed_texts(self, texts: Sequence[str]) -> list[tuple[float, ...]]: ...


class DeterministicHashEmbedding:
    """Dependency-free embedding for stable synthetic tests, never production."""

    def __init__(self, dimensions: int = 128) -> None:
        if dimensions < 8:
            raise ValueError("dimensions must be at least 8")
        self.dimensions = dimensions
        self.model_id = f"deterministic-token-hash-v1-{dimensions}"

    def embed_texts(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        return [self._embed(text) for text in texts]

    def _embed(self, text: str) -> tuple[float, ...]:
        values = [0.0] * self.dimensions
        tokens = _TOKEN_PATTERN.findall(text.lower())
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:8], "big") % self.dimensions
            values[bucket] += 1.0

        magnitude = math.sqrt(sum(value * value for value in values))
        if magnitude == 0.0:
            return tuple(values)
        return tuple(value / magnitude for value in values)


class LlamaIndexEmbeddingAdapter:
    """Adapt an injected LlamaIndex BaseEmbedding without importing providers."""

    def __init__(self, embedding: object, *, model_id: str, dimensions: int) -> None:
        if not hasattr(embedding, "get_text_embedding_batch"):
            raise TypeError("embedding must expose get_text_embedding_batch(texts)")
        if dimensions < 1:
            raise ValueError("dimensions must be positive")
        self._embedding = embedding
        self.model_id = model_id
        self.dimensions = dimensions

    def embed_texts(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        from core.model_measurement import measure_embedding
        with measure_embedding(self.model_id):
            vectors = self._embedding.get_text_embedding_batch(list(texts))
        result = [tuple(float(value) for value in vector) for vector in vectors]
        if len(result) != len(texts):
            raise ValueError("embedding provider returned an unexpected batch size")
        if any(len(vector) != self.dimensions for vector in result):
            raise ValueError("embedding provider returned an unexpected dimension")
        return result
