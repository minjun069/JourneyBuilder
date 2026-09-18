"""F11-T04 vertical leakage regression at every production serialization edge."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

import httpx
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from core.callback_masking import CallbackPayloadMasker
from core.masking import MaskingSink
from core.model_boundary import ModelPurpose, secure_ainvoke, secure_system_prompt
from core.observability_masking import (
    FakeSerializedInstrumentationSink,
    StructuredLogInstrumentationExporter,
    build_masked_runtime_instrumentation,
)
from core.runtime_instrumentation import RuntimeStage
from graph.executor import execute_route_plan
from graph.planner import TASK_TYPE_CONTRACTS
from main import _stream_v2_chat_events
from tests.security.masking_contract import (
    MASKING_SENTINELS,
    Sink as ContractSink,
    assert_no_raw_sentinels,
)
from transport.contracts import ChatV2Request
from transport.runtime import (
    AsyncChatTransport,
    CallbackClient,
    InMemoryRequestCheckpointStore,
    request_digest,
)


TEST_KEY = b"f11-t04-integrated-leakage-key-32bytes"
STEP_ID = "financial-goals"
FIELD_REGISTRY = [{
    "step_id": STEP_ID,
    "section": "financial_goals",
    "path": "monthly_target_expense",
    "value_type": "integer",
    "unit": "KRW",
    "constraints": {"min": 0, "period": "month"},
    "cardinality": "single",
    "writable_by_ai": True,
    "nullable": False,
    "operations": ["set", "update"],
}]

TASK_AGENT = {
    "answer_question": "qa",
    "propose_form_change": "input_assist",
    "review_validation_error": "error_review",
    "interpret_chart": "chart_interpret",
    "collect_qualitative_info": "elicit",
    "answer_dashboard_metric": "qa",
}
TASK_MODEL_PURPOSE = {
    "answer_question": ModelPurpose.QA,
    "propose_form_change": ModelPurpose.INPUT_ASSIST,
    "review_validation_error": ModelPurpose.ERROR_REVIEW,
    "interpret_chart": ModelPurpose.CHART_INTERPRET,
    "collect_qualitative_info": ModelPurpose.ELICIT,
    "answer_dashboard_metric": ModelPurpose.QA,
}


def _sentinel_fields() -> dict[str, str]:
    return {case.field_name: case.raw_value for case in MASKING_SENTINELS}


def _sentinel_text() -> str:
    return " | ".join(case.raw_value for case in MASKING_SENTINELS)


def _task(task_type: str) -> dict:
    contract = TASK_TYPE_CONTRACTS[task_type]
    executor_kind = str(contract["executor_kind"])
    executor = (
        str(contract["capability"])
        if executor_kind == "tool"
        else TASK_AGENT[task_type]
    )
    task = {
        "id": f"task-{task_type}",
        "type": task_type,
        "description": _sentinel_text(),
        "depends_on": [],
        "bindings": {},
        "executor_kind": executor_kind,
        "produces": str(contract["produces"]),
        "route": {
            "task_id": f"task-{task_type}",
            "executor_kind": executor_kind,
            "executor": executor,
            "reason_code": "intent_match",
        },
    }
    if task_type == "lookup_reference_value":
        task["arguments"] = {
            "reference_id": "debt_service_ratio_max",
            "age": 47,
            "period": "annual",
            "population": "retirewise_kr_households",
        }
    if task_type == "lookup_retirement_gap":
        task["arguments"] = {"metric_id": "retirement_gap"}
    return task


def _completed_result(task_type: str, task_id: str) -> dict:
    artifact_type = str(TASK_TYPE_CONTRACTS[task_type]["produces"])
    if artifact_type == "form_change":
        artifact = {
            "type": "form_change",
            "target": "draft_form",
            "user_requested": False,
            "confidence": 0.9,
            "changes": [{
                "section": "financial_goals",
                "path": "monthly_target_expense",
                "operation": "set",
                "value": 3_000_000,
                "unit": "KRW",
                "reason": "검증된 금융 입력",
            }],
        }
    elif artifact_type == "reference_value":
        artifact = {
            "type": "reference_value",
            "schema_version": "1.0",
            "producer_task_id": task_id,
            "reference_id": "debt_service_ratio_max",
            "age": 47,
            "value": 30,
            "unit": "percent",
            "period": "annual",
            "population": "retirewise_kr_households",
            "comparator": "lte",
            "source_id": "approved-policy",
            "source_version": "1.0.0",
            "as_of": "2026-09-11",
            "calculation_policy_version": "2026-09-07.v1",
        }
    elif artifact_type == "dashboard_metric":
        artifact = {
            "type": "dashboard_metric",
            "schema_version": "1.0",
            "producer_task_id": task_id,
            "availability": "ready",
            "metric_id": "retirement_gap",
            "value": 248_600_000,
            "unit": "KRW",
            "report_id": 17,
            "report_version": 3,
            "calculation_policy_version": "2026-09-07.v1",
            "generated_at": "2026-09-11T00:00:00Z",
        }
    else:
        artifact = {"type": "text_response", "text": "안전한 응답"}
    return {
        "task_id": task_id,
        "status": "completed",
        "response": "안전한 응답",
        "artifact": artifact,
    }


class RecordingAsyncModel:
    def __init__(self) -> None:
        self.rendered_calls: list[str] = []

    async def ainvoke(self, messages) -> AIMessage:
        self.rendered_calls.append(json.dumps(
            [message.content for message in messages],
            ensure_ascii=False,
            sort_keys=True,
        ))
        return AIMessage(content="안전한 응답")


class TaskStructureLeakageTests(IsolatedAsyncioTestCase):
    async def test_every_registered_task_reaches_masked_sink_without_raw_values(self):
        expected_types = set(TASK_TYPE_CONTRACTS)
        self.assertEqual(set(TASK_AGENT) | {
            "lookup_reference_value",
            "lookup_retirement_gap",
        }, expected_types)

        downstream = FakeSerializedInstrumentationSink()
        adapter, _sink = build_masked_runtime_instrumentation(
            pseudonym_key=TEST_KEY,
            downstream=downstream,
            sink=MaskingSink.TRACE_EXPORT,
        )
        model = RecordingAsyncModel()

        async def agent_executor(payload: dict) -> dict:
            task_type = str(payload["task"]["type"])
            purpose = TASK_MODEL_PURPOSE.get(task_type)
            if purpose is not None:
                with patch("core.model_boundary._runtime_key", return_value=TEST_KEY):
                    await secure_ainvoke(
                        model,
                        [
                            SystemMessage(content=secure_system_prompt("테스트 역할")),
                            HumanMessage(content=json.dumps(
                                payload,
                                ensure_ascii=False,
                                sort_keys=True,
                            )),
                        ],
                        purpose=purpose,
                        sensitive_payload=payload,
                    )
            return _completed_result(task_type, str(payload["task"]["id"]))

        async def tool_executor(payload: dict) -> dict:
            task_type = str(payload["task"]["type"])
            return _completed_result(task_type, str(payload["task"]["id"]))

        for task_type in sorted(expected_types):
            task = _task(task_type)
            executor_name = str(task["route"]["executor"])
            report = await execute_route_plan(
                plan={
                    "plan_id": f"plan-{task_type}",
                    "tasks": [task],
                },
                step_id=STEP_ID,
                customer_info={"current_form": _sentinel_fields()},
                field_registry=FIELD_REGISTRY,
                agent_executors=(
                    {executor_name: agent_executor}
                    if task["executor_kind"] == "agent"
                    else {}
                ),
                tool_executors=(
                    {executor_name: tool_executor}
                    if task["executor_kind"] == "tool"
                    else {}
                ),
                request_id="P13-REQUEST-RAW-0001",
                instrumentation=adapter.bind(
                    request_id="P13-REQUEST-RAW-0001",
                    thread_id=f"thread-{task_type}",
                    plan_id=f"plan-{task_type}",
                    step_id=STEP_ID,
                ),
            )
            with self.subTest(task_type=task_type):
                self.assertEqual(
                    report.task_results[task["id"]]["status"],
                    "completed",
                )

        rendered_model = "\n".join(model.rendered_calls)
        assert_no_raw_sentinels(rendered_model, ContractSink.MODEL_INPUT)
        rendered_trace = json.dumps(
            downstream.payloads,
            ensure_ascii=False,
            sort_keys=True,
        )
        assert_no_raw_sentinels(rendered_trace, ContractSink.TRACE_EXPORT)
        model_events = [
            payload
            for payload in downstream.payloads
            if payload.get("event_family") == "operation"
            and payload.get("stage") == "model"
        ]
        self.assertEqual(len(model_events), len(TASK_MODEL_PURPOSE))
        for event in model_events:
            self.assertEqual(
                [message["role"] for message in event["input"]],
                ["system", "user"],
            )
            self.assertEqual(event["output"]["role"], "assistant")
        forbidden_event_fields = {
            "message",
            "customer_info",
            "description",
            "bindings",
            "arguments",
            "artifact",
            "value",
        }
        for payload in downstream.payloads:
            self.assertTrue(forbidden_event_fields.isdisjoint(payload))
        observed_executors = {
            str(payload["executor_name"])
            for payload in downstream.payloads
            if payload.get("stage") == RuntimeStage.TASK_EXECUTION.value
            and payload.get("status") == "succeeded"
        }
        self.assertEqual(
            observed_executors,
            set(TASK_AGENT.values())
            | {"lookup_reference_value", "get_dashboard_metric"},
        )

    async def test_failed_and_blocked_tasks_export_only_masked_metadata(self):
        downstream = FakeSerializedInstrumentationSink()
        adapter, _sink = build_masked_runtime_instrumentation(
            pseudonym_key=TEST_KEY,
            downstream=downstream,
            sink=MaskingSink.TRACE_EXPORT,
        )

        async def fail(_payload: dict) -> dict:
            raise RuntimeError(_sentinel_text())

        plan = {
            "plan_id": "plan-failure-sentinel",
            "tasks": [
                {
                    **_task("answer_question"),
                    "id": "task-failed",
                    "route": {
                        "task_id": "task-failed",
                        "executor_kind": "agent",
                        "executor": "qa",
                        "reason_code": "intent_match",
                    },
                },
                {
                    **_task("propose_form_change"),
                    "id": "task-blocked",
                    "depends_on": ["task-failed"],
                    "route": {
                        "task_id": "task-blocked",
                        "executor_kind": "agent",
                        "executor": "input_assist",
                        "reason_code": "intent_match",
                    },
                },
            ],
        }
        report = await execute_route_plan(
            plan=plan,
            step_id=STEP_ID,
            customer_info={"current_form": _sentinel_fields()},
            field_registry=FIELD_REGISTRY,
            agent_executors={"qa": fail},
            tool_executors={},
            request_id="P13-REQUEST-RAW-0001",
            instrumentation=adapter.bind(
                request_id="P13-REQUEST-RAW-0001",
                thread_id="thread-failure",
                plan_id="plan-failure-sentinel",
                step_id=STEP_ID,
            ),
        )

        self.assertEqual(report.task_results["task-failed"]["status"], "failed")
        self.assertEqual(report.task_results["task-blocked"]["status"], "blocked")
        rendered = json.dumps(downstream.payloads, ensure_ascii=False, sort_keys=True)
        assert_no_raw_sentinels(rendered, ContractSink.TRACE_EXPORT)
        self.assertIn('"status": "failed"', rendered)
        self.assertIn('"status": "blocked"', rendered)


class _RecordHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.payloads: list[dict] = []

    def emit(self, record: logging.LogRecord) -> None:
        payload = getattr(record, "instrumentation_event", None)
        if isinstance(payload, dict):
            self.payloads.append(payload)


class SerializedBoundaryTests(TestCase):
    def test_application_log_and_trace_receive_only_masked_mappings(self):
        for sink_kind, contract_sink in (
            (MaskingSink.APPLICATION_LOG, ContractSink.APPLICATION_LOG),
            (MaskingSink.TRACE_EXPORT, ContractSink.TRACE_EXPORT),
        ):
            downstream = FakeSerializedInstrumentationSink()
            adapter, _sink = build_masked_runtime_instrumentation(
                pseudonym_key=TEST_KEY,
                downstream=downstream,
                sink=sink_kind,
            )
            observed = adapter.bind(
                request_id="P13-REQUEST-RAW-0001",
                thread_id="P13-THREAD-RAW-0001",
                plan_id="P13-PLAN-RAW-0001",
                step_id=STEP_ID,
            )
            span = observed.start(RuntimeStage.PLANNER)
            observed.succeed(span)
            rendered = json.dumps(
                downstream.payloads,
                ensure_ascii=False,
                sort_keys=True,
            )
            with self.subTest(sink=sink_kind.value):
                assert_no_raw_sentinels(rendered, contract_sink)

    def test_structured_logger_receives_the_already_masked_payload(self):
        logger = logging.getLogger("f11-t04-structured-export")
        previous_level = logger.level
        previous_propagate = logger.propagate
        handler = _RecordHandler()
        logger.handlers.append(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        try:
            adapter, _sink = build_masked_runtime_instrumentation(
                pseudonym_key=TEST_KEY,
                downstream=StructuredLogInstrumentationExporter(logger),
                sink=MaskingSink.TRACE_EXPORT,
            )
            observed = adapter.bind(
                request_id="P13-REQUEST-RAW-0001",
                thread_id="P13-THREAD-RAW-0001",
                plan_id="P13-PLAN-RAW-0001",
                step_id=STEP_ID,
            )
            span = observed.start(RuntimeStage.AGGREGATION)
            observed.succeed(span)
        finally:
            logger.handlers.remove(handler)
            logger.setLevel(previous_level)
            logger.propagate = previous_propagate

        self.assertEqual(len(handler.payloads), 2)
        rendered = json.dumps(handler.payloads, ensure_ascii=False, sort_keys=True)
        assert_no_raw_sentinels(rendered, ContractSink.TRACE_EXPORT)


class CallbackAndStreamLeakageTests(IsolatedAsyncioTestCase):
    async def test_actual_callback_body_checkpoint_and_attempt_trace_are_safe(self):
        downstream = FakeSerializedInstrumentationSink()
        instrumentation, _sink = build_masked_runtime_instrumentation(
            pseudonym_key=TEST_KEY,
            downstream=downstream,
            sink=MaskingSink.TRACE_EXPORT,
        )
        received: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            received.append(json.loads(request.content))
            return httpx.Response(200, request=request)

        callback_client = CallbackClient(
            url="http://backend.test/api/v2/internal/ai/callback/",
            bearer_token="test-token",
            instrumentation=instrumentation,
            client_factory=lambda **kwargs: httpx.AsyncClient(
                transport=httpx.MockTransport(handler),
                **kwargs,
            ),
        )

        async def processor(request: ChatV2Request, _budget) -> dict:
            observed = instrumentation.bind(
                request_id=request.request_id,
                thread_id=request.thread_id,
                plan_id=f"plan-{request.request_id}",
                step_id=request.step_id,
            )
            span = observed.start(RuntimeStage.AGGREGATION)
            observed.succeed(span)
            return {
                "response": (
                    "p13-user@example.invalid와 010-0000-0000으로 안내했습니다."
                ),
                "step_id": request.step_id,
                "action": {
                    "type": "form_change",
                    "target": "draft_form",
                    "user_requested": False,
                    "confidence": 0.9,
                    "changes": [{
                        "section": "financial_goals",
                        "path": "monthly_target_expense",
                        "operation": "set",
                        "value": 3_000_000,
                        "unit": "KRW",
                        "reason": "검증된 금융 입력",
                    }],
                },
            }

        store = InMemoryRequestCheckpointStore()
        request = _request("P13-REQUEST-RAW-0001")
        transport = AsyncChatTransport(
            checkpoint_store=store,
            processor=processor,
            callback_client=callback_client,
            callback_masker=CallbackPayloadMasker(pseudonym_key=TEST_KEY),
            instrumentation=instrumentation,
        )
        await store.claim(request, request_digest(request))

        terminal = await transport.run(request)
        checkpoint = await store.get(request.request_id)

        self.assertEqual(received, [terminal])
        self.assertEqual(checkpoint.terminal_payload, terminal)
        self.assertEqual(terminal["request_id"], request.request_id)
        self.assertEqual(
            terminal["output"]["action"]["changes"][0]["value"],
            3_000_000,
        )
        callback_rendered = json.dumps(received, ensure_ascii=False, sort_keys=True)
        assert_no_raw_sentinels(callback_rendered, ContractSink.CALLBACK)

        trace_ids = {payload["trace_id"] for payload in downstream.payloads}
        self.assertEqual(len(trace_ids), 1)
        trace_rendered = json.dumps(
            downstream.payloads,
            ensure_ascii=False,
            sort_keys=True,
        )
        assert_no_raw_sentinels(trace_rendered, ContractSink.TRACE_EXPORT)
        self.assertTrue(any(
            payload.get("stage") == "callback"
            for payload in downstream.payloads
        ))

    async def test_malicious_model_output_becomes_safe_policy_callback(self):
        downstream = FakeSerializedInstrumentationSink()
        instrumentation, _sink = build_masked_runtime_instrumentation(
            pseudonym_key=TEST_KEY,
            downstream=downstream,
            sink=MaskingSink.TRACE_EXPORT,
        )
        received: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            received.append(json.loads(request.content))
            return httpx.Response(200, request=request)

        callback_client = CallbackClient(
            url="http://backend.test/api/v2/internal/ai/callback/",
            bearer_token="test-token",
            instrumentation=instrumentation,
            client_factory=lambda **kwargs: httpx.AsyncClient(
                transport=httpx.MockTransport(handler),
                **kwargs,
            ),
        )

        class MaliciousModel:
            async def ainvoke(self, _messages) -> AIMessage:
                return AIMessage(
                    content="p13-user@example.invalid 010-0000-0000"
                )

        async def processor(_request: ChatV2Request, _budget) -> dict:
            with patch("core.model_boundary._runtime_key", return_value=TEST_KEY):
                await secure_ainvoke(
                    MaliciousModel(),
                    [
                        SystemMessage(content=secure_system_prompt("테스트 역할")),
                        HumanMessage(content="안전한 질문"),
                    ],
                    purpose=ModelPurpose.QA,
                )
            raise AssertionError("blocked model output must not continue")

        store = InMemoryRequestCheckpointStore()
        request = _request("policy-blocked-request")
        transport = AsyncChatTransport(
            checkpoint_store=store,
            processor=processor,
            callback_client=callback_client,
            callback_masker=CallbackPayloadMasker(pseudonym_key=TEST_KEY),
            instrumentation=instrumentation,
        )
        await store.claim(request, request_digest(request))

        terminal = await transport.run(request)

        self.assertEqual(terminal["status"], "failed")
        self.assertEqual(terminal["error"]["code"], "POLICY_BLOCKED")
        output_audits = [
            payload for payload in downstream.payloads
            if payload.get("boundary") == "output_guardrail"
        ]
        self.assertEqual(len(output_audits), 1)
        self.assertEqual(output_audits[0]["decision"], "denied")
        rendered = json.dumps(received, ensure_ascii=False, sort_keys=True)
        self.assertNotIn("p13-user@example.invalid", rendered)
        self.assertNotIn("010-0000-0000", rendered)

    @patch("main.get_callbacks", return_value=[])
    async def test_sse_discards_unchecked_chunks_and_masks_terminal(self, _callbacks):
        class ContextAPI:
            terminal = None

            async def read_context(self, _scope):
                return {}

            async def read_terminal(self, _scope, _request_id, _payload_digest):
                return self.terminal

            async def begin_execution(self, _scope, _request_id):
                return None

            async def delete_execution(self, _scope, _request_id):
                return None

            async def commit(
                self,
                _scope,
                _request_id,
                _payload_digest,
                _context,
                terminal,
                _completed_turn=None,
            ):
                self.terminal = terminal
                return terminal

            async def commit_terminal(
                self, _scope, _request_id, _payload_digest, terminal
            ):
                self.terminal = terminal
                return terminal

            def state_channels(self, scope, _context, _retrieved_memories=()):
                return {"context_scope": {"session_id": scope.session_id}}

            def config_for(self, scope, _request_id):
                return {"configurable": {"thread_id": scope.thread_id}}

        class Graph:
            async def astream_events(self, _input, *, config, version):
                yield {
                    "event": "on_chat_model_stream",
                    "metadata": {"langgraph_node": "merge"},
                    "data": {"chunk": AIMessage(content="010-0000-0000")},
                }
                yield {
                    "event": "on_chain_end",
                    "parent_ids": [],
                    "data": {"output": {
                        "conversation_context": {
                            "schema_version": "3.0",
                            "summary": {},
                            "structured_state": {
                                "current_goal": None,
                                "response_preferences": [],
                                "decisions": [],
                                "open_questions": [],
                                "entities": [],
                            },
                            "turns": [],
                        },
                        "output": {
                            "response": "p13-user@example.invalid 010-0000-0000",
                            "step_id": STEP_ID,
                            "action": None,
                        },
                    }},
                }

        rendered = "".join([
            frame
            async for frame in _stream_v2_chat_events(
                Graph(),
                ContextAPI(),
                _request("sse-request-safe-root"),
                CallbackPayloadMasker(pseudonym_key=TEST_KEY),
            )
        ])
        self.assertNotIn("p13-user@example.invalid", rendered)
        self.assertNotIn("010-0000-0000", rendered)
        self.assertEqual(rendered.count("event: delta"), 0)
        self.assertEqual(rendered.count("event: done"), 1)


def _request(request_id: str) -> ChatV2Request:
    return ChatV2Request.model_validate({
        "contract_version": "2.0",
        "request_id": request_id,
        "payload_digest": "0" * 64,
        "session_id": 731,
        "thread_id": "thread-f11-t04",
        "step_id": STEP_ID,
        "message": _sentinel_text(),
        "deadline_at": datetime.now(timezone.utc) + timedelta(seconds=60),
        "customer_info": {
            "saved_data": {},
            "current_form": _sentinel_fields(),
        },
        "field_registry": FIELD_REGISTRY,
        "chart_payload": None,
    })


if __name__ == "__main__":
    import unittest

    unittest.main()
