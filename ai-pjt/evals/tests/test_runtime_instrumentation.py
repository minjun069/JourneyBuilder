import unittest

from core.monitoring import AssistanceMode, AttemptEventType, AttemptTraceEvent
from core.runtime_instrumentation import (
    FakeInstrumentationSink,
    InstrumentationPort,
    RuntimeStage,
    RuntimeStatus,
    RuntimeTraceEvent,
    build_artifact_id,
)
from evals.observability_baseline import build_observability_baseline


TRACE_ID = "trace_00000001"
REQUEST_ID = "request-00000001"
THREAD_ID = "thread-00000001"
PLAN_ID = "plan-request-00000001"


def runtime_event(
    *,
    stage: RuntimeStage,
    status: RuntimeStatus,
    occurred_at_epoch_ms: int = 10,
    **values,
) -> RuntimeTraceEvent:
    return RuntimeTraceEvent(
        trace_id=TRACE_ID,
        request_id=REQUEST_ID,
        thread_id=THREAD_ID,
        plan_id=PLAN_ID,
        occurred_at_epoch_ms=occurred_at_epoch_ms,
        stage=stage,
        status=status,
        **values,
    )


class RuntimeInstrumentationTests(unittest.TestCase):
    def test_every_f10_t02_runtime_stage_can_reach_the_fake_sink(self):
        sink = FakeInstrumentationSink()
        port = InstrumentationPort(sink)
        artifact_type = "text_response"
        artifact_id = build_artifact_id(
            plan_id=PLAN_ID,
            producer_task_id="task-1",
            artifact_type=artifact_type,
        )
        events = (
            runtime_event(
                stage=RuntimeStage.PLANNER,
                status=RuntimeStatus.STARTED,
            ),
            runtime_event(
                stage=RuntimeStage.PLAN_VALIDATION,
                status=RuntimeStatus.SUCCEEDED,
                duration_ms=1,
            ),
            runtime_event(
                stage=RuntimeStage.BINDING_RESOLUTION,
                status=RuntimeStatus.SUCCEEDED,
                task_id="task-1",
                depends_on=(),
                duration_ms=1,
            ),
            runtime_event(
                stage=RuntimeStage.ARTIFACT_VALIDATION,
                status=RuntimeStatus.SUCCEEDED,
                task_id="task-1",
                depends_on=(),
                artifact_id=artifact_id,
                artifact_type=artifact_type,
                producer_task_id="task-1",
                duration_ms=1,
            ),
            runtime_event(
                stage=RuntimeStage.TASK_EXECUTION,
                status=RuntimeStatus.SUCCEEDED,
                task_id="task-1",
                depends_on=(),
                duration_ms=2,
            ),
            runtime_event(
                stage=RuntimeStage.DEPENDENCY_BLOCK,
                status=RuntimeStatus.BLOCKED,
                task_id="task-2",
                depends_on=("task-1",),
                blocked_by=("task-1",),
                duration_ms=0,
                reason_code="dependency_failed",
            ),
            runtime_event(
                stage=RuntimeStage.AGGREGATION,
                status=RuntimeStatus.SUCCEEDED,
                duration_ms=1,
            ),
        )

        for event in events:
            port.emit(event)

        self.assertEqual(sink.runtime_events, events)
        self.assertEqual(
            tuple(payload["stage"] for payload in sink.serialized_events()),
            tuple(stage.value for stage in RuntimeStage),
        )

    def test_port_preserves_runtime_correlation_and_p11_baseline_events(self):
        sink = FakeInstrumentationSink()
        port = InstrumentationPort(sink)
        started = AttemptTraceEvent(
            trace_id=TRACE_ID,
            attempt_id="attempt_00000001",
            occurred_at_epoch_ms=1,
            event_type=AttemptEventType.STARTED,
            step_id="basic-info",
            assistance_mode=AssistanceMode.AI,
        )
        completed = AttemptTraceEvent(
            trace_id=TRACE_ID,
            attempt_id="attempt_00000001",
            occurred_at_epoch_ms=30,
            event_type=AttemptEventType.VALID_COMPLETED,
            step_id="basic-info",
        )
        artifact_id = build_artifact_id(
            plan_id=PLAN_ID,
            producer_task_id="task-1",
            artifact_type="reference_value",
        )
        runtime_events = (
            runtime_event(
                stage=RuntimeStage.PLANNER,
                status=RuntimeStatus.SUCCEEDED,
                duration_ms=3,
            ),
            runtime_event(
                stage=RuntimeStage.TASK_EXECUTION,
                status=RuntimeStatus.SUCCEEDED,
                task_id="task-1",
                depends_on=(),
                duration_ms=10,
            ),
            runtime_event(
                stage=RuntimeStage.ARTIFACT_VALIDATION,
                status=RuntimeStatus.SUCCEEDED,
                task_id="task-1",
                depends_on=(),
                artifact_id=artifact_id,
                artifact_type="reference_value",
                producer_task_id="task-1",
                duration_ms=2,
            ),
            runtime_event(
                stage=RuntimeStage.DEPENDENCY_BLOCK,
                status=RuntimeStatus.BLOCKED,
                task_id="task-2",
                depends_on=("task-1",),
                blocked_by=("task-1",),
                duration_ms=0,
                reason_code="dependency_failed",
            ),
        )

        port.emit(started)
        for event in runtime_events:
            port.emit(event)
        port.emit(completed)

        self.assertEqual(sink.runtime_events, runtime_events)
        baseline = build_observability_baseline(list(sink.baseline_events))
        self.assertEqual(baseline.valid_completion_rate.value, 1.0)
        blocked_payload = sink.serialized_events()[4]
        self.assertEqual(blocked_payload["request_id"], REQUEST_ID)
        self.assertEqual(blocked_payload["thread_id"], THREAD_ID)
        self.assertEqual(blocked_payload["plan_id"], PLAN_ID)
        self.assertEqual(blocked_payload["task_id"], "task-2")
        self.assertEqual(blocked_payload["depends_on"], ["task-1"])
        self.assertEqual(blocked_payload["blocked_by"], ["task-1"])
        artifact_payload = sink.serialized_events()[3]
        self.assertEqual(artifact_payload["artifact_id"], artifact_id)
        self.assertNotIn("artifact", artifact_payload)
        self.assertNotIn("message", repr(sink.serialized_events()).lower())
        self.assertNotIn("customer_info", repr(sink.serialized_events()).lower())

    def test_artifact_id_is_deterministic_and_must_match_event_identity(self):
        artifact_id = build_artifact_id(
            plan_id=PLAN_ID,
            producer_task_id="task-1",
            artifact_type="dashboard_metric",
        )
        self.assertEqual(
            artifact_id,
            "plan-request-00000001:task-1:dashboard_metric",
        )
        with self.assertRaisesRegex(ValueError, "artifact_id"):
            runtime_event(
                stage=RuntimeStage.ARTIFACT_VALIDATION,
                status=RuntimeStatus.FAILED,
                task_id="task-1",
                artifact_id="unrelated-artifact",
                artifact_type="dashboard_metric",
                producer_task_id="task-1",
                duration_ms=1,
                reason_code="artifact_invalid",
            )

    def test_dependency_block_requires_declared_blockers(self):
        with self.assertRaisesRegex(ValueError, "subset"):
            runtime_event(
                stage=RuntimeStage.DEPENDENCY_BLOCK,
                status=RuntimeStatus.BLOCKED,
                task_id="task-2",
                depends_on=("task-1",),
                blocked_by=("task-unknown",),
                duration_ms=0,
                reason_code="dependency_failed",
            )
        with self.assertRaisesRegex(ValueError, "blocked status"):
            runtime_event(
                stage=RuntimeStage.DEPENDENCY_BLOCK,
                status=RuntimeStatus.FAILED,
                task_id="task-2",
                depends_on=("task-1",),
                duration_ms=0,
                reason_code="dependency_failed",
            )

    def test_stage_scope_and_terminal_fields_are_validated(self):
        with self.assertRaisesRegex(ValueError, "cannot contain task identifiers"):
            runtime_event(
                stage=RuntimeStage.PLANNER,
                status=RuntimeStatus.SUCCEEDED,
                task_id="task-1",
                duration_ms=1,
            )
        with self.assertRaisesRegex(ValueError, "terminal events require"):
            runtime_event(
                stage=RuntimeStage.AGGREGATION,
                status=RuntimeStatus.SUCCEEDED,
            )
        with self.assertRaisesRegex(ValueError, "cannot contain duration"):
            runtime_event(
                stage=RuntimeStage.TASK_EXECUTION,
                status=RuntimeStatus.STARTED,
                task_id="task-1",
                duration_ms=0,
            )

    def test_port_and_fake_sink_reject_untyped_payloads(self):
        sink = FakeInstrumentationSink()
        port = InstrumentationPort(sink)
        raw_payload = {"message": "raw user text"}
        with self.assertRaises(TypeError):
            port.emit(raw_payload)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            sink.emit(raw_payload)  # type: ignore[arg-type]
        with self.assertRaisesRegex(TypeError, "provide emit"):
            InstrumentationPort(object())  # type: ignore[arg-type]

    def test_sink_failures_are_not_hidden_by_the_port(self):
        class FailingSink:
            def emit(self, event):
                raise RuntimeError("sink unavailable")

        port = InstrumentationPort(FailingSink())
        event = runtime_event(
            stage=RuntimeStage.PLAN_VALIDATION,
            status=RuntimeStatus.SUCCEEDED,
            duration_ms=1,
        )
        with self.assertRaisesRegex(RuntimeError, "sink unavailable"):
            port.emit(event)

    def test_fake_sink_returns_immutable_snapshots_and_can_clear(self):
        sink = FakeInstrumentationSink()
        event = runtime_event(
            stage=RuntimeStage.AGGREGATION,
            status=RuntimeStatus.STARTED,
        )
        sink.emit(event)
        snapshot = sink.events
        sink.clear()
        self.assertEqual(snapshot, (event,))
        self.assertEqual(sink.events, ())


if __name__ == "__main__":
    unittest.main()
