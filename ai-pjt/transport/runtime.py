"""Request budget, checkpoint, worker, and callback delivery primitives."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import random
from time import monotonic
from typing import Any, Protocol

import httpx
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from core.monitoring import OperationStatus
from core.runtime_instrumentation_adapter import RuntimeInstrumentationAdapter

from .contracts import (
    CallbackError,
    ChatAccepted,
    ChatV2Request,
    CompletedCallback,
    FailedCallback,
    ServiceContractError,
)


ACTIVE_TTL = timedelta(minutes=10)
TERMINAL_TTL = timedelta(hours=24)
CALLBACK_RETRYABLE_STATUSES = {408, 429, 502, 503, 504}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def request_digest(request: ChatV2Request) -> str:
    encoded = json.dumps(
        request.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ExecutionBudget:
    deadline_at: datetime
    started_at_monotonic: float
    task_limit: int = 3
    tasks_used: int = 0
    tool_calls_used: int = 0
    tool_call_limit: int = 3
    retries_used: int = 0

    @classmethod
    def from_request(cls, request: ChatV2Request) -> "ExecutionBudget":
        return cls(
            deadline_at=request.deadline_at.astimezone(timezone.utc),
            started_at_monotonic=monotonic(),
        )

    def remaining_seconds(self, *, now: datetime | None = None) -> float:
        current = (now or utc_now()).astimezone(timezone.utc)
        return max(0.0, (self.deadline_at - current).total_seconds())

    def require_time(self) -> None:
        if self.remaining_seconds() <= 0:
            raise TimeoutError("request deadline exceeded")

    def consume_task(self) -> "ExecutionBudget":
        if self.tasks_used >= self.task_limit:
            raise RuntimeError("task execution budget exhausted")
        return replace(self, tasks_used=self.tasks_used + 1)

    def consume_tool_call(self) -> "ExecutionBudget":
        return replace(self, tool_calls_used=self.tool_calls_used + 1)

    def consume_retry(self) -> "ExecutionBudget":
        return replace(self, retries_used=self.retries_used + 1)

    def to_state(self) -> dict[str, Any]:
        return {
            "deadline_at": self.deadline_at.astimezone(timezone.utc).isoformat(),
            "task_limit": self.task_limit,
            "tasks_used": self.tasks_used,
            "tool_calls_used": self.tool_calls_used,
            "tool_call_limit": self.tool_call_limit,
            "retries_used": self.retries_used,
        }


@dataclass(frozen=True)
class RequestCheckpoint:
    request_id: str
    payload_digest: str
    status: str
    terminal_payload: dict[str, Any] | None = None
    callback_delivered: bool = False


@dataclass(frozen=True)
class ClaimResult:
    created: bool
    checkpoint: RequestCheckpoint


@dataclass(frozen=True, slots=True)
class CallbackDeliveryContext:
    request_id: str
    thread_id: str
    plan_id: str
    step_id: str

    @classmethod
    def from_request(cls, request: ChatV2Request) -> "CallbackDeliveryContext":
        return cls(
            request_id=request.request_id,
            thread_id=request.thread_id,
            plan_id=f"plan-{request.request_id}",
            step_id=request.step_id,
        )


class RequestCheckpointStore(Protocol):
    async def setup(self) -> None: ...

    async def claim(
        self,
        request: ChatV2Request,
        payload_digest: str,
    ) -> ClaimResult: ...

    async def mark_running(self, request_id: str) -> None: ...

    async def save_terminal(
        self,
        request_id: str,
        payload: Mapping[str, Any],
    ) -> RequestCheckpoint: ...

    async def mark_callback_delivered(self, request_id: str) -> None: ...

    async def get(self, request_id: str) -> RequestCheckpoint | None: ...


class InMemoryRequestCheckpointStore:
    """Deterministic store for unit tests and isolated development."""

    def __init__(self) -> None:
        self._entries: dict[str, RequestCheckpoint] = {}
        self._lock = asyncio.Lock()

    async def setup(self) -> None:
        return None

    async def claim(
        self,
        request: ChatV2Request,
        payload_digest: str,
    ) -> ClaimResult:
        async with self._lock:
            current = self._entries.get(request.request_id)
            if current is not None:
                if current.payload_digest != payload_digest:
                    raise ServiceContractError(
                        status_code=409,
                        code="REQUEST_ID_CONFLICT",
                        message="같은 request_id의 요청 내용이 다릅니다.",
                    )
                return ClaimResult(created=False, checkpoint=current)
            checkpoint = RequestCheckpoint(
                request_id=request.request_id,
                payload_digest=payload_digest,
                status="accepted",
            )
            self._entries[request.request_id] = checkpoint
            return ClaimResult(created=True, checkpoint=checkpoint)

    async def mark_running(self, request_id: str) -> None:
        async with self._lock:
            current = self._require(request_id)
            if current.status == "accepted":
                self._entries[request_id] = replace(current, status="running")

    async def save_terminal(
        self,
        request_id: str,
        payload: Mapping[str, Any],
    ) -> RequestCheckpoint:
        frozen = json.loads(json.dumps(dict(payload), ensure_ascii=False))
        async with self._lock:
            current = self._require(request_id)
            if current.terminal_payload is not None:
                if current.terminal_payload != frozen:
                    raise RuntimeError("terminal checkpoint is immutable")
                return current
            updated = replace(
                current,
                status=str(frozen["status"]),
                terminal_payload=frozen,
            )
            self._entries[request_id] = updated
            return updated

    async def mark_callback_delivered(self, request_id: str) -> None:
        async with self._lock:
            current = self._require(request_id)
            self._entries[request_id] = replace(current, callback_delivered=True)

    async def get(self, request_id: str) -> RequestCheckpoint | None:
        async with self._lock:
            return self._entries.get(request_id)

    def _require(self, request_id: str) -> RequestCheckpoint:
        checkpoint = self._entries.get(request_id)
        if checkpoint is None:
            raise KeyError(request_id)
        return checkpoint


class PostgresRequestCheckpointStore:
    """Persistent request-id and terminal-payload checkpoint."""

    _SCHEMA = """
        CREATE TABLE IF NOT EXISTS ai_request_checkpoints (
            request_id TEXT PRIMARY KEY,
            payload_digest TEXT NOT NULL,
            request_payload JSONB NOT NULL,
            status TEXT NOT NULL,
            terminal_payload JSONB,
            callback_delivered BOOLEAN NOT NULL DEFAULT FALSE,
            expires_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    async def setup(self) -> None:
        async with await AsyncConnection.connect(self._dsn, autocommit=True) as conn:
            await conn.execute(self._SCHEMA)
            await conn.execute(
                "DELETE FROM ai_request_checkpoints WHERE expires_at <= NOW()"
            )

    async def claim(
        self,
        request: ChatV2Request,
        payload_digest: str,
    ) -> ClaimResult:
        payload = request.model_dump(mode="json")
        expires_at = utc_now() + ACTIVE_TTL
        async with await AsyncConnection.connect(self._dsn) as conn:
            cursor = await conn.execute(
                """
                INSERT INTO ai_request_checkpoints (
                    request_id, payload_digest, request_payload, status, expires_at
                ) VALUES (%s, %s, %s, 'accepted', %s)
                ON CONFLICT (request_id) DO NOTHING
                RETURNING request_id
                """,
                (request.request_id, payload_digest, Jsonb(payload), expires_at),
            )
            created = await cursor.fetchone() is not None
            cursor = await conn.execute(
                """
                SELECT payload_digest, status, terminal_payload, callback_delivered
                FROM ai_request_checkpoints WHERE request_id = %s
                """,
                (request.request_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("claimed request checkpoint disappeared")
        if row[0] != payload_digest:
            raise ServiceContractError(
                status_code=409,
                code="REQUEST_ID_CONFLICT",
                message="같은 request_id의 요청 내용이 다릅니다.",
            )
        return ClaimResult(
            created=created,
            checkpoint=RequestCheckpoint(
                request_id=request.request_id,
                payload_digest=row[0],
                status=row[1],
                terminal_payload=row[2],
                callback_delivered=row[3],
            ),
        )

    async def mark_running(self, request_id: str) -> None:
        await self._execute(
            """
            UPDATE ai_request_checkpoints SET status = 'running', updated_at = NOW()
            WHERE request_id = %s AND status = 'accepted'
            """,
            (request_id,),
        )

    async def save_terminal(
        self,
        request_id: str,
        payload: Mapping[str, Any],
    ) -> RequestCheckpoint:
        frozen = dict(payload)
        status = str(frozen["status"])
        async with await AsyncConnection.connect(self._dsn) as conn:
            await conn.execute(
                """
                UPDATE ai_request_checkpoints
                SET status = %s, terminal_payload = %s, expires_at = %s,
                    updated_at = NOW()
                WHERE request_id = %s AND terminal_payload IS NULL
                """,
                (status, Jsonb(frozen), utc_now() + TERMINAL_TTL, request_id),
            )
            cursor = await conn.execute(
                """
                SELECT payload_digest, status, terminal_payload, callback_delivered
                FROM ai_request_checkpoints WHERE request_id = %s
                """,
                (request_id,),
            )
            row = await cursor.fetchone()
        if row is None or row[2] is None:
            raise RuntimeError("request checkpoint not found")
        if row[2] != frozen:
            raise RuntimeError("terminal checkpoint is immutable")
        return RequestCheckpoint(request_id, row[0], row[1], row[2], row[3])

    async def mark_callback_delivered(self, request_id: str) -> None:
        await self._execute(
            """
            UPDATE ai_request_checkpoints
            SET callback_delivered = TRUE, updated_at = NOW()
            WHERE request_id = %s AND terminal_payload IS NOT NULL
            """,
            (request_id,),
        )

    async def get(self, request_id: str) -> RequestCheckpoint | None:
        async with await AsyncConnection.connect(self._dsn, autocommit=True) as conn:
            cursor = await conn.execute(
                """
                SELECT payload_digest, status, terminal_payload, callback_delivered
                FROM ai_request_checkpoints
                WHERE request_id = %s AND expires_at > NOW()
                """,
                (request_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return RequestCheckpoint(request_id, row[0], row[1], row[2], row[3])

    async def _execute(self, query: str, params: tuple[Any, ...]) -> None:
        async with await AsyncConnection.connect(self._dsn, autocommit=True) as conn:
            await conn.execute(query, params)


class CallbackClient:
    def __init__(
        self,
        *,
        url: str,
        bearer_token: str,
        instrumentation: RuntimeInstrumentationAdapter,
        timeout_seconds: float = 5.0,
        max_attempts: int = 4,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
        client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient,
        monotonic_clock: Callable[[], float] = monotonic,
    ) -> None:
        if not isinstance(instrumentation, RuntimeInstrumentationAdapter):
            raise TypeError("callback instrumentation adapter is required")
        self._url = url
        self._bearer_token = bearer_token
        self._instrumentation = instrumentation
        self._timeout_seconds = timeout_seconds
        self._max_attempts = max_attempts
        self._sleeper = sleeper
        self._jitter = jitter
        self._client_factory = client_factory
        self._monotonic = monotonic_clock

    async def send(
        self,
        payload: Mapping[str, Any],
        *,
        context: CallbackDeliveryContext,
    ) -> None:
        if not isinstance(context, CallbackDeliveryContext):
            raise TypeError("callback delivery context is required")
        frozen_payload = json.dumps(
            dict(payload),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            started_at = self._monotonic()
            try:
                async with self._client_factory(timeout=self._timeout_seconds) as client:
                    response = await client.post(
                        self._url,
                        content=frozen_payload,
                        headers={
                            "Authorization": f"Bearer {self._bearer_token}",
                            "Content-Type": "application/json",
                        },
                    )
                if response.status_code in CALLBACK_RETRYABLE_STATUSES:
                    raise httpx.HTTPStatusError(
                        "retryable callback response",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                self._record_attempt(
                    context=context,
                    status=OperationStatus.SUCCEEDED,
                    started_at=started_at,
                )
                return
            except httpx.TimeoutException as exc:
                last_error = exc
                self._record_attempt(
                    context=context,
                    status=OperationStatus.TIMEOUT,
                    started_at=started_at,
                    error_code="callback_timeout",
                )
            except httpx.HTTPStatusError as exc:
                last_error = exc
                self._record_attempt(
                    context=context,
                    status=OperationStatus.FAILED,
                    started_at=started_at,
                    error_code=f"callback_http_{exc.response.status_code}",
                )
                if exc.response.status_code not in CALLBACK_RETRYABLE_STATUSES:
                    raise
            except httpx.TransportError as exc:
                last_error = exc
                self._record_attempt(
                    context=context,
                    status=OperationStatus.FAILED,
                    started_at=started_at,
                    error_code="callback_transport_error",
                )
            if attempt < self._max_attempts:
                backoff = 2 ** (attempt - 1)
                await self._sleeper(backoff + min(0.25, self._jitter() * 0.25))
        if last_error is not None:
            raise last_error
        raise RuntimeError("callback delivery exhausted")

    def _record_attempt(
        self,
        *,
        context: CallbackDeliveryContext,
        status: OperationStatus,
        started_at: float,
        error_code: str | None = None,
    ) -> None:
        duration_ms = max(0, int((self._monotonic() - started_at) * 1000))
        self._instrumentation.record_callback_attempt(
            request_id=context.request_id,
            thread_id=context.thread_id,
            plan_id=context.plan_id,
            step_id=context.step_id,
            status=status,
            duration_ms=duration_ms,
            error_code=error_code,
        )


GraphProcessor = Callable[
    [ChatV2Request, ExecutionBudget],
    Awaitable[Mapping[str, Any]],
]


class CallbackPayloadMaskerPort(Protocol):
    def mask(self, payload: Mapping[str, Any]) -> dict[str, Any]: ...


class AsyncChatTransport:
    def __init__(
        self,
        *,
        checkpoint_store: RequestCheckpointStore,
        processor: GraphProcessor,
        callback_client: CallbackClient,
        callback_masker: CallbackPayloadMaskerPort,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if not callable(getattr(callback_masker, "mask", None)):
            raise TypeError("callback payload masker is required")
        self._checkpoint_store = checkpoint_store
        self._processor = processor
        self._callback_client = callback_client
        self._callback_masker = callback_masker
        self._clock = clock

    async def accept(
        self,
        request: ChatV2Request,
        schedule: Callable[[Callable[..., Awaitable[Any]], Any], None],
    ) -> ChatAccepted:
        claim = await self._checkpoint_store.claim(request, request_digest(request))
        if claim.created:
            schedule(self.run, request)
        return ChatAccepted(request_id=request.request_id)

    async def run(self, request: ChatV2Request) -> dict[str, Any]:
        checkpoint = await self._checkpoint_store.get(request.request_id)
        if checkpoint is not None and checkpoint.terminal_payload is not None:
            payload = self._callback_masker.mask(checkpoint.terminal_payload)
        else:
            await self._checkpoint_store.mark_running(request.request_id)
            budget = ExecutionBudget.from_request(request)
            try:
                budget.require_time()
                output = await asyncio.wait_for(
                    self._processor(request, budget),
                    timeout=budget.remaining_seconds(),
                )
                raw_payload = CompletedCallback(
                    request_id=request.request_id,
                    session_id=request.session_id,
                    thread_id=request.thread_id,
                    completed_at=self._clock(),
                    output=dict(output),
                ).model_dump(mode="json")
            except (TimeoutError, asyncio.TimeoutError):
                raw_payload = self._failed_payload(
                    request,
                    code="LLM_TIMEOUT",
                    message="AI 응답 생성 시간이 초과되었습니다.",
                    retryable=True,
                )
            except Exception:
                raw_payload = self._failed_payload(
                    request,
                    code="INTERNAL_ERROR",
                    message="AI 응답 생성에 실패했습니다.",
                    retryable=False,
                )
            payload = self._callback_masker.mask(raw_payload)
            checkpoint = await self._checkpoint_store.save_terminal(
                request.request_id,
                payload,
            )
            payload = checkpoint.terminal_payload or payload

        await self._callback_client.send(
            payload,
            context=CallbackDeliveryContext.from_request(request),
        )
        await self._checkpoint_store.mark_callback_delivered(request.request_id)
        return payload

    def _failed_payload(
        self,
        request: ChatV2Request,
        *,
        code: str,
        message: str,
        retryable: bool,
    ) -> dict[str, Any]:
        return FailedCallback(
            request_id=request.request_id,
            session_id=request.session_id,
            thread_id=request.thread_id,
            completed_at=self._clock(),
            error=CallbackError(
                code=code,
                message=message,
                retryable=retryable,
            ),
        ).model_dump(mode="json")
