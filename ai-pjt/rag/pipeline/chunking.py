from __future__ import annotations

import hashlib
import re

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


class MarkdownStructureChunker(DeterministicChunker):
    """Chunk Markdown on headings, paragraphs, and complete table blocks."""

    _HEADING = re.compile(r"^[ \t]{0,3}#{1,6}\s+")

    def __init__(self, chunk_size: int = 900, overlap: int = 100) -> None:
        super().__init__(chunk_size=chunk_size, overlap=overlap)

    @property
    def chunker_id(self) -> str:
        return f"markdown-v1-size-{self.chunk_size}-overlap-{self.overlap}"

    def chunk_document(self, document: SourceDocument) -> list[Chunk]:
        ranges = self._block_ranges(document.text)
        packed: list[tuple[int, int]] = []
        current: tuple[int, int] | None = None

        for start, end, is_table in ranges:
            if end - start > self.chunk_size and not is_table:
                if current is not None:
                    packed.append(current)
                    current = None
                packed.extend(self._split_long_block(document.text, start, end))
                continue

            if current is None:
                current = (start, end)
            elif end - current[0] <= self.chunk_size:
                current = (current[0], end)
            else:
                packed.append(current)
                current = (start, end)

        if current is not None:
            packed.append(current)
        return self._make_chunks(document, packed)

    def _block_ranges(self, text: str) -> list[tuple[int, int, bool]]:
        lines = text.splitlines(keepends=True)
        ranges: list[tuple[int, int, bool]] = []
        offset = 0
        block_start: int | None = None
        block_end = 0
        block_is_table = False

        def flush() -> None:
            nonlocal block_start, block_end, block_is_table
            if block_start is not None:
                start, end = self._trim_range(text, block_start, block_end)
                if start < end:
                    ranges.append((start, end, block_is_table))
            block_start = None
            block_end = 0
            block_is_table = False

        for line in lines:
            line_start = offset
            offset += len(line)
            stripped = line.strip()
            is_table = line.lstrip().startswith("|")
            is_heading = bool(self._HEADING.match(line))

            if not stripped:
                flush()
                continue
            if is_heading or (block_start is not None and is_table != block_is_table):
                flush()
            if block_start is None:
                block_start = line_start
                block_is_table = is_table
            block_end = offset
        flush()
        return ranges

    def _split_long_block(self, text: str, start: int, end: int) -> list[tuple[int, int]]:
        ranges: list[tuple[int, int]] = []
        cursor = start
        while cursor < end:
            window_end = min(cursor + self.chunk_size, end)
            split_at = window_end
            if window_end < end:
                minimum = cursor + max(1, self.chunk_size // 2)
                candidates = [
                    text.rfind("\n\n", minimum, window_end),
                    text.rfind("\n", minimum, window_end),
                    text.rfind(". ", minimum, window_end),
                ]
                boundary = max(candidates)
                if boundary >= minimum:
                    split_at = boundary + (2 if text[boundary:boundary + 2] in {"\n\n", ". "} else 1)
            chunk_start, chunk_end = self._trim_range(text, cursor, split_at)
            if chunk_start < chunk_end:
                ranges.append((chunk_start, chunk_end))
            if split_at >= end:
                break
            next_cursor = max(cursor + 1, split_at - self.overlap)
            while next_cursor < end and text[next_cursor].isspace():
                next_cursor += 1
            cursor = next_cursor
        return ranges

    @staticmethod
    def _trim_range(text: str, start: int, end: int) -> tuple[int, int]:
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        return start, end

    def _make_chunks(
        self, document: SourceDocument, ranges: list[tuple[int, int]]
    ) -> list[Chunk]:
        chunks: list[Chunk] = []
        for ordinal, (start, end) in enumerate(ranges):
            content = document.text[start:end]
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            identity = "\x1f".join(
                (
                    document.metadata.source_id,
                    document.metadata.version,
                    self.chunker_id,
                    str(ordinal),
                    str(start),
                    str(end),
                    content_hash,
                )
            )
            chunks.append(
                Chunk(
                    chunk_id=hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32],
                    text=content,
                    metadata=document.metadata,
                    ordinal=ordinal,
                    start_offset=start,
                    end_offset=end,
                    content_hash=content_hash,
                    chunker_id=self.chunker_id,
                )
            )
        return chunks
