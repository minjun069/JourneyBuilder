"""Committed conversation context and request-replay storage.

LangGraph checkpoints are deliberately temporary and request-scoped.  This
repository owns the only durable conversation state: one current completed
context plus one rollback context for each Django session/thread scope.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Any, Mapping

from core.context import (
    CONTEXT_SCHEMA_VERSION,
    ContextScope,
    ConversationContext,
)


_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


class ContextCommitConflict(RuntimeError):
    """Raised when a request id is reused with another scope or payload."""


def committed_scope_key(scope: ContextScope) -> str:
    canonical = json.dumps(
        {"session_id": scope.session_id, "thread_id": scope.thread_id},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(canonical).hexdigest()


def _validate_request_identity(request_id: str, payload_digest: str) -> None:
    if not isinstance(request_id, str) or not request_id.strip():
        raise ValueError("request_id must be a non-empty string")
    if not isinstance(payload_digest, str) or _SHA256_HEX.fullmatch(payload_digest) is None:
        raise ValueError("payload_digest must be a lowercase SHA-256 hex digest")


@dataclass
class _MemoryContextRow:
    current: ConversationContext
    previous: ConversationContext | None
    current_request_id: str
    version: int


class InMemoryCommittedContextStore:
    """Deterministic repository used by graph and context contract tests."""

    def __init__(self, *, execution_ttl_seconds: int = 600) -> None:
        self._contexts: dict[str, _MemoryContextRow] = {}
        self._receipts: dict[str, tuple[str, str, dict[str, Any]]] = {}
        self._executions: dict[str, tuple[str, float]] = {}
        self._execution_ttl_seconds = execution_ttl_seconds
        self._lock = asyncio.Lock()

    async def register_execution(self, thread_key: str, request_id: str) -> None:
        async with self._lock:
            self._executions[thread_key] = (
                request_id,
                asyncio.get_running_loop().time() + self._execution_ttl_seconds,
            )

    async def expired_execution_keys(self) -> tuple[str, ...]:
        now = asyncio.get_running_loop().time()
        async with self._lock:
            return tuple(
                key for key, (_, expires_at) in self._executions.items()
                if expires_at <= now
            )

    async def forget_execution(self, thread_key: str) -> None:
        async with self._lock:
            self._executions.pop(thread_key, None)

    async def read(self, scope: ContextScope) -> ConversationContext:
        async with self._lock:
            row = self._contexts.get(committed_scope_key(scope))
            return deepcopy(row.current) if row else ConversationContext()

    async def read_previous(self, scope: ContextScope) -> ConversationContext | None:
        async with self._lock:
            row = self._contexts.get(committed_scope_key(scope))
            return deepcopy(row.previous) if row and row.previous else None

    async def read_terminal(
        self,
        scope: ContextScope,
        request_id: str,
        payload_digest: str,
    ) -> Mapping[str, Any] | None:
        _validate_request_identity(request_id, payload_digest)
        async with self._lock:
            receipt = self._receipts.get(request_id)
            if receipt is None:
                return None
            scope_key, stored_digest, terminal = receipt
            if scope_key != committed_scope_key(scope) or stored_digest != payload_digest:
                raise ContextCommitConflict("request identity does not match committed terminal")
            return deepcopy(terminal)

    async def commit(
        self,
        scope: ContextScope,
        request_id: str,
        payload_digest: str,
        context: ConversationContext,
        terminal: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        _validate_request_identity(request_id, payload_digest)
        terminal_copy = deepcopy(dict(terminal))
        scope_key = committed_scope_key(scope)
        async with self._lock:
            receipt = self._receipts.get(request_id)
            if receipt is not None:
                stored_scope, stored_digest, stored_terminal = receipt
                if stored_scope != scope_key or stored_digest != payload_digest:
                    raise ContextCommitConflict("request id was already committed differently")
                return deepcopy(stored_terminal)
            existing = self._contexts.get(scope_key)
            self._contexts[scope_key] = _MemoryContextRow(
                current=deepcopy(context),
                previous=deepcopy(existing.current) if existing else None,
                current_request_id=request_id,
                version=(existing.version + 1) if existing else 1,
            )
            self._receipts[request_id] = (scope_key, payload_digest, terminal_copy)
            return deepcopy(terminal_copy)

    async def commit_terminal(
        self,
        scope: ContextScope,
        request_id: str,
        payload_digest: str,
        terminal: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        _validate_request_identity(request_id, payload_digest)
        terminal_copy = deepcopy(dict(terminal))
        scope_key = committed_scope_key(scope)
        async with self._lock:
            receipt = self._receipts.get(request_id)
            if receipt is not None:
                stored_scope, stored_digest, stored_terminal = receipt
                if stored_scope != scope_key or stored_digest != payload_digest:
                    raise ContextCommitConflict("request id was already committed differently")
                return deepcopy(stored_terminal)
            self._receipts[request_id] = (scope_key, payload_digest, terminal_copy)
            return deepcopy(terminal_copy)

    async def delete(self, scope: ContextScope) -> None:
        scope_key = committed_scope_key(scope)
        async with self._lock:
            self._contexts.pop(scope_key, None)
            self._receipts = {
                request_id: receipt
                for request_id, receipt in self._receipts.items()
                if receipt[0] != scope_key
            }


class PostgresCommittedContextStore:
    """PostgreSQL implementation with atomic context replacement and replay."""

    def __init__(
        self,
        dsn: str,
        *,
        execution_ttl_seconds: int = 600,
        receipt_ttl_seconds: int = 86_400,
        closed_retention_seconds: int = 86_400,
    ) -> None:
        self._dsn = dsn
        self._execution_ttl_seconds = execution_ttl_seconds
        self._receipt_ttl_seconds = receipt_ttl_seconds
        self._closed_retention_seconds = closed_retention_seconds

    async def _connect(self, *, autocommit: bool = False):
        from psycopg import AsyncConnection
        from psycopg.rows import dict_row

        return await AsyncConnection.connect(
            self._dsn,
            autocommit=autocommit,
            row_factory=dict_row,
        )

    async def setup(self) -> None:
        async with await self._connect(autocommit=True) as connection:
            await connection.execute("""
                CREATE TABLE IF NOT EXISTS ai_committed_contexts (
                    scope_key CHAR(64) PRIMARY KEY,
                    session_id BIGINT NOT NULL,
                    thread_id TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    current_context JSONB NOT NULL,
                    previous_context JSONB,
                    current_request_id VARCHAR(128) NOT NULL,
                    previous_request_id VARCHAR(128),
                    version BIGINT NOT NULL,
                    delete_after TIMESTAMPTZ,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            await connection.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS ai_committed_context_scope_identity
                ON ai_committed_contexts (session_id, thread_id)
            """)
            await connection.execute("""
                CREATE TABLE IF NOT EXISTS ai_context_terminal_receipts (
                    request_id VARCHAR(128) PRIMARY KEY,
                    scope_key CHAR(64) NOT NULL REFERENCES ai_committed_contexts(scope_key)
                        ON DELETE CASCADE,
                    payload_digest CHAR(64) NOT NULL,
                    terminal JSONB NOT NULL,
                    context_version BIGINT NOT NULL,
                    committed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    expires_at TIMESTAMPTZ NOT NULL
                )
            """)
            await connection.execute("""
                CREATE INDEX IF NOT EXISTS ai_context_terminal_receipts_expiry
                ON ai_context_terminal_receipts (expires_at)
            """)
            await connection.execute("""
                CREATE TABLE IF NOT EXISTS ai_temporary_executions (
                    thread_key TEXT PRIMARY KEY,
                    request_id VARCHAR(128) NOT NULL,
                    expires_at TIMESTAMPTZ NOT NULL
                )
            """)
            await self._discard_legacy_scope_checkpoints(connection)
        await self.cleanup_closed_contexts()

    async def _discard_legacy_scope_checkpoints(self, connection) -> None:
        for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
            await connection.execute(
                f"DELETE FROM {table} WHERE thread_id LIKE 'ai-context-v1:%'"
            )

    async def register_execution(self, thread_key: str, request_id: str) -> None:
        async with await self._connect(autocommit=True) as connection:
            await connection.execute(
                """
                INSERT INTO ai_temporary_executions (thread_key, request_id, expires_at)
                VALUES (%s, %s, NOW() + (%s * INTERVAL '1 second'))
                ON CONFLICT (thread_key) DO UPDATE
                SET request_id = EXCLUDED.request_id, expires_at = EXCLUDED.expires_at
                """,
                (thread_key, request_id, self._execution_ttl_seconds),
            )

    async def expired_execution_keys(self) -> tuple[str, ...]:
        async with await self._connect(autocommit=True) as connection:
            cursor = await connection.execute(
                "SELECT thread_key FROM ai_temporary_executions WHERE expires_at <= NOW()"
            )
            return tuple(row["thread_key"] async for row in cursor)

    async def forget_execution(self, thread_key: str) -> None:
        async with await self._connect(autocommit=True) as connection:
            await connection.execute(
                "DELETE FROM ai_temporary_executions WHERE thread_key = %s",
                (thread_key,),
            )

    async def read(self, scope: ContextScope) -> ConversationContext:
        scope_key = committed_scope_key(scope)
        async with await self._connect() as connection:
            cursor = await connection.execute(
                """
                SELECT schema_version, current_context
                FROM ai_committed_contexts
                WHERE scope_key = %s AND (delete_after IS NULL OR delete_after > NOW())
                """,
                (scope_key,),
            )
            row = await cursor.fetchone()
            if row is None:
                return ConversationContext()
            try:
                if row["schema_version"] != CONTEXT_SCHEMA_VERSION:
                    raise ValueError("unsupported committed context schema")
                return ConversationContext.from_state(row["current_context"])
            except (TypeError, ValueError):
                await connection.execute(
                    "DELETE FROM ai_committed_contexts WHERE scope_key = %s",
                    (scope_key,),
                )
                return ConversationContext()

    async def read_previous(self, scope: ContextScope) -> ConversationContext | None:
        scope_key = committed_scope_key(scope)
        async with await self._connect(autocommit=True) as connection:
            cursor = await connection.execute(
                """
                SELECT schema_version, previous_context
                FROM ai_committed_contexts
                WHERE scope_key = %s AND (delete_after IS NULL OR delete_after > NOW())
                """,
                (scope_key,),
            )
            row = await cursor.fetchone()
            if (
                row is None
                or row["schema_version"] != CONTEXT_SCHEMA_VERSION
                or row["previous_context"] is None
            ):
                return None
            try:
                return ConversationContext.from_state(row["previous_context"])
            except (TypeError, ValueError):
                return None

    async def read_terminal(
        self,
        scope: ContextScope,
        request_id: str,
        payload_digest: str,
    ) -> Mapping[str, Any] | None:
        _validate_request_identity(request_id, payload_digest)
        scope_key = committed_scope_key(scope)
        async with await self._connect(autocommit=True) as connection:
            cursor = await connection.execute(
                """
                SELECT scope_key, payload_digest, terminal
                FROM ai_context_terminal_receipts
                WHERE request_id = %s AND expires_at > NOW()
                """,
                (request_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            if row["scope_key"].strip() != scope_key or row["payload_digest"].strip() != payload_digest:
                raise ContextCommitConflict("request identity does not match committed terminal")
            return deepcopy(row["terminal"])

    async def commit(
        self,
        scope: ContextScope,
        request_id: str,
        payload_digest: str,
        context: ConversationContext,
        terminal: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        from psycopg.types.json import Jsonb

        _validate_request_identity(request_id, payload_digest)
        scope_key = committed_scope_key(scope)
        terminal_copy = deepcopy(dict(terminal))
        async with await self._connect() as connection:
            async with connection.transaction():
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (scope_key,),
                )
                cursor = await connection.execute(
                    """
                    SELECT scope_key, payload_digest, terminal
                    FROM ai_context_terminal_receipts
                    WHERE request_id = %s
                    FOR UPDATE
                    """,
                    (request_id,),
                )
                receipt = await cursor.fetchone()
                if receipt is not None:
                    if (
                        receipt["scope_key"].strip() != scope_key
                        or receipt["payload_digest"].strip() != payload_digest
                    ):
                        raise ContextCommitConflict(
                            "request id was already committed differently"
                        )
                    return deepcopy(receipt["terminal"])

                cursor = await connection.execute(
                    """
                    SELECT current_context, current_request_id, version
                    FROM ai_committed_contexts
                    WHERE scope_key = %s
                    FOR UPDATE
                    """,
                    (scope_key,),
                )
                existing = await cursor.fetchone()
                next_version = int(existing["version"]) + 1 if existing else 1
                if existing is None:
                    await connection.execute(
                        """
                        INSERT INTO ai_committed_contexts (
                            scope_key, session_id, thread_id, schema_version,
                            current_context, current_request_id, version
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            scope_key,
                            scope.session_id,
                            scope.thread_id,
                            CONTEXT_SCHEMA_VERSION,
                            Jsonb(context.to_state()),
                            request_id,
                            next_version,
                        ),
                    )
                else:
                    await connection.execute(
                        """
                        UPDATE ai_committed_contexts
                        SET previous_context = CASE
                                WHEN version > 0 THEN current_context
                                ELSE previous_context
                            END,
                            previous_request_id = CASE
                                WHEN version > 0 THEN current_request_id
                                ELSE previous_request_id
                            END,
                            current_context = %s,
                            current_request_id = %s,
                            schema_version = %s,
                            version = %s,
                            delete_after = NULL,
                            updated_at = NOW()
                        WHERE scope_key = %s
                        """,
                        (
                            Jsonb(context.to_state()),
                            request_id,
                            CONTEXT_SCHEMA_VERSION,
                            next_version,
                            scope_key,
                        ),
                    )
                await connection.execute(
                    """
                    INSERT INTO ai_context_terminal_receipts (
                        request_id, scope_key, payload_digest, terminal,
                        context_version, expires_at
                    ) VALUES (%s, %s, %s, %s, %s,
                              NOW() + (%s * INTERVAL '1 second'))
                    """,
                    (
                        request_id,
                        scope_key,
                        payload_digest,
                        Jsonb(terminal_copy),
                        next_version,
                        self._receipt_ttl_seconds,
                    ),
                )
        return terminal_copy

    async def commit_terminal(
        self,
        scope: ContextScope,
        request_id: str,
        payload_digest: str,
        terminal: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Persist a final error receipt while leaving committed context unchanged."""

        from psycopg.types.json import Jsonb

        _validate_request_identity(request_id, payload_digest)
        scope_key = committed_scope_key(scope)
        terminal_copy = deepcopy(dict(terminal))
        async with await self._connect() as connection:
            async with connection.transaction():
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (scope_key,),
                )
                cursor = await connection.execute(
                    """
                    SELECT scope_key, payload_digest, terminal
                    FROM ai_context_terminal_receipts
                    WHERE request_id = %s
                    FOR UPDATE
                    """,
                    (request_id,),
                )
                receipt = await cursor.fetchone()
                if receipt is not None:
                    if (
                        receipt["scope_key"].strip() != scope_key
                        or receipt["payload_digest"].strip() != payload_digest
                    ):
                        raise ContextCommitConflict(
                            "request id was already committed differently"
                        )
                    return deepcopy(receipt["terminal"])

                cursor = await connection.execute(
                    """
                    SELECT version
                    FROM ai_committed_contexts
                    WHERE scope_key = %s
                    FOR UPDATE
                    """,
                    (scope_key,),
                )
                context_row = await cursor.fetchone()
                if context_row is None:
                    await connection.execute(
                        """
                        INSERT INTO ai_committed_contexts (
                            scope_key, session_id, thread_id, schema_version,
                            current_context, current_request_id, version
                        ) VALUES (%s, %s, %s, %s, %s, %s, 0)
                        """,
                        (
                            scope_key,
                            scope.session_id,
                            scope.thread_id,
                            CONTEXT_SCHEMA_VERSION,
                            Jsonb(ConversationContext().to_state()),
                            request_id,
                        ),
                    )
                    context_version = 0
                else:
                    context_version = int(context_row["version"])

                await connection.execute(
                    """
                    INSERT INTO ai_context_terminal_receipts (
                        request_id, scope_key, payload_digest, terminal,
                        context_version, expires_at
                    ) VALUES (%s, %s, %s, %s, %s,
                              NOW() + (%s * INTERVAL '1 second'))
                    """,
                    (
                        request_id,
                        scope_key,
                        payload_digest,
                        Jsonb(terminal_copy),
                        context_version,
                        self._receipt_ttl_seconds,
                    ),
                )
        return terminal_copy

    async def delete(self, scope: ContextScope) -> None:
        async with await self._connect(autocommit=True) as connection:
            await connection.execute(
                "DELETE FROM ai_committed_contexts WHERE scope_key = %s",
                (committed_scope_key(scope),),
            )

    async def cleanup_closed_contexts(self) -> None:
        """Expire completed/abandoned Backend sessions without deleting records."""

        async with await self._connect() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    UPDATE ai_committed_contexts AS context
                    SET delete_after = COALESCE(
                        context.delete_after,
                        NOW() + (%s * INTERVAL '1 second')
                    )
                    FROM consulting_sessions AS session
                    WHERE context.session_id = session.id
                      AND session.status IN ('completed', 'abandoned')
                    """,
                    (self._closed_retention_seconds,),
                )
                await connection.execute(
                    "DELETE FROM ai_committed_contexts WHERE delete_after <= NOW()"
                )
                await connection.execute(
                    "DELETE FROM ai_context_terminal_receipts WHERE expires_at <= NOW()"
                )
