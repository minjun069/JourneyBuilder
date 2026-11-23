from datetime import timedelta
from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase
from django.utils import timezone

from .ai_execution import (
    AI_PROCESSING_EXPIRY_SECONDS,
    expire_stale_execution,
    expired_state_from_execution,
    is_stale_execution,
    processing_expires_at,
)
from .models import AIRequestExecution


class AIExecutionExpiryTests(SimpleTestCase):
    def execution(self, *, now, expires_at=None, created_delta=0):
        execution = SimpleNamespace(
            request_id='ai_req_stale',
            status=AIRequestExecution.Status.PROCESSING,
            expires_at=expires_at,
            created_at=now - timedelta(seconds=created_delta),
            completed_at=None,
            request_payload={'request_id': 'ai_req_stale'},
        )
        execution.save = Mock()
        return execution

    def test_expiry_deadline_uses_three_minute_processing_lease(self):
        now = timezone.now()

        self.assertEqual(
            processing_expires_at(now),
            now + timedelta(seconds=AI_PROCESSING_EXPIRY_SECONDS),
        )

    def test_null_expiry_falls_back_to_created_at_for_existing_rows(self):
        now = timezone.now()

        self.assertFalse(is_stale_execution(
            self.execution(now=now, created_delta=179),
            now=now,
        ))
        self.assertTrue(is_stale_execution(
            self.execution(now=now, created_delta=180),
            now=now,
        ))

    @patch('consulting.ai_execution.transaction.on_commit')
    def test_stale_execution_is_expired_and_transient_state_is_cleaned(
        self,
        on_commit,
    ):
        now = timezone.now()
        execution = self.execution(now=now, expires_at=now)
        store = Mock()
        store.get_request.return_value = {
            'request_id': execution.request_id,
            'status': 'processing',
        }
        on_commit.side_effect = lambda callback: callback()

        did_expire, state = expire_stale_execution(execution, store, now=now)

        self.assertTrue(did_expire)
        self.assertEqual(execution.status, AIRequestExecution.Status.EXPIRED)
        self.assertEqual(execution.request_payload, {})
        self.assertEqual(state['status'], 'expired')
        self.assertEqual(state['error']['code'], 'UPSTREAM_UNAVAILABLE')
        execution.save.assert_called_once_with(
            update_fields=['status', 'completed_at', 'request_payload'],
        )
        store.save_terminal.assert_called_once_with(state)
        store.delete_token_vault.assert_called_once_with(execution.request_id)

    def test_live_execution_remains_processing(self):
        now = timezone.now()
        execution = self.execution(
            now=now,
            expires_at=now + timedelta(seconds=1),
        )

        did_expire, state = expire_stale_execution(execution, Mock(), now=now)

        self.assertFalse(did_expire)
        self.assertIsNone(state)
        execution.save.assert_not_called()

    @patch('consulting.ai_execution.transaction.on_commit')
    def test_redis_read_failure_does_not_keep_stale_database_lock(self, on_commit):
        now = timezone.now()
        execution = self.execution(now=now, expires_at=now)
        store = Mock()
        store.get_request.side_effect = RuntimeError('redis unavailable')
        on_commit.side_effect = lambda callback: callback()

        did_expire, state = expire_stale_execution(execution, store, now=now)

        self.assertTrue(did_expire)
        self.assertIsNone(state)
        self.assertEqual(execution.status, AIRequestExecution.Status.EXPIRED)
        store.save_terminal.assert_not_called()
        store.delete_token_vault.assert_called_once_with(execution.request_id)

    def test_expired_state_can_be_rebuilt_from_durable_execution(self):
        now = timezone.now()
        execution = self.execution(now=now, expires_at=now)
        execution.session_id = 12
        execution.session = SimpleNamespace(user_id=3)
        execution.user_message_id = 41
        execution.thread_id = 'user-3-session-12'
        execution.step_id = 'general'
        execution.payload_digest = '0' * 64
        execution.completed_at = now

        state = expired_state_from_execution(execution)

        self.assertEqual(state['status'], 'expired')
        self.assertEqual(state['session_id'], 12)
        self.assertEqual(state['user_id'], 3)
        self.assertEqual(state['error']['code'], 'UPSTREAM_UNAVAILABLE')


class ExpireStaleAIRequestsCommandTests(SimpleTestCase):
    @patch(
        'consulting.management.commands.expire_stale_ai_requests.RedisLifecycleStore'
    )
    @patch(
        'consulting.management.commands.expire_stale_ai_requests.AIRequestExecution.objects'
    )
    def test_default_mode_only_reports_candidates(self, execution_objects, store_class):
        values = MagicMock()
        values.__getitem__.return_value = [(11, 12), (13, 12)]
        query = execution_objects.filter.return_value.filter.return_value
        query.order_by.return_value.values_list.return_value = values
        output = StringIO()

        call_command('expire_stale_ai_requests', stdout=output)

        self.assertIn('2건 확인; 변경 없음', output.getvalue())
        store_class.assert_not_called()

    def test_limit_must_stay_within_the_operational_bound(self):
        with self.assertRaises(CommandError):
            call_command('expire_stale_ai_requests', limit=0)
