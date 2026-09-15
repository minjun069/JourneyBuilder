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
    def __init__(self) -> None:
        self.ended = False

    def end(self) -> None:
        self.ended = True


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[dict, _FakeObservation]] = []
        self.shutdown_called = False

    def start_observation(self, **kwargs):
        observation = _FakeObservation()
        self.calls.append((kwargs, observation))
        return observation

    def shutdown(self) -> None:
        self.shutdown_called = True


class LangfuseInstrumentationExporterTests(unittest.TestCase):
    def test_exports_masked_metadata_with_deterministic_trace_correlation(self) -> None:
        client = _FakeClient()
        exporter = LangfuseInstrumentationExporter(client)
        payload = {
            "contract_version": "1.4",
            "event_family": "runtime",
            "masking_status": "applied",
            "trace_id": "trace_0123456789abcdef01234567",
            "request_id": "request_maskedvalue0001",
            "stage": "planner",
            "status": "succeeded",
        }

        exporter.emit(payload)
        exporter.emit({**payload, "stage": "aggregation"})

        first, second = (call[0] for call in client.calls)
        self.assertEqual(first["name"], "retirewise.runtime.planner")
        self.assertEqual(first["as_type"], "span")
        self.assertEqual(first["metadata"], payload)
        self.assertNotIn("input", first)
        self.assertNotIn("output", first)
        trace_id = first["trace_context"]["trace_id"]
        self.assertRegex(trace_id, re.compile(r"^[0-9a-f]{32}$"))
        self.assertEqual(trace_id, second["trace_context"]["trace_id"])
        self.assertTrue(all(call[1].ended for call in client.calls))

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

        args = client.calls[0][0]
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

        args = client.calls[0][0]
        self.assertEqual(args["level"], "ERROR")
        self.assertNotIn("status_message", args)

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

    def test_shutdown_delegates_to_client(self) -> None:
        client = _FakeClient()
        LangfuseInstrumentationExporter(client).shutdown()
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
            )

        self.assertIsInstance(exporter, LangfuseInstrumentationExporter)
        self.assertEqual(captured["public_key"], "pk-lf-test")
        self.assertEqual(captured["secret_key"], "sk-lf-test")
        self.assertEqual(captured["base_url"], "https://langfuse.example.com")
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
        self.assertEqual(len(client.calls), len(events))

    def test_masked_sink_is_the_only_runtime_path_and_isolates_sdk_failure(self) -> None:
        class FailingObservation(_FakeObservation):
            def end(self) -> None:
                raise RuntimeError("simulated SDK failure")

        class FailingClient(_FakeClient):
            def start_observation(self, **kwargs):
                observation = FailingObservation()
                self.calls.append((kwargs, observation))
                return observation

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
        self.assertEqual(len(client.calls), 2)
        for args, _observation in client.calls:
            rendered = repr(args)
            self.assertNotIn("request-raw-0001", rendered)
            self.assertNotIn("thread-raw-0001", rendered)
            self.assertNotIn("plan-request-raw-0001", rendered)
            self.assertRegex(
                args["metadata"]["trace_id"],
                re.compile(r"^trace_[0-9a-f]{24}$"),
            )


if __name__ == "__main__":
    unittest.main()
