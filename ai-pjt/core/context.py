from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from math import ceil
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Protocol
import json
import unicodedata


ScopePart = str | int
MESSAGE_OVERHEAD_UNITS = 4


def _freeze_facts(facts: Mapping[str, Any]) -> Mapping[str, Any]:
    copied: dict[str, Any] = {}
    for path, value in facts.items():
        if not isinstance(path, str) or not path.strip():
            raise ValueError("confirmed fact paths must be non-empty strings")
        copied[path] = deepcopy(value)
    return MappingProxyType(copied)


@dataclass(frozen=True)
class ContextScope:
    """Internal isolation key; a G0 adapter will bind it to transport IDs."""

    user_id: ScopePart
    session_id: ScopePart
    thread_id: ScopePart

    def __post_init__(self) -> None:
        for name, value in (
            ("user_id", self.user_id),
            ("session_id", self.session_id),
            ("thread_id", self.thread_id),
        ):
            if isinstance(value, bool) or not isinstance(value, (str, int)):
                raise TypeError(f"{name} must be a string or integer")
            if isinstance(value, str) and not value.strip():
                raise ValueError(f"{name} must not be blank")


@dataclass(frozen=True)
class ConversationTurn:
    """One completed user/assistant exchange stored for later context."""

    turn_id: str
    user_message: str
    assistant_message: str
    confirmed_facts: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.turn_id, str) or not self.turn_id.strip():
            raise ValueError("turn_id must be a non-empty string")
        if not isinstance(self.user_message, str):
            raise TypeError("user_message must be a string")
        if not isinstance(self.assistant_message, str):
            raise TypeError("assistant_message must be a string")
        object.__setattr__(self, "confirmed_facts", _freeze_facts(self.confirmed_facts))


@dataclass(frozen=True)
class ContextBudgetPolicy:
    """Model-independent limits for completed conversation history."""

    max_recent_turns: int = 5
    max_history_units: int = 3_200
    max_summary_units: int = 600

    def __post_init__(self) -> None:
        if self.max_recent_turns < 0:
            raise ValueError("max_recent_turns must not be negative")
        if self.max_history_units < 0:
            raise ValueError("max_history_units must not be negative")
        if self.max_summary_units < 0:
            raise ValueError("max_summary_units must not be negative")


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
    """In-memory store used to prove scope isolation before G0 integration."""

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
    """Structured, non-LLM summary of facts from omitted raw turns."""

    omitted_turn_ids: tuple[str, ...]
    fact_items: tuple[tuple[str, Any], ...]
    estimated_units: int
    truncated_fact_count: int


@dataclass(frozen=True)
class ContextWindow:
    """Bounded raw history plus facts that remain authoritative."""

    turns: tuple[ConversationTurn, ...]
    confirmed_facts: Mapping[str, Any]
    summary: ContextSummary | None
    history_units: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "confirmed_facts", _freeze_facts(self.confirmed_facts))


def resolve_confirmed_facts(
    *,
    active_step_fields: Iterable[str],
    saved_data: Mapping[str, Any],
    current_form: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Resolve canonical path/value maps using the agreed source precedence.

    An adapter added after G0 will flatten the transport snapshot into canonical
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
    store: ContextStore,
    scope: ContextScope,
    current_confirmed_facts: Mapping[str, Any],
    policy: ContextBudgetPolicy | None = None,
    counter: TextUnitCounter | None = None,
) -> ContextWindow:
    """Build a bounded context without binding to transport or persistence."""

    active_policy = policy or ContextBudgetPolicy()
    active_counter = counter or ApproximateUnitCounter()
    all_turns = store.read(scope)

    selected_reversed: list[ConversationTurn] = []
    raw_units = 0
    for turn in reversed(all_turns):
        if len(selected_reversed) >= active_policy.max_recent_turns:
            break
        turn_units = _estimate_turn(turn, active_counter)
        if raw_units + turn_units > active_policy.max_history_units:
            break
        selected_reversed.append(turn)
        raw_units += turn_units

    selected = tuple(reversed(selected_reversed))
    omitted_count = len(all_turns) - len(selected)
    omitted_turns = all_turns[:omitted_count]

    fact_versions: dict[str, tuple[int, Any]] = {}
    for index, turn in enumerate(all_turns):
        for path, value in turn.confirmed_facts.items():
            fact_versions[path] = (index, deepcopy(value))
    for path, value in current_confirmed_facts.items():
        fact_versions[path] = (len(all_turns), deepcopy(value))

    confirmed_facts = {
        path: deepcopy(value)
        for path, (_, value) in fact_versions.items()
    }

    summary = _build_summary(
        omitted_turns=omitted_turns,
        fact_versions=fact_versions,
        omitted_count=omitted_count,
        available_units=max(active_policy.max_history_units - raw_units, 0),
        max_summary_units=active_policy.max_summary_units,
        counter=active_counter,
    )
    summary_units = summary.estimated_units if summary else 0

    return ContextWindow(
        turns=selected,
        confirmed_facts=confirmed_facts,
        summary=summary,
        history_units=raw_units + summary_units,
    )


def _estimate_turn(turn: ConversationTurn, counter: TextUnitCounter) -> int:
    return (
        counter.estimate_text(turn.user_message)
        + counter.estimate_text(turn.assistant_message)
        + (MESSAGE_OVERHEAD_UNITS * 2)
    )


def _build_summary(
    *,
    omitted_turns: tuple[ConversationTurn, ...],
    fact_versions: Mapping[str, tuple[int, Any]],
    omitted_count: int,
    available_units: int,
    max_summary_units: int,
    counter: TextUnitCounter,
) -> ContextSummary | None:
    if not omitted_turns:
        return None

    summary_limit = min(available_units, max_summary_units)
    candidates = [
        (source_index, path, value)
        for path, (source_index, value) in fact_versions.items()
        if source_index < omitted_count
    ]
    candidates.sort(key=lambda item: (-item[0], item[1]))

    selected: list[tuple[str, Any]] = []
    used_units = 0
    for _, path, value in candidates:
        serialized = _serialize_fact_value(value)
        item_units = counter.estimate_text(f"{path}={serialized}")
        overhead = MESSAGE_OVERHEAD_UNITS if not selected else 0
        if used_units + overhead + item_units > summary_limit:
            continue
        if not selected:
            used_units += MESSAGE_OVERHEAD_UNITS
        selected.append((path, deepcopy(value)))
        used_units += item_units

    return ContextSummary(
        omitted_turn_ids=tuple(turn.turn_id for turn in omitted_turns),
        fact_items=tuple(selected),
        estimated_units=used_units,
        truncated_fact_count=len(candidates) - len(selected),
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
