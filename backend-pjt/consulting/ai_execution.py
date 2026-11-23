"""Durable AI execution lease expiry and stale-lock recovery."""

from __future__ import annotations

import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .ai_lifecycle import isoformat_utc
from .models import AIRequestExecution


logger = logging.getLogger(__name__)
AI_PROCESSING_EXPIRY_SECONDS = 180
_EXPIRED_ERROR = {
    'code': 'UPSTREAM_UNAVAILABLE',
    'message': '이전 AI 응답 연결이 만료되었습니다. 다시 시도해주세요.',
    'retryable': True,
}


def processing_expires_at(now=None):
    current = now or timezone.now()
    return current + timedelta(seconds=AI_PROCESSING_EXPIRY_SECONDS)


def is_stale_execution(execution, *, now=None):
    current = now or timezone.now()
    expires_at = execution.expires_at
    if expires_at is not None:
        return expires_at <= current
    return execution.created_at <= current - timedelta(
        seconds=AI_PROCESSING_EXPIRY_SECONDS,
    )


def make_expired_state(state, *, completed_at=None):
    expired = {
        **state,
        'status': 'expired',
        'completed_at': isoformat_utc(completed_at or timezone.now()),
        'error': dict(_EXPIRED_ERROR),
    }
    expired.pop('retry_after_disconnect', None)
    return expired


def expired_state_from_execution(execution):
    """Rebuild the public terminal state from the durable expired row."""

    return make_expired_state(
        {
            'contract_version': '2.0',
            'request_id': execution.request_id,
            'session_id': execution.session_id,
            'user_id': execution.session.user_id,
            'user_message_id': execution.user_message_id,
            'thread_id': execution.thread_id,
            'step_id': execution.step_id,
            'payload_digest': execution.payload_digest,
        },
        completed_at=execution.completed_at,
    )


def expire_stale_execution(execution, store, *, now=None):
    """Expire one locked processing row and clean its transient Redis state."""

    current = now or timezone.now()
    if (
        execution.status != AIRequestExecution.Status.PROCESSING
        or not is_stale_execution(execution, now=current)
    ):
        return False, None

    try:
        cached_state = store.get_request(execution.request_id)
    except Exception:
        cached_state = None
        logger.exception(
            '만료 대상 AI Redis 상태 조회 실패',
            extra={
                'request_id': execution.request_id,
                'reason_code': 'stale_execution_state_unavailable',
            },
        )
    expired_state = (
        make_expired_state(cached_state, completed_at=current)
        if cached_state is not None
        else None
    )
    execution.status = AIRequestExecution.Status.EXPIRED
    execution.completed_at = current
    execution.request_payload = {}
    execution.save(update_fields=['status', 'completed_at', 'request_payload'])

    def cleanup_transient_state():
        if expired_state is not None:
            try:
                store.save_terminal(expired_state)
            except Exception:
                logger.exception(
                    '만료된 AI terminal 상태 저장 실패',
                    extra={
                        'request_id': execution.request_id,
                        'reason_code': 'stale_execution_terminal_save_failed',
                    },
                )
        try:
            store.delete_token_vault(execution.request_id)
        except Exception:
            logger.exception(
                '만료된 AI token vault 정리 실패',
                extra={
                    'request_id': execution.request_id,
                    'reason_code': 'stale_execution_vault_cleanup_failed',
                },
            )

    transaction.on_commit(cleanup_transient_state)
    logger.warning(
        '오래된 AI 실행 잠금 만료',
        extra={
            'request_id': execution.request_id,
            'reason_code': 'stale_processing_expired',
        },
    )
    return True, expired_state
