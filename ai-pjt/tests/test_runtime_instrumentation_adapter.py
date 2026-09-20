from __future__ import annotations

import importlib.util
import unittest
from itertools import count

from core.monitoring import OperationStage, OperationStatus, OperationTraceEvent
from core.operation_observation import record_operation_observation
from core.prompt_registry import PromptInvocationRecord
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

    def test_actual_model_call_recorder_emits_generation_content(self) -> None:
        observed, sink = _bound_adapter()
        prompt = [
            {"role": "system", "content": "마스킹된 시스템 프롬프트"},
            {"role": "user", "content": "마스킹된 사용자 프롬프트"},
        ]
        response = {"role": "assistant", "content": "모델 응답"}

        observed.record_model_call(  # type: ignore[attr-defined]
            purpose="qa",
            operation_name="qa.judge",
            status="succeeded",
            duration_ms=12,
            task_id="task-1",
            input=prompt,
            output=response,
            prompt_id="qa.judge",
            prompt_version="1.2.0",
            prompt_hash="b" * 64,
            prompt_release="2026-09-17.1",
            prompt_policy_version="1.1",
        )

        self.assertEqual(len(sink.baseline_events), 1)
        event = sink.baseline_events[0]
        self.assertIsInstance(event, OperationTraceEvent)
        self.assertEqual(event.stage, OperationStage.MODEL)
        self.assertEqual(event.operation_name, "qa.judge")
        self.assertEqual(event.task_id, "task-1")
        self.assertEqual(event.agent, "qa")
        self.assertEqual(event.input, prompt)
        self.assertEqual(event.output, response)
        self.assertTrue(event.model_provider)
        self.assertTrue(event.model_name)
        self.assertEqual(event.prompt_id, "qa.judge")
        self.assertEqual(event.prompt_version, "1.2.0")
        self.assertEqual(event.prompt_hash, "b" * 64)
        self.assertEqual(event.prompt_release, "2026-09-17.1")
        self.assertEqual(event.prompt_policy_version, "1.1")

    def test_prompt_invocation_emits_complete_model_metadata(self) -> None:
        observed, sink = _bound_adapter()
        observed.record_prompt_invocation(PromptInvocationRecord(
            prompt_id="qa.answer_direct",
            prompt_version="1.0.0",
            prompt_hash="a" * 64,
            prompt_release="2026-09-17.1",
            prompt_policy_version="1.1",
            purpose="qa",
            model_provider="gemini",
            model_name="gemini-2.5-flash-lite",
            model_temperature=0.0,
            status="succeeded",
            duration_ms=25,
        ))

        event = sink.baseline_events[0]
        self.assertEqual(event.stage, OperationStage.MODEL)
        self.assertEqual(event.operation_name, "qa.answer_direct")
        self.assertEqual(event.prompt_id, "qa.answer_direct")
        self.assertEqual(event.prompt_version, "1.0.0")
        self.assertEqual(event.prompt_hash, "a" * 64)
        self.assertEqual(event.prompt_release, "2026-09-17.1")
        self.assertEqual(event.prompt_policy_version, "1.1")
        self.assertEqual(event.model_temperature, 0.0)

    async def test_executor_emits_correlated_stage_and_operation_events(self) -> None:
        observed, sink = _bound_adapter()

        async def qa(_: dict) -> dict:
            record_operation_observation(
                stage=OperationStage.SEARCH,
                status=OperationStatus.SUCCEEDED,
                operation_name="qa.search",
                duration_ms=3,
                agent="qa",
                resource_ids=("approved-guide",),
                scores=(0.9,),
                result_count=1,
            )
            return {
                "task_id": "task-1",
                "status": "completed",
                "response": "정상 응답",
                "artifact": {"type": "text_response", "text": "정상 응답"},
            }

        report = await execute_route_plan(
            plan={
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
            request_id="request-test",
            step_id="step-test",
            customer_info={},
            agent_executors={"qa": qa},
            tool_executors={},
            instrumentation=observed,  # type: ignore[arg-type]
        )

        self.assertEqual(report.task_results["task-1"]["status"], "completed")
        runtime = sink.runtime_events
        self.assertEqual(
            [event.stage for event in runtime],
            [
                RuntimeStage.TASK_EXECUTION,
                RuntimeStage.BINDING_RESOLUTION,
                RuntimeStage.BINDING_RESOLUTION,
                RuntimeStage.ARTIFACT_VALIDATION,
                RuntimeStage.ARTIFACT_VALIDATION,
                RuntimeStage.TASK_EXECUTION,
                RuntimeStage.AGGREGATION,
                RuntimeStage.AGGREGATION,
            ],
        )
        self.assertEqual(runtime[-1].status, RuntimeStatus.SUCCEEDED)
        self.assertTrue(all(event.trace_id == runtime[0].trace_id for event in runtime))
        self.assertEqual(runtime[0].executor_name, "qa")
        self.assertEqual(runtime[0].input["task_type"], "answer_question")
        search = next(
            event
            for event in sink.baseline_events
            if isinstance(event, OperationTraceEvent)
            and event.stage is OperationStage.SEARCH
        )
        self.assertEqual(search.task_id, "task-1")
        self.assertEqual(runtime[5].response_owner, "agent")
        self.assertEqual(runtime[5].output["response"], "정상 응답")
        self.assertEqual(runtime[3].producer_task_id, "task-1")


if __name__ == "__main__":
    unittest.main()
