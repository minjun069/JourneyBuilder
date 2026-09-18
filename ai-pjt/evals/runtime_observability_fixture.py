"""Deterministic synthetic F10 cohort used to create the checked-in baseline."""

from __future__ import annotations

from core.monitoring import OperationStage, OperationStatus, OperationTraceEvent
from core.runtime_instrumentation import (
    RuntimeStage,
    RuntimeStatus,
    RuntimeTraceEvent,
    build_artifact_id,
)


def reference_runtime_events() -> tuple[RuntimeTraceEvent | OperationTraceEvent, ...]:
    events: list[RuntimeTraceEvent | OperationTraceEvent] = []
    events.extend(_successful_partial_request(1))
    events.extend(_all_tasks_failed_request(2))
    events.extend(_timeout_request(3))
    events.extend(_callback_attempts())
    return tuple(events)


def _runtime(
    number: int,
    stage: RuntimeStage,
    status: RuntimeStatus,
    *,
    occurred_at: int,
    duration_ms: int | None = None,
    reason_code: str | None = None,
    task_id: str | None = None,
    depends_on: tuple[str, ...] = (),
    blocked_by: tuple[str, ...] = (),
    executor_kind: str | None = None,
    executor_name: str | None = None,
    response_owner: str | None = None,
    artifact_type: str | None = None,
) -> RuntimeTraceEvent:
    request_id = f"request-{number:08d}"
    artifact_id = None
    producer_task_id = None
    if artifact_type is not None:
        producer_task_id = task_id
        artifact_id = build_artifact_id(
            request_id=request_id,
            producer_task_id=str(task_id),
            artifact_type=artifact_type,
        )
    return RuntimeTraceEvent(
        trace_id=f"trace_{number:08d}",
        request_id=request_id,
        thread_id=f"thread-{number:08d}",
        occurred_at_epoch_ms=occurred_at,
        stage=stage,
        status=status,
        task_id=task_id,
        depends_on=depends_on,
        blocked_by=blocked_by,
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        producer_task_id=producer_task_id,
        duration_ms=duration_ms,
        reason_code=reason_code,
        executor_kind=executor_kind,
        executor_name=executor_name,
        response_owner=response_owner,
    )


def _pair(number: int, stage: RuntimeStage, *, start: int, duration: int, **values):
    started = _runtime(number, stage, RuntimeStatus.STARTED, occurred_at=start, **values)
    terminal = _runtime(
        number,
        stage,
        RuntimeStatus.SUCCEEDED,
        occurred_at=start + duration,
        duration_ms=duration,
        **values,
    )
    return started, terminal


def _successful_partial_request(number: int):
    events: list[RuntimeTraceEvent] = []
    events.extend(_pair(number, RuntimeStage.REQUEST_PROCESSING, start=1_000, duration=100))
    events.extend(_pair(number, RuntimeStage.PLANNER, start=1_001, duration=10))
    events.extend(_pair(number, RuntimeStage.PLAN_VALIDATION, start=1_011, duration=5))
    events.extend(_pair(
        number,
        RuntimeStage.TASK_EXECUTION,
        start=1_020,
        duration=30,
        task_id="task-1",
        executor_kind="agent",
        executor_name="qa",
        response_owner="agent",
    ))
    events.extend(_pair(
        number,
        RuntimeStage.ARTIFACT_VALIDATION,
        start=1_050,
        duration=5,
        task_id="task-1",
        artifact_type="text_response",
    ))
    events.extend(_pair(
        number,
        RuntimeStage.BINDING_RESOLUTION,
        start=1_056,
        duration=2,
        task_id="task-2",
    ))
    tool_start = _runtime(
        number,
        RuntimeStage.TASK_EXECUTION,
        RuntimeStatus.STARTED,
        occurred_at=1_060,
        task_id="task-2",
        executor_kind="tool",
        executor_name="get_approved_reference_value",
    )
    tool_failed = _runtime(
        number,
        RuntimeStage.TASK_EXECUTION,
        RuntimeStatus.FAILED,
        occurred_at=1_080,
        duration_ms=20,
        reason_code="tool_timeout",
        task_id="task-2",
        executor_kind="tool",
        executor_name="get_approved_reference_value",
    )
    events.extend((tool_start, tool_failed))
    events.append(_runtime(
        number,
        RuntimeStage.DEPENDENCY_BLOCK,
        RuntimeStatus.BLOCKED,
        occurred_at=1_081,
        duration_ms=0,
        reason_code="dependency_failed",
        task_id="task-3",
        depends_on=("task-2",),
        blocked_by=("task-2",),
    ))
    events.extend(_pair(number, RuntimeStage.AGGREGATION, start=1_082, duration=10))
    return events


def _all_tasks_failed_request(number: int):
    events: list[RuntimeTraceEvent] = []
    events.extend(_pair(number, RuntimeStage.REQUEST_PROCESSING, start=2_000, duration=90))
    tool_start = _runtime(
        number,
        RuntimeStage.TASK_EXECUTION,
        RuntimeStatus.STARTED,
        occurred_at=2_010,
        task_id="task-1",
        executor_kind="tool",
        executor_name="get_approved_reference_value",
    )
    tool_failed = _runtime(
        number,
        RuntimeStage.TASK_EXECUTION,
        RuntimeStatus.FAILED,
        occurred_at=2_030,
        duration_ms=20,
        reason_code="tool_dependency_unavailable",
        task_id="task-1",
        executor_kind="tool",
        executor_name="get_approved_reference_value",
    )
    events.extend((tool_start, tool_failed))
    events.extend(_pair(number, RuntimeStage.AGGREGATION, start=2_031, duration=8))
    return events


def _timeout_request(number: int):
    return (
        _runtime(
            number,
            RuntimeStage.REQUEST_PROCESSING,
            RuntimeStatus.STARTED,
            occurred_at=3_000,
        ),
        _runtime(
            number,
            RuntimeStage.REQUEST_PROCESSING,
            RuntimeStatus.FAILED,
            occurred_at=3_200,
            duration_ms=200,
            reason_code="request_timeout",
        ),
    )


def _callback_attempts():
    specs = (
        (1, 1, OperationStatus.FAILED, "callback_http_503", 10),
        (1, 2, OperationStatus.FAILED, "callback_http_503", 12),
        (1, 3, OperationStatus.SUCCEEDED, None, 8),
        (2, 1, OperationStatus.TIMEOUT, "callback_timeout", 50),
        (2, 2, OperationStatus.TIMEOUT, "callback_timeout", 50),
        (2, 3, OperationStatus.TIMEOUT, "callback_timeout", 50),
        (2, 4, OperationStatus.TIMEOUT, "callback_timeout", 50),
        (3, 1, OperationStatus.SUCCEEDED, None, 7),
    )
    return tuple(
        OperationTraceEvent(
            trace_id=f"trace_{number:08d}",
            attempt_id=f"attempt_{number:08d}",
            occurred_at_epoch_ms=4_000 + number * 100 + attempt,
            stage=OperationStage.CALLBACK,
            status=status,
            duration_ms=duration,
            operation_name="backend_callback",
            step_id="basic-info",
            error_code=error_code,
        )
        for number, attempt, status, error_code, duration in specs
    )
