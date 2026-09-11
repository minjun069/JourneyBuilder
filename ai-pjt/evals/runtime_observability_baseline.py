"""Build the F10 runtime baseline from closed, masking-safe event cohorts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import ceil, floor
from collections import Counter, defaultdict
from typing import Iterable

from core.monitoring import OperationStage, OperationStatus, OperationTraceEvent
from core.runtime_instrumentation import RuntimeStage, RuntimeStatus, RuntimeTraceEvent
from evals.observability_baseline import Distribution, Rate


RUNTIME_BASELINE_SCHEMA_VERSION = "f10-runtime-observability-baseline-v1"


@dataclass(frozen=True, slots=True)
class RuntimeStageBaseline:
    total: int
    succeeded: int
    failed: int
    blocked: int
    success_rate: Rate
    latency_ms: Distribution
    reason_counts: dict[str, int]


@dataclass(frozen=True, slots=True)
class TaskOutcomeBaseline:
    total: int
    completed: int
    failed: int
    blocked: int
    completion_rate: Rate
    by_executor: dict[str, dict[str, int]]


@dataclass(frozen=True, slots=True)
class CallbackDeliveryBaseline:
    requests: int
    attempts: int
    succeeded_attempts: int
    failed_attempts: int
    timeout_attempts: int
    retried_requests: int
    eventually_succeeded: int
    exhausted: int
    latency_ms: Distribution


@dataclass(frozen=True, slots=True)
class RuntimeObservabilityBaseline:
    schema_version: str
    measurement_disposition: str
    requests: int
    request_success_rate: Rate
    request_timeout_count: int
    task_outcomes: TaskOutcomeBaseline
    stages: dict[str, RuntimeStageBaseline]
    callback_delivery: CallbackDeliveryBaseline
    fallback_request_rate: Rate
    aggregation_success_rate: Rate

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def build_runtime_observability_baseline(
    events: Iterable[RuntimeTraceEvent | OperationTraceEvent],
) -> RuntimeObservabilityBaseline:
    """Aggregate one closed cohort and reject incomplete runtime spans."""

    collected = tuple(events)
    if any(not isinstance(event, (RuntimeTraceEvent, OperationTraceEvent)) for event in collected):
        raise TypeError("runtime baseline accepts only runtime and operation events")
    runtime_events = [event for event in collected if isinstance(event, RuntimeTraceEvent)]
    operation_events = [event for event in collected if isinstance(event, OperationTraceEvent)]
    _validate_closed_runtime_spans(runtime_events)

    terminal_by_stage = {
        stage: [
            event
            for event in runtime_events
            if event.stage is stage and event.status is not RuntimeStatus.STARTED
        ]
        for stage in RuntimeStage
    }
    stages = {
        stage.value: _stage_baseline(terminal_by_stage[stage])
        for stage in RuntimeStage
    }

    task_terminals = terminal_by_stage[RuntimeStage.TASK_EXECUTION]
    blocked_terminals = terminal_by_stage[RuntimeStage.DEPENDENCY_BLOCK]
    completed = sum(event.status is RuntimeStatus.SUCCEEDED for event in task_terminals)
    failed = sum(event.status is RuntimeStatus.FAILED for event in task_terminals)
    blocked = len(blocked_terminals)
    by_executor: dict[str, Counter[str]] = defaultdict(Counter)
    for event in task_terminals:
        outcome = "completed" if event.status is RuntimeStatus.SUCCEEDED else "failed"
        by_executor[event.executor_name or "unassigned"][outcome] += 1
    for event in blocked_terminals:
        by_executor["dependency_block"]["blocked"] += 1
    serialized_executors = {
        name: {
            "completed": counts["completed"],
            "failed": counts["failed"],
            "blocked": counts["blocked"],
        }
        for name, counts in sorted(by_executor.items())
    }
    total_tasks = completed + failed + blocked
    task_outcomes = TaskOutcomeBaseline(
        total=total_tasks,
        completed=completed,
        failed=failed,
        blocked=blocked,
        completion_rate=Rate(completed, total_tasks),
        by_executor=serialized_executors,
    )

    request_terminals = terminal_by_stage[RuntimeStage.REQUEST_PROCESSING]
    request_successes = sum(
        event.status is RuntimeStatus.SUCCEEDED for event in request_terminals
    )
    request_timeouts = sum(
        event.reason_code == "request_timeout" for event in request_terminals
    )
    fallback_requests = {
        event.trace_id
        for event in task_terminals
        if event.executor_name == "fallback"
    }
    aggregations = terminal_by_stage[RuntimeStage.AGGREGATION]
    aggregation_successes = sum(
        event.status is RuntimeStatus.SUCCEEDED for event in aggregations
    )

    return RuntimeObservabilityBaseline(
        schema_version=RUNTIME_BASELINE_SCHEMA_VERSION,
        measurement_disposition="baseline_only",
        requests=len(request_terminals),
        request_success_rate=Rate(request_successes, len(request_terminals)),
        request_timeout_count=request_timeouts,
        task_outcomes=task_outcomes,
        stages=stages,
        callback_delivery=_callback_baseline(operation_events),
        fallback_request_rate=Rate(len(fallback_requests), len(request_terminals)),
        aggregation_success_rate=Rate(aggregation_successes, len(aggregations)),
    )


def _span_key(event: RuntimeTraceEvent) -> tuple[str, str, RuntimeStage, str | None, str | None]:
    return (
        event.trace_id,
        event.plan_id,
        event.stage,
        event.task_id,
        event.artifact_id,
    )


def _validate_closed_runtime_spans(events: list[RuntimeTraceEvent]) -> None:
    starts: dict[tuple[str, str, RuntimeStage, str | None, str | None], RuntimeTraceEvent] = {}
    terminals: set[tuple[str, str, RuntimeStage, str | None, str | None]] = set()
    for event in events:
        key = _span_key(event)
        if event.status is RuntimeStatus.STARTED:
            if key in starts:
                raise ValueError(f"duplicate runtime span start: {key}")
            starts[key] = event
            continue
        if event.stage is RuntimeStage.DEPENDENCY_BLOCK:
            if key in terminals:
                raise ValueError(f"duplicate runtime span terminal: {key}")
            terminals.add(key)
            continue
        if key not in starts:
            raise ValueError(f"runtime terminal without start: {key}")
        if key in terminals:
            raise ValueError(f"duplicate runtime span terminal: {key}")
        terminals.add(key)
    unfinished = set(starts) - terminals
    if unfinished:
        raise ValueError(f"runtime span without terminal: {sorted(map(str, unfinished))}")


def _stage_baseline(events: list[RuntimeTraceEvent]) -> RuntimeStageBaseline:
    succeeded = sum(event.status is RuntimeStatus.SUCCEEDED for event in events)
    failed = sum(event.status is RuntimeStatus.FAILED for event in events)
    blocked = sum(event.status is RuntimeStatus.BLOCKED for event in events)
    reasons = Counter(
        event.reason_code for event in events if event.reason_code is not None
    )
    return RuntimeStageBaseline(
        total=len(events),
        succeeded=succeeded,
        failed=failed,
        blocked=blocked,
        success_rate=Rate(succeeded, len(events)),
        latency_ms=_distribution([event.duration_ms or 0 for event in events]),
        reason_counts=dict(sorted(reasons.items())),
    )


def _callback_baseline(events: list[OperationTraceEvent]) -> CallbackDeliveryBaseline:
    callbacks = [event for event in events if event.stage is OperationStage.CALLBACK]
    by_trace: dict[str, list[OperationTraceEvent]] = defaultdict(list)
    for event in callbacks:
        by_trace[event.trace_id].append(event)
    for trace_events in by_trace.values():
        trace_events.sort(key=lambda event: event.occurred_at_epoch_ms)
    return CallbackDeliveryBaseline(
        requests=len(by_trace),
        attempts=len(callbacks),
        succeeded_attempts=sum(
            event.status is OperationStatus.SUCCEEDED for event in callbacks
        ),
        failed_attempts=sum(
            event.status is OperationStatus.FAILED for event in callbacks
        ),
        timeout_attempts=sum(
            event.status is OperationStatus.TIMEOUT for event in callbacks
        ),
        retried_requests=sum(len(trace_events) > 1 for trace_events in by_trace.values()),
        eventually_succeeded=sum(
            trace_events[-1].status is OperationStatus.SUCCEEDED
            for trace_events in by_trace.values()
        ),
        exhausted=sum(
            trace_events[-1].status is not OperationStatus.SUCCEEDED
            for trace_events in by_trace.values()
        ),
        latency_ms=_distribution([event.duration_ms for event in callbacks]),
    )


def _distribution(values: list[int]) -> Distribution:
    return Distribution(
        count=len(values),
        p50=_percentile(values, 0.50),
        p90=_percentile(values, 0.90),
        p95=_percentile(values, 0.95),
    )


def _percentile(values: list[int], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = floor(position)
    upper = ceil(position)
    if lower == upper:
        return float(ordered[lower])
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight
