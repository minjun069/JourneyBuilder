import unittest

from core.monitoring import (
    AssistanceMode,
    AttemptEventType,
    AttemptTraceEvent,
    ChangeEventType,
    ChangeTraceEvent,
    ManualTransitionReason,
    OperationStage,
    OperationStatus,
    OperationTraceEvent,
    ProposalDecisionEvent,
    ProposalOutcome,
    RollbackReason,
    SurveyTraceEvent,
)
from evals.observability_baseline import build_observability_baseline


def attempt_event(
    attempt_number,
    event_type,
    *,
    assistance_mode=None,
    duration_ms=None,
    transition_reason=None,
):
    suffix = f"{attempt_number:08d}"
    return AttemptTraceEvent(
        trace_id=f"trace_{suffix}",
        attempt_id=f"attempt_{suffix}",
        occurred_at_epoch_ms=attempt_number * 1000,
        event_type=event_type,
        step_id="basic-info",
        assistance_mode=assistance_mode,
        duration_ms=duration_ms,
        transition_reason=transition_reason,
    )


def operation_event(
    attempt_number,
    stage,
    status,
    duration_ms,
    operation_name,
    **kwargs,
):
    suffix = f"{attempt_number:08d}"
    return OperationTraceEvent(
        trace_id=f"trace_{suffix}",
        attempt_id=f"attempt_{suffix}",
        occurred_at_epoch_ms=attempt_number * 1000 + duration_ms,
        stage=stage,
        status=status,
        duration_ms=duration_ms,
        operation_name=operation_name,
        **kwargs,
    )


def synthetic_events():
    events = [
        attempt_event(1, AttemptEventType.STARTED, assistance_mode=AssistanceMode.AI),
        attempt_event(2, AttemptEventType.STARTED, assistance_mode=AssistanceMode.AI),
        attempt_event(3, AttemptEventType.STARTED, assistance_mode=AssistanceMode.NO_AI),
        attempt_event(1, AttemptEventType.ACTIVE_INTERVAL, duration_ms=1000),
        attempt_event(1, AttemptEventType.ACTIVE_INTERVAL, duration_ms=2000),
        attempt_event(3, AttemptEventType.ACTIVE_INTERVAL, duration_ms=5000),
        attempt_event(1, AttemptEventType.USER_TURN),
        attempt_event(1, AttemptEventType.USER_TURN),
        attempt_event(2, AttemptEventType.USER_TURN),
        attempt_event(3, AttemptEventType.USER_TURN),
        attempt_event(3, AttemptEventType.USER_TURN),
        attempt_event(3, AttemptEventType.USER_TURN),
        attempt_event(1, AttemptEventType.VALID_COMPLETED),
        attempt_event(2, AttemptEventType.ABANDONED),
        attempt_event(
            2,
            AttemptEventType.MANUAL_TRANSITION,
            transition_reason=ManualTransitionReason.AI_FAILURE,
        ),
        attempt_event(3, AttemptEventType.VALID_COMPLETED),
        ProposalDecisionEvent(
            trace_id="trace_00000001",
            attempt_id="attempt_00000001",
            proposal_id="proposal_00000001",
            occurred_at_epoch_ms=2000,
            outcome=ProposalOutcome.APPROVED,
        ),
        ProposalDecisionEvent(
            trace_id="trace_00000002",
            attempt_id="attempt_00000002",
            proposal_id="proposal_00000002",
            occurred_at_epoch_ms=3000,
            outcome=ProposalOutcome.NO_RESPONSE,
        ),
        ChangeTraceEvent(
            trace_id="trace_00000001",
            attempt_id="attempt_00000001",
            change_id="change_00000001",
            occurred_at_epoch_ms=2100,
            event_type=ChangeEventType.APPLIED,
        ),
        ChangeTraceEvent(
            trace_id="trace_00000001",
            attempt_id="attempt_00000001",
            change_id="change_00000001",
            occurred_at_epoch_ms=2200,
            event_type=ChangeEventType.PRE_SAVE_REWORKED,
        ),
        ChangeTraceEvent(
            trace_id="trace_00000001",
            attempt_id="attempt_00000001",
            change_id="change_00000001",
            occurred_at_epoch_ms=2300,
            event_type=ChangeEventType.POST_SAVE_ROLLED_BACK,
            rollback_reason=RollbackReason.WRONG_SUGGESTION,
        ),
        operation_event(
            1,
            OperationStage.ROUTE,
            OperationStatus.SUCCEEDED,
            100,
            "qa",
        ),
        operation_event(
            1,
            OperationStage.SEARCH,
            OperationStatus.SUCCEEDED,
            200,
            "approved_retriever",
            resource_ids=("pension-guide:2026-01",),
            scores=(0.9,),
            result_count=1,
        ),
        operation_event(
            2,
            OperationStage.TOOL,
            OperationStatus.TIMEOUT,
            300,
            "get_dashboard_metric",
            error_code="TOOL_TIMEOUT",
        ),
        operation_event(
            1,
            OperationStage.MODEL,
            OperationStatus.SUCCEEDED,
            500,
            "chat_completion",
            input_tokens=100,
            output_tokens=50,
            cost_microusd=1200,
        ),
        operation_event(
            2,
            OperationStage.MODEL,
            OperationStatus.FAILED,
            400,
            "chat_completion",
            input_tokens=80,
            output_tokens=0,
            cost_microusd=800,
            error_code="LLM_ERROR",
        ),
        operation_event(
            1,
            OperationStage.CALLBACK,
            OperationStatus.SUCCEEDED,
            100,
            "django_callback",
        ),
        operation_event(
            2,
            OperationStage.CALLBACK,
            OperationStatus.FAILED,
            150,
            "django_callback",
            error_code="CALLBACK_ERROR",
        ),
        operation_event(
            1,
            OperationStage.OUTPUT_VALIDATION,
            OperationStatus.SUCCEEDED,
            10,
            "validate_form_update",
        ),
        operation_event(
            2,
            OperationStage.OUTPUT_VALIDATION,
            OperationStatus.BLOCKED,
            12,
            "validate_form_update",
            error_code="INVALID_FORM_ACTION",
        ),
        SurveyTraceEvent(
            trace_id="trace_00000001",
            attempt_id="attempt_00000001",
            occurred_at_epoch_ms=4000,
            helpfulness=5,
            trust=4,
            effort_reduction=5,
        ),
        SurveyTraceEvent(
            trace_id="trace_00000002",
            attempt_id="attempt_00000002",
            occurred_at_epoch_ms=4000,
            trust=2,
        ),
        SurveyTraceEvent(
            trace_id="trace_00000003",
            attempt_id="attempt_00000003",
            occurred_at_epoch_ms=4000,
            helpfulness=4,
            trust=4,
            effort_reduction=3,
        ),
    ]
    return events


class ObservabilityBaselineTests(unittest.TestCase):
    def test_core_ax_outcomes_and_efficiency(self):
        baseline = build_observability_baseline(synthetic_events())
        self.assertEqual(baseline.started_attempts, 3)
        self.assertEqual(baseline.valid_completion_rate.value, 2 / 3)
        self.assertEqual(baseline.abandonment_rate.value, 1 / 3)
        self.assertEqual(baseline.active_completion_time_ms.p50, 4000)
        self.assertEqual(baseline.active_completion_time_ms.p90, 4800)
        self.assertEqual(baseline.user_turns_to_completion.p50, 2.5)
        self.assertEqual(baseline.total_ai_cost_microusd, 2000)
        self.assertEqual(baseline.ai_cost_per_valid_completion_microusd, 2000)

    def test_rework_rollback_transition_and_proposal_outcomes(self):
        baseline = build_observability_baseline(synthetic_events())
        self.assertEqual(baseline.pre_save_rework_rate.value, 1.0)
        self.assertEqual(baseline.post_save_rollback_rate.value, 1.0)
        self.assertEqual(baseline.wrong_suggestion_rollback_rate.value, 1.0)
        self.assertEqual(baseline.manual_transition_rate.value, 1 / 3)
        self.assertEqual(baseline.proposal_outcomes["approved"].value, 0.5)
        self.assertEqual(baseline.proposal_outcomes["no_response"].value, 0.5)
        self.assertEqual(baseline.proposal_outcomes["modified"].value, 0.0)

    def test_operational_latency_callback_and_validation(self):
        baseline = build_observability_baseline(synthetic_events())
        self.assertEqual(baseline.callback_completion_rate.value, 0.5)
        self.assertEqual(baseline.deterministic_validation_block_rate.value, 0.5)
        self.assertEqual(baseline.operation_stages["model"].input_tokens, 180)
        self.assertEqual(baseline.operation_stages["tool"].timeout, 1)
        self.assertEqual(baseline.operation_stages["search"].latency_ms.p95, 200)

    def test_survey_and_controlled_ai_no_ai_comparison(self):
        baseline = build_observability_baseline(synthetic_events())
        self.assertEqual(baseline.survey.response_count, 3)
        self.assertEqual(baseline.survey.helpfulness_mean, 4.5)
        self.assertAlmostEqual(baseline.survey.trust_mean, 10 / 3)
        self.assertEqual(baseline.survey.effort_reduction_mean, 4.0)
        self.assertEqual(baseline.experiment.ai.valid_completion_rate.value, 0.5)
        self.assertEqual(baseline.experiment.no_ai.valid_completion_rate.value, 1.0)
        self.assertEqual(baseline.experiment.valid_completion_rate_delta, -0.5)
        self.assertEqual(baseline.experiment.active_time_p50_delta_ms, -2000)

    def test_serialized_report_has_no_slo_or_price_table(self):
        payload = build_observability_baseline(synthetic_events()).to_dict()
        rendered = repr(payload)
        self.assertEqual(payload["valid_completion_rate"]["value"], 2 / 3)
        self.assertNotIn("target", payload)
        self.assertNotIn("slo", rendered.lower())
        self.assertNotIn("price", rendered.lower())

    def test_orphan_event_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "without started attempt"):
            build_observability_baseline(
                [attempt_event(1, AttemptEventType.USER_TURN)]
            )

    def test_completed_and_abandoned_attempt_is_rejected(self):
        events = [
            attempt_event(1, AttemptEventType.STARTED, assistance_mode=AssistanceMode.AI),
            attempt_event(1, AttemptEventType.VALID_COMPLETED),
            attempt_event(1, AttemptEventType.ABANDONED),
        ]
        with self.assertRaisesRegex(ValueError, "completed and abandoned"):
            build_observability_baseline(events)

    def test_rework_without_applied_change_is_rejected(self):
        events = [
            attempt_event(1, AttemptEventType.STARTED, assistance_mode=AssistanceMode.AI),
            ChangeTraceEvent(
                trace_id="trace_00000001",
                attempt_id="attempt_00000001",
                change_id="change_00000001",
                occurred_at_epoch_ms=2,
                event_type=ChangeEventType.PRE_SAVE_REWORKED,
            ),
        ]
        with self.assertRaisesRegex(ValueError, "matching applied change"):
            build_observability_baseline(events)

    def test_cost_per_completion_is_unavailable_without_ai_completion(self):
        events = [
            attempt_event(1, AttemptEventType.STARTED, assistance_mode=AssistanceMode.AI),
            attempt_event(1, AttemptEventType.ABANDONED),
            operation_event(
                1,
                OperationStage.MODEL,
                OperationStatus.FAILED,
                100,
                "chat_completion",
                cost_microusd=100,
                error_code="LLM_ERROR",
            ),
        ]
        baseline = build_observability_baseline(events)
        self.assertEqual(baseline.total_ai_cost_microusd, 100)
        self.assertIsNone(baseline.ai_cost_per_valid_completion_microusd)


if __name__ == "__main__":
    unittest.main()
