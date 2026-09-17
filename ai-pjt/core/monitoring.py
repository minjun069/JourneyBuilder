"""Safe P11 observability contracts for the typed production exporter.

Input/output snapshots are accepted as typed event fields and must pass
the masking sink before export. Raw LangChain callbacks remain disabled because
they bypass that masking-safe boundary.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from enum import Enum
from math import isfinite
from typing import Any, Protocol, TypeAlias

OBSERVABILITY_CONTRACT_VERSION = "1.3"

_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")
_PSEUDONYMOUS_SUFFIX = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_TRUTHY = frozenset({"1", "true", "yes", "on"})


class OperationStage(str, Enum):
    ROUTE = "route"
    SEARCH = "search"
    TOOL = "tool"
    MODEL = "model"
    OUTPUT_VALIDATION = "output_validation"
    CALLBACK = "callback"


class OperationStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"
    BLOCKED = "blocked"


class AttemptEventType(str, Enum):
    STARTED = "started"
    ACTIVE_INTERVAL = "active_interval"
    USER_TURN = "user_turn"
    VALID_COMPLETED = "valid_completed"
    ABANDONED = "abandoned"
    MANUAL_TRANSITION = "manual_transition"


class AssistanceMode(str, Enum):
    AI = "ai"
    NO_AI = "no_ai"


class ManualTransitionReason(str, Enum):
    USER_PREFERENCE = "user_preference"
    AI_FAILURE = "ai_failure"
    LOW_CONFIDENCE = "low_confidence"
    UNSUPPORTED_TASK = "unsupported_task"
    ACCESSIBILITY = "accessibility"
    UNKNOWN = "unknown"


class ProposalOutcome(str, Enum):
    APPROVED = "approved"
    MODIFIED = "modified"
    REJECTED = "rejected"
    NO_RESPONSE = "no_response"


class ChangeEventType(str, Enum):
    APPLIED = "applied"
    PRE_SAVE_REWORKED = "pre_save_reworked"
    POST_SAVE_ROLLED_BACK = "post_save_rolled_back"


class RollbackReason(str, Enum):
    WRONG_SUGGESTION = "wrong_suggestion"
    USER_PREFERENCE = "user_preference"
    SOURCE_DATA_CHANGED = "source_data_changed"
    UNKNOWN = "unknown"


def _validate_pseudonymous_id(value: str, prefix: str, field_name: str) -> None:
    expected = f"{prefix}_"
    if not value.startswith(expected):
        raise ValueError(f"{field_name} must start with {expected!r}")
    suffix = value[len(expected):]
    if _PSEUDONYMOUS_SUFFIX.fullmatch(suffix) is None:
        raise ValueError(
            f"{field_name} must contain an 8-64 character pseudonymous suffix"
        )


def _validate_safe_token(value: str | None, field_name: str) -> None:
    if value is not None and _SAFE_TOKEN.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a non-sensitive machine token")


def _validate_nonnegative_int(value: int | None, field_name: str) -> None:
    if value is not None and (
        isinstance(value, bool) or not isinstance(value, int) or value < 0
    ):
        raise ValueError(f"{field_name} must be a non-negative integer")


def _validate_observation_content(value: Any, field_name: str) -> None:
    if value is None:
        return
    try:
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_name} must be JSON-serializable") from error


@dataclass(frozen=True, slots=True)
class OperationTraceEvent:
    """One route/search/tool/model/validation/callback measurement.

    IDs must already be pseudonymized. Search events may contain approved
    document IDs and scores, never excerpts or queries. Costs use integer
    micro-US dollars so pricing is versioned outside this contract.
    """

    trace_id: str
    attempt_id: str
    occurred_at_epoch_ms: int
    stage: OperationStage
    status: OperationStatus
    duration_ms: int
    operation_name: str
    step_id: str | None = None
    task_id: str | None = None
    agent: str | None = None
    error_code: str | None = None
    model_provider: str | None = None
    model_name: str | None = None
    model_temperature: float | None = None
    prompt_id: str | None = None
    prompt_version: str | None = None
    prompt_hash: str | None = None
    prompt_release: str | None = None
    prompt_policy_version: str | None = None
    index_version: str | None = None
    tool_version: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_microusd: int | None = None
    resource_ids: tuple[str, ...] = ()
    scores: tuple[float, ...] = ()
    result_count: int | None = None
    input: Any | None = None
    output: Any | None = None
    error_type: str | None = None
    error_fingerprint: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.stage, OperationStage):
            raise ValueError("stage must be an OperationStage")
        if not isinstance(self.status, OperationStatus):
            raise ValueError("status must be an OperationStatus")
        _validate_pseudonymous_id(self.trace_id, "trace", "trace_id")
        _validate_pseudonymous_id(self.attempt_id, "attempt", "attempt_id")
        _validate_nonnegative_int(self.occurred_at_epoch_ms, "occurred_at_epoch_ms")
        _validate_nonnegative_int(self.duration_ms, "duration_ms")
        _validate_nonnegative_int(self.input_tokens, "input_tokens")
        _validate_nonnegative_int(self.output_tokens, "output_tokens")
        _validate_nonnegative_int(self.cost_microusd, "cost_microusd")
        _validate_nonnegative_int(self.result_count, "result_count")
        for field_name in (
            "operation_name",
            "step_id",
            "task_id",
            "agent",
            "error_code",
            "model_provider",
            "model_name",
            "prompt_id",
            "prompt_version",
            "prompt_hash",
            "prompt_release",
            "prompt_policy_version",
            "index_version",
            "tool_version",
        ):
            _validate_safe_token(getattr(self, field_name), field_name)
        if self.model_temperature is not None and (
            isinstance(self.model_temperature, bool)
            or not isinstance(self.model_temperature, (int, float))
            or not isfinite(self.model_temperature)
            or self.model_temperature < 0
            or self.model_temperature > 1
        ):
            raise ValueError("model_temperature must be between 0 and 1")
        for resource_id in self.resource_ids:
            _validate_safe_token(resource_id, "resource_id")
        if any(
            isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not isfinite(score)
            for score in self.scores
        ):
            raise ValueError("scores must contain only finite numbers")
        if self.scores and len(self.scores) != len(self.resource_ids):
            raise ValueError("scores and resource_ids must have the same length")
        if self.stage is not OperationStage.SEARCH and (
            self.resource_ids or self.scores or self.result_count is not None
        ):
            raise ValueError("search result metadata is allowed only for search events")
        if self.stage is not OperationStage.MODEL and (
            self.input_tokens is not None
            or self.output_tokens is not None
            or self.model_provider is not None
            or self.model_name is not None
            or self.model_temperature is not None
            or self.prompt_id is not None
            or self.prompt_version is not None
            or self.prompt_hash is not None
            or self.prompt_release is not None
            or self.prompt_policy_version is not None
        ):
            raise ValueError("model metadata is allowed only for model events")
        if self.status is OperationStatus.SUCCEEDED and self.error_code is not None:
            raise ValueError("succeeded events cannot contain error_code")
        if self.status is not OperationStatus.SUCCEEDED and self.error_code is None:
            raise ValueError("non-success events require a stable error_code")
        _validate_observation_content(self.input, "input")
        _validate_observation_content(self.output, "output")
        if (self.error_type is None) != (self.error_fingerprint is None):
            raise ValueError(
                "error_type and error_fingerprint must be provided together"
            )
        for field_name in ("error_type", "error_fingerprint"):
            value = getattr(self, field_name)
            if value is not None:
                _validate_safe_token(value, field_name)
        if self.status is OperationStatus.SUCCEEDED and self.error_type is not None:
            raise ValueError("error identity is allowed only on failed operations")

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "contract_version": OBSERVABILITY_CONTRACT_VERSION,
            "event_family": "operation",
            "trace_id": self.trace_id,
            "attempt_id": self.attempt_id,
            "occurred_at_epoch_ms": self.occurred_at_epoch_ms,
            "stage": self.stage.value,
            "status": self.status.value,
            "duration_ms": self.duration_ms,
            "operation_name": self.operation_name,
        }
        for name in (
            "step_id",
            "task_id",
            "agent",
            "error_code",
            "model_provider",
            "model_name",
            "model_temperature",
            "prompt_id",
            "prompt_version",
            "prompt_hash",
            "prompt_release",
            "prompt_policy_version",
            "index_version",
            "tool_version",
            "input_tokens",
            "output_tokens",
            "cost_microusd",
            "result_count",
            "input",
            "output",
            "error_type",
            "error_fingerprint",
        ):
            value = getattr(self, name)
            if value is not None:
                payload[name] = value
        if self.resource_ids:
            payload["resource_ids"] = list(self.resource_ids)
        if self.scores:
            payload["scores"] = list(self.scores)
        return payload


@dataclass(frozen=True, slots=True)
class AttemptTraceEvent:
    """Form-attempt lifecycle event for completion, time, turn, and exit AX."""

    trace_id: str
    attempt_id: str
    occurred_at_epoch_ms: int
    event_type: AttemptEventType
    step_id: str
    assistance_mode: AssistanceMode | None = None
    duration_ms: int | None = None
    transition_reason: ManualTransitionReason | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.event_type, AttemptEventType):
            raise ValueError("event_type must be an AttemptEventType")
        if self.assistance_mode is not None and not isinstance(
            self.assistance_mode, AssistanceMode
        ):
            raise ValueError("assistance_mode must be an AssistanceMode")
        if self.transition_reason is not None and not isinstance(
            self.transition_reason, ManualTransitionReason
        ):
            raise ValueError("transition_reason must be a ManualTransitionReason")
        _validate_pseudonymous_id(self.trace_id, "trace", "trace_id")
        _validate_pseudonymous_id(self.attempt_id, "attempt", "attempt_id")
        _validate_nonnegative_int(self.occurred_at_epoch_ms, "occurred_at_epoch_ms")
        _validate_nonnegative_int(self.duration_ms, "duration_ms")
        _validate_safe_token(self.step_id, "step_id")
        if self.event_type is AttemptEventType.STARTED:
            if self.assistance_mode is None:
                raise ValueError("started events require assistance_mode")
        elif self.assistance_mode is not None:
            raise ValueError("assistance_mode is allowed only on started events")
        if self.event_type is AttemptEventType.ACTIVE_INTERVAL:
            if self.duration_ms is None:
                raise ValueError("active_interval events require duration_ms")
        elif self.duration_ms is not None:
            raise ValueError("duration_ms is allowed only on active_interval events")
        if self.event_type is AttemptEventType.MANUAL_TRANSITION:
            if self.transition_reason is None:
                raise ValueError("manual_transition events require transition_reason")
        elif self.transition_reason is not None:
            raise ValueError(
                "transition_reason is allowed only on manual_transition events"
            )

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "contract_version": OBSERVABILITY_CONTRACT_VERSION,
            "event_family": "attempt",
            "trace_id": self.trace_id,
            "attempt_id": self.attempt_id,
            "occurred_at_epoch_ms": self.occurred_at_epoch_ms,
            "event_type": self.event_type.value,
            "step_id": self.step_id,
        }
        if self.assistance_mode is not None:
            payload["assistance_mode"] = self.assistance_mode.value
        if self.duration_ms is not None:
            payload["duration_ms"] = self.duration_ms
        if self.transition_reason is not None:
            payload["transition_reason"] = self.transition_reason.value
        return payload


@dataclass(frozen=True, slots=True)
class ProposalDecisionEvent:
    trace_id: str
    attempt_id: str
    proposal_id: str
    occurred_at_epoch_ms: int
    outcome: ProposalOutcome

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, ProposalOutcome):
            raise ValueError("outcome must be a ProposalOutcome")
        _validate_pseudonymous_id(self.trace_id, "trace", "trace_id")
        _validate_pseudonymous_id(self.attempt_id, "attempt", "attempt_id")
        _validate_pseudonymous_id(self.proposal_id, "proposal", "proposal_id")
        _validate_nonnegative_int(self.occurred_at_epoch_ms, "occurred_at_epoch_ms")

    def to_dict(self) -> dict[str, object]:
        return {
            "contract_version": OBSERVABILITY_CONTRACT_VERSION,
            "event_family": "proposal_decision",
            "trace_id": self.trace_id,
            "attempt_id": self.attempt_id,
            "proposal_id": self.proposal_id,
            "occurred_at_epoch_ms": self.occurred_at_epoch_ms,
            "outcome": self.outcome.value,
        }


@dataclass(frozen=True, slots=True)
class ChangeTraceEvent:
    trace_id: str
    attempt_id: str
    change_id: str
    occurred_at_epoch_ms: int
    event_type: ChangeEventType
    rollback_reason: RollbackReason | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.event_type, ChangeEventType):
            raise ValueError("event_type must be a ChangeEventType")
        if self.rollback_reason is not None and not isinstance(
            self.rollback_reason, RollbackReason
        ):
            raise ValueError("rollback_reason must be a RollbackReason")
        _validate_pseudonymous_id(self.trace_id, "trace", "trace_id")
        _validate_pseudonymous_id(self.attempt_id, "attempt", "attempt_id")
        _validate_pseudonymous_id(self.change_id, "change", "change_id")
        _validate_nonnegative_int(self.occurred_at_epoch_ms, "occurred_at_epoch_ms")
        if self.event_type is ChangeEventType.POST_SAVE_ROLLED_BACK:
            if self.rollback_reason is None:
                raise ValueError("post-save rollback events require rollback_reason")
        elif self.rollback_reason is not None:
            raise ValueError("rollback_reason is allowed only on rollback events")

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "contract_version": OBSERVABILITY_CONTRACT_VERSION,
            "event_family": "change",
            "trace_id": self.trace_id,
            "attempt_id": self.attempt_id,
            "change_id": self.change_id,
            "occurred_at_epoch_ms": self.occurred_at_epoch_ms,
            "event_type": self.event_type.value,
        }
        if self.rollback_reason is not None:
            payload["rollback_reason"] = self.rollback_reason.value
        return payload


@dataclass(frozen=True, slots=True)
class SurveyTraceEvent:
    """Optional sampled post-task ratings; satisfaction is intentionally omitted."""

    trace_id: str
    attempt_id: str
    occurred_at_epoch_ms: int
    helpfulness: int | None = None
    trust: int | None = None
    effort_reduction: int | None = None

    def __post_init__(self) -> None:
        _validate_pseudonymous_id(self.trace_id, "trace", "trace_id")
        _validate_pseudonymous_id(self.attempt_id, "attempt", "attempt_id")
        _validate_nonnegative_int(self.occurred_at_epoch_ms, "occurred_at_epoch_ms")
        ratings = (self.helpfulness, self.trust, self.effort_reduction)
        if all(rating is None for rating in ratings):
            raise ValueError("survey events require at least one rating")
        if any(
            rating is not None
            and (
                isinstance(rating, bool)
                or not isinstance(rating, int)
                or not 1 <= rating <= 5
            )
            for rating in ratings
        ):
            raise ValueError("survey ratings must be integers from 1 to 5")

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "contract_version": OBSERVABILITY_CONTRACT_VERSION,
            "event_family": "survey",
            "trace_id": self.trace_id,
            "attempt_id": self.attempt_id,
            "occurred_at_epoch_ms": self.occurred_at_epoch_ms,
        }
        for name in ("helpfulness", "trust", "effort_reduction"):
            value = getattr(self, name)
            if value is not None:
                payload[name] = value
        return payload


ObservabilityEvent: TypeAlias = (
    OperationTraceEvent
    | AttemptTraceEvent
    | ProposalDecisionEvent
    | ChangeTraceEvent
    | SurveyTraceEvent
)
_EVENT_TYPES = (
    OperationTraceEvent,
    AttemptTraceEvent,
    ProposalDecisionEvent,
    ChangeTraceEvent,
    SurveyTraceEvent,
)


class TraceSink(Protocol):
    def emit(self, event: ObservabilityEvent) -> None: ...


class FakeTraceSink:
    """Deterministic in-memory sink for the P11 foundation baseline."""

    def __init__(self) -> None:
        self._events: list[ObservabilityEvent] = []

    def emit(self, event: ObservabilityEvent) -> None:
        if not isinstance(event, _EVENT_TYPES):
            raise TypeError("fake trace sink accepts only typed observability events")
        self._events.append(event)

    @property
    def events(self) -> tuple[ObservabilityEvent, ...]:
        return tuple(self._events)

    def serialized_events(self) -> tuple[dict[str, object], ...]:
        return tuple(event.to_dict() for event in self._events)

    def clear(self) -> None:
        self._events.clear()


class TraceExportBlockedError(RuntimeError):
    """Raised when external tracing is requested before F11 integration."""


def _raw_model_tracing_requested() -> bool:
    return any(
        os.environ.get(name, "").lower() in _TRUTHY
        for name in (
            "LANGCHAIN_TRACING_V2",
            "LANGSMITH_TRACING",
        )
    )


def get_callbacks() -> list[object]:
    """Reject raw LangChain tracing and return no model-level callbacks.

    LangSmith may attach itself from environment variables without an explicit
    callback, so its enable flags are checked here too. Production startup calls
    this function before accepting traffic; runtime calls keep the boundary
    fail-closed if process environment is mutated later.
    """

    if _raw_model_tracing_requested():
        raise TraceExportBlockedError(
            "raw external tracing bypasses the masked instrumentation exporter"
        )
    return []
