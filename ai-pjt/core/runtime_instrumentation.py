"""Sink-independent runtime instrumentation contracts for F10-T01.

The port accepts the existing P11 observability events and runtime orchestration
events without exposing arbitrary metadata.  Producers must pass only
pseudonymous correlation identifiers and stable machine tokens; message text,
customer snapshots, tool arguments, and artifact values do not belong here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, TypeAlias

from core.monitoring import (
    AttemptTraceEvent,
    ChangeTraceEvent,
    ObservabilityEvent,
    OperationTraceEvent,
    ProposalDecisionEvent,
    SurveyTraceEvent,
)


RUNTIME_INSTRUMENTATION_CONTRACT_VERSION = "1.2"

_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")
_PSEUDONYMOUS_SUFFIX = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_FOUNDATION_EVENT_TYPES = (
    OperationTraceEvent,
    AttemptTraceEvent,
    ProposalDecisionEvent,
    ChangeTraceEvent,
    SurveyTraceEvent,
)


class RuntimeStage(str, Enum):
    REQUEST_PROCESSING = "request_processing"
    PLANNER = "planner"
    PLAN_VALIDATION = "plan_validation"
    BINDING_RESOLUTION = "binding_resolution"
    ARTIFACT_VALIDATION = "artifact_validation"
    TASK_EXECUTION = "task_execution"
    DEPENDENCY_BLOCK = "dependency_block"
    AGGREGATION = "aggregation"


class RuntimeStatus(str, Enum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"


_TASK_SCOPED_STAGES = frozenset(
    {
        RuntimeStage.BINDING_RESOLUTION,
        RuntimeStage.ARTIFACT_VALIDATION,
        RuntimeStage.TASK_EXECUTION,
        RuntimeStage.DEPENDENCY_BLOCK,
    }
)


def _validate_safe_token(value: str, field_name: str) -> None:
    if not isinstance(value, str) or _SAFE_TOKEN.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a non-sensitive machine token")


def _validate_trace_id(value: str) -> None:
    expected = "trace_"
    if not isinstance(value, str) or not value.startswith(expected):
        raise ValueError("trace_id must start with 'trace_'")
    if _PSEUDONYMOUS_SUFFIX.fullmatch(value[len(expected):]) is None:
        raise ValueError(
            "trace_id must contain an 8-64 character pseudonymous suffix"
        )


def _validate_nonnegative_int(value: int | None, field_name: str) -> None:
    if value is not None and (
        isinstance(value, bool) or not isinstance(value, int) or value < 0
    ):
        raise ValueError(f"{field_name} must be a non-negative integer")


def _validate_identifier_tuple(value: tuple[str, ...], field_name: str) -> None:
    if not isinstance(value, tuple):
        raise ValueError(f"{field_name} must be a tuple of machine tokens")
    for identifier in value:
        _validate_safe_token(identifier, field_name)
    if len(value) != len(set(value)):
        raise ValueError(f"{field_name} cannot contain duplicates")


def build_artifact_id(
    *,
    plan_id: str,
    producer_task_id: str,
    artifact_type: str,
) -> str:
    """Build the observation-only identity for a task's single typed artifact."""

    _validate_safe_token(plan_id, "plan_id")
    _validate_safe_token(producer_task_id, "producer_task_id")
    _validate_safe_token(artifact_type, "artifact_type")
    return f"{plan_id}:{producer_task_id}:{artifact_type}"


@dataclass(frozen=True, slots=True)
class RuntimeTraceEvent:
    """One validated planner, task, dependency, artifact, or aggregation event."""

    trace_id: str
    request_id: str
    thread_id: str
    plan_id: str
    occurred_at_epoch_ms: int
    stage: RuntimeStage
    status: RuntimeStatus
    task_id: str | None = None
    depends_on: tuple[str, ...] = ()
    blocked_by: tuple[str, ...] = ()
    artifact_id: str | None = None
    artifact_type: str | None = None
    producer_task_id: str | None = None
    duration_ms: int | None = None
    reason_code: str | None = None
    executor_kind: str | None = None
    executor_name: str | None = None
    response_owner: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.stage, RuntimeStage):
            raise ValueError("stage must be a RuntimeStage")
        if not isinstance(self.status, RuntimeStatus):
            raise ValueError("status must be a RuntimeStatus")
        _validate_trace_id(self.trace_id)
        for field_name in ("request_id", "thread_id", "plan_id"):
            _validate_safe_token(getattr(self, field_name), field_name)
        _validate_nonnegative_int(self.occurred_at_epoch_ms, "occurred_at_epoch_ms")
        _validate_nonnegative_int(self.duration_ms, "duration_ms")
        _validate_identifier_tuple(self.depends_on, "depends_on")
        _validate_identifier_tuple(self.blocked_by, "blocked_by")

        task_scoped = self.stage in _TASK_SCOPED_STAGES
        if task_scoped:
            if self.task_id is None:
                raise ValueError(f"{self.stage.value} events require task_id")
            _validate_safe_token(self.task_id, "task_id")
        elif self.task_id is not None or self.depends_on or self.blocked_by:
            raise ValueError("plan-scoped events cannot contain task identifiers")

        terminal = self.status is not RuntimeStatus.STARTED
        if terminal and self.duration_ms is None:
            raise ValueError("terminal events require duration_ms")
        if not terminal and self.duration_ms is not None:
            raise ValueError("started events cannot contain duration_ms")
        failed = self.status in {RuntimeStatus.FAILED, RuntimeStatus.BLOCKED}
        if failed and self.reason_code is None:
            raise ValueError("failed or blocked events require reason_code")
        if not failed and self.reason_code is not None:
            raise ValueError("started or succeeded events cannot contain reason_code")
        if self.reason_code is not None:
            _validate_safe_token(self.reason_code, "reason_code")
        for field_name in ("executor_kind", "executor_name", "response_owner"):
            value = getattr(self, field_name)
            if value is not None:
                _validate_safe_token(value, field_name)
        if self.stage is not RuntimeStage.TASK_EXECUTION and (
            self.executor_kind is not None
            or self.executor_name is not None
            or self.response_owner is not None
        ):
            raise ValueError(
                "executor and response-owner fields are allowed only on task execution events"
            )
        if (self.executor_kind is None) != (self.executor_name is None):
            raise ValueError("executor_kind and executor_name must be provided together")

        if self.status is RuntimeStatus.BLOCKED and (
            self.stage is not RuntimeStage.DEPENDENCY_BLOCK
        ):
            raise ValueError("blocked status is reserved for dependency_block events")
        if self.stage is RuntimeStage.DEPENDENCY_BLOCK:
            if self.status is not RuntimeStatus.BLOCKED:
                raise ValueError("dependency_block events must have blocked status")
            if not self.blocked_by:
                raise ValueError("dependency_block events require blocked_by")
            if not set(self.blocked_by).issubset(self.depends_on):
                raise ValueError("blocked_by must be a subset of depends_on")
        elif self.blocked_by:
            raise ValueError("blocked_by is allowed only on dependency_block events")

        artifact_fields = (
            self.artifact_id,
            self.artifact_type,
            self.producer_task_id,
        )
        if self.stage is RuntimeStage.ARTIFACT_VALIDATION:
            if any(value is None for value in artifact_fields):
                raise ValueError(
                    "artifact_validation events require artifact identity fields"
                )
            assert self.artifact_id is not None
            assert self.artifact_type is not None
            assert self.producer_task_id is not None
            _validate_safe_token(self.artifact_type, "artifact_type")
            _validate_safe_token(self.producer_task_id, "producer_task_id")
            if self.producer_task_id != self.task_id:
                raise ValueError("producer_task_id must match task_id")
            expected_artifact_id = build_artifact_id(
                plan_id=self.plan_id,
                producer_task_id=self.producer_task_id,
                artifact_type=self.artifact_type,
            )
            if self.artifact_id != expected_artifact_id:
                raise ValueError("artifact_id does not match its correlation fields")
        elif any(value is not None for value in artifact_fields):
            raise ValueError(
                "artifact identity fields are allowed only on artifact_validation events"
            )

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "contract_version": RUNTIME_INSTRUMENTATION_CONTRACT_VERSION,
            "event_family": "runtime",
            "trace_id": self.trace_id,
            "request_id": self.request_id,
            "thread_id": self.thread_id,
            "plan_id": self.plan_id,
            "occurred_at_epoch_ms": self.occurred_at_epoch_ms,
            "stage": self.stage.value,
            "status": self.status.value,
        }
        if self.task_id is not None:
            payload["task_id"] = self.task_id
            payload["depends_on"] = list(self.depends_on)
        if self.blocked_by:
            payload["blocked_by"] = list(self.blocked_by)
        for field_name in (
            "artifact_id",
            "artifact_type",
            "producer_task_id",
            "duration_ms",
            "reason_code",
            "executor_kind",
            "executor_name",
            "response_owner",
        ):
            value = getattr(self, field_name)
            if value is not None:
                payload[field_name] = value
        return payload


InstrumentationEvent: TypeAlias = ObservabilityEvent | RuntimeTraceEvent


def _is_foundation_event(event: object) -> bool:
    return isinstance(event, _FOUNDATION_EVENT_TYPES)


def _is_instrumentation_event(event: object) -> bool:
    return isinstance(event, RuntimeTraceEvent) or _is_foundation_event(event)


class InstrumentationSink(Protocol):
    def emit(self, event: InstrumentationEvent) -> None: ...


class InstrumentationPort:
    """Validate the typed boundary and delegate storage to the configured sink."""

    __slots__ = ("_sink",)

    def __init__(self, sink: InstrumentationSink) -> None:
        if not callable(getattr(sink, "emit", None)):
            raise TypeError("instrumentation sink must provide emit(event)")
        self._sink = sink

    def emit(self, event: InstrumentationEvent) -> None:
        if not _is_instrumentation_event(event):
            raise TypeError("instrumentation port accepts only typed events")
        self._sink.emit(event)


class FakeInstrumentationSink:
    """Deterministic in-memory sink shared by P11 and runtime contract tests."""

    def __init__(self) -> None:
        self._events: list[InstrumentationEvent] = []

    def emit(self, event: InstrumentationEvent) -> None:
        if not _is_instrumentation_event(event):
            raise TypeError("fake instrumentation sink accepts only typed events")
        self._events.append(event)

    @property
    def events(self) -> tuple[InstrumentationEvent, ...]:
        return tuple(self._events)

    @property
    def baseline_events(self) -> tuple[ObservabilityEvent, ...]:
        return tuple(
            event for event in self._events if _is_foundation_event(event)
        )

    @property
    def runtime_events(self) -> tuple[RuntimeTraceEvent, ...]:
        return tuple(
            event for event in self._events if isinstance(event, RuntimeTraceEvent)
        )

    def serialized_events(self) -> tuple[dict[str, object], ...]:
        return tuple(event.to_dict() for event in self._events)

    def clear(self) -> None:
        self._events.clear()
