from __future__ import annotations

import hashlib

from .models import Chunk, SourceDocument


class DeterministicChunker:
    """Split text by stable character offsets while preserving provenance."""

    def __init__(self, chunk_size: int = 480, overlap: int = 80) -> None:
        if chunk_size < 1:
            raise ValueError("chunk_size must be positive")
        if overlap < 0 or overlap >= chunk_size:
            raise ValueError("overlap must satisfy 0 <= overlap < chunk_size")
        self.chunk_size = chunk_size
        self.overlap = overlap

    @property
    def chunker_id(self) -> str:
        return f"character-v1-size-{self.chunk_size}-overlap-{self.overlap}"

    def chunk_documents(self, documents: list[SourceDocument]) -> list[Chunk]:
        chunks: list[Chunk] = []
        ordered = sorted(
            documents,
            key=lambda document: (
                document.metadata.document_kind,
                document.metadata.source_id,
                document.metadata.version,
            ),
        )
        for document in ordered:
            chunks.extend(self.chunk_document(document))
        return chunks

    def chunk_document(self, document: SourceDocument) -> list[Chunk]:
        chunks: list[Chunk] = []
        start = 0
        ordinal = 0
        text_length = len(document.text)

        while start < text_length:
            window_end = min(start + self.chunk_size, text_length)
            raw = document.text[start:window_end]
            leading = len(raw) - len(raw.lstrip())
            trailing = len(raw) - len(raw.rstrip())
            chunk_start = start + leading
            chunk_end = window_end - trailing
            content = document.text[chunk_start:chunk_end]

            if content:
                content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                identity = "\x1f".join(
                    (
                        document.metadata.source_id,
                        document.metadata.version,
                        self.chunker_id,
                        str(ordinal),
                        str(chunk_start),
                        str(chunk_end),
                        content_hash,
                    )
                )
                chunk_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
                chunks.append(
                    Chunk(
                        chunk_id=chunk_id,
                        text=content,
                        metadata=document.metadata,
                        ordinal=ordinal,
                        start_offset=chunk_start,
                        end_offset=chunk_end,
                        content_hash=content_hash,
                        chunker_id=self.chunker_id,
                    )
                )
                ordinal += 1

            if window_end == text_length:
                break
            start = window_end - self.overlap

        return chunks
