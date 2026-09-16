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
    ArchivedConversationTurn,
    ContextSummary,
    ContextScope,
    ConversationContext,
    ConversationTurn,
    RetrievedMemory,
    StructuredConversationState,
    SummaryJob,
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


@dataclass
class _MemoryArchiveRow:
    archive_id: int
    sequence: int
    scope: ContextScope
    turn: ConversationTurn
    embedding: tuple[float, ...] | None = None


@dataclass
class _MemorySummaryJob:
    scope: ContextScope
    target_cursor: int
    attempts: int = 0
    leased: bool = False


class InMemoryCommittedContextStore:
    """Deterministic repository used by graph and context contract tests."""

    def __init__(self, *, execution_ttl_seconds: int = 600) -> None:
        self._contexts: dict[str, _MemoryContextRow] = {}
        self._receipts: dict[str, tuple[str, str, dict[str, Any]]] = {}
        self._executions: dict[str, tuple[str, float]] = {}
        self._archives: dict[str, list[_MemoryArchiveRow]] = {}
        self._summary_jobs: dict[str, _MemorySummaryJob] = {}
        self._next_archive_id = 1
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
        completed_turn: ConversationTurn | None = None,
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
            effective_context = context
            if (
                existing is not None
                and _summary_cursor(existing.current) > _summary_cursor(context)
            ):
                effective_context = ConversationContext(
                    turns=context.turns,
                    summary=deepcopy(existing.current.summary),
                    structured_state=deepcopy(existing.current.structured_state),
                )
            archived_turn = completed_turn or next(
                (turn for turn in reversed(context.turns) if turn.turn_id == request_id),
                None,
            )
            if archived_turn is None:
                raise ValueError("completed context must include the committed turn")
            archive = self._archives.setdefault(scope_key, [])
            same_turn = next(
                (row for row in archive if row.turn.turn_id == archived_turn.turn_id),
                None,
            )
            if same_turn is not None and same_turn.turn != archived_turn:
                raise ContextCommitConflict("turn id was already archived differently")
            if same_turn is None:
                archive.append(_MemoryArchiveRow(
                    archive_id=self._next_archive_id,
                    sequence=len(archive) + 1,
                    scope=scope,
                    turn=deepcopy(archived_turn),
                ))
                self._next_archive_id += 1
            self._contexts[scope_key] = _MemoryContextRow(
                current=deepcopy(effective_context),
                previous=deepcopy(existing.current) if existing else None,
                current_request_id=request_id,
                version=(existing.version + 1) if existing else 1,
            )
            self._enqueue_summary_if_needed(scope, effective_context)
            self._receipts[request_id] = (scope_key, payload_digest, terminal_copy)
            return deepcopy(terminal_copy)

    def _enqueue_summary_if_needed(
        self,
        scope: ContextScope,
        context: ConversationContext,
    ) -> None:
        scope_key = committed_scope_key(scope)
        archive = self._archives.get(scope_key, [])
        retained = {turn.turn_id for turn in context.turns}
        cursor = (
            context.summary.summarized_through_sequence
            if context.summary is not None
            else 0
        )
        targets = [
            row.sequence
            for row in archive
            if row.sequence > cursor and row.turn.turn_id not in retained
        ]
        if not targets:
            return
        target = max(targets)
        job = self._summary_jobs.get(scope_key)
        if job is None:
            self._summary_jobs[scope_key] = _MemorySummaryJob(scope, target)
        else:
            job.target_cursor = max(job.target_cursor, target)

    async def retrieve_memories(
        self,
        scope: ContextScope,
        query_embedding: tuple[float, ...] | None,
        *,
        exclude_turn_ids: tuple[str, ...],
        limit: int,
    ) -> tuple[RetrievedMemory, ...]:
        if limit <= 0:
            return ()
        excluded = set(exclude_turn_ids)
        async with self._lock:
            rows = [
                row
                for row in self._archives.get(committed_scope_key(scope), [])
                if row.turn.turn_id not in excluded
            ]
            if query_embedding is not None:
                rows = sorted(
                    rows,
                    key=lambda row: (
                        _cosine_distance(query_embedding, row.embedding)
                        if row.embedding is not None
                        else 2.0,
                        -row.sequence,
                    ),
                )
            else:
                rows = sorted(rows, key=lambda row: -row.sequence)
            return tuple(
                RetrievedMemory(
                    sequence=row.sequence,
                    turn=deepcopy(row.turn),
                    score=(
                        1.0 - _cosine_distance(query_embedding, row.embedding)
                        if query_embedding is not None and row.embedding is not None
                        else None
                    ),
                )
                for row in rows[:limit]
            )

    async def pending_archive_embeddings(
        self,
        *,
        limit: int,
    ) -> tuple[ArchivedConversationTurn, ...]:
        async with self._lock:
            pending = [
                row
                for rows in self._archives.values()
                for row in rows
                if row.embedding is None
            ][:limit]
            return tuple(
                ArchivedConversationTurn(
                    archive_id=row.archive_id,
                    sequence=row.sequence,
                    scope=row.scope,
                    turn=deepcopy(row.turn),
                )
                for row in pending
            )

    async def store_archive_embedding(
        self,
        archive_id: int,
        embedding: tuple[float, ...],
    ) -> None:
        async with self._lock:
            for rows in self._archives.values():
                for row in rows:
                    if row.archive_id == archive_id:
                        row.embedding = tuple(embedding)
                        return

    async def claim_summary_job(self) -> SummaryJob | None:
        async with self._lock:
            for scope_key, pending in self._summary_jobs.items():
                if pending.leased:
                    continue
                context_row = self._contexts.get(scope_key)
                if context_row is None:
                    continue
                context = context_row.current
                base = (
                    context.summary.summarized_through_sequence
                    if context.summary is not None
                    else 0
                )
                turns = tuple(
                    deepcopy(row.turn)
                    for row in self._archives.get(scope_key, [])
                    if base < row.sequence <= pending.target_cursor
                )
                if not turns:
                    self._summary_jobs.pop(scope_key, None)
                    return None
                pending.leased = True
                return SummaryJob(
                    scope=pending.scope,
                    base_cursor=base,
                    target_cursor=pending.target_cursor,
                    attempts=pending.attempts,
                    existing_summary=deepcopy(context.summary),
                    existing_structured_state=deepcopy(context.structured_state),
                    source_turns=turns,
                )
        return None

    async def complete_summary_job(
        self,
        job: SummaryJob,
        *,
        summary: ContextSummary,
        structured_state: StructuredConversationState,
    ) -> bool:
        if summary.summarized_through_sequence != job.target_cursor:
            raise ValueError("verified summary must advance exactly to the job target")
        scope_key = committed_scope_key(job.scope)
        async with self._lock:
            row = self._contexts.get(scope_key)
            pending = self._summary_jobs.get(scope_key)
            if row is None or pending is None:
                return False
            current_cursor = (
                row.current.summary.summarized_through_sequence
                if row.current.summary is not None
                else 0
            )
            if current_cursor != job.base_cursor:
                pending.leased = False
                return False
            row.current = ConversationContext(
                turns=row.current.turns,
                summary=deepcopy(summary),
                structured_state=deepcopy(structured_state),
            )
            if pending.target_cursor > job.target_cursor:
                pending.leased = False
                pending.attempts = 0
            else:
                self._summary_jobs.pop(scope_key, None)
            return True

    async def fail_summary_job(self, job: SummaryJob, error_code: str) -> None:
        del error_code
        async with self._lock:
            pending = self._summary_jobs.get(committed_scope_key(job.scope))
            if pending is not None:
                pending.attempts += 1
                pending.leased = False

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
            self._archives.pop(scope_key, None)
            self._summary_jobs.pop(scope_key, None)
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
        embedding_dimensions: int = 3_072,
        summary_lease_seconds: int = 120,
        summary_retry_base_seconds: int = 5,
    ) -> None:
        self._dsn = dsn
        self._execution_ttl_seconds = execution_ttl_seconds
        self._receipt_ttl_seconds = receipt_ttl_seconds
        self._closed_retention_seconds = closed_retention_seconds
        if embedding_dimensions <= 0:
            raise ValueError("embedding_dimensions must be positive")
        self._embedding_dimensions = embedding_dimensions
        self._summary_lease_seconds = summary_lease_seconds
        self._summary_retry_base_seconds = summary_retry_base_seconds

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
            await connection.execute("CREATE EXTENSION IF NOT EXISTS vector")
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
            await connection.execute(f"""
                CREATE TABLE IF NOT EXISTS ai_conversation_turn_archive (
                    archive_id BIGSERIAL PRIMARY KEY,
                    scope_key CHAR(64) NOT NULL REFERENCES ai_committed_contexts(scope_key)
                        ON DELETE CASCADE,
                    turn_sequence BIGINT NOT NULL,
                    turn_id VARCHAR(128) NOT NULL,
                    user_message TEXT NOT NULL,
                    assistant_message TEXT NOT NULL,
                    embedding vector({self._embedding_dimensions}),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (scope_key, turn_sequence),
                    UNIQUE (scope_key, turn_id)
                )
            """)
            await connection.execute("""
                CREATE INDEX IF NOT EXISTS ai_conversation_archive_scope_sequence
                ON ai_conversation_turn_archive (scope_key, turn_sequence DESC)
            """)
            await connection.execute("""
                CREATE TABLE IF NOT EXISTS ai_context_summary_jobs (
                    scope_key CHAR(64) PRIMARY KEY REFERENCES ai_committed_contexts(scope_key)
                        ON DELETE CASCADE,
                    target_cursor BIGINT NOT NULL,
                    status VARCHAR(16) NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    lease_until TIMESTAMPTZ,
                    last_error_code VARCHAR(128),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    CHECK (status IN ('pending', 'processing'))
                )
            """)
            await connection.execute("""
                CREATE INDEX IF NOT EXISTS ai_context_summary_jobs_due
                ON ai_context_summary_jobs (next_attempt_at, updated_at)
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
            rows = await cursor.fetchall()
            return tuple(row["thread_key"] for row in rows)

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
        completed_turn: ConversationTurn | None = None,
    ) -> Mapping[str, Any]:
        from psycopg.types.json import Jsonb

        _validate_request_identity(request_id, payload_digest)
        scope_key = committed_scope_key(scope)
        terminal_copy = deepcopy(dict(terminal))
        archived_turn = completed_turn or next(
            (turn for turn in reversed(context.turns) if turn.turn_id == request_id),
            None,
        )
        if archived_turn is None:
            raise ValueError("completed context must include the committed turn")
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
                effective_context = context
                if existing is not None:
                    stored_context = ConversationContext.from_state(
                        existing["current_context"]
                    )
                    if _summary_cursor(stored_context) > _summary_cursor(context):
                        effective_context = ConversationContext(
                            turns=context.turns,
                            summary=stored_context.summary,
                            structured_state=stored_context.structured_state,
                        )
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
                            Jsonb(effective_context.to_state()),
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
                            Jsonb(effective_context.to_state()),
                            request_id,
                            CONTEXT_SCHEMA_VERSION,
                            next_version,
                            scope_key,
                        ),
                    )
                cursor = await connection.execute(
                    """
                    SELECT COALESCE(MAX(turn_sequence), 0) + 1 AS next_sequence
                    FROM ai_conversation_turn_archive
                    WHERE scope_key = %s
                    """,
                    (scope_key,),
                )
                sequence_row = await cursor.fetchone()
                next_sequence = int(sequence_row["next_sequence"])
                await connection.execute(
                    """
                    INSERT INTO ai_conversation_turn_archive (
                        scope_key, turn_sequence, turn_id,
                        user_message, assistant_message
                    ) VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (scope_key, turn_id) DO NOTHING
                    """,
                    (
                        scope_key,
                        next_sequence,
                        archived_turn.turn_id,
                        archived_turn.user_message,
                        archived_turn.assistant_message,
                    ),
                )
                cursor = await connection.execute(
                    """
                    SELECT user_message, assistant_message
                    FROM ai_conversation_turn_archive
                    WHERE scope_key = %s AND turn_id = %s
                    """,
                    (scope_key, archived_turn.turn_id),
                )
                archived = await cursor.fetchone()
                if archived is None or (
                    archived["user_message"] != archived_turn.user_message
                    or archived["assistant_message"] != archived_turn.assistant_message
                ):
                    raise ContextCommitConflict(
                        "turn id was already archived differently"
                    )

                retained_turn_ids = [turn.turn_id for turn in effective_context.turns]
                summary_cursor = (
                    effective_context.summary.summarized_through_sequence
                    if effective_context.summary is not None
                    else 0
                )
                cursor = await connection.execute(
                    """
                    SELECT MAX(turn_sequence) AS target_cursor
                    FROM ai_conversation_turn_archive
                    WHERE scope_key = %s
                      AND turn_sequence > %s
                      AND NOT (turn_id = ANY(%s))
                    """,
                    (scope_key, summary_cursor, retained_turn_ids),
                )
                target_row = await cursor.fetchone()
                target_cursor = target_row["target_cursor"]
                if target_cursor is not None:
                    await connection.execute(
                        """
                        INSERT INTO ai_context_summary_jobs (
                            scope_key, target_cursor, status, next_attempt_at
                        ) VALUES (%s, %s, 'pending', NOW())
                        ON CONFLICT (scope_key) DO UPDATE
                        SET target_cursor = GREATEST(
                                ai_context_summary_jobs.target_cursor,
                                EXCLUDED.target_cursor
                            ),
                            updated_at = NOW()
                        """,
                        (scope_key, int(target_cursor)),
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

    async def retrieve_memories(
        self,
        scope: ContextScope,
        query_embedding: tuple[float, ...] | None,
        *,
        exclude_turn_ids: tuple[str, ...],
        limit: int,
    ) -> tuple[RetrievedMemory, ...]:
        if limit <= 0:
            return ()
        if query_embedding is not None:
            self._validate_embedding(query_embedding)
        scope_key = committed_scope_key(scope)
        vector = _vector_literal(query_embedding) if query_embedding is not None else None
        async with await self._connect(autocommit=True) as connection:
            cursor = await connection.execute(
                """
                SELECT archive.turn_sequence, archive.turn_id,
                       archive.user_message, archive.assistant_message,
                       CASE
                           WHEN %s::vector IS NULL OR archive.embedding IS NULL THEN NULL
                           ELSE 1 - (archive.embedding <=> %s::vector)
                       END AS score
                FROM ai_conversation_turn_archive AS archive
                JOIN ai_committed_contexts AS context
                  ON context.scope_key = archive.scope_key
                WHERE archive.scope_key = %s
                  AND NOT (archive.turn_id = ANY(%s))
                  AND (context.delete_after IS NULL OR context.delete_after > NOW())
                ORDER BY
                    (
                        archive.turn_sequence > COALESCE(
                            (context.current_context->'summary'->>
                                'summarized_through_sequence')::BIGINT,
                            0
                        )
                    ) DESC,
                    CASE
                        WHEN %s::vector IS NULL OR archive.embedding IS NULL THEN 2.0
                        ELSE archive.embedding <=> %s::vector
                    END,
                    archive.turn_sequence DESC
                LIMIT %s
                """,
                (
                    vector,
                    vector,
                    scope_key,
                    list(exclude_turn_ids),
                    vector,
                    vector,
                    limit,
                ),
            )
            rows = await cursor.fetchall()
        return tuple(
            RetrievedMemory(
                sequence=int(row["turn_sequence"]),
                turn=ConversationTurn(
                    turn_id=row["turn_id"],
                    user_message=row["user_message"],
                    assistant_message=row["assistant_message"],
                ),
                score=float(row["score"]) if row["score"] is not None else None,
            )
            for row in rows
        )

    async def pending_archive_embeddings(
        self,
        *,
        limit: int,
    ) -> tuple[ArchivedConversationTurn, ...]:
        if limit <= 0:
            return ()
        async with await self._connect(autocommit=True) as connection:
            cursor = await connection.execute(
                """
                SELECT archive.archive_id, archive.turn_sequence,
                       archive.user_message, archive.assistant_message,
                       archive.turn_id, context.session_id, context.thread_id
                FROM ai_conversation_turn_archive AS archive
                JOIN ai_committed_contexts AS context
                  ON context.scope_key = archive.scope_key
                WHERE archive.embedding IS NULL
                  AND (context.delete_after IS NULL OR context.delete_after > NOW())
                ORDER BY archive.archive_id
                LIMIT %s
                """,
                (limit,),
            )
            rows = await cursor.fetchall()
        return tuple(
            ArchivedConversationTurn(
                archive_id=int(row["archive_id"]),
                sequence=int(row["turn_sequence"]),
                scope=ContextScope(
                    session_id=int(row["session_id"]),
                    thread_id=row["thread_id"],
                ),
                turn=ConversationTurn(
                    turn_id=row["turn_id"],
                    user_message=row["user_message"],
                    assistant_message=row["assistant_message"],
                ),
            )
            for row in rows
        )

    async def store_archive_embedding(
        self,
        archive_id: int,
        embedding: tuple[float, ...],
    ) -> None:
        self._validate_embedding(embedding)
        async with await self._connect(autocommit=True) as connection:
            await connection.execute(
                """
                UPDATE ai_conversation_turn_archive
                SET embedding = %s::vector
                WHERE archive_id = %s AND embedding IS NULL
                """,
                (_vector_literal(embedding), archive_id),
            )

    def _validate_embedding(self, embedding: tuple[float, ...]) -> None:
        if len(embedding) != self._embedding_dimensions:
            raise ValueError("conversation embedding has an unexpected dimension")
        if any(not isinstance(value, (int, float)) for value in embedding):
            raise TypeError("conversation embedding must contain numbers")

    async def claim_summary_job(self) -> SummaryJob | None:
        async with await self._connect() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    SELECT job.scope_key, job.target_cursor, job.attempts,
                           context.session_id, context.thread_id,
                           context.current_context
                    FROM ai_context_summary_jobs AS job
                    JOIN ai_committed_contexts AS context
                      ON context.scope_key = job.scope_key
                    WHERE job.next_attempt_at <= NOW()
                      AND (
                          job.status = 'pending'
                          OR job.lease_until IS NULL
                          OR job.lease_until <= NOW()
                      )
                      AND (context.delete_after IS NULL OR context.delete_after > NOW())
                    ORDER BY job.next_attempt_at, job.updated_at
                    FOR UPDATE OF job SKIP LOCKED
                    LIMIT 1
                    """
                )
                row = await cursor.fetchone()
                if row is None:
                    return None
                await connection.execute(
                    """
                    UPDATE ai_context_summary_jobs
                    SET status = 'processing',
                        lease_until = NOW() + (%s * INTERVAL '1 second'),
                        updated_at = NOW()
                    WHERE scope_key = %s
                    """,
                    (self._summary_lease_seconds, row["scope_key"]),
                )
                context = ConversationContext.from_state(row["current_context"])
                base_cursor = (
                    context.summary.summarized_through_sequence
                    if context.summary is not None
                    else 0
                )
                target_cursor = int(row["target_cursor"])
                if target_cursor <= base_cursor:
                    await connection.execute(
                        "DELETE FROM ai_context_summary_jobs WHERE scope_key = %s",
                        (row["scope_key"],),
                    )
                    return None
                source_cursor = await connection.execute(
                    """
                    SELECT turn_id, user_message, assistant_message
                    FROM ai_conversation_turn_archive
                    WHERE scope_key = %s
                      AND turn_sequence > %s
                      AND turn_sequence <= %s
                    ORDER BY turn_sequence
                    """,
                    (row["scope_key"], base_cursor, target_cursor),
                )
                source_rows = await source_cursor.fetchall()
                if not source_rows:
                    raise RuntimeError("summary job source archive is missing")
                return SummaryJob(
                    scope=ContextScope(
                        session_id=int(row["session_id"]),
                        thread_id=row["thread_id"],
                    ),
                    base_cursor=base_cursor,
                    target_cursor=target_cursor,
                    attempts=int(row["attempts"]),
                    existing_summary=context.summary,
                    existing_structured_state=context.structured_state,
                    source_turns=tuple(
                        ConversationTurn(
                            turn_id=source["turn_id"],
                            user_message=source["user_message"],
                            assistant_message=source["assistant_message"],
                        )
                        for source in source_rows
                    ),
                )

    async def complete_summary_job(
        self,
        job: SummaryJob,
        *,
        summary: ContextSummary,
        structured_state: StructuredConversationState,
    ) -> bool:
        from psycopg.types.json import Jsonb

        if summary.summarized_through_sequence != job.target_cursor:
            raise ValueError("verified summary must advance exactly to the job target")
        scope_key = committed_scope_key(job.scope)
        async with await self._connect() as connection:
            async with connection.transaction():
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (scope_key,),
                )
                cursor = await connection.execute(
                    """
                    SELECT current_context
                    FROM ai_committed_contexts
                    WHERE scope_key = %s
                    FOR UPDATE
                    """,
                    (scope_key,),
                )
                row = await cursor.fetchone()
                if row is None:
                    return False
                current = ConversationContext.from_state(row["current_context"])
                current_cursor = (
                    current.summary.summarized_through_sequence
                    if current.summary is not None
                    else 0
                )
                if current_cursor != job.base_cursor:
                    await connection.execute(
                        """
                        UPDATE ai_context_summary_jobs
                        SET status = 'pending', lease_until = NULL,
                            next_attempt_at = NOW(), updated_at = NOW()
                        WHERE scope_key = %s
                        """,
                        (scope_key,),
                    )
                    return False
                updated = ConversationContext(
                    turns=current.turns,
                    summary=summary,
                    structured_state=structured_state,
                )
                await connection.execute(
                    """
                    UPDATE ai_committed_contexts
                    SET current_context = %s, updated_at = NOW()
                    WHERE scope_key = %s
                    """,
                    (Jsonb(updated.to_state()), scope_key),
                )
                cursor = await connection.execute(
                    """
                    SELECT target_cursor
                    FROM ai_context_summary_jobs
                    WHERE scope_key = %s
                    FOR UPDATE
                    """,
                    (scope_key,),
                )
                pending = await cursor.fetchone()
                if pending is None or int(pending["target_cursor"]) <= job.target_cursor:
                    await connection.execute(
                        "DELETE FROM ai_context_summary_jobs WHERE scope_key = %s",
                        (scope_key,),
                    )
                else:
                    await connection.execute(
                        """
                        UPDATE ai_context_summary_jobs
                        SET status = 'pending', attempts = 0, lease_until = NULL,
                            next_attempt_at = NOW(), last_error_code = NULL,
                            updated_at = NOW()
                        WHERE scope_key = %s
                        """,
                        (scope_key,),
                    )
                return True

    async def fail_summary_job(self, job: SummaryJob, error_code: str) -> None:
        bounded_code = re.sub(r"[^a-z0-9_.-]", "_", error_code.lower())[:128]
        delay = min(
            self._summary_retry_base_seconds * (2 ** min(job.attempts, 8)),
            3_600,
        )
        async with await self._connect(autocommit=True) as connection:
            await connection.execute(
                """
                UPDATE ai_context_summary_jobs
                SET status = 'pending', attempts = attempts + 1,
                    lease_until = NULL,
                    next_attempt_at = NOW() + (%s * INTERVAL '1 second'),
                    last_error_code = %s,
                    updated_at = NOW()
                WHERE scope_key = %s
                """,
                (delay, bounded_code or "summary_failed", committed_scope_key(job.scope)),
            )

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


def _vector_literal(values: tuple[float, ...] | None) -> str | None:
    if values is None:
        return None
    return "[" + ",".join(format(float(value), ".17g") for value in values) + "]"


def _summary_cursor(context: ConversationContext) -> int:
    return (
        context.summary.summarized_through_sequence
        if context.summary is not None
        else 0
    )


def _cosine_distance(
    left: tuple[float, ...],
    right: tuple[float, ...] | None,
) -> float:
    if right is None or len(left) != len(right):
        return 2.0
    left_norm = sum(value * value for value in left) ** 0.5
    right_norm = sum(value * value for value in right) ** 0.5
    if left_norm == 0.0 or right_norm == 0.0:
        return 1.0
    similarity = sum(a * b for a, b in zip(left, right, strict=True)) / (
        left_norm * right_norm
    )
    return 1.0 - max(-1.0, min(1.0, similarity))
