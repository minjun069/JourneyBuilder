from __future__ import annotations

import json
from pathlib import Path
import unittest

from core.masking import MaskingPolicy, MaskingSink
from core.monitoring import OperationStage, OperationStatus
from core.observability_masking import (
    FakeSerializedInstrumentationSink,
    MaskedInstrumentationSink,
    build_masked_runtime_instrumentation,
    mask_application_log_payload,
    mask_trace_payload,
)
from core.runtime_instrumentation import RuntimeStage
from graph.executor import execute_route_plan
from tools.contracts import ToolAuditEvent
from tests.security.masking_contract import (
    MASKING_SENTINELS,
    Sink as ContractSink,
    assert_no_raw_sentinels,
)


TEST_MASKING_KEY = b"f11-t03-observability-test-key-32bytes"
AI_ROOT = Path(__file__).resolve().parents[1]


class LogAndTraceMaskingTests(unittest.TestCase):
    def test_log_and_trace_payloads_remove_all_contract_sentinels(self) -> None:
        payload = {
            "message": "raw user text",
            "nested": {
                case.field_name: case.raw_value
                for case in MASKING_SENTINELS
            },
        }
        policy = MaskingPolicy(pseudonym_key=TEST_MASKING_KEY)
        for masker, contract_sink in (
            (mask_application_log_payload, ContractSink.APPLICATION_LOG),
            (mask_trace_payload, ContractSink.TRACE_EXPORT),
        ):
            rendered = json.dumps(
                masker(payload, policy=policy),
                ensure_ascii=False,
                sort_keys=True,
            )
            with self.subTest(sink=contract_sink.value):
                assert_no_raw_sentinels(rendered, contract_sink)

    def test_constructor_rejects_missing_or_non_observability_dependencies(self) -> None:
        downstream = FakeSerializedInstrumentationSink()
        policy = MaskingPolicy(pseudonym_key=TEST_MASKING_KEY)
        with self.assertRaises(TypeError):
            MaskedInstrumentationSink(
                policy=None,  # type: ignore[arg-type]
                sink=MaskingSink.TRACE_EXPORT,
                downstream=downstream,
            )
        with self.assertRaises(ValueError):
            MaskedInstrumentationSink(
                policy=policy,
                sink=MaskingSink.MODEL_INPUT,
                downstream=downstream,
            )

    def test_production_startup_uses_masked_instrumentation_sink(self) -> None:
        source = (AI_ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("build_masked_runtime_instrumentation", source)
        self.assertNotIn("build_export_blocked_instrumentation", source)
        self.assertIn("if settings.langfuse_enabled", source)
        self.assertIn("downstream=instrumentation_exporter", source)


class MaskedInstrumentationRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_trace_store_outage_drops_exports_without_failing_business(self) -> None:
        downstream = FakeSerializedInstrumentationSink(fail=True)
        adapter, sink = build_masked_runtime_instrumentation(
            pseudonym_key=TEST_MASKING_KEY,
            downstream=downstream,
            sink=MaskingSink.TRACE_EXPORT,
        )

        async def qa(_: dict) -> dict:
            return {
                "task_id": "task-1",
                "status": "completed",
                "response": "정상 금융 설명",
                "artifact": {
                    "type": "text_response",
                    "text": "정상 금융 설명",
                },
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
            request_id="request-raw-0001",
            step_id="basic-info",
            customer_info={},
            agent_executors={"qa": qa},
            tool_executors={},
            instrumentation=adapter.bind(
                request_id="request-raw-0001",
                thread_id="thread-raw-0001",
                step_id="basic-info",
            ),
        )

        self.assertEqual(report.task_results["task-1"]["status"], "completed")
        self.assertGreater(sink.dropped_exports, 0)
        self.assertEqual(sink.dropped_exports, len(downstream.payloads))
        rendered = json.dumps(downstream.payloads, ensure_ascii=False, sort_keys=True)
        self.assertIn("질문", rendered)
        self.assertIn("정상 금융 설명", rendered)
        for raw in (
            "request-raw-0001",
            "thread-raw-0001",
            "task-1",
        ):
            self.assertNotIn(raw, rendered)

    def test_callback_attempt_uses_same_masked_correlation(self) -> None:
        downstream = FakeSerializedInstrumentationSink()
        adapter, sink = build_masked_runtime_instrumentation(
            pseudonym_key=TEST_MASKING_KEY,
            downstream=downstream,
            sink=MaskingSink.TRACE_EXPORT,
        )
        observed = adapter.bind(
            request_id="request-raw-0001",
            thread_id="thread-raw-0001",
            step_id="basic-info",
        )
        span = observed.start(RuntimeStage.PLANNER)
        observed.succeed(span)
        adapter.record_callback_attempt(
            request_id="request-raw-0001",
            thread_id="thread-raw-0001",
            step_id="basic-info",
            status=OperationStatus.SUCCEEDED,
            duration_ms=25,
        )

        self.assertEqual(sink.dropped_exports, 0)
        self.assertEqual(len(downstream.payloads), 3)
        trace_ids = {payload["trace_id"] for payload in downstream.payloads}
        self.assertEqual(len(trace_ids), 1)
        callback = downstream.payloads[-1]
        self.assertEqual(callback["stage"], OperationStage.CALLBACK.value)
        self.assertEqual(callback["status"], OperationStatus.SUCCEEDED.value)

    def test_tool_authorization_audit_uses_masked_typed_port(self) -> None:
        downstream = FakeSerializedInstrumentationSink()
        adapter, sink = build_masked_runtime_instrumentation(
            pseudonym_key=TEST_MASKING_KEY,
            downstream=downstream,
            sink=MaskingSink.TRACE_EXPORT,
        )
        observed = adapter.bind(
            request_id="request-raw-0001",
            thread_id="thread-raw-0001",
            step_id="basic-info",
        )

        observed.record_tool_audit(ToolAuditEvent(
            request_id="request-raw-0001",
            task_id="task-1",
            step_id="basic-info",
            task_type="lookup_reference_value",
            tool_name="get_approved_reference_value",
            tool_version="1.0",
            agent="qa",
            decision="denied",
            status="failed",
            duration_ms=0,
            argument_classes=("financial_metric_identifier",),
            call_source="graph_task",
            error_code="TOOL_ARGUMENT_SCHEMA_INVALID",
        ))

        self.assertEqual(sink.dropped_exports, 0)
        self.assertEqual(len(downstream.payloads), 1)
        payload = downstream.payloads[0]
        self.assertEqual(payload["event_family"], "security_decision")
        self.assertEqual(payload["decision"], "denied")
        self.assertNotIn("request-raw-0001", json.dumps(payload))
        self.assertNotIn("arguments", payload)


if __name__ == "__main__":
    unittest.main()
