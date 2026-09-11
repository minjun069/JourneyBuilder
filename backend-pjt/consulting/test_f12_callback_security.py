from __future__ import annotations

import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

from django.db import close_old_connections
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models import User

from .ai_lifecycle import CONTRACT_VERSION, LifecycleError
from .ai_security import (
    finalize_expired_request,
    finalize_terminal_callback,
    recover_terminal_state,
)
from .models import AITerminalReceipt, ConsultingSession, Message
from .v2_views import V2CallbackView, V2PollingView


SUITE_PATH = (
    Path(__file__).resolve().parents[2]
    / "ai-pjt/tests/security/service-tool-security-cases.json"
)


def _load_t03_cases():
    with SUITE_PATH.open(encoding="utf-8") as suite_file:
        suite = json.load(suite_file)
    return {
        case["id"]: case
        for case in suite["cases"]
        if case["downstream_owner"] == "F12-T03"
    }


@override_settings(AI_SERVICE_BEARER_TOKEN="f12-service-token")
class F12CallbackSecurityTests(TransactionTestCase):
    def setUp(self):
        self.cases = _load_t03_cases()
        self.factory = APIRequestFactory()
        self.user_a = User.objects.create_user(
            email="f12-user-a@example.test",
            password="test-password",
            name="F12 사용자 A",
        )
        self.user_b = User.objects.create_user(
            email="f12-user-b@example.test",
            password="test-password",
            name="F12 사용자 B",
        )
        self.session_a = ConsultingSession.objects.create(
            user=self.user_a,
            status=ConsultingSession.Status.IN_PROGRESS,
        )
        self.session_b = ConsultingSession.objects.create(
            user=self.user_b,
            status=ConsultingSession.Status.IN_PROGRESS,
        )
        self.user_message_a = Message.objects.create(
            session=self.session_a,
            role=Message.Role.USER,
            content="합성 요청 A",
        )
        self.user_message_b = Message.objects.create(
            session=self.session_b,
            role=Message.Role.USER,
            content="합성 요청 B",
        )

    def state(self, **changes):
        state = {
            "contract_version": CONTRACT_VERSION,
            "request_id": "F12-SYNTHETIC-REQUEST-CBK-001",
            "status": "processing",
            "session_id": self.session_a.id,
            "user_id": self.user_a.id,
            "user_message_id": self.user_message_a.id,
            "thread_id": f"user-{self.user_a.id}-session-{self.session_a.id}",
            "step_id": "basic-info",
            "processing_deadline_at": "2026-09-11T01:01:30Z",
        }
        state.update(changes)
        return state

    def payload(self, **changes):
        payload = {
            "contract_version": CONTRACT_VERSION,
            "request_id": "F12-SYNTHETIC-REQUEST-CBK-001",
            "session_id": self.session_a.id,
            "thread_id": f"user-{self.user_a.id}-session-{self.session_a.id}",
            "status": "completed",
            "completed_at": "2026-09-11T01:00:42Z",
            "output": {"response": "합성 완료 응답"},
        }
        payload.update(changes)
        return payload

    def callback_request(self, payload):
        return self.factory.post(
            "/api/v2/internal/ai/callback/",
            payload,
            format="json",
            HTTP_AUTHORIZATION="Bearer f12-service-token",
        )

    @patch("consulting.v2_views.RedisLifecycleStore")
    def test_own_001_hides_a_foreign_session_before_state_access(self, store_class):
        case = self.cases["F12-OWN-001"]
        request = self.factory.get(
            f"/api/v2/consultations/{self.session_b.id}/ai-responses/unknown/"
        )
        force_authenticate(request, user=self.user_a)

        response = V2PollingView.as_view()(
            request,
            session_id=self.session_b.id,
            request_id="unknown",
        )

        self.assertEqual(response.status_code, case["expected"]["http_status"])
        self.assertEqual(response.data["error"]["code"], case["expected"]["reason_code"])
        store_class.assert_not_called()

    def test_own_002_rejects_state_user_and_session_owner_mismatch(self):
        case = self.cases["F12-OWN-002"]
        state = self.state(
            request_id=case["input"]["request_id"],
            session_id=self.session_b.id,
            user_id=self.user_a.id,
            user_message_id=self.user_message_b.id,
            thread_id=f"user-{self.user_b.id}-session-{self.session_b.id}",
        )
        payload = self.payload(
            request_id=case["input"]["request_id"],
            session_id=self.session_b.id,
            thread_id=state["thread_id"],
        )

        with self.assertRaises(LifecycleError) as raised:
            finalize_terminal_callback(state, payload)

        self.assertEqual(raised.exception.status_code, case["expected"]["http_status"])
        self.assertEqual(raised.exception.code, case["expected"]["reason_code"])
        self.assertEqual(AITerminalReceipt.objects.count(), 0)
        self.assertEqual(Message.objects.filter(role=Message.Role.ASSISTANT).count(), 0)

    def test_cbk_001_and_rpl_003_004_are_exactly_once(self):
        accepted = self.cases["F12-CBK-001"]
        replay_case = self.cases["F12-RPL-003"]
        conflict_case = self.cases["F12-RPL-004"]
        state = self.state()
        payload = self.payload()

        first = finalize_terminal_callback(state, payload)
        replay = finalize_terminal_callback(state, payload)

        self.assertFalse(first.replay)
        self.assertTrue(replay.replay)
        self.assertEqual(accepted["expected"]["http_status"], 200)
        self.assertEqual(replay_case["expected"]["http_status"], 200)
        self.assertEqual(AITerminalReceipt.objects.count(), 1)
        self.assertEqual(Message.objects.filter(role=Message.Role.ASSISTANT).count(), 1)
        self.assertEqual(first.message.id, replay.message.id)

        with self.assertRaises(LifecycleError) as raised:
            finalize_terminal_callback(
                state,
                self.payload(completed_at="2026-09-11T01:00:43Z"),
            )
        self.assertEqual(raised.exception.status_code, conflict_case["expected"]["http_status"])
        self.assertEqual(raised.exception.code, conflict_case["expected"]["reason_code"])
        self.assertEqual(AITerminalReceipt.objects.count(), 1)
        self.assertEqual(Message.objects.filter(role=Message.Role.ASSISTANT).count(), 1)

    def test_simultaneous_identical_callbacks_share_one_receipt_and_message(self):
        state = self.state()
        payload = self.payload()

        def finalize_in_own_connection():
            close_old_connections()
            try:
                return finalize_terminal_callback(state, payload).replay
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            replay_flags = list(executor.map(lambda _index: finalize_in_own_connection(), range(2)))

        self.assertEqual(sorted(replay_flags), [False, True])
        self.assertEqual(AITerminalReceipt.objects.count(), 1)
        self.assertEqual(Message.objects.filter(role=Message.Role.ASSISTANT).count(), 1)

    def test_cbk_002_and_003_preserve_the_original_state(self):
        mismatch = self.cases["F12-CBK-002"]
        expired = self.cases["F12-CBK-003"]

        with self.assertRaises(LifecycleError) as mismatch_error:
            finalize_terminal_callback(
                self.state(),
                self.payload(session_id=self.session_b.id, thread_id="foreign-thread"),
            )
        self.assertEqual(mismatch_error.exception.code, mismatch["expected"]["reason_code"])

        expired_state = self.state(status="expired", error={
            "code": "AI_REQUEST_EXPIRED",
            "message": "AI 작업 시간이 만료되었습니다. 다시 요청해주세요.",
            "retryable": True,
        })
        terminal_expired = finalize_expired_request(expired_state)
        self.assertEqual(terminal_expired["status"], "expired")

        with self.assertRaises(LifecycleError) as expired_error:
            finalize_terminal_callback(
                self.state(),
                self.payload(),
            )
        self.assertEqual(expired_error.exception.code, expired["expected"]["reason_code"])
        self.assertEqual(AITerminalReceipt.objects.count(), 1)
        self.assertEqual(Message.objects.filter(role=Message.Role.ASSISTANT).count(), 0)

    @patch("consulting.v2_views.RedisLifecycleStore")
    def test_callback_retry_recovers_after_db_commit_and_redis_failure(self, store_class):
        store = MagicMock()
        store.get_request.side_effect = [self.state(), None]
        store.save_terminal.side_effect = ConnectionError("synthetic Redis failure")
        store_class.return_value = store
        payload = self.payload()

        first_response = V2CallbackView.as_view()(self.callback_request(payload))

        self.assertEqual(first_response.status_code, 503)
        self.assertEqual(AITerminalReceipt.objects.count(), 1)
        self.assertEqual(Message.objects.filter(role=Message.Role.ASSISTANT).count(), 1)

        store.save_terminal.side_effect = None
        replay_response = V2CallbackView.as_view()(self.callback_request(payload))

        self.assertEqual(replay_response.status_code, 200)
        self.assertEqual(AITerminalReceipt.objects.count(), 1)
        self.assertEqual(Message.objects.filter(role=Message.Role.ASSISTANT).count(), 1)
        recovered = recover_terminal_state(payload["request_id"])
        self.assertEqual(recovered["assistant_message_id"], replay_response.data["data"]["message_id"])

    @patch("consulting.v2_views.RedisLifecycleStore")
    def test_polling_recovers_a_terminal_receipt_when_redis_state_is_missing(self, store_class):
        finalized = finalize_terminal_callback(self.state(), self.payload())
        store = MagicMock()
        store.get_request.return_value = None
        store_class.return_value = store
        request = self.factory.get(
            f"/api/v2/consultations/{self.session_a.id}/ai-responses/{finalized.state['request_id']}/"
        )
        force_authenticate(request, user=self.user_a)

        response = V2PollingView.as_view()(
            request,
            session_id=self.session_a.id,
            request_id=finalized.state["request_id"],
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["status"], "completed")
        self.assertEqual(response.data["data"]["message"]["id"], finalized.message.id)
        store.save_terminal.assert_called_once()
