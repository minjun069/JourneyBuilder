from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest import TestCase

from transport.contracts import (
    ChatV2Request,
    ServiceContractError,
    verify_service_authorization,
)
from transport.runtime import ExecutionBudget


def make_request(**changes) -> ChatV2Request:
    payload = {
        "contract_version": "2.0",
        "request_id": "ai_req_transport",
        "payload_digest": "0" * 64,
        "session_id": 1,
        "thread_id": "user-3-session-1",
        "step_id": "basic-info",
        "message": "월급은 세후 300만 원이에요.",
        "deadline_at": datetime.now(timezone.utc) + timedelta(seconds=60),
        "customer_info": {"saved_data": {}, "current_form": {}},
        "chart_payload": None,
    }
    payload.update(changes)
    return ChatV2Request.model_validate(payload)


class ExecutionBudgetTests(TestCase):
    def test_budget_tracks_only_the_outer_deadline(self):
        deadline = datetime.now(timezone.utc) + timedelta(seconds=30)
        budget = ExecutionBudget(
            deadline_at=deadline,
            started_at_monotonic=0,
        )

        self.assertEqual(budget.deadline_at, deadline)
        self.assertGreater(budget.remaining_seconds(), 0)
        self.assertFalse(hasattr(budget, "tasks_used"))
        self.assertFalse(hasattr(budget, "tool_calls_used"))


class ServiceAuthorizationTests(TestCase):
    def test_bearer_is_required_and_empty_token_never_bypasses_auth(self):
        for supplied, expected in (
            (None, "test-token"),
            ("Bearer wrong", "test-token"),
            ("Bearer ", ""),
        ):
            with self.subTest(supplied=supplied, expected=expected):
                with self.assertRaises(ServiceContractError) as raised:
                    verify_service_authorization(supplied, expected)
                self.assertEqual(raised.exception.code, "SERVICE_UNAUTHORIZED")

        verify_service_authorization("Bearer test-token", "test-token")


class FeatureControlContractTests(TestCase):
    def test_error_review_is_save_bound_without_a_trigger_field(self):
        review = make_request(request_mode="error_review")
        self.assertEqual(review.request_mode, "error_review")

        with self.assertRaises(ValueError):
            make_request(request_mode="error_review", review_trigger="save")

    def test_legacy_elicit_action_is_rejected(self):
        with self.assertRaises(ValueError):
            make_request(elicit_action="start")

    def test_presentation_entry_rejects_legacy_review_trigger(self):
        request = make_request(request_mode="presentation_entry")
        self.assertEqual(request.request_mode, "presentation_entry")

        with self.assertRaises(ValueError):
            make_request(request_mode="presentation_entry", review_trigger="save")
