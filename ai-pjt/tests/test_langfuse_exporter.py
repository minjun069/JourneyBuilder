from __future__ import annotations

import re
import sys
from types import ModuleType
import unittest
from unittest.mock import patch

from core.langfuse_exporter import (
    LangfuseInstrumentationExporter,
    build_langfuse_instrumentation_exporter,
)
from core.masking import MaskingSink
from core.monitoring import (
    AssistanceMode,
    AttemptEventType,
    AttemptTraceEvent,
    ChangeEventType,
    ChangeTraceEvent,
    OperationStage,
    OperationStatus,
    OperationTraceEvent,
    ProposalDecisionEvent,
    ProposalOutcome,
    RollbackReason,
    SurveyTraceEvent,
)
from core.observability_masking import build_masked_runtime_instrumentation
from core.runtime_instrumentation import RuntimeStage


class _FakeObservation:
    def __init__(self, client, *, parent=None) -> None:
        self.client = client
        self.parent = parent
        self.updates: list[dict] = []
        self.ended = False

    def start_observation(self, **kwargs):
        return self.client._start(parent=self, **kwargs)

    def update(self, **kwargs) -> None:
        self.updates.append(kwargs)

    def end(self) -> None:
        self.ended = True


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[dict, _FakeObservation]] = []
        self.shutdown_called = False

    def _start(self, *, parent=None, **kwargs):
        observation = _FakeObservation(self, parent=parent)
        self.calls.append((kwargs, observation))
        return observation

    def start_observation(self, **kwargs):
        return self._start(**kwargs)

    def shutdown(self) -> None:
        self.shutdown_called = True


class LangfuseInstrumentationExporterTests(unittest.TestCase):
    def test_groups_runtime_lifecycle_under_one_request_root(self) -> None:
        client = _FakeClient()
        exporter = LangfuseInstrumentationExporter(client)
        payload = {
            "contract_version": "1.4",
            "event_family": "runtime",
            "masking_status": "applied",
            "trace_id": "trace_0123456789abcdef01234567",
            "request_id": "request_maskedvalue0001",
            "thread_id": "thread_maskedvalue0001",
            "plan_id": "plan_maskedvalue0001",
            "stage": "planner",
            "status": "started",
            "input": {"message": "은퇴 준비를 알려줘"},
        }

        exporter.emit({
            **payload,
            "stage": "request_processing",
        })
        exporter.emit(payload)
        exporter.emit({
            **payload,
            "status": "succeeded",
            "duration_ms": 25,
            "output": {"tasks": 1},
        })
        exporter.emit({
            **payload,
            "stage": "request_processing",
            "status": "succeeded",
            "duration_ms": 30,
            "output": {"response": "준비 순서를 안내할게요."},
        })

        self.assertEqual(len(client.calls), 2)
        root_args, root = client.calls[0]
        planner_args, planner = client.calls[1]
        self.assertEqual(root_args["name"], "retirewise.request")
        self.assertEqual(root_args["as_type"], "span")
        self.assertEqual(
            root_args["metadata"],
            {
                "plan_id": "plan_maskedvalue0001",
                "request_id": "request_maskedvalue0001",
                "thread_id": "thread_maskedvalue0001",
            },
        )
        self.assertEqual(planner_args["name"], "retirewise.runtime.planner")
        self.assertEqual(planner_args["metadata"], {})
        self.assertIs(planner.parent, root)
        self.assertNotIn("trace_context", planner_args)
        self.assertEqual(planner_args["input"], payload["input"])
        self.assertEqual(planner.updates[-1]["output"], {"tasks": 1})
        self.assertEqual(root.updates[-1]["output"], {
            "response": "준비 순서를 안내할게요.",
        })
        trace_id = root_args["trace_context"]["trace_id"]
        self.assertRegex(trace_id, re.compile(r"^[0-9a-f]{32}$"))
        self.assertTrue(root.ended)
        self.assertTrue(planner.ended)

    def test_model_operation_maps_usage_cost_and_masked_content(self) -> None:
        client = _FakeClient()
        exporter = LangfuseInstrumentationExporter(client)

        exporter.emit({
            "contract_version": "1.1",
            "event_family": "operation",
            "masking_status": "applied",
            "trace_id": "trace_0123456789abcdef01234567",
            "stage": "model",
            "status": "succeeded",
            "model_name": "gemini-2.5-flash-lite",
            "input_tokens": 12,
            "output_tokens": 5,
            "cost_microusd": 1250,
            "input": {"message": "단기 지향은 어느 정도 기간이야?"},
            "output": {"status": "matched", "agent": "qa"},
        })

        self.assertEqual(len(client.calls), 2)
        root_args, root = client.calls[0]
        args, generation = client.calls[1]
        self.assertIs(generation.parent, root)
        self.assertNotIn("trace_context", args)
        self.assertEqual(root_args["input"], args["input"])
        self.assertEqual(args["as_type"], "generation")
        self.assertEqual(args["model"], "gemini-2.5-flash-lite")
        self.assertEqual(
            args["usage_details"],
            {"prompt_tokens": 12, "completion_tokens": 5},
        )
        self.assertEqual(args["cost_details"], {"total_cost": 0.00125})
        self.assertEqual(
            args["input"],
            {"message": "단기 지향은 어느 정도 기간이야?"},
        )
        self.assertEqual(args["output"], {"status": "matched", "agent": "qa"})
        self.assertNotIn("input", args["metadata"])
        self.assertNotIn("output", args["metadata"])
        self.assertNotIn("trace_id", args["metadata"])
        self.assertNotIn("masking_status", args["metadata"])
        self.assertNotIn("occurred_at_epoch_ms", args["metadata"])

    def test_failure_is_marked_without_copying_error_text(self) -> None:
        client = _FakeClient()
        exporter = LangfuseInstrumentationExporter(client)

        exporter.emit({
            "contract_version": "1.4",
            "event_family": "security_decision",
            "masking_status": "applied",
            "trace_id": "trace_0123456789abcdef01234567",
            "boundary": "tool_authorization",
            "decision": "denied",
            "reason_code": "TOOL_CONTEXT_MISMATCH",
        })

        args = client.calls[1][0]
        self.assertEqual(args["level"], "ERROR")
        self.assertEqual(args["status_message"], "TOOL_CONTEXT_MISMATCH")

    def test_late_callback_reuses_the_ended_request_root(self) -> None:
        client = _FakeClient()
        exporter = LangfuseInstrumentationExporter(client)
        request_event = {
            "contract_version": "1.4",
            "event_family": "runtime",
            "masking_status": "applied",
            "trace_id": "trace_0123456789abcdef01234567",
            "request_id": "request_maskedvalue0001",
            "thread_id": "thread_maskedvalue0001",
            "plan_id": "plan_maskedvalue0001",
            "stage": "request_processing",
        }
        exporter.emit({**request_event, "status": "started"})
        exporter.emit({
            **request_event,
            "status": "succeeded",
            "duration_ms": 10,
        })
        exporter.emit({
            "contract_version": "1.2",
            "event_family": "operation",
            "masking_status": "applied",
            "trace_id": "trace_0123456789abcdef01234567",
            "attempt_id": "attempt_maskedvalue0001",
            "stage": "callback",
            "status": "succeeded",
            "duration_ms": 5,
            "operation_name": "backend_callback",
        })

        self.assertEqual(len(client.calls), 2)
        root = client.calls[0][1]
        callback_args, callback = client.calls[1]
        self.assertTrue(root.ended)
        self.assertIs(callback.parent, root)
        self.assertEqual(callback_args["name"], "retirewise.operation.callback")
        self.assertNotIn("trace_context", callback_args)

    def test_rejects_payload_without_masked_contract_identity(self) -> None:
        exporter = LangfuseInstrumentationExporter(_FakeClient())
        for missing in ("trace_id", "event_family", "contract_version"):
            payload = {
                "contract_version": "1.4",
                "event_family": "runtime",
                "masking_status": "applied",
                "trace_id": "trace_0123456789abcdef01234567",
            }
            del payload[missing]
            with self.subTest(missing=missing):
                with self.assertRaisesRegex(ValueError, missing):
                    exporter.emit(payload)

    def test_rejects_unmasked_trace_or_content_bearing_fields(self) -> None:
        exporter = LangfuseInstrumentationExporter(_FakeClient())
        base = {
            "contract_version": "1.4",
            "event_family": "runtime",
            "masking_status": "applied",
            "trace_id": "trace_0123456789abcdef01234567",
        }
        with self.assertRaisesRegex(ValueError, "masked trace_id"):
            exporter.emit({**base, "trace_id": "trace_request-raw-0001"})
        with self.assertRaisesRegex(ValueError, "content-bearing"):
            exporter.emit({**base, "message": "even masked free text is excluded"})
        with self.assertRaisesRegex(ValueError, "unapproved fields"):
            exporter.emit({**base, "secret": "sk-lf-must-not-export"})
        with self.assertRaisesRegex(ValueError, "machine-token metadata"):
            exporter.emit({**base, "stage": "free form text is excluded"})

    def test_rejects_payload_that_did_not_pass_the_masking_sink(self) -> None:
        exporter = LangfuseInstrumentationExporter(_FakeClient())
        with self.assertRaisesRegex(ValueError, "masking_status=applied"):
            exporter.emit({
                "contract_version": "1.4",
                "event_family": "runtime",
                "trace_id": "trace_0123456789abcdef01234567",
                "input": {"message": "untrusted raw content"},
            })

    def test_masked_sink_exports_non_sensitive_text_and_masks_sensitive_values(self) -> None:
        from core.runtime_instrumentation import RuntimeStatus, RuntimeTraceEvent

        client = _FakeClient()
        exporter = LangfuseInstrumentationExporter(client)
        _adapter, sink = build_masked_runtime_instrumentation(
            pseudonym_key=b"f15-langfuse-content-test-key-32bytes",
            downstream=exporter,
            sink=MaskingSink.TRACE_EXPORT,
        )
        sink.emit(RuntimeTraceEvent(
            trace_id="trace_contenttrace001",
            request_id="request-raw-0001",
            thread_id="thread-raw-0001",
            plan_id="plan-request-raw-0001",
            occurred_at_epoch_ms=1,
            stage=RuntimeStage.PLANNER,
            status=RuntimeStatus.STARTED,
            input={
                "message": "안정추구란 정확히 뭘 말하는거야? 월급 350만 원",
                "customer_info": {"customer_name": "홍길동"},
            },
        ))

        args = client.calls[0][0]
        rendered = repr(args)
        self.assertIn("안정추구란 정확히 뭘 말하는거야?", rendered)
        self.assertIn("[FINANCIAL_VALUE_MASKED]", rendered)
        self.assertIn("[DIRECT_IDENTIFIER]", rendered)
        self.assertNotIn("350만 원", rendered)
        self.assertNotIn("홍길동", rendered)

    def test_actual_chat_prompt_is_masked_and_exported_as_generation_input(self) -> None:
        client = _FakeClient()
        exporter = LangfuseInstrumentationExporter(client)
        adapter, _sink = build_masked_runtime_instrumentation(
            pseudonym_key=b"f15-langfuse-prompt-test-key-32bytes",
            downstream=exporter,
            sink=MaskingSink.TRACE_EXPORT,
        )
        observed = adapter.bind(
            request_id="request-raw-0001",
            thread_id="thread-raw-0001",
            plan_id="plan-request-raw-0001",
            step_id="basic-info",
        )

        observed.record_model_call(
            purpose="qa",
            status="succeeded",
            duration_ms=15,
            input=[
                {"role": "system", "content": "QA 실제 시스템 프롬프트"},
                {
                    "role": "user",
                    "content": "[DIRECT_IDENTIFIER] 고객의 월급은 350만 원입니다.",
                },
            ],
            output={"role": "assistant", "content": "맞춤 안내입니다."},
        )

        self.assertEqual(len(client.calls), 2)
        args, _generation = client.calls[1]
        self.assertEqual(args["as_type"], "generation")
        self.assertEqual(args["input"][0], {
            "role": "system",
            "content": "QA 실제 시스템 프롬프트",
        })
        rendered = repr(args["input"])
        self.assertIn("[DIRECT_IDENTIFIER]", rendered)
        self.assertIn("[FINANCIAL_VALUE_MASKED]", rendered)
        self.assertNotIn("350만 원", rendered)
        self.assertEqual(
            args["output"],
            {"role": "assistant", "content": "맞춤 안내입니다."},
        )

    def test_shutdown_delegates_to_client(self) -> None:
        client = _FakeClient()
        exporter = LangfuseInstrumentationExporter(client)
        exporter.emit({
            "contract_version": "1.4",
            "event_family": "runtime",
            "masking_status": "applied",
            "trace_id": "trace_0123456789abcdef01234567",
            "request_id": "request_maskedvalue0001",
            "thread_id": "thread_maskedvalue0001",
            "plan_id": "plan_maskedvalue0001",
            "stage": "planner",
            "status": "started",
        })

        exporter.shutdown()

        self.assertTrue(client.shutdown_called)
        self.assertTrue(all(observation.ended for _, observation in client.calls))

    def test_shutdown_still_stops_client_when_observation_close_fails(self) -> None:
        class FailingEndObservation(_FakeObservation):
            def end(self) -> None:
                raise RuntimeError("simulated observation close failure")

        class FailingEndClient(_FakeClient):
            def _start(self, *, parent=None, **kwargs):
                observation = FailingEndObservation(self, parent=parent)
                self.calls.append((kwargs, observation))
                return observation

        client = FailingEndClient()
        exporter = LangfuseInstrumentationExporter(client)
        exporter.emit({
            "contract_version": "1.4",
            "event_family": "runtime",
            "masking_status": "applied",
            "trace_id": "trace_0123456789abcdef01234567",
            "request_id": "request_maskedvalue0001",
            "thread_id": "thread_maskedvalue0001",
            "plan_id": "plan_maskedvalue0001",
            "stage": "planner",
            "status": "started",
        })

        with self.assertRaisesRegex(RuntimeError, "observation close"):
            exporter.shutdown()

        self.assertTrue(client.shutdown_called)

    def test_builder_exports_only_langfuse_created_spans(self) -> None:
        captured: dict = {}
        langfuse_module = ModuleType("langfuse")

        def filter_marker(_span) -> bool:
            return True

        def client_factory(**kwargs):
            captured.update(kwargs)
            return _FakeClient()

        langfuse_module.Langfuse = client_factory  # type: ignore[attr-defined]
        langfuse_module.is_langfuse_span = filter_marker  # type: ignore[attr-defined]
        with patch.dict(sys.modules, {"langfuse": langfuse_module}):
            exporter = build_langfuse_instrumentation_exporter(
                public_key="pk-lf-test",
                secret_key="sk-lf-test",
                base_url="https://langfuse.example.com",
                release="candidate-sha-dirty",
            )

        self.assertIsInstance(exporter, LangfuseInstrumentationExporter)
        self.assertEqual(captured["public_key"], "pk-lf-test")
        self.assertEqual(captured["secret_key"], "sk-lf-test")
        self.assertEqual(captured["base_url"], "https://langfuse.example.com")
        self.assertEqual(captured["release"], "candidate-sha-dirty")
        self.assertTrue(captured["tracing_enabled"])
        self.assertIs(captured["should_export_span"], filter_marker)

    def test_all_foundation_event_families_pass_the_masked_allowlist(self) -> None:
        client = _FakeClient()
        exporter = LangfuseInstrumentationExporter(client)
        _adapter, sink = build_masked_runtime_instrumentation(
            pseudonym_key=b"f15-langfuse-family-test-key-32bytes",
            downstream=exporter,
            sink=MaskingSink.TRACE_EXPORT,
        )
        events = (
            OperationTraceEvent(
                trace_id="trace_familytrace0001",
                attempt_id="attempt_familyattempt01",
                occurred_at_epoch_ms=1,
                stage=OperationStage.MODEL,
                status=OperationStatus.SUCCEEDED,
                duration_ms=2,
                operation_name="model.generate",
                model_provider="gemini",
                model_name="gemini-2.5-flash-lite",
                prompt_version="qa.v1",
                input_tokens=3,
                output_tokens=4,
                cost_microusd=5,
            ),
            AttemptTraceEvent(
                trace_id="trace_familytrace0001",
                attempt_id="attempt_familyattempt01",
                occurred_at_epoch_ms=2,
                event_type=AttemptEventType.STARTED,
                step_id="basic-info",
                assistance_mode=AssistanceMode.AI,
            ),
            ProposalDecisionEvent(
                trace_id="trace_familytrace0001",
                attempt_id="attempt_familyattempt01",
                proposal_id="proposal_familyproposal1",
                occurred_at_epoch_ms=3,
                outcome=ProposalOutcome.APPROVED,
            ),
            ChangeTraceEvent(
                trace_id="trace_familytrace0001",
                attempt_id="attempt_familyattempt01",
                change_id="change_familychange01",
                occurred_at_epoch_ms=4,
                event_type=ChangeEventType.POST_SAVE_ROLLED_BACK,
                rollback_reason=RollbackReason.WRONG_SUGGESTION,
            ),
            SurveyTraceEvent(
                trace_id="trace_familytrace0001",
                attempt_id="attempt_familyattempt01",
                occurred_at_epoch_ms=5,
                helpfulness=4,
                trust=5,
            ),
        )

        for event in events:
            sink.emit(event)

        self.assertEqual(sink.dropped_exports, 0)
        self.assertEqual(len(client.calls), len(events) + 1)
        root = client.calls[0][1]
        self.assertTrue(
            all(
                observation.parent is root
                for _, observation in client.calls[1:]
            )
        )

    def test_masked_sink_is_the_only_runtime_path_and_isolates_sdk_failure(self) -> None:
        class FailingClient(_FakeClient):
            def __init__(self) -> None:
                super().__init__()
                self.attempts: list[dict] = []

            def start_observation(self, **kwargs):
                self.attempts.append(kwargs)
                raise RuntimeError("simulated SDK failure")

        client = FailingClient()
        exporter = LangfuseInstrumentationExporter(client)
        adapter, sink = build_masked_runtime_instrumentation(
            pseudonym_key=b"f15-langfuse-masking-test-key-32bytes",
            downstream=exporter,
            sink=MaskingSink.TRACE_EXPORT,
        )
        observed = adapter.bind(
            request_id="request-raw-0001",
            thread_id="thread-raw-0001",
            plan_id="plan-request-raw-0001",
            step_id="basic-info",
        )

        span = observed.start(RuntimeStage.PLANNER)
        observed.succeed(span)

        self.assertEqual(sink.dropped_exports, 2)
        self.assertEqual(len(client.attempts), 2)
        for args in client.attempts:
            rendered = repr(args)
            self.assertNotIn("request-raw-0001", rendered)
            self.assertNotIn("thread-raw-0001", rendered)
            self.assertNotIn("plan-request-raw-0001", rendered)
            self.assertRegex(
                args["trace_context"]["trace_id"],
                re.compile(r"^[0-9a-f]{32}$"),
            )


if __name__ == "__main__":
    unittest.main()
