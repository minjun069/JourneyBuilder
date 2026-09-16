from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import unittest

from pydantic import ValidationError

from transport.contracts import ChatV2Request


FIXTURE = json.loads((
    Path(__file__).resolve().parents[2]
    / "contract-fixtures/ai-lifecycle/v2/tool-contexts.json"
).read_text(encoding="utf-8"))


def request_payload(tool_context=None) -> dict:
    return {
        "contract_version": "2.0",
        "request_id": "req-tool-context",
        "payload_digest": "0" * 64,
        "session_id": 12,
        "thread_id": "user-3-session-12",
        "step_id": "basic-info",
        "message": "은퇴 부족액을 알려줘",
        "deadline_at": (
            datetime.now(timezone.utc) + timedelta(seconds=30)
        ).isoformat(),
        "customer_info": {"saved_data": {}, "current_form": {}},
        "field_registry": [],
        "tool_context": deepcopy(tool_context),
        "chart_payload": None,
    }


class ToolContextConsumerContractTests(unittest.TestCase):
    def test_ready_fixture_is_accepted_without_recalculating_metadata(self):
        request = ChatV2Request.model_validate(request_payload(FIXTURE["ready"]))
        metric = request.tool_context.dashboard_metrics["retirement_gap"]

        self.assertEqual(metric.value, 375_000_000)
        self.assertEqual(metric.report_id, 42)
        self.assertEqual(metric.report_version, 3)
        self.assertEqual(metric.calculation_policy_version, "2026-09-12.v1")

    def test_tool_context_remains_optional_for_v2_compatibility(self):
        payload = request_payload(None)
        payload.pop("tool_context")

        self.assertIsNone(ChatV2Request.model_validate(payload).tool_context)

    def test_session_and_step_mismatch_are_rejected(self):
        for field, value in (("session_id", 99), ("step_id", "income")):
            context = deepcopy(FIXTURE["ready"])
            context[field] = value
            with self.subTest(field=field), self.assertRaises(ValidationError):
                ChatV2Request.model_validate(request_payload(context))

    def test_unknown_metric_and_extra_sensitive_field_are_rejected(self):
        unknown_metric = deepcopy(FIXTURE["ready"])
        unknown_metric["dashboard_metrics"]["invented_metric"] = {
            "availability": "unavailable",
            "reason": "report_not_found",
        }
        with self.assertRaises(ValidationError):
            ChatV2Request.model_validate(request_payload(unknown_metric))

        sensitive = deepcopy(FIXTURE["ready"])
        sensitive["user_id"] = 3
        with self.assertRaises(ValidationError):
            ChatV2Request.model_validate(request_payload(sensitive))

    def test_all_backend_unavailable_reasons_are_contract_values(self):
        for reason in FIXTURE["unavailable_reasons"]:
            context = deepcopy(FIXTURE["ready"])
            context["dashboard_metrics"]["retirement_gap"] = {
                "availability": "unavailable",
                "reason": reason,
            }
            with self.subTest(reason=reason):
                request = ChatV2Request.model_validate(request_payload(context))
                metric = request.tool_context.dashboard_metrics["retirement_gap"]
                self.assertEqual(metric.reason, reason)


if __name__ == "__main__":
    unittest.main()
