from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from hashlib import sha256
from math import ceil
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Protocol
import json
import unicodedata


MESSAGE_OVERHEAD_UNITS = 4
CONTEXT_SCHEMA_VERSION = "1.0"
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


def checkpoint_thread_key(scope: ContextScope) -> str:
    """Return a stable, opaque checkpointer key for exactly one scope."""

    canonical = json.dumps(
        {"session_id": scope.session_id, "thread_id": scope.thread_id},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"ai-context-v1:{sha256(canonical).hexdigest()}"


def checkpoint_config(scope: ContextScope) -> dict[str, dict[str, str]]:
    """Build the only supported LangGraph checkpoint configuration."""

    return {
        "configurable": {
            "thread_id": checkpoint_thread_key(scope),
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
    async def aget_tuple(self, config: Mapping[str, Any]) -> Any:
        """Read the latest checkpoint tuple for a graph config."""

    async def adelete_thread(self, thread_id: str) -> None:
        """Delete every checkpoint namespace and write for a thread key."""


class ContextStateAPI:
    """Small state boundary shared by transport, graph, and deletion adapters."""

    def __init__(self, checkpointer: AsyncContextCheckpointer) -> None:
        self._checkpointer = checkpointer

    @staticmethod
    def config_for(scope: ContextScope) -> dict[str, dict[str, str]]:
        return checkpoint_config(scope)

    async def read_values(self, scope: ContextScope) -> Mapping[str, Any] | None:
        checkpoint_tuple = await self._checkpointer.aget_tuple(checkpoint_config(scope))
        if checkpoint_tuple is None:
            return None

        checkpoint = getattr(checkpoint_tuple, "checkpoint", None)
        if not isinstance(checkpoint, Mapping):
            raise ContextIsolationError("checkpoint tuple has no readable checkpoint")
        values = checkpoint.get("channel_values")
        if not isinstance(values, Mapping):
            raise ContextIsolationError("checkpoint has no readable channel values")
        persisted_scope = values.get("context_scope")
        if not isinstance(persisted_scope, Mapping):
            raise ContextIsolationError("checkpoint has no context scope")
        validate_persisted_scope(persisted_scope, scope)
        return MappingProxyType(deepcopy(dict(values)))

    async def read_context(self, scope: ContextScope) -> ConversationContext:
        values = await self.read_values(scope)
        if values is None:
            return ConversationContext()
        payload = values.get("conversation_context")
        if not isinstance(payload, Mapping):
            raise ContextIsolationError("checkpoint has no conversation context")
        try:
            return ConversationContext.from_state(payload)
        except (TypeError, ValueError) as exc:
            raise ContextIsolationError("checkpoint conversation context is invalid") from exc

    @staticmethod
    def state_channels(
        scope: ContextScope,
        context: ConversationContext,
    ) -> dict[str, Any]:
        return {
            "context_scope": scope.to_state(),
            "conversation_context": context.to_state(),
        }

    async def delete_scope(self, scope: ContextScope) -> None:
        await self._checkpointer.adelete_thread(checkpoint_thread_key(scope))


@dataclass(frozen=True)
class ConversationTurn:
    """One completed user/assistant exchange stored for later context."""

    turn_id: str
    user_message: str
    assistant_message: str
    confirmed_facts: Mapping[str, Any] = field(default_factory=dict)
    fact_kinds: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.turn_id, str) or not self.turn_id.strip():
            raise ValueError("turn_id must be a non-empty string")
        if not isinstance(self.user_message, str):
            raise TypeError("user_message must be a string")
        if not isinstance(self.assistant_message, str):
            raise TypeError("assistant_message must be a string")
        object.__setattr__(self, "confirmed_facts", _freeze_facts(self.confirmed_facts))
        kinds = dict(self.fact_kinds)
        if any(path not in self.confirmed_facts for path in kinds):
            raise ValueError("fact_kinds must refer to confirmed fact paths")
        if any(not isinstance(kind, str) or not kind.strip() for kind in kinds.values()):
            raise ValueError("fact kinds must be non-empty strings")
        object.__setattr__(self, "fact_kinds", MappingProxyType(kinds))

    def to_state(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "user_message": self.user_message,
            "assistant_message": self.assistant_message,
            "confirmed_facts": deepcopy(dict(self.confirmed_facts)),
            "fact_kinds": dict(self.fact_kinds),
        }

    @classmethod
    def from_state(cls, payload: Mapping[str, Any]) -> ConversationTurn:
        return cls(
            turn_id=payload.get("turn_id"),
            user_message=payload.get("user_message"),
            assistant_message=payload.get("assistant_message"),
            confirmed_facts=payload.get("confirmed_facts", {}),
            fact_kinds=payload.get("fact_kinds", {}),
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
class FactRevision:
    """One source-bound value revision in an append-only correction chain."""

    revision_id: str
    path: str
    value: Any
    value_kind: str
    source_turn_id: str
    supersedes_revision_id: str | None

    def __post_init__(self) -> None:
        for name, value in (
            ("revision_id", self.revision_id),
            ("path", self.path),
            ("value_kind", self.value_kind),
            ("source_turn_id", self.source_turn_id),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if self.supersedes_revision_id is not None and (
            not isinstance(self.supersedes_revision_id, str)
            or not self.supersedes_revision_id.strip()
        ):
            raise ValueError("supersedes_revision_id must be null or non-empty")
        object.__setattr__(self, "value", deepcopy(self.value))

    def to_state(self) -> dict[str, Any]:
        return {
            "revision_id": self.revision_id,
            "path": self.path,
            "value": deepcopy(self.value),
            "value_kind": self.value_kind,
            "source_turn_id": self.source_turn_id,
            "supersedes_revision_id": self.supersedes_revision_id,
        }

    @classmethod
    def from_state(cls, payload: Mapping[str, Any]) -> FactRevision:
        return cls(
            revision_id=payload.get("revision_id"),
            path=payload.get("path"),
            value=payload.get("value"),
            value_kind=payload.get("value_kind"),
            source_turn_id=payload.get("source_turn_id"),
            supersedes_revision_id=payload.get("supersedes_revision_id"),
        )


@dataclass(frozen=True)
class ContextSummary:
    """Incremental summary state produced without rereading original history."""

    text: str
    source_turn_ids: tuple[str, ...]
    estimated_units: int

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError("summary text must be a string")
        if any(
            not isinstance(turn_id, str) or not turn_id.strip()
            for turn_id in self.source_turn_ids
        ):
            raise ValueError("summary source turn ids must be non-empty strings")
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
            "source_turn_ids": list(self.source_turn_ids),
            "estimated_units": self.estimated_units,
        }

    @classmethod
    def from_state(cls, payload: Mapping[str, Any]) -> ContextSummary:
        return cls(
            text=payload.get("text", ""),
            source_turn_ids=tuple(payload.get("source_turn_ids", ())),
            estimated_units=payload.get("estimated_units", 0),
        )


@dataclass(frozen=True)
class ConversationContext:
    """Persisted context state; removal happens only through scope deletion."""

    turns: tuple[ConversationTurn, ...] = ()
    summary: ContextSummary | None = None
    fact_revisions: tuple[FactRevision, ...] = ()
    turn_digests: tuple[tuple[str, str], ...] = ()

    def to_state(self) -> dict[str, Any]:
        return {
            "schema_version": CONTEXT_SCHEMA_VERSION,
            "summary": self.summary.to_state() if self.summary else {},
            "structured_context": [item.to_state() for item in self.fact_revisions],
            "turns": [turn.to_state() for turn in self.turns],
            "turn_digests": [list(item) for item in self.turn_digests],
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
        revisions = payload.get("structured_context", ())
        turns = payload.get("turns", ())
        digests = payload.get("turn_digests", ())
        if not isinstance(revisions, (list, tuple)):
            raise TypeError("structured_context must be a list")
        if not isinstance(turns, (list, tuple)):
            raise TypeError("turns must be a list")
        if not isinstance(digests, (list, tuple)):
            raise TypeError("turn_digests must be a list")
        if any(not isinstance(item, Mapping) for item in revisions):
            raise TypeError("structured context entries must be objects")
        if any(not isinstance(item, Mapping) for item in turns):
            raise TypeError("turn entries must be objects")

        parsed_digests: list[tuple[str, str]] = []
        for item in digests:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                raise TypeError("turn digest entries must be pairs")
            turn_id, digest = item
            if (
                not isinstance(turn_id, str)
                or not turn_id.strip()
                or not isinstance(digest, str)
                or not digest.strip()
            ):
                raise ValueError("turn digest pairs must contain non-empty strings")
            parsed_digests.append((turn_id, digest))
        return cls(
            turns=tuple(ConversationTurn.from_state(item) for item in turns),
            summary=summary,
            fact_revisions=tuple(FactRevision.from_state(item) for item in revisions),
            turn_digests=tuple(parsed_digests),
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
        prior_ids = existing_summary.source_turn_ids if existing_summary else ()
        return ContextSummary(
            text=text,
            source_turn_ids=prior_ids + (new_turn.turn_id,),
            estimated_units=counter.estimate_text(text) if text else 0,
        )


@dataclass(frozen=True)
class ContextWindow:
    """The only token-budgeted projection allowed to reach a model."""

    turns: tuple[ConversationTurn, ...]
    confirmed_facts: Mapping[str, Any]
    summary: ContextSummary | None
    structured_facts: tuple[FactRevision, ...]
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
    structured = _select_structured_facts(
        revisions=context.fact_revisions,
        current_confirmed_facts=current_confirmed_facts,
        max_units=min(
            active_policy.max_structured_units,
            active_policy.max_model_input_units - summary_units,
        ),
        counter=active_counter,
    )
    structured_units = sum(
        _estimate_fact_revision(item, active_counter) for item in structured
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
        confirmed_facts={item.path: deepcopy(item.value) for item in structured},
        summary=summary,
        structured_facts=structured,
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
    digest = _turn_digest(new_turn)
    known_digests = dict(context.turn_digests)
    if new_turn.turn_id in known_digests:
        if known_digests[new_turn.turn_id] == digest:
            return context
        raise ValueError(f"conflicting turn_id in context: {new_turn.turn_id}")

    turns = list(context.turns)
    turns.append(new_turn)
    revisions = _append_fact_revisions(
        existing=context.fact_revisions,
        turn=new_turn,
    )
    summary = context.summary

    while turns and _all_projection_units(
        turns=turns,
        summary=summary,
        revisions=revisions,
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
        fact_revisions=revisions,
        turn_digests=context.turn_digests + ((new_turn.turn_id, digest),),
    )


def _estimate_turn(turn: ConversationTurn, counter: TextUnitCounter) -> int:
    return (
        counter.estimate_text(turn.user_message)
        + counter.estimate_text(turn.assistant_message)
        + (MESSAGE_OVERHEAD_UNITS * 2)
    )


def _append_fact_revisions(
    *,
    existing: tuple[FactRevision, ...],
    turn: ConversationTurn,
) -> tuple[FactRevision, ...]:
    revisions = list(existing)
    latest = {item.path: item for item in existing}
    for path in sorted(turn.confirmed_facts):
        previous = latest.get(path)
        revision_id = sha256(f"{turn.turn_id}\0{path}".encode("utf-8")).hexdigest()
        revision = FactRevision(
            revision_id=revision_id,
            path=path,
            value=turn.confirmed_facts[path],
            value_kind=turn.fact_kinds.get(path)
            or _infer_fact_kind(path, turn.confirmed_facts[path]),
            source_turn_id=turn.turn_id,
            supersedes_revision_id=previous.revision_id if previous else None,
        )
        revisions.append(revision)
        latest[path] = revision
    return tuple(revisions)


def _select_structured_facts(
    *,
    revisions: tuple[FactRevision, ...],
    current_confirmed_facts: Mapping[str, Any],
    max_units: int,
    counter: TextUnitCounter,
) -> tuple[FactRevision, ...]:
    latest: dict[str, tuple[int, FactRevision]] = {}
    for index, item in enumerate(revisions):
        latest[item.path] = (index, item)

    offset = len(revisions)
    for index, path in enumerate(sorted(current_confirmed_facts)):
        previous = latest.get(path)
        revision_id = sha256(f"current-snapshot\0{path}".encode("utf-8")).hexdigest()
        latest[path] = (
            offset + index,
            FactRevision(
                revision_id=revision_id,
                path=path,
                value=current_confirmed_facts[path],
                value_kind=_infer_fact_kind(path, current_confirmed_facts[path]),
                source_turn_id="current-snapshot",
                supersedes_revision_id=previous[1].revision_id if previous else None,
            ),
        )

    candidates = sorted(
        latest.values(),
        key=lambda pair: (
            pair[1].source_turn_id != "current-snapshot",
            pair[1].supersedes_revision_id is None,
            pair[1].value_kind not in {"amount", "date"},
            -pair[0],
            pair[1].path,
        ),
    )
    selected: list[FactRevision] = []
    used_units = 0
    for _, item in candidates:
        item_units = _estimate_fact_revision(item, counter)
        if used_units + item_units > max_units:
            continue
        selected.append(item)
        used_units += item_units
    return tuple(selected)


def _all_projection_units(
    *,
    turns: list[ConversationTurn],
    summary: ContextSummary | None,
    revisions: tuple[FactRevision, ...],
    policy: ContextBudgetPolicy,
    counter: TextUnitCounter,
) -> int:
    summary_units = counter.estimate_text(summary.text) if summary and summary.text else 0
    facts = _select_structured_facts(
        revisions=revisions,
        current_confirmed_facts={},
        max_units=policy.max_structured_units,
        counter=counter,
    )
    return (
        summary_units
        + sum(_estimate_fact_revision(item, counter) for item in facts)
        + sum(_estimate_turn(turn, counter) for turn in turns)
    )


def _estimate_fact_revision(item: FactRevision, counter: TextUnitCounter) -> int:
    return MESSAGE_OVERHEAD_UNITS + counter.estimate_text(
        json.dumps(item.to_state(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


def _turn_digest(turn: ConversationTurn) -> str:
    serialized = json.dumps(
        turn.to_state(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(serialized).hexdigest()


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
        source_turn_ids=summary.source_turn_ids,
        estimated_units=counter.estimate_text(text) if text else 0,
    )


def _serialize_fact_value(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError):
        return repr(value)
