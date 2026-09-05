"""Aggregate safe P11 trace events into operational and AX baselines.

This module defines measurements, not targets. Thresholds and SLOs remain
owned by the requirements and evaluation documents and are set only after a
real baseline has been reviewed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from math import ceil, floor
from statistics import fmean

from core.monitoring import (
    AssistanceMode,
    AttemptEventType,
    AttemptTraceEvent,
    ChangeEventType,
    ChangeTraceEvent,
    ObservabilityEvent,
    OperationStage,
    OperationStatus,
    OperationTraceEvent,
    ProposalDecisionEvent,
    ProposalOutcome,
    RollbackReason,
    SurveyTraceEvent,
)


@dataclass(frozen=True, slots=True)
class Rate:
    numerator: int
    denominator: int
    value: float | None = field(init=False)

    def __post_init__(self) -> None:
        if self.numerator < 0 or self.denominator < 0:
            raise ValueError("rate counts must be non-negative")
        if self.numerator > self.denominator:
            raise ValueError("rate numerator cannot exceed denominator")
        value = self.numerator / self.denominator if self.denominator else None
        object.__setattr__(self, "value", value)


@dataclass(frozen=True, slots=True)
class Distribution:
    count: int
    p50: float | None
    p90: float | None
    p95: float | None


@dataclass(frozen=True, slots=True)
class OperationBaseline:
    total: int
    succeeded: int
    failed: int
    timeout: int
    blocked: int
    success_rate: Rate
    latency_ms: Distribution
    input_tokens: int
    output_tokens: int
    cost_microusd: int


@dataclass(frozen=True, slots=True)
class SurveyBaseline:
    response_count: int
    helpfulness_mean: float | None
    trust_mean: float | None
    effort_reduction_mean: float | None


@dataclass(frozen=True, slots=True)
class CohortBaseline:
    started: int
    valid_completed: int
    abandoned: int
    valid_completion_rate: Rate
    abandonment_rate: Rate
    active_completion_time_ms: Distribution
    user_turns_to_completion: Distribution


@dataclass(frozen=True, slots=True)
class ExperimentComparison:
    ai: CohortBaseline
    no_ai: CohortBaseline
    valid_completion_rate_delta: float | None
    abandonment_rate_delta: float | None
    active_time_p50_delta_ms: float | None


@dataclass(frozen=True, slots=True)
class ObservabilityBaseline:
    started_attempts: int
    valid_completed_attempts: int
    abandoned_attempts: int
    valid_completion_rate: Rate
    abandonment_rate: Rate
    active_completion_time_ms: Distribution
    active_time_coverage: Rate
    user_turns_to_completion: Distribution
    pre_save_rework_rate: Rate
    post_save_rollback_rate: Rate
    wrong_suggestion_rollback_rate: Rate
    manual_transition_rate: Rate
    proposal_outcomes: dict[str, Rate]
    callback_completion_rate: Rate
    deterministic_validation_block_rate: Rate
    total_ai_cost_microusd: int
    ai_cost_per_valid_completion_microusd: float | None
    operation_stages: dict[str, OperationBaseline]
    survey: SurveyBaseline
    experiment: ExperimentComparison

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible report without adding target thresholds."""

        return asdict(self)


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


def _distribution(values: list[int]) -> Distribution:
    return Distribution(
        count=len(values),
        p50=_percentile(values, 0.50),
        p90=_percentile(values, 0.90),
        p95=_percentile(values, 0.95),
    )


def _rate(numerator: int, denominator: int) -> Rate:
    return Rate(numerator=numerator, denominator=denominator)


def _mean(values: list[int]) -> float | None:
    return fmean(values) if values else None


def _operation_baseline(events: list[OperationTraceEvent]) -> OperationBaseline:
    counts = {status: 0 for status in OperationStatus}
    for event in events:
        counts[event.status] += 1
    return OperationBaseline(
        total=len(events),
        succeeded=counts[OperationStatus.SUCCEEDED],
        failed=counts[OperationStatus.FAILED],
        timeout=counts[OperationStatus.TIMEOUT],
        blocked=counts[OperationStatus.BLOCKED],
        success_rate=_rate(counts[OperationStatus.SUCCEEDED], len(events)),
        latency_ms=_distribution([event.duration_ms for event in events]),
        input_tokens=sum(event.input_tokens or 0 for event in events),
        output_tokens=sum(event.output_tokens or 0 for event in events),
        cost_microusd=sum(event.cost_microusd or 0 for event in events),
    )


def _cohort_baseline(
    attempt_ids: set[str],
    completed: set[str],
    abandoned: set[str],
    active_time_by_attempt: dict[str, int],
    user_turns_by_attempt: dict[str, int],
) -> CohortBaseline:
    cohort_completed = attempt_ids & completed
    cohort_abandoned = attempt_ids & abandoned
    active_times = [
        active_time_by_attempt[attempt_id]
        for attempt_id in cohort_completed
        if attempt_id in active_time_by_attempt
    ]
    turns = [user_turns_by_attempt.get(attempt_id, 0) for attempt_id in cohort_completed]
    return CohortBaseline(
        started=len(attempt_ids),
        valid_completed=len(cohort_completed),
        abandoned=len(cohort_abandoned),
        valid_completion_rate=_rate(len(cohort_completed), len(attempt_ids)),
        abandonment_rate=_rate(len(cohort_abandoned), len(attempt_ids)),
        active_completion_time_ms=_distribution(active_times),
        user_turns_to_completion=_distribution(turns),
    )


def _delta(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return left - right


def build_observability_baseline(
    events: tuple[ObservabilityEvent, ...] | list[ObservabilityEvent],
) -> ObservabilityBaseline:
    """Build one deterministic baseline from a closed observation cohort.

    ``ABANDONED`` and ``NO_RESPONSE`` must be emitted only after their agreed
    observation windows close. The function rejects orphaned and contradictory
    events instead of silently producing misleading rates.
    """

    all_events = tuple(events)
    starts: dict[str, AttemptTraceEvent] = {}
    trace_by_attempt: dict[str, str] = {}
    completed: set[str] = set()
    abandoned: set[str] = set()
    manual_transitions: set[str] = set()
    active_time_by_attempt: dict[str, int] = {}
    user_turns_by_attempt: dict[str, int] = {}
    operation_events: list[OperationTraceEvent] = []
    proposal_events: list[ProposalDecisionEvent] = []
    change_events: list[ChangeTraceEvent] = []
    survey_events: list[SurveyTraceEvent] = []

    for event in all_events:
        previous_trace = trace_by_attempt.setdefault(event.attempt_id, event.trace_id)
        if previous_trace != event.trace_id:
            raise ValueError("one attempt_id cannot be associated with multiple trace_ids")
        if isinstance(event, AttemptTraceEvent):
            if event.event_type is AttemptEventType.STARTED:
                if event.attempt_id in starts:
                    raise ValueError(f"duplicate started event: {event.attempt_id}")
                starts[event.attempt_id] = event
            elif event.event_type is AttemptEventType.VALID_COMPLETED:
                if event.attempt_id in completed:
                    raise ValueError(f"duplicate completion event: {event.attempt_id}")
                completed.add(event.attempt_id)
            elif event.event_type is AttemptEventType.ABANDONED:
                if event.attempt_id in abandoned:
                    raise ValueError(f"duplicate abandonment event: {event.attempt_id}")
                abandoned.add(event.attempt_id)
            elif event.event_type is AttemptEventType.MANUAL_TRANSITION:
                manual_transitions.add(event.attempt_id)
            elif event.event_type is AttemptEventType.ACTIVE_INTERVAL:
                active_time_by_attempt[event.attempt_id] = (
                    active_time_by_attempt.get(event.attempt_id, 0)
                    + (event.duration_ms or 0)
                )
            elif event.event_type is AttemptEventType.USER_TURN:
                user_turns_by_attempt[event.attempt_id] = (
                    user_turns_by_attempt.get(event.attempt_id, 0) + 1
                )
        elif isinstance(event, OperationTraceEvent):
            operation_events.append(event)
        elif isinstance(event, ProposalDecisionEvent):
            proposal_events.append(event)
        elif isinstance(event, ChangeTraceEvent):
            change_events.append(event)
        elif isinstance(event, SurveyTraceEvent):
            survey_events.append(event)
        else:
            raise TypeError("baseline accepts only typed observability events")

    orphan_attempts = set(trace_by_attempt) - set(starts)
    if orphan_attempts:
        raise ValueError(f"events without started attempt: {sorted(orphan_attempts)}")
    conflicts = completed & abandoned
    if conflicts:
        raise ValueError(f"attempts cannot be completed and abandoned: {sorted(conflicts)}")

    for event in all_events:
        start = starts[event.attempt_id]
        if isinstance(event, AttemptTraceEvent) and event.step_id != start.step_id:
            raise ValueError("attempt lifecycle events must keep one step_id")

    proposal_by_id: dict[str, ProposalDecisionEvent] = {}
    for event in proposal_events:
        if event.proposal_id in proposal_by_id:
            raise ValueError(f"duplicate proposal decision: {event.proposal_id}")
        proposal_by_id[event.proposal_id] = event

    change_keys: set[tuple[str, ChangeEventType]] = set()
    applied_changes: set[str] = set()
    reworked_changes: set[str] = set()
    rolled_back_changes: set[str] = set()
    wrong_suggestion_rollbacks: set[str] = set()
    for event in change_events:
        key = (event.change_id, event.event_type)
        if key in change_keys:
            raise ValueError(
                f"duplicate change event: {event.change_id}/{event.event_type.value}"
            )
        change_keys.add(key)
        if event.event_type is ChangeEventType.APPLIED:
            applied_changes.add(event.change_id)
        elif event.event_type is ChangeEventType.PRE_SAVE_REWORKED:
            reworked_changes.add(event.change_id)
        elif event.event_type is ChangeEventType.POST_SAVE_ROLLED_BACK:
            rolled_back_changes.add(event.change_id)
            if event.rollback_reason is RollbackReason.WRONG_SUGGESTION:
                wrong_suggestion_rollbacks.add(event.change_id)
    if not reworked_changes <= applied_changes:
        raise ValueError("pre-save rework requires a matching applied change")
    if not rolled_back_changes <= applied_changes:
        raise ValueError("post-save rollback requires a matching applied change")

    survey_attempts: set[str] = set()
    for event in survey_events:
        if event.attempt_id in survey_attempts:
            raise ValueError(f"duplicate survey response: {event.attempt_id}")
        survey_attempts.add(event.attempt_id)

    operations_by_stage = {
        stage: [event for event in operation_events if event.stage is stage]
        for stage in OperationStage
    }
    operation_stages = {
        stage.value: _operation_baseline(stage_events)
        for stage, stage_events in operations_by_stage.items()
    }
    callback = operation_stages[OperationStage.CALLBACK.value]
    validation = operation_stages[OperationStage.OUTPUT_VALIDATION.value]

    proposal_counts = {outcome: 0 for outcome in ProposalOutcome}
    for event in proposal_events:
        proposal_counts[event.outcome] += 1
    proposal_total = len(proposal_events)
    proposal_outcomes = {
        outcome.value: _rate(proposal_counts[outcome], proposal_total)
        for outcome in ProposalOutcome
    }

    valid_completed_attempts = len(completed)
    ai_attempt_ids = {
        attempt_id
        for attempt_id, event in starts.items()
        if event.assistance_mode is AssistanceMode.AI
    }
    ai_completed_attempts = completed & ai_attempt_ids
    total_ai_cost = sum(
        event.cost_microusd or 0
        for event in operation_events
        if event.attempt_id in ai_attempt_ids
    )
    cost_per_completion = (
        total_ai_cost / len(ai_completed_attempts)
        if ai_completed_attempts
        else None
    )
    active_completion_times = [
        active_time_by_attempt[attempt_id]
        for attempt_id in completed
        if attempt_id in active_time_by_attempt
    ]
    completed_turns = [
        user_turns_by_attempt.get(attempt_id, 0) for attempt_id in completed
    ]

    started_by_mode = {
        mode: {
            attempt_id
            for attempt_id, event in starts.items()
            if event.assistance_mode is mode
        }
        for mode in AssistanceMode
    }
    ai_cohort = _cohort_baseline(
        started_by_mode[AssistanceMode.AI],
        completed,
        abandoned,
        active_time_by_attempt,
        user_turns_by_attempt,
    )
    no_ai_cohort = _cohort_baseline(
        started_by_mode[AssistanceMode.NO_AI],
        completed,
        abandoned,
        active_time_by_attempt,
        user_turns_by_attempt,
    )
    experiment = ExperimentComparison(
        ai=ai_cohort,
        no_ai=no_ai_cohort,
        valid_completion_rate_delta=_delta(
            ai_cohort.valid_completion_rate.value,
            no_ai_cohort.valid_completion_rate.value,
        ),
        abandonment_rate_delta=_delta(
            ai_cohort.abandonment_rate.value,
            no_ai_cohort.abandonment_rate.value,
        ),
        active_time_p50_delta_ms=_delta(
            ai_cohort.active_completion_time_ms.p50,
            no_ai_cohort.active_completion_time_ms.p50,
        ),
    )

    survey = SurveyBaseline(
        response_count=len(survey_events),
        helpfulness_mean=_mean(
            [event.helpfulness for event in survey_events if event.helpfulness is not None]
        ),
        trust_mean=_mean(
            [event.trust for event in survey_events if event.trust is not None]
        ),
        effort_reduction_mean=_mean(
            [
                event.effort_reduction
                for event in survey_events
                if event.effort_reduction is not None
            ]
        ),
    )

    return ObservabilityBaseline(
        started_attempts=len(starts),
        valid_completed_attempts=valid_completed_attempts,
        abandoned_attempts=len(abandoned),
        valid_completion_rate=_rate(valid_completed_attempts, len(starts)),
        abandonment_rate=_rate(len(abandoned), len(starts)),
        active_completion_time_ms=_distribution(active_completion_times),
        active_time_coverage=_rate(len(active_completion_times), valid_completed_attempts),
        user_turns_to_completion=_distribution(completed_turns),
        pre_save_rework_rate=_rate(len(reworked_changes), len(applied_changes)),
        post_save_rollback_rate=_rate(len(rolled_back_changes), len(applied_changes)),
        wrong_suggestion_rollback_rate=_rate(
            len(wrong_suggestion_rollbacks), len(applied_changes)
        ),
        manual_transition_rate=_rate(len(manual_transitions), len(starts)),
        proposal_outcomes=proposal_outcomes,
        callback_completion_rate=_rate(callback.succeeded, callback.total),
        deterministic_validation_block_rate=_rate(validation.blocked, validation.total),
        total_ai_cost_microusd=total_ai_cost,
        ai_cost_per_valid_completion_microusd=cost_per_completion,
        operation_stages=operation_stages,
        survey=survey,
        experiment=experiment,
    )
