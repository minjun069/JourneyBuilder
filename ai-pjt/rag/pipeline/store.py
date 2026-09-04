from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any, Protocol

from .models import EmbeddedChunk, IndexManifest, SearchResult


class IndexStore(Protocol):
    def clear_collection(self, collection: str) -> None: ...

    def upsert(
        self, collection: str, chunks: list[EmbeddedChunk]
    ) -> tuple[int, int]: ...

    def list_chunks(self, collection: str) -> list[EmbeddedChunk]: ...

    def write_manifest(self, collection: str, manifest: IndexManifest) -> None: ...

    def get_manifest(self, collection: str) -> IndexManifest | None: ...

    def activate(self, logical_collection: str, physical_collection: str) -> None: ...

    def active_collection(self, logical_collection: str) -> str | None: ...

    def search(
        self, collection: str, query_vector: tuple[float, ...], top_k: int
    ) -> list[SearchResult]: ...


def _validate_name(name: str) -> None:
    if not name or not all(char.isalnum() or char in "_-" for char in name):
        raise ValueError(f"unsafe collection name: {name!r}")


class JsonVectorStore:
    """Small persistent vector store for isolated synthetic pipeline runs."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._collections = root / "collections"
        self._active_path = root / "active.json"
        self._collections.mkdir(parents=True, exist_ok=True)

    def _collection_path(self, collection: str) -> Path:
        _validate_name(collection)
        return self._collections / f"{collection}.json"

    @staticmethod
    def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as temporary:
                json.dump(payload, temporary, ensure_ascii=False, indent=2, sort_keys=True)
                temporary.write("\n")
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, path)
        except Exception:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise

    def _load_collection(self, collection: str) -> dict[str, Any]:
        path = self._collection_path(collection)
        if not path.exists():
            return {"manifest": None, "chunks": {}}
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or set(payload) != {"manifest", "chunks"}:
            raise ValueError(f"invalid collection file: {path}")
        if not isinstance(payload["chunks"], dict):
            raise ValueError(f"invalid chunks payload: {path}")
        return payload

    def clear_collection(self, collection: str) -> None:
        self._collection_path(collection).unlink(missing_ok=True)

    def upsert(
        self, collection: str, chunks: list[EmbeddedChunk]
    ) -> tuple[int, int]:
        payload = self._load_collection(collection)
        stored = payload["chunks"]
        inserted = 0
        updated = 0
        for embedded in chunks:
            if embedded.chunk.chunk_id in stored:
                updated += 1
            else:
                inserted += 1
            stored[embedded.chunk.chunk_id] = embedded.to_dict()
        self._atomic_write(self._collection_path(collection), payload)
        return inserted, updated

    def list_chunks(self, collection: str) -> list[EmbeddedChunk]:
        payload = self._load_collection(collection)
        return [
            EmbeddedChunk.from_dict(payload["chunks"][chunk_id])
            for chunk_id in sorted(payload["chunks"])
        ]

    def write_manifest(self, collection: str, manifest: IndexManifest) -> None:
        if collection != manifest.physical_collection:
            raise ValueError("manifest physical collection does not match target")
        payload = self._load_collection(collection)
        payload["manifest"] = manifest.to_dict()
        self._atomic_write(self._collection_path(collection), payload)

    def get_manifest(self, collection: str) -> IndexManifest | None:
        payload = self._load_collection(collection)
        manifest = payload["manifest"]
        return IndexManifest.from_dict(manifest) if manifest else None

    def _load_active(self) -> dict[str, str]:
        if not self._active_path.exists():
            return {}
        payload = json.loads(self._active_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in payload.items()
        ):
            raise ValueError(f"invalid active collection map: {self._active_path}")
        return payload

    def activate(self, logical_collection: str, physical_collection: str) -> None:
        _validate_name(logical_collection)
        manifest = self.get_manifest(physical_collection)
        if manifest is None or manifest.collection != logical_collection:
            raise ValueError("cannot activate a collection without a matching manifest")
        active = self._load_active()
        active[logical_collection] = physical_collection
        self._atomic_write(self._active_path, active)

    def active_collection(self, logical_collection: str) -> str | None:
        _validate_name(logical_collection)
        return self._load_active().get(logical_collection)

    def search(
        self, collection: str, query_vector: tuple[float, ...], top_k: int
    ) -> list[SearchResult]:
        if top_k < 1:
            raise ValueError("top_k must be positive")
        query_magnitude = math.sqrt(sum(value * value for value in query_vector))
        scored: list[tuple[float, EmbeddedChunk]] = []
        for embedded in self.list_chunks(collection):
            if len(embedded.vector) != len(query_vector):
                raise ValueError("query and index embedding dimensions differ")
            dot_product = sum(
                query_value * chunk_value
                for query_value, chunk_value in zip(query_vector, embedded.vector)
            )
            chunk_magnitude = math.sqrt(
                sum(value * value for value in embedded.vector)
            )
            denominator = query_magnitude * chunk_magnitude
            score = dot_product / denominator if denominator else 0.0
            scored.append((score, embedded))
        scored.sort(key=lambda item: (-item[0], item[1].chunk.chunk_id))
        return [
            SearchResult(chunk=embedded.chunk, score=score, rank=rank)
            for rank, (score, embedded) in enumerate(scored[:top_k], start=1)
        ]
