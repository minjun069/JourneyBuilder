from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from django.db import close_old_connections
from django.test import TransactionTestCase

from accounts.models import User

from .ai_lifecycle import CONTRACT_VERSION, LifecycleError
from .ai_security import finalize_terminal_result, recover_terminal_state
from .models import AIRequestExecution, AITerminalReceipt, ConsultingSession, Message


class F12TerminalReceiptSecurityTests(TransactionTestCase):
    def setUp(self):
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
        self.payload_digest = "a" * 64
        AIRequestExecution.objects.create(
            request_id="F12-SYNTHETIC-REQUEST-TRM-001",
            session=self.session_a,
            thread_id=f"user-{self.user_a.id}-session-{self.session_a.id}",
            step_id="basic-info",
            user_message=self.user_message_a,
            payload_digest=self.payload_digest,
            request_payload={"request_id": "F12-SYNTHETIC-REQUEST-TRM-001"},
        )

    def state(self, **changes):
        state = {
            "contract_version": CONTRACT_VERSION,
            "request_id": "F12-SYNTHETIC-REQUEST-TRM-001",
            "status": "processing",
            "session_id": self.session_a.id,
            "user_id": self.user_a.id,
            "user_message_id": self.user_message_a.id,
            "thread_id": f"user-{self.user_a.id}-session-{self.session_a.id}",
            "step_id": "basic-info",
            "payload_digest": self.payload_digest,
        }
        state.update(changes)
        return state

    def payload(self, **changes):
        payload = {
            "contract_version": CONTRACT_VERSION,
            "request_id": "F12-SYNTHETIC-REQUEST-TRM-001",
            "session_id": self.session_a.id,
            "thread_id": f"user-{self.user_a.id}-session-{self.session_a.id}",
            "status": "completed",
            "completed_at": "2026-09-11T01:00:42Z",
            "output": {"response": "합성 완료 응답"},
        }
        payload.update(changes)
        return payload

    def test_owner_mismatch_does_not_create_a_receipt_or_message(self):
        state = self.state(
            session_id=self.session_b.id,
            user_id=self.user_a.id,
            user_message_id=self.user_message_b.id,
            thread_id=f"user-{self.user_b.id}-session-{self.session_b.id}",
        )
        payload = self.payload(
            session_id=self.session_b.id,
            thread_id=state["thread_id"],
        )

        with self.assertRaises(LifecycleError) as raised:
            finalize_terminal_result(state, payload)

        self.assertEqual(raised.exception.code, "TERMINAL_BINDING_MISMATCH")
        self.assertEqual(AITerminalReceipt.objects.count(), 0)
        self.assertEqual(Message.objects.filter(role=Message.Role.ASSISTANT).count(), 0)

    def test_identical_terminal_results_are_exactly_once(self):
        state = self.state()
        payload = self.payload()

        first = finalize_terminal_result(state, payload)
        replay = finalize_terminal_result(state, payload)

        self.assertFalse(first.replay)
        self.assertTrue(replay.replay)
        self.assertEqual(AITerminalReceipt.objects.count(), 1)
        self.assertEqual(Message.objects.filter(role=Message.Role.ASSISTANT).count(), 1)
        self.assertEqual(first.message.id, replay.message.id)

        with self.assertRaises(LifecycleError) as raised:
            finalize_terminal_result(
                state,
                self.payload(completed_at="2026-09-11T01:00:43Z"),
            )
        self.assertEqual(raised.exception.code, "TERMINAL_CONFLICT")
        self.assertEqual(AITerminalReceipt.objects.count(), 1)
        self.assertEqual(Message.objects.filter(role=Message.Role.ASSISTANT).count(), 1)

    def test_simultaneous_identical_results_share_one_receipt_and_message(self):
        state = self.state()
        payload = self.payload()

        def finalize_in_own_connection():
            close_old_connections()
            try:
                return finalize_terminal_result(state, payload).replay
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            replay_flags = list(
                executor.map(lambda _index: finalize_in_own_connection(), range(2))
            )

        self.assertEqual(sorted(replay_flags), [False, True])
        self.assertEqual(AITerminalReceipt.objects.count(), 1)
        self.assertEqual(Message.objects.filter(role=Message.Role.ASSISTANT).count(), 1)

    def test_mismatched_or_expired_state_rejects_terminal_result(self):
        with self.assertRaises(LifecycleError) as mismatch_error:
            finalize_terminal_result(
                self.state(),
                self.payload(session_id=self.session_b.id, thread_id="foreign-thread"),
            )
        self.assertEqual(
            mismatch_error.exception.code,
            "TERMINAL_BINDING_MISMATCH",
        )

        with self.assertRaises(LifecycleError) as expired_error:
            finalize_terminal_result(
                self.state(status="expired"),
                self.payload(),
            )
        self.assertEqual(expired_error.exception.code, "TERMINAL_CONFLICT")
        self.assertEqual(AITerminalReceipt.objects.count(), 0)
        self.assertEqual(Message.objects.filter(role=Message.Role.ASSISTANT).count(), 0)

    def test_receipt_recovers_completed_result_after_redis_loss(self):
        finalized = finalize_terminal_result(self.state(), self.payload())

        recovered = recover_terminal_state(
            finalized.state["request_id"],
            state=None,
            expected_session_id=self.session_a.id,
            expected_user_id=self.user_a.id,
        )

        self.assertEqual(recovered["status"], "completed")
        self.assertEqual(
            recovered["assistant_message_id"],
            finalized.message.id,
        )
        self.assertEqual(AITerminalReceipt.objects.count(), 1)
        self.assertEqual(Message.objects.filter(role=Message.Role.ASSISTANT).count(), 1)

    def test_failed_result_creates_receipt_without_assistant_message(self):
        payload = self.payload(
            status="failed",
            error={
                "code": "LLM_TIMEOUT",
                "message": "AI 응답 생성 시간이 초과되었습니다.",
                "retryable": True,
            },
        )
        payload.pop("output")

        finalized = finalize_terminal_result(self.state(), payload)

        self.assertEqual(finalized.state["status"], "failed")
        self.assertIsNone(finalized.message)
        self.assertEqual(AITerminalReceipt.objects.count(), 1)
        self.assertEqual(Message.objects.filter(role=Message.Role.ASSISTANT).count(), 0)
