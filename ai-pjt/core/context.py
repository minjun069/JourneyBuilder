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
CONTEXT_SCHEMA_VERSION = "2.0"
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
    ) -> Mapping[str, Any]:
        """Atomically replace context and record its completed terminal."""

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
    ) -> Mapping[str, Any]:
        return await self._committed.commit(
            scope,
            request_id,
            payload_digest,
            context,
            terminal,
        )

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
    ) -> dict[str, Any]:
        return {
            "context_scope": scope.to_state(),
            "conversation_context": context.to_state(),
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
class ContextBudgetPolicy:
    """Token-budget projection limits with no fixed recent-turn cap."""

    max_model_input_units: int = 3_200
    max_summary_units: int = 600
    max_structured_units: int = 1_000

    def __post_init__(self) -> None:
        if self.max_model_input_units <= 0:
            raise ValueError("max_model_input_units must be greater than 0")
        if self.max_summary_units < 0:
            raise ValueError("max_summary_units must not be negative")
        if self.max_structured_units < 0:
            raise ValueError("max_structured_units must not be negative")
        if self.max_summary_units + self.max_structured_units > self.max_model_input_units:
            raise ValueError("summary and structured budgets exceed model input budget")


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

    def to_state(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "estimated_units": self.estimated_units,
        }

    @classmethod
    def from_state(cls, payload: Mapping[str, Any]) -> ContextSummary:
        return cls(
            text=payload.get("text", ""),
            estimated_units=payload.get("estimated_units", 0),
        )


@dataclass(frozen=True)
class ConversationContext:
    """Latest completed conversation state stored outside LangGraph checkpoints."""

    turns: tuple[ConversationTurn, ...] = ()
    summary: ContextSummary | None = None

    def to_state(self) -> dict[str, Any]:
        return {
            "schema_version": CONTEXT_SCHEMA_VERSION,
            "summary": self.summary.to_state() if self.summary else {},
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
        return cls(
            turns=tuple(ConversationTurn.from_state(item) for item in turns),
            summary=summary,
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

    turns: tuple[ConversationTurn, ...]
    confirmed_facts: Mapping[str, Any]
    summary: ContextSummary | None
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
    confirmed_facts = _select_current_facts(
        current_confirmed_facts=current_confirmed_facts,
        max_units=min(
            active_policy.max_structured_units,
            active_policy.max_model_input_units - summary_units,
        ),
        counter=active_counter,
    )
    structured_units = sum(
        _estimate_current_fact(path, value, active_counter)
        for path, value in confirmed_facts.items()
    )

    remaining_units = max(
        active_policy.max_model_input_units - summary_units - structured_units,
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
        turns=selected,
        confirmed_facts=confirmed_facts,
        summary=summary,
        input_units=raw_units + summary_units + structured_units,
    )


def advance_context(
    *,
    context: ConversationContext,
    new_turn: ConversationTurn,
    policy: ContextBudgetPolicy | None = None,
    counter: TextUnitCounter | None = None,
    summary_updater: IncrementalSummaryUpdater | None = None,
) -> ConversationContext:
    """Append one completed turn and incrementally summarize only on overflow."""

    active_policy = policy or ContextBudgetPolicy()
    active_counter = counter or ApproximateUnitCounter()
    active_updater = summary_updater or DeterministicIncrementalSummaryUpdater()
    turns = list(context.turns)
    turns.append(new_turn)
    summary = context.summary

    while turns and _all_projection_units(
        turns=turns,
        summary=summary,
        policy=active_policy,
        counter=active_counter,
    ) > active_policy.max_model_input_units:
        omitted = turns.pop(0)
        summary = active_updater.update(
            existing_summary=summary,
            new_turn=omitted,
            max_units=active_policy.max_summary_units,
            counter=active_counter,
        )

    return ConversationContext(
        turns=tuple(turns),
        summary=summary,
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
    )
