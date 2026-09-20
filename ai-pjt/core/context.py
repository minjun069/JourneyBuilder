from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from math import ceil
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Protocol
import json
import unicodedata


MESSAGE_OVERHEAD_UNITS = 4
CONTEXT_SCHEMA_VERSION = "3.0"
# LangGraph reserves the empty namespace for a top-level compiled graph. Schema
# versioning stays in the opaque thread key and embedded state instead.
CONTEXT_CHECKPOINT_NAMESPACE = ""


def _freeze_facts(facts: Mapping[str, Any]) -> Mapping[str, Any]:
    copied: dict[str, Any] = {}
    for path, value in facts.items():
        if not isinstance(path, str) or not path.strip():
            raise ValueError("confirmed fact paths must be non-empty strings")
        copied[path] = deepcopy(value)
    return MappingProxyType(copied)


@dataclass(frozen=True)
class ContextScope:
    """Django-owned session and opaque conversation identity.

    Django validates the user/session ownership before issuing ``thread_id``.
    AI code deliberately does not parse a user identifier from that value.
    """

    session_id: int
    thread_id: str

    def __post_init__(self) -> None:
        if isinstance(self.session_id, bool) or not isinstance(self.session_id, int):
            raise TypeError("session_id must be an integer")
        if self.session_id <= 0:
            raise ValueError("session_id must be greater than 0")
        if not isinstance(self.thread_id, str):
            raise TypeError("thread_id must be a string")
        if not self.thread_id.strip():
            raise ValueError("thread_id must not be blank")

    def to_state(self) -> dict[str, Any]:
        return {
            "schema_version": CONTEXT_SCHEMA_VERSION,
            "session_id": self.session_id,
            "thread_id": self.thread_id,
        }

    @classmethod
    def from_state(cls, payload: Mapping[str, Any]) -> ContextScope:
        if payload.get("schema_version") != CONTEXT_SCHEMA_VERSION:
            raise ContextIsolationError("unsupported context scope schema")
        return cls(
            session_id=payload.get("session_id"),
            thread_id=payload.get("thread_id"),
        )


class ContextIsolationError(RuntimeError):
    """Raised when persisted state is not bound to the requested scope."""


def checkpoint_thread_key(scope: ContextScope, request_id: str) -> str:
    """Return an opaque, request-scoped key for one transient graph run."""

    if not isinstance(request_id, str) or not request_id.strip():
        raise ValueError("request_id must be a non-empty string")

    canonical = json.dumps(
        {
            "session_id": scope.session_id,
            "thread_id": scope.thread_id,
            "request_id": request_id,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"ai-execution-v1:{sha256(canonical).hexdigest()}"


def checkpoint_config(
    scope: ContextScope,
    request_id: str,
) -> dict[str, dict[str, str]]:
    """Build a request-scoped LangGraph checkpoint configuration."""

    return {
        "configurable": {
            "thread_id": checkpoint_thread_key(scope, request_id),
            "checkpoint_ns": CONTEXT_CHECKPOINT_NAMESPACE,
        }
    }


def validate_persisted_scope(
    payload: Mapping[str, Any],
    expected: ContextScope,
) -> None:
    """Fail closed when a checkpoint claims a different session/thread scope."""

    persisted = ContextScope.from_state(payload)
    if persisted != expected:
        raise ContextIsolationError("persisted context scope does not match request")


class AsyncContextCheckpointer(Protocol):
    async def adelete_thread(self, thread_id: str) -> None:
        """Delete every checkpoint namespace and write for a thread key."""


class CommittedContextRepository(Protocol):
    async def register_execution(
        self,
        thread_key: str,
        request_id: str,
    ) -> None:
        """Register one temporary graph checkpoint for TTL cleanup."""

    async def expired_execution_keys(self) -> tuple[str, ...]:
        """Return temporary checkpoint keys whose execution lease expired."""

    async def forget_execution(self, thread_key: str) -> None:
        """Remove one temporary checkpoint registry entry."""

    async def read(self, scope: ContextScope) -> ConversationContext:
        """Read the latest committed context for one conversation scope."""

    async def read_previous(self, scope: ContextScope) -> ConversationContext | None:
        """Read the single rollback context retained for one scope."""

    async def read_terminal(
        self,
        scope: ContextScope,
        request_id: str,
        payload_digest: str,
    ) -> Mapping[str, Any] | None:
        """Return an exact completed terminal for an idempotent replay."""

    async def commit(
        self,
        scope: ContextScope,
        request_id: str,
        payload_digest: str,
        context: ConversationContext,
        terminal: Mapping[str, Any],
        completed_turn: ConversationTurn | None = None,
    ) -> Mapping[str, Any]:
        """Atomically archive a turn, replace context, and record its terminal."""

    async def retrieve_memories(
        self,
        scope: ContextScope,
        query_embedding: tuple[float, ...] | None,
        *,
        exclude_turn_ids: tuple[str, ...],
        limit: int,
    ) -> tuple[RetrievedMemory, ...]:
        """Read scope-local archived turns relevant to the current question."""

    async def pending_archive_embeddings(
        self,
        *,
        limit: int,
    ) -> tuple[ArchivedConversationTurn, ...]:
        """Return archive rows whose retrieval embedding is still missing."""

    async def store_archive_embedding(
        self,
        archive_id: int,
        embedding: tuple[float, ...],
    ) -> None:
        """Attach one validated embedding to an archived turn."""

    async def claim_summary_job(self) -> SummaryJob | None:
        """Lease one due summary job and its lossless source turns."""

    async def complete_summary_job(
        self,
        job: SummaryJob,
        *,
        summary: ContextSummary,
        structured_state: StructuredConversationState,
    ) -> bool:
        """Apply a verified summary iff its source cursor is still current."""

    async def fail_summary_job(self, job: SummaryJob, error_code: str) -> None:
        """Leave the old summary intact and persist a bounded retry."""

    async def commit_terminal(
        self,
        scope: ContextScope,
        request_id: str,
        payload_digest: str,
        terminal: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Record a final error terminal without advancing conversation context."""

    async def delete(self, scope: ContextScope) -> None:
        """Delete committed context and request receipts for one scope."""


class ContextStateAPI:
    """Small state boundary shared by transport, graph, and deletion adapters."""

    def __init__(
        self,
        checkpointer: AsyncContextCheckpointer,
        committed: CommittedContextRepository,
    ) -> None:
        self._checkpointer = checkpointer
        self._committed = committed

    @staticmethod
    def config_for(
        scope: ContextScope,
        request_id: str,
    ) -> dict[str, dict[str, str]]:
        return checkpoint_config(scope, request_id)

    async def read_context(self, scope: ContextScope) -> ConversationContext:
        return await self._committed.read(scope)

    async def read_previous_context(
        self,
        scope: ContextScope,
    ) -> ConversationContext | None:
        return await self._committed.read_previous(scope)

    async def read_terminal(
        self,
        scope: ContextScope,
        request_id: str,
        payload_digest: str,
    ) -> Mapping[str, Any] | None:
        return await self._committed.read_terminal(
            scope,
            request_id,
            payload_digest,
        )

    async def commit(
        self,
        scope: ContextScope,
        request_id: str,
        payload_digest: str,
        context: ConversationContext,
        terminal: Mapping[str, Any],
        completed_turn: ConversationTurn | None = None,
    ) -> Mapping[str, Any]:
        return await self._committed.commit(
            scope,
            request_id,
            payload_digest,
            context,
            terminal,
            completed_turn,
        )

    async def retrieve_memories(
        self,
        scope: ContextScope,
        query_embedding: tuple[float, ...] | None,
        *,
        exclude_turn_ids: tuple[str, ...] = (),
        limit: int = 5,
    ) -> tuple[RetrievedMemory, ...]:
        return await self._committed.retrieve_memories(
            scope,
            query_embedding,
            exclude_turn_ids=exclude_turn_ids,
            limit=limit,
        )

    async def pending_archive_embeddings(
        self,
        *,
        limit: int = 8,
    ) -> tuple[ArchivedConversationTurn, ...]:
        return await self._committed.pending_archive_embeddings(limit=limit)

    async def store_archive_embedding(
        self,
        archive_id: int,
        embedding: tuple[float, ...],
    ) -> None:
        await self._committed.store_archive_embedding(archive_id, embedding)

    async def claim_summary_job(self) -> SummaryJob | None:
        return await self._committed.claim_summary_job()

    async def complete_summary_job(
        self,
        job: SummaryJob,
        *,
        summary: ContextSummary,
        structured_state: StructuredConversationState,
    ) -> bool:
        return await self._committed.complete_summary_job(
            job,
            summary=summary,
            structured_state=structured_state,
        )

    async def fail_summary_job(self, job: SummaryJob, error_code: str) -> None:
        await self._committed.fail_summary_job(job, error_code)

    async def commit_terminal(
        self,
        scope: ContextScope,
        request_id: str,
        payload_digest: str,
        terminal: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        return await self._committed.commit_terminal(
            scope,
            request_id,
            payload_digest,
            terminal,
        )

    @staticmethod
    def state_channels(
        scope: ContextScope,
        context: ConversationContext,
        retrieved_memories: tuple[RetrievedMemory, ...] = (),
    ) -> dict[str, Any]:
        return {
            "conversation_context": context.to_state(),
            "retrieved_memories": [memory.to_state() for memory in retrieved_memories],
        }

    async def delete_execution(
        self,
        scope: ContextScope,
        request_id: str,
    ) -> None:
        thread_key = checkpoint_thread_key(scope, request_id)
        try:
            await self._checkpointer.adelete_thread(thread_key)
        finally:
            await self._committed.forget_execution(thread_key)

    async def begin_execution(
        self,
        scope: ContextScope,
        request_id: str,
    ) -> None:
        await self._committed.register_execution(
            checkpoint_thread_key(scope, request_id),
            request_id,
        )

    async def cleanup_expired_executions(self) -> int:
        keys = await self._committed.expired_execution_keys()
        for thread_key in keys:
            try:
                await self._checkpointer.adelete_thread(thread_key)
            finally:
                await self._committed.forget_execution(thread_key)
        return len(keys)

    async def delete_scope(self, scope: ContextScope) -> None:
        await self._committed.delete(scope)


@dataclass(frozen=True)
class ConversationTurn:
    """One completed user/assistant exchange stored for later context."""

    turn_id: str
    user_message: str
    assistant_message: str

    def __post_init__(self) -> None:
        if not isinstance(self.turn_id, str) or not self.turn_id.strip():
            raise ValueError("turn_id must be a non-empty string")
        if not isinstance(self.user_message, str):
            raise TypeError("user_message must be a string")
        if not isinstance(self.assistant_message, str):
            raise TypeError("assistant_message must be a string")

    def to_state(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "user_message": self.user_message,
            "assistant_message": self.assistant_message,
        }

    @classmethod
    def from_state(cls, payload: Mapping[str, Any]) -> ConversationTurn:
        return cls(
            turn_id=payload.get("turn_id"),
            user_message=payload.get("user_message"),
            assistant_message=payload.get("assistant_message"),
        )


@dataclass(frozen=True)
class ArchivedConversationTurn:
    """One lossless completed turn stored independently of prompt projection."""

    archive_id: int
    sequence: int
    scope: ContextScope
    turn: ConversationTurn


@dataclass(frozen=True)
class RetrievedMemory:
    """Advisory projection of an archived turn for the current question."""

    sequence: int
    turn: ConversationTurn
    score: float | None = None

    def to_state(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "turn": self.turn.to_state(),
            "score": self.score,
            "authority": "advisory_conversation_memory",
        }

    @classmethod
    def from_state(cls, payload: Mapping[str, Any]) -> RetrievedMemory:
        turn = payload.get("turn")
        if not isinstance(turn, Mapping):
            raise TypeError("retrieved memory turn must be an object")
        sequence = payload.get("sequence")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence <= 0:
            raise ValueError("retrieved memory sequence must be a positive integer")
        score = payload.get("score")
        if score is not None and not isinstance(score, (int, float)):
            raise TypeError("retrieved memory score must be numeric or null")
        return cls(
            sequence=sequence,
            turn=ConversationTurn.from_state(turn),
            score=float(score) if score is not None else None,
        )


@dataclass(frozen=True)
class StructuredConversationState:
    """Non-financial conversational memory extracted from verified evidence."""

    current_goal: str | None = None
    response_preferences: tuple[str, ...] = ()
    decisions: tuple[str, ...] = ()
    open_questions: tuple[str, ...] = ()
    entities: tuple[str, ...] = ()

    def to_state(self) -> dict[str, Any]:
        return {
            "current_goal": self.current_goal,
            "response_preferences": list(self.response_preferences),
            "decisions": list(self.decisions),
            "open_questions": list(self.open_questions),
            "entities": list(self.entities),
        }

    @classmethod
    def from_state(cls, payload: Mapping[str, Any]) -> StructuredConversationState:
        goal = payload.get("current_goal")
        if goal is not None and not isinstance(goal, str):
            raise TypeError("structured current_goal must be a string or null")

        def values(name: str) -> tuple[str, ...]:
            raw = payload.get(name, ())
            if not isinstance(raw, (list, tuple)) or any(
                not isinstance(item, str) for item in raw
            ):
                raise TypeError(f"structured {name} must be a string list")
            return tuple(raw)

        return cls(
            current_goal=goal,
            response_preferences=values("response_preferences"),
            decisions=values("decisions"),
            open_questions=values("open_questions"),
            entities=values("entities"),
        )


@dataclass(frozen=True)
class SummaryJob:
    """Persisted summary lease built from archived, never reconstructed turns."""

    scope: ContextScope
    base_cursor: int
    target_cursor: int
    attempts: int
    existing_summary: ContextSummary | None
    existing_structured_state: StructuredConversationState
    source_turns: tuple[ConversationTurn, ...]


@dataclass(frozen=True)
class ContextBudgetPolicy:
    """Independent budgets for the layered model-context projection."""

    max_model_input_units: int = 3_200
    max_summary_units: int = 600
    max_structured_units: int = 1_000
    max_memory_units: int = 600
    max_recent_turns: int = 12

    def __post_init__(self) -> None:
        if self.max_model_input_units <= 0:
            raise ValueError("max_model_input_units must be greater than 0")
        if self.max_summary_units < 0:
            raise ValueError("max_summary_units must not be negative")
        if self.max_structured_units < 0:
            raise ValueError("max_structured_units must not be negative")
        if self.max_memory_units < 0:
            raise ValueError("max_memory_units must not be negative")
        if self.max_recent_turns <= 0:
            raise ValueError("max_recent_turns must be greater than 0")
        if (
            self.max_summary_units
            + self.max_structured_units
            + self.max_memory_units
            > self.max_model_input_units
        ):
            raise ValueError("reserved context budgets exceed model input budget")


class TextUnitCounter(Protocol):
    def estimate_text(self, text: str) -> int:
        """Return deterministic model-independent size units for text."""


class ApproximateUnitCounter:
    """Conservative size estimator that does not depend on a model tokenizer."""

    def estimate_text(self, text: str) -> int:
        if not isinstance(text, str):
            raise TypeError("text must be a string")

        units = 0
        ascii_run = 0

        def flush_ascii_run() -> None:
            nonlocal ascii_run, units
            if ascii_run:
                units += ceil(ascii_run / 4)
                ascii_run = 0

        for character in text:
            if character.isascii() and character.isalnum():
                ascii_run += 1
                continue

            flush_ascii_run()
            if character.isspace():
                continue

            codepoint = ord(character)
            if 0xAC00 <= codepoint <= 0xD7A3:
                units += 2
            elif self._is_hangul_jamo(codepoint):
                units += 3
            elif unicodedata.category(character).startswith("P"):
                units += 1
            else:
                units += 3

        flush_ascii_run()
        return units

    @staticmethod
    def _is_hangul_jamo(codepoint: int) -> bool:
        return any(
            start <= codepoint <= end
            for start, end in (
                (0x1100, 0x11FF),
                (0x3130, 0x318F),
                (0xA960, 0xA97F),
                (0xD7B0, 0xD7FF),
            )
        )


class ContextStore(Protocol):
    def append(self, scope: ContextScope, turn: ConversationTurn) -> None:
        """Append one completed turn to exactly one scope."""

    def read(self, scope: ContextScope) -> tuple[ConversationTurn, ...]:
        """Read turns belonging to exactly one scope in chronological order."""


class FakeContextStore:
    """In-memory store used to prove scope isolation before F02 runtime binding."""

    def __init__(self) -> None:
        self._turns: dict[ContextScope, list[ConversationTurn]] = {}

    def append(self, scope: ContextScope, turn: ConversationTurn) -> None:
        bucket = self._turns.setdefault(scope, [])
        for existing in bucket:
            if existing.turn_id != turn.turn_id:
                continue
            if existing == turn:
                return
            raise ValueError(f"conflicting turn_id in scope: {turn.turn_id}")
        bucket.append(turn)

    def read(self, scope: ContextScope) -> tuple[ConversationTurn, ...]:
        return tuple(self._turns.get(scope, ()))


@dataclass(frozen=True)
class ContextSummary:
    """Incremental summary state produced without rereading original history."""

    text: str
    estimated_units: int
    summarized_through_sequence: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError("summary text must be a string")
        if isinstance(self.estimated_units, bool) or not isinstance(
            self.estimated_units,
            int,
        ):
            raise TypeError("summary estimated_units must be an integer")
        if self.estimated_units < 0:
            raise ValueError("summary estimated_units must not be negative")
        if (
            isinstance(self.summarized_through_sequence, bool)
            or not isinstance(self.summarized_through_sequence, int)
        ):
            raise TypeError("summary cursor must be an integer")
        if self.summarized_through_sequence < 0:
            raise ValueError("summary cursor must not be negative")

    def to_state(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "estimated_units": self.estimated_units,
            "summarized_through_sequence": self.summarized_through_sequence,
        }

    @classmethod
    def from_state(cls, payload: Mapping[str, Any]) -> ContextSummary:
        return cls(
            text=payload.get("text", ""),
            estimated_units=payload.get("estimated_units", 0),
            summarized_through_sequence=payload.get(
                "summarized_through_sequence",
                0,
            ),
        )


@dataclass(frozen=True)
class ConversationContext:
    """Latest completed conversation state stored outside LangGraph checkpoints."""

    turns: tuple[ConversationTurn, ...] = ()
    summary: ContextSummary | None = None
    structured_state: StructuredConversationState = StructuredConversationState()

    def to_state(self) -> dict[str, Any]:
        return {
            "schema_version": CONTEXT_SCHEMA_VERSION,
            "summary": self.summary.to_state() if self.summary else {},
            "structured_state": self.structured_state.to_state(),
            "turns": [turn.to_state() for turn in self.turns],
        }

    @classmethod
    def from_state(cls, payload: Mapping[str, Any]) -> ConversationContext:
        if payload.get("schema_version") != CONTEXT_SCHEMA_VERSION:
            raise ValueError("unsupported conversation context schema")
        summary_payload = payload.get("summary")
        summary = (
            ContextSummary.from_state(summary_payload)
            if isinstance(summary_payload, Mapping) and summary_payload
            else None
        )
        turns = payload.get("turns", ())
        if not isinstance(turns, (list, tuple)):
            raise TypeError("turns must be a list")
        if any(not isinstance(item, Mapping) for item in turns):
            raise TypeError("turn entries must be objects")
        structured_payload = payload.get("structured_state", {})
        if not isinstance(structured_payload, Mapping):
            raise TypeError("structured_state must be an object")
        return cls(
            turns=tuple(ConversationTurn.from_state(item) for item in turns),
            summary=summary,
            structured_state=StructuredConversationState.from_state(
                structured_payload
            ),
        )


class IncrementalSummaryUpdater(Protocol):
    def update(
        self,
        *,
        existing_summary: ContextSummary | None,
        new_turn: ConversationTurn,
        max_units: int,
        counter: TextUnitCounter,
    ) -> ContextSummary:
        """Update using exactly the existing summary and one newly omitted turn."""


class DeterministicIncrementalSummaryUpdater:
    """Compact one turn at a time; structured facts remain authoritative."""

    def update(
        self,
        *,
        existing_summary: ContextSummary | None,
        new_turn: ConversationTurn,
        max_units: int,
        counter: TextUnitCounter,
    ) -> ContextSummary:
        previous_text = existing_summary.text if existing_summary else ""
        fragment = json.dumps(
            {
                "turn_id": new_turn.turn_id,
                "user": " ".join(new_turn.user_message.split()),
                "assistant": " ".join(new_turn.assistant_message.split()),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        combined = f"{previous_text}\n{fragment}" if previous_text else fragment
        text = _retain_text_suffix(combined, max_units=max_units, counter=counter)
        return ContextSummary(
            text=text,
            estimated_units=counter.estimate_text(text) if text else 0,
        )


@dataclass(frozen=True)
class ContextWindow:
    """The only token-budgeted projection allowed to reach a model."""

    confirmed_facts: Mapping[str, Any]
    structured_state: StructuredConversationState
    summary: ContextSummary | None
    retrieved_memories: tuple[RetrievedMemory, ...]
    turns: tuple[ConversationTurn, ...]
    input_units: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "confirmed_facts", _freeze_facts(self.confirmed_facts))


def resolve_confirmed_facts(
    *,
    active_step_fields: Iterable[str],
    saved_data: Mapping[str, Any],
    current_form: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Resolve canonical path/value maps using the agreed source precedence.

    The F02 runtime adapter will flatten the D1 transport snapshot into canonical
    path/value maps. For fields owned by the active step, key presence in
    current_form wins, including falsey values and explicit null. All other
    current_form fields are ignored and saved_data remains authoritative.
    """

    active_paths = set(active_step_fields)
    if any(not isinstance(path, str) or not path.strip() for path in active_paths):
        raise ValueError("active step fields must be non-empty strings")

    resolved = deepcopy(dict(saved_data))
    for path in sorted(active_paths):
        if path in current_form:
            resolved[path] = deepcopy(current_form[path])
    return MappingProxyType(resolved)


def build_context_window(
    *,
    context: ConversationContext,
    current_confirmed_facts: Mapping[str, Any],
    retrieved_memories: tuple[RetrievedMemory, ...] = (),
    policy: ContextBudgetPolicy | None = None,
    counter: TextUnitCounter | None = None,
) -> ContextWindow:
    """Project persisted context into one bounded model input."""

    active_policy = policy or ContextBudgetPolicy()
    active_counter = counter or ApproximateUnitCounter()
    summary = _project_summary(
        context.summary,
        max_units=min(
            active_policy.max_summary_units,
            active_policy.max_model_input_units,
        ),
        counter=active_counter,
    )
    summary_units = (
        active_counter.estimate_text(summary.text)
        if summary and summary.text
        else 0
    )
    structured_state = _project_structured_state(
        context.structured_state,
        max_units=min(
            active_policy.max_structured_units,
            active_policy.max_model_input_units - summary_units,
        ),
        counter=active_counter,
    )
    memory_state_units = _estimate_structured_state(
        structured_state,
        active_counter,
    )
    confirmed_facts = _select_current_facts(
        current_confirmed_facts=current_confirmed_facts,
        max_units=min(
            active_policy.max_structured_units,
            active_policy.max_model_input_units
            - summary_units
            - memory_state_units,
        ),
        counter=active_counter,
    )
    confirmed_units = sum(
        _estimate_current_fact(path, value, active_counter)
        for path, value in confirmed_facts.items()
    )

    memory_budget = min(
        active_policy.max_memory_units,
        max(
            active_policy.max_model_input_units
            - summary_units
            - memory_state_units
            - confirmed_units,
            0,
        ),
    )
    selected_memories: list[RetrievedMemory] = []
    retrieved_units = 0
    for memory in retrieved_memories:
        item_units = _estimate_turn(memory.turn, active_counter)
        if retrieved_units + item_units > memory_budget:
            continue
        selected_memories.append(memory)
        retrieved_units += item_units

    remaining_units = max(
        active_policy.max_model_input_units
        - summary_units
        - memory_state_units
        - confirmed_units
        - retrieved_units,
        0,
    )
    selected_reversed: list[ConversationTurn] = []
    raw_units = 0
    for turn in reversed(context.turns):
        turn_units = _estimate_turn(turn, active_counter)
        if raw_units + turn_units > remaining_units:
            break
        selected_reversed.append(turn)
        raw_units += turn_units
    selected = tuple(reversed(selected_reversed))

    return ContextWindow(
        confirmed_facts=confirmed_facts,
        structured_state=structured_state,
        summary=summary,
        retrieved_memories=tuple(selected_memories),
        turns=selected,
        input_units=(
            raw_units
            + summary_units
            + memory_state_units
            + confirmed_units
            + retrieved_units
        ),
    )


def advance_context(
    *,
    context: ConversationContext,
    new_turn: ConversationTurn,
    policy: ContextBudgetPolicy | None = None,
    counter: TextUnitCounter | None = None,
    summary_updater: IncrementalSummaryUpdater | None = None,
) -> ConversationContext:
    """Append one turn and evict old prompt copies without synthesizing memory.

    Every completed turn is archived atomically by the repository.  Evicted
    turns are therefore lossless summary/retrieval inputs, and this synchronous
    request path never replaces the last verified summary.
    """

    active_policy = policy or ContextBudgetPolicy()
    active_counter = counter or ApproximateUnitCounter()
    del summary_updater  # retained as a source-compatible no-op during v3 migration
    turns = list(context.turns)
    turns.append(new_turn)

    while len(turns) > 1 and (
        len(turns) > active_policy.max_recent_turns
        or _all_projection_units(
        turns=turns,
        summary=context.summary,
        policy=active_policy,
        counter=active_counter,
        )
        > active_policy.max_model_input_units
    ):
        turns.pop(0)

    return ConversationContext(
        turns=tuple(turns),
        summary=context.summary,
        structured_state=context.structured_state,
    )


def _estimate_turn(turn: ConversationTurn, counter: TextUnitCounter) -> int:
    return (
        counter.estimate_text(turn.user_message)
        + counter.estimate_text(turn.assistant_message)
        + (MESSAGE_OVERHEAD_UNITS * 2)
    )


def _select_current_facts(
    *,
    current_confirmed_facts: Mapping[str, Any],
    max_units: int,
    counter: TextUnitCounter,
) -> Mapping[str, Any]:
    candidates = sorted(
        current_confirmed_facts.items(),
        key=lambda item: (
            _infer_fact_kind(item[0], item[1]) not in {"amount", "date"},
            item[0],
        ),
    )
    selected: dict[str, Any] = {}
    used_units = 0
    for path, value in candidates:
        item_units = _estimate_current_fact(path, value, counter)
        if used_units + item_units > max_units:
            continue
        selected[path] = deepcopy(value)
        used_units += item_units
    return MappingProxyType(selected)


def _all_projection_units(
    *,
    turns: list[ConversationTurn],
    summary: ContextSummary | None,
    policy: ContextBudgetPolicy,
    counter: TextUnitCounter,
) -> int:
    summary_units = counter.estimate_text(summary.text) if summary and summary.text else 0
    return summary_units + sum(_estimate_turn(turn, counter) for turn in turns)


def _estimate_structured_state(
    state: StructuredConversationState,
    counter: TextUnitCounter,
) -> int:
    return counter.estimate_text(json.dumps(
        state.to_state(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ))


def _project_structured_state(
    state: StructuredConversationState,
    *,
    max_units: int,
    counter: TextUnitCounter,
) -> StructuredConversationState:
    if max_units <= 0:
        return StructuredConversationState()

    selected = StructuredConversationState()
    goal = state.current_goal
    if goal is not None:
        candidate = StructuredConversationState(current_goal=goal)
        if _estimate_structured_state(candidate, counter) <= max_units:
            selected = candidate

    def append_values(name: str, values: tuple[str, ...]) -> None:
        nonlocal selected
        for value in values:
            candidate_values = (*getattr(selected, name), value)
            candidate = StructuredConversationState(
                current_goal=selected.current_goal,
                response_preferences=(
                    candidate_values
                    if name == "response_preferences"
                    else selected.response_preferences
                ),
                decisions=(
                    candidate_values if name == "decisions" else selected.decisions
                ),
                open_questions=(
                    candidate_values
                    if name == "open_questions"
                    else selected.open_questions
                ),
                entities=(
                    candidate_values if name == "entities" else selected.entities
                ),
            )
            if _estimate_structured_state(candidate, counter) > max_units:
                return
            selected = candidate

    append_values("response_preferences", state.response_preferences)
    append_values("decisions", state.decisions)
    append_values("open_questions", state.open_questions)
    append_values("entities", state.entities)
    return selected


def _estimate_current_fact(
    path: str,
    value: Any,
    counter: TextUnitCounter,
) -> int:
    return MESSAGE_OVERHEAD_UNITS + counter.estimate_text(
        json.dumps(
            {"path": path, "value": value},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _infer_fact_kind(path: str, value: Any) -> str:
    lowered_path = path.lower()
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        amount_markers = (
            "amount",
            "asset",
            "balance",
            "cost",
            "expense",
            "income",
            "price",
            "principal",
            "saving",
            "value",
        )
        return "amount" if any(marker in lowered_path for marker in amount_markers) else "number"
    if isinstance(value, str):
        try:
            from datetime import date

            date.fromisoformat(value[:10])
        except (ValueError, TypeError):
            return "text"
        return "date"
    if isinstance(value, (list, dict)):
        return "object"
    return "text"


def _retain_text_suffix(
    text: str,
    *,
    max_units: int,
    counter: TextUnitCounter,
) -> str:
    if max_units <= 0:
        return ""
    if counter.estimate_text(text) <= max_units:
        return text
    lower = 0
    upper = len(text)
    while lower < upper:
        middle = (lower + upper) // 2
        if counter.estimate_text(text[middle:]) <= max_units:
            upper = middle
        else:
            lower = middle + 1
    return text[lower:]


def _project_summary(
    summary: ContextSummary | None,
    *,
    max_units: int,
    counter: TextUnitCounter,
) -> ContextSummary | None:
    if summary is None:
        return None
    text = _retain_text_suffix(summary.text, max_units=max_units, counter=counter)
    if not text:
        return None
    return ContextSummary(
        text=text,
        estimated_units=counter.estimate_text(text) if text else 0,
        summarized_through_sequence=summary.summarized_through_sequence,
    )
