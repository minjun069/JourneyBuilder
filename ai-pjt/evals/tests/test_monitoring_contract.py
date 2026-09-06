import os
import unittest
from unittest.mock import patch

from core.monitoring import (
    AssistanceMode,
    AttemptEventType,
    AttemptTraceEvent,
    FakeTraceSink,
    OperationStage,
    OperationStatus,
    OperationTraceEvent,
    SurveyTraceEvent,
    TraceExportBlockedError,
    get_callbacks,
)


TRACE_ID = "trace_00000001"
ATTEMPT_ID = "attempt_00000001"


class MonitoringContractTests(unittest.TestCase):
    def test_fake_sink_accepts_only_typed_events(self):
        sink = FakeTraceSink()
        event = AttemptTraceEvent(
            trace_id=TRACE_ID,
            attempt_id=ATTEMPT_ID,
            occurred_at_epoch_ms=1,
            event_type=AttemptEventType.STARTED,
            step_id="basic-info",
            assistance_mode=AssistanceMode.AI,
        )
        sink.emit(event)
        self.assertEqual(sink.events, (event,))
        with self.assertRaises(TypeError):
            sink.emit({"message": "raw user text"})  # type: ignore[arg-type]

    def test_raw_request_identifiers_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "trace_id"):
            AttemptTraceEvent(
                trace_id="ai_req_raw_0001",
                attempt_id=ATTEMPT_ID,
                occurred_at_epoch_ms=1,
                event_type=AttemptEventType.STARTED,
                step_id="basic-info",
                assistance_mode=AssistanceMode.AI,
            )

    def test_search_event_serializes_ids_and_scores_without_query_or_excerpt(self):
        event = OperationTraceEvent(
            trace_id=TRACE_ID,
            attempt_id=ATTEMPT_ID,
            occurred_at_epoch_ms=2,
            stage=OperationStage.SEARCH,
            status=OperationStatus.SUCCEEDED,
            duration_ms=15,
            operation_name="approved_retriever",
            resource_ids=("pension-guide:2026-01",),
            scores=(0.91,),
            result_count=1,
        )
        payload = event.to_dict()
        self.assertEqual(payload["resource_ids"], ["pension-guide:2026-01"])
        self.assertNotIn("query", payload)
        self.assertNotIn("excerpt", payload)
        self.assertNotIn("message", payload)
        self.assertNotIn("customer_info", payload)

    def test_search_metadata_is_rejected_on_other_stages(self):
        with self.assertRaisesRegex(ValueError, "search result metadata"):
            OperationTraceEvent(
                trace_id=TRACE_ID,
                attempt_id=ATTEMPT_ID,
                occurred_at_epoch_ms=2,
                stage=OperationStage.TOOL,
                status=OperationStatus.SUCCEEDED,
                duration_ms=15,
                operation_name="get_form_field_spec",
                resource_ids=("raw-result",),
            )

    def test_failed_operation_requires_stable_error_code(self):
        with self.assertRaisesRegex(ValueError, "stable error_code"):
            OperationTraceEvent(
                trace_id=TRACE_ID,
                attempt_id=ATTEMPT_ID,
                occurred_at_epoch_ms=2,
                stage=OperationStage.CALLBACK,
                status=OperationStatus.FAILED,
                duration_ms=15,
                operation_name="django_callback",
            )

    def test_survey_keeps_only_recommended_sampled_dimensions(self):
        event = SurveyTraceEvent(
            trace_id=TRACE_ID,
            attempt_id=ATTEMPT_ID,
            occurred_at_epoch_ms=3,
            helpfulness=5,
            trust=4,
            effort_reduction=4,
        )
        payload = event.to_dict()
        self.assertNotIn("satisfaction", payload)
        self.assertEqual(payload["trust"], 4)
        with self.assertRaisesRegex(ValueError, "1 to 5"):
            SurveyTraceEvent(
                trace_id=TRACE_ID,
                attempt_id=ATTEMPT_ID,
                occurred_at_epoch_ms=3,
                helpfulness=6,
            )

    def test_external_tracing_is_empty_when_disabled(self):
        with patch.dict(
            os.environ,
            {
                "LANGFUSE_ENABLED": "false",
                "LANGCHAIN_TRACING_V2": "false",
                "LANGSMITH_TRACING": "false",
            },
        ):
            self.assertEqual(get_callbacks(), [])

    def test_langfuse_is_fail_closed_before_f11(self):
        with patch.dict(
            os.environ,
            {
                "LANGFUSE_ENABLED": "true",
                "LANGCHAIN_TRACING_V2": "false",
                "LANGSMITH_TRACING": "false",
            },
        ):
            with self.assertRaisesRegex(TraceExportBlockedError, "F11"):
                get_callbacks()

    def test_automatic_langsmith_tracing_is_fail_closed_before_f11(self):
        with patch.dict(
            os.environ,
            {
                "LANGFUSE_ENABLED": "false",
                "LANGCHAIN_TRACING_V2": "true",
                "LANGSMITH_TRACING": "false",
            },
        ):
            with self.assertRaisesRegex(TraceExportBlockedError, "F11"):
                get_callbacks()


if __name__ == "__main__":
    unittest.main()
