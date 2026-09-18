from __future__ import annotations

import json
import unittest

from core.runtime_instrumentation import RuntimeStage, RuntimeStatus, RuntimeTraceEvent
from evals.runtime_observability_baseline import build_runtime_observability_baseline
from evals.runtime_observability_fixture import reference_runtime_events


class RuntimeObservabilityBaselineTests(unittest.TestCase):
    def test_task_request_retry_failure_response_and_aggregation_baseline(self) -> None:
        baseline = build_runtime_observability_baseline(reference_runtime_events())

        self.assertEqual(baseline.requests, 3)
        self.assertEqual(baseline.request_success_rate.value, 2 / 3)
        self.assertEqual(baseline.request_timeout_count, 1)
        self.assertEqual(baseline.task_outcomes.total, 4)
        self.assertEqual(baseline.task_outcomes.completed, 1)
        self.assertEqual(baseline.task_outcomes.failed, 2)
        self.assertEqual(baseline.task_outcomes.blocked, 1)
        self.assertEqual(baseline.all_tasks_failed_response_rate.value, 1 / 3)
        self.assertEqual(baseline.aggregation_success_rate.value, 1.0)
        self.assertEqual(baseline.callback_delivery.attempts, 8)
        self.assertEqual(baseline.callback_delivery.retried_requests, 2)
        self.assertEqual(baseline.callback_delivery.eventually_succeeded, 2)
        self.assertEqual(baseline.callback_delivery.exhausted, 1)

    def test_stage_latency_and_reasons_are_reported_without_targets(self) -> None:
        payload = build_runtime_observability_baseline(
            reference_runtime_events()
        ).to_dict()

        self.assertEqual(payload["measurement_disposition"], "baseline_only")
        self.assertEqual(
            payload["stages"]["request_processing"]["reason_counts"],
            {"request_timeout": 1},
        )
        self.assertEqual(payload["stages"]["aggregation"]["latency_ms"]["p50"], 9.0)
        rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True).lower()
        self.assertNotIn("target", rendered)
        self.assertNotIn("slo", rendered)
        self.assertNotIn("customer_info", rendered)

    def test_incomplete_and_duplicate_runtime_spans_are_rejected(self) -> None:
        start = RuntimeTraceEvent(
            trace_id="trace_00000001",
            request_id="request-00000001",
            thread_id="thread-00000001",
            plan_id="plan-request-00000001",
            occurred_at_epoch_ms=1,
            stage=RuntimeStage.REQUEST_PROCESSING,
            status=RuntimeStatus.STARTED,
        )
        with self.assertRaisesRegex(ValueError, "without terminal"):
            build_runtime_observability_baseline([start])

        terminal = RuntimeTraceEvent(
            trace_id=start.trace_id,
            request_id=start.request_id,
            thread_id=start.thread_id,
            plan_id=start.plan_id,
            occurred_at_epoch_ms=2,
            stage=start.stage,
            status=RuntimeStatus.SUCCEEDED,
            duration_ms=1,
        )
        with self.assertRaisesRegex(ValueError, "duplicate runtime span terminal"):
            build_runtime_observability_baseline([start, terminal, terminal])


if __name__ == "__main__":
    unittest.main()
