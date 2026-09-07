from __future__ import annotations

import json
import math
import os
import tempfile
from collections.abc import Callable
from datetime import datetime, timezone
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


class PostgresIndexStore:
    """Versioned operational index store backed by the existing pgvector database."""

    def __init__(
        self,
        database_url: str,
        *,
        connect_factory: Callable[..., Any] | None = None,
    ) -> None:
        if not database_url.strip():
            raise ValueError("database_url must not be empty")
        self.database_url = database_url
        self._connect_factory = connect_factory

    def _connect(self) -> Any:
        if self._connect_factory is None:
            try:
                import psycopg
            except ImportError as error:
                raise RuntimeError("psycopg is required for operational RAG") from error
            self._connect_factory = psycopg.connect
        return self._connect_factory(self.database_url)

    def ensure_schema(self) -> None:
        statements = (
            "CREATE EXTENSION IF NOT EXISTS vector",
            """
            CREATE TABLE IF NOT EXISTS rag_index_manifests (
                physical_collection text PRIMARY KEY,
                logical_collection text NOT NULL,
                manifest jsonb NOT NULL,
                created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
                retired_at timestamptz NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS rag_index_chunks (
                physical_collection text NOT NULL,
                chunk_id text NOT NULL,
                source_id text NOT NULL,
                chunk jsonb NOT NULL,
                embedding vector NOT NULL,
                PRIMARY KEY (physical_collection, chunk_id)
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS rag_index_chunks_source_idx
            ON rag_index_chunks (physical_collection, source_id)
            """,
            """
            CREATE TABLE IF NOT EXISTS rag_active_indexes (
                logical_collection text PRIMARY KEY,
                physical_collection text NOT NULL,
                activated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
        )
        with self._connect() as connection:
            with connection.cursor() as cursor:
                for statement in statements:
                    cursor.execute(statement)

    def clear_collection(self, collection: str) -> None:
        _validate_name(collection)
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM rag_active_indexes WHERE physical_collection = %s",
                    (collection,),
                )
                if cursor.fetchone() is not None:
                    raise ValueError("cannot clear an active physical collection")
                cursor.execute(
                    "DELETE FROM rag_index_chunks WHERE physical_collection = %s",
                    (collection,),
                )
                cursor.execute(
                    "DELETE FROM rag_index_manifests WHERE physical_collection = %s",
                    (collection,),
                )

    def upsert(
        self, collection: str, chunks: list[EmbeddedChunk]
    ) -> tuple[int, int]:
        _validate_name(collection)
        if not chunks:
            return 0, 0
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT chunk_id FROM rag_index_chunks WHERE physical_collection = %s",
                    (collection,),
                )
                existing = {row[0] for row in cursor.fetchall()}
                rows = [
                    (
                        collection,
                        embedded.chunk.chunk_id,
                        embedded.chunk.metadata.source_id,
                        json.dumps(
                            embedded.chunk.to_dict(),
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                        self._vector_literal(embedded.vector),
                    )
                    for embedded in chunks
                ]
                cursor.executemany(
                    """
                    INSERT INTO rag_index_chunks (
                        physical_collection, chunk_id, source_id, chunk, embedding
                    ) VALUES (%s, %s, %s, %s::jsonb, %s::vector)
                    ON CONFLICT (physical_collection, chunk_id) DO UPDATE SET
                        source_id = EXCLUDED.source_id,
                        chunk = EXCLUDED.chunk,
                        embedding = EXCLUDED.embedding
                    """,
                    rows,
                )
        inserted = sum(embedded.chunk.chunk_id not in existing for embedded in chunks)
        return inserted, len(chunks) - inserted

    def list_chunks(self, collection: str) -> list[EmbeddedChunk]:
        _validate_name(collection)
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT chunk, embedding::text
                    FROM rag_index_chunks
                    WHERE physical_collection = %s
                    ORDER BY chunk_id
                    """,
                    (collection,),
                )
                return [self._embedded_from_row(row) for row in cursor.fetchall()]

    def write_manifest(self, collection: str, manifest: IndexManifest) -> None:
        if collection != manifest.physical_collection:
            raise ValueError("manifest physical collection does not match target")
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO rag_index_manifests (
                        physical_collection, logical_collection, manifest, created_at,
                        retired_at
                    ) VALUES (%s, %s, %s::jsonb, %s, NULL)
                    ON CONFLICT (physical_collection) DO UPDATE SET
                        logical_collection = EXCLUDED.logical_collection,
                        manifest = EXCLUDED.manifest,
                        created_at = EXCLUDED.created_at,
                        retired_at = NULL
                    """,
                    (
                        collection,
                        manifest.collection,
                        json.dumps(manifest.to_dict(), ensure_ascii=False, sort_keys=True),
                        manifest.built_at,
                    ),
                )

    def get_manifest(self, collection: str) -> IndexManifest | None:
        _validate_name(collection)
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT manifest FROM rag_index_manifests WHERE physical_collection = %s",
                    (collection,),
                )
                row = cursor.fetchone()
        if row is None:
            return None
        payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        return IndexManifest.from_dict(payload)

    def activate(self, logical_collection: str, physical_collection: str) -> None:
        _validate_name(logical_collection)
        _validate_name(physical_collection)
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    (f"rag:{logical_collection}",),
                )
                cursor.execute(
                    """
                    SELECT logical_collection
                    FROM rag_index_manifests
                    WHERE physical_collection = %s
                    """,
                    (physical_collection,),
                )
                row = cursor.fetchone()
                if row is None or row[0] != logical_collection:
                    raise ValueError("cannot activate a collection without a matching manifest")
                cursor.execute(
                    """
                    SELECT physical_collection
                    FROM rag_active_indexes
                    WHERE logical_collection = %s
                    FOR UPDATE
                    """,
                    (logical_collection,),
                )
                current = cursor.fetchone()
                if current is not None and current[0] != physical_collection:
                    cursor.execute(
                        """
                        UPDATE rag_index_manifests
                        SET retired_at = CURRENT_TIMESTAMP
                        WHERE physical_collection = %s
                        """,
                        (current[0],),
                    )
                cursor.execute(
                    """
                    INSERT INTO rag_active_indexes (
                        logical_collection, physical_collection, activated_at
                    ) VALUES (%s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (logical_collection) DO UPDATE SET
                        physical_collection = EXCLUDED.physical_collection,
                        activated_at = EXCLUDED.activated_at
                    """,
                    (logical_collection, physical_collection),
                )
                cursor.execute(
                    """
                    UPDATE rag_index_manifests
                    SET retired_at = NULL
                    WHERE physical_collection = %s
                    """,
                    (physical_collection,),
                )

    def active_collection(self, logical_collection: str) -> str | None:
        _validate_name(logical_collection)
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT physical_collection
                    FROM rag_active_indexes
                    WHERE logical_collection = %s
                    """,
                    (logical_collection,),
                )
                row = cursor.fetchone()
        return row[0] if row else None

    def search(
        self, collection: str, query_vector: tuple[float, ...], top_k: int
    ) -> list[SearchResult]:
        _validate_name(collection)
        if top_k < 1:
            raise ValueError("top_k must be positive")
        vector = self._vector_literal(query_vector)
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT chunk, 1 - (embedding <=> %s::vector) AS score
                    FROM rag_index_chunks
                    WHERE physical_collection = %s
                    ORDER BY embedding <=> %s::vector, chunk_id
                    LIMIT %s
                    """,
                    (vector, collection, vector, top_k),
                )
                rows = cursor.fetchall()
        results: list[SearchResult] = []
        for rank, (chunk_payload, score) in enumerate(rows, start=1):
            payload = (
                chunk_payload
                if isinstance(chunk_payload, dict)
                else json.loads(chunk_payload)
            )
            from .models import Chunk

            results.append(
                SearchResult(
                    chunk=Chunk.from_dict(payload),
                    score=float(score),
                    dense_score=float(score),
                    rank=rank,
                )
            )
        return results

    def prune_retired(self, logical_collection: str, *, retention_days: int = 30) -> int:
        _validate_name(logical_collection)
        if retention_days < 1:
            raise ValueError("retention_days must be positive")
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT physical_collection
                    FROM rag_index_manifests
                    WHERE logical_collection = %s
                      AND retired_at IS NOT NULL
                      AND retired_at < CURRENT_TIMESTAMP - (%s * INTERVAL '1 day')
                      AND physical_collection NOT IN (
                          SELECT physical_collection FROM rag_active_indexes
                      )
                    """,
                    (logical_collection, retention_days),
                )
                retired = [row[0] for row in cursor.fetchall()]
                for physical in retired:
                    cursor.execute(
                        "DELETE FROM rag_index_chunks WHERE physical_collection = %s",
                        (physical,),
                    )
                    cursor.execute(
                        "DELETE FROM rag_index_manifests WHERE physical_collection = %s",
                        (physical,),
                    )
        return len(retired)

    @staticmethod
    def _vector_literal(vector: tuple[float, ...]) -> str:
        return "[" + ",".join(format(value, ".17g") for value in vector) + "]"

    @staticmethod
    def _embedded_from_row(row: tuple[Any, Any]) -> EmbeddedChunk:
        payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        raw_vector = row[1]
        if isinstance(raw_vector, str):
            vector = tuple(float(value) for value in raw_vector.strip("[]").split(","))
        else:
            vector = tuple(float(value) for value in raw_vector)
        from .models import Chunk

        return EmbeddedChunk(chunk=Chunk.from_dict(payload), vector=vector)
