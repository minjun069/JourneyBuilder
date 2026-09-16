from __future__ import annotations

import importlib.util
import unittest
from itertools import count

from core.monitoring import OperationStage, OperationStatus, OperationTraceEvent
from core.runtime_instrumentation import (
    FakeInstrumentationSink,
    InstrumentationPort,
    RuntimeStage,
    RuntimeStatus,
)
from core.runtime_instrumentation_adapter import (
    ExportBlockedInstrumentationSink,
    RuntimeInstrumentationAdapter,
    build_export_blocked_instrumentation,
)
from graph.executor import execute_route_plan


LANGGRAPH_AVAILABLE = importlib.util.find_spec("langgraph") is not None


def _bound_adapter() -> tuple[object, FakeInstrumentationSink]:
    sink = FakeInstrumentationSink()
    ticks = count(10)
    adapter = RuntimeInstrumentationAdapter(
        InstrumentationPort(sink),
        epoch_ms=lambda: 1_800_000_000_000,
        monotonic_clock=lambda: next(ticks) / 10,
    )
    return (
        adapter.bind(
            request_id="request-test",
            thread_id="thread-test",
            plan_id="plan-test",
            step_id="step-test",
        ),
        sink,
    )


class RuntimeInstrumentationAdapterTests(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless(LANGGRAPH_AVAILABLE, "LangGraph runtime image에서 실행")
    def test_graph_rejects_missing_or_none_adapter(self) -> None:
        from graph.builder import build_graph

        with self.assertRaises(TypeError):
            build_graph(None)  # type: ignore[call-arg]
        with self.assertRaises(TypeError):
            build_graph(None, instrumentation=None)  # type: ignore[arg-type]

    def test_pre_masking_startup_policy_is_explicitly_blocked(self) -> None:
        adapter, sink = build_export_blocked_instrumentation()
        self.assertIsInstance(sink, ExportBlockedInstrumentationSink)
        observed = adapter.bind(
            request_id="request-test",
            thread_id="thread-test",
            plan_id="plan-test",
            step_id="step-test",
        )
        span = observed.start(RuntimeStage.PLANNER)
        observed.succeed(span)
        self.assertEqual(sink.discarded_count, 2)

    def test_observation_content_has_no_fixed_byte_ceiling(self) -> None:
        observed, sink = _bound_adapter()
        large_context = {"context": {"conversation": "가" * 100_000}}
        span = observed.start(RuntimeStage.PLANNER, input=large_context)
        observed.succeed(span, output={"ok": True})
        self.assertEqual(sink.runtime_events[0].input, large_context)

    def test_callback_adapter_emits_only_typed_terminal_operation(self) -> None:
        sink = FakeInstrumentationSink()
        adapter = RuntimeInstrumentationAdapter(InstrumentationPort(sink))
        adapter.record_callback_attempt(
            request_id="request-test",
            thread_id="thread-test",
            plan_id="plan-test",
            step_id="step-test",
            status=OperationStatus.TIMEOUT,
            duration_ms=125,
            error_code="callback_timeout",
        )

        event = sink.baseline_events[0]
        self.assertIsInstance(event, OperationTraceEvent)
        self.assertEqual(event.stage, OperationStage.CALLBACK)
        self.assertEqual(event.status, OperationStatus.TIMEOUT)
        self.assertEqual(event.error_code, "callback_timeout")

    async def test_executor_emits_correlated_stage_and_operation_events(self) -> None:
        observed, sink = _bound_adapter()

        async def qa(_: dict) -> dict:
            return {
                "task_id": "task-1",
                "status": "completed",
                "response": "정상 응답",
                "artifact": {"type": "text_response", "text": "정상 응답"},
            }

        report = await execute_route_plan(
            plan={
                "plan_id": "plan-test",
                "tasks": [{
                    "id": "task-1",
                    "type": "answer_question",
                    "description": "질문",
                    "depends_on": [],
                    "bindings": {},
                    "executor_kind": "agent",
                    "produces": "text_response",
                    "route": {
                        "task_id": "task-1",
                        "executor_kind": "agent",
                        "executor": "qa",
                        "reason_code": "intent_match",
                    },
                }],
            },
            step_id="step-test",
            customer_info={},
            field_registry=[],
            agent_executors={"qa": qa},
            tool_executors={},
            instrumentation=observed,  # type: ignore[arg-type]
        )

        self.assertEqual(report.task_results["task-1"]["status"], "completed")
        runtime = sink.runtime_events
        self.assertEqual(
            [event.stage for event in runtime],
            [
                RuntimeStage.BINDING_RESOLUTION,
                RuntimeStage.BINDING_RESOLUTION,
                RuntimeStage.TASK_EXECUTION,
                RuntimeStage.ARTIFACT_VALIDATION,
                RuntimeStage.ARTIFACT_VALIDATION,
                RuntimeStage.TASK_EXECUTION,
                RuntimeStage.AGGREGATION,
                RuntimeStage.AGGREGATION,
            ],
        )
        self.assertEqual(runtime[-1].status, RuntimeStatus.SUCCEEDED)
        self.assertTrue(all(event.trace_id == runtime[0].trace_id for event in runtime))
        self.assertEqual(runtime[2].executor_name, "qa")
        self.assertEqual(runtime[2].input["message"], "질문")
        self.assertEqual(runtime[5].response_owner, "agent")
        self.assertEqual(runtime[5].output["response"], "정상 응답")
        self.assertEqual(runtime[3].producer_task_id, "task-1")
        self.assertEqual(len(sink.baseline_events), 1)
        self.assertIsInstance(sink.baseline_events[0], OperationTraceEvent)
        self.assertEqual(sink.baseline_events[0].input["message"], "질문")
        self.assertEqual(sink.baseline_events[0].output["response"], "정상 응답")


if __name__ == "__main__":
    unittest.main()
