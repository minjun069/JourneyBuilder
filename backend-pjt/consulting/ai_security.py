"""Ownership-bound, exactly-once terminalization for AI SSE results."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import logging

from django.db import transaction

from .ai_lifecycle import (
    CONTRACT_VERSION,
    LifecycleError,
    bind_terminal_result,
    create_v2_assistant_message,
    isoformat_utc,
)
from .ai_services import build_thread_id
from .models import AITerminalReceipt, ConsultingSession, Message


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TerminalFinalization:
    state: dict
    message: Message | None
    replay: bool


def _receipt_state(receipt, state=None):
    return {
        **(state or {}),
        'contract_version': CONTRACT_VERSION,
        'request_id': receipt.request_id,
        'status': receipt.status,
        'session_id': receipt.session_id,
        'user_id': receipt.session.user_id,
        'user_message_id': receipt.user_message_id,
        'thread_id': receipt.thread_id,
        'step_id': receipt.step_id,
        'completed_at': isoformat_utc(receipt.completed_at),
        'callback_digest': receipt.callback_digest,
        **(
            {'assistant_message_id': receipt.assistant_message_id}
            if receipt.assistant_message_id is not None else {}
        ),
        **({'error': receipt.error} if receipt.error is not None else {}),
    }


def recover_terminal_state(
    request_id,
    *,
    state=None,
    expected_session_id=None,
    expected_user_id=None,
):
    receipt = (
        AITerminalReceipt.objects.select_related(
            'session',
            'user_message',
            'assistant_message',
        )
        .filter(request_id=request_id)
        .first()
    )
    if receipt is None:
        return state
    if (
        expected_session_id is not None
        and receipt.session_id != expected_session_id
    ) or (
        expected_user_id is not None
        and receipt.session.user_id != expected_user_id
    ):
        return state
    if state is not None and (
        state.get('request_id') != receipt.request_id
        or state.get('session_id') != receipt.session_id
        or state.get('user_id') != receipt.session.user_id
    ):
        return state
    return _receipt_state(receipt, state)


def _raise_conflict(message='이미 종결된 AI 작업과 terminal 결과가 충돌합니다.'):
    raise LifecycleError(409, 'TERMINAL_CONFLICT', message)


def _validate_receipt(receipt, state, payload):
    if (
        receipt.session_id != state.get('session_id')
        or receipt.thread_id != state.get('thread_id')
        or receipt.step_id != state.get('step_id')
        or receipt.user_message_id != state.get('user_message_id')
        or receipt.callback_digest != state.get('callback_digest')
        or receipt.status != payload['status']
    ):
        _raise_conflict()


def _audit_denial(payload, error):
    if error.code not in ('TERMINAL_BINDING_MISMATCH', 'TERMINAL_CONFLICT'):
        return
    logger.warning(
        'AI SSE terminal result denied',
        extra={
            'request_ref': sha256(
                str(payload.get('request_id', '')).encode('utf-8')
            ).hexdigest()[:12],
            'reason_code': error.code,
        },
    )


def finalize_terminal_result(state, payload):
    """Atomically bind an SSE terminal payload to one owner and one DB result."""

    try:
        bound, redis_replay = bind_terminal_result(state, payload)
        digest = bound['callback_digest']

        with transaction.atomic():
            session = (
                ConsultingSession.objects.select_related('user')
                .select_for_update()
                .filter(
                    id=bound.get('session_id'),
                    user_id=bound.get('user_id'),
                )
                .first()
            )
            if session is None or bound.get('thread_id') != build_thread_id(
                session.user,
                session,
            ):
                raise LifecycleError(
                    409,
                    'TERMINAL_BINDING_MISMATCH',
                    'terminal 결과 소유권이 원래 요청과 다릅니다.',
                )

            receipt = (
                AITerminalReceipt.objects.select_for_update()
                .filter(request_id=payload['request_id'])
                .first()
            )
            if receipt is not None:
                _validate_receipt(receipt, bound, payload)
                return TerminalFinalization(
                    _receipt_state(receipt, bound),
                    receipt.assistant_message,
                    True,
                )

            if redis_replay:
                message = None
                if bound.get('assistant_message_id') is not None:
                    message = Message.objects.filter(
                        id=bound['assistant_message_id'],
                        session=session,
                        role=Message.Role.ASSISTANT,
                    ).first()
                return TerminalFinalization(bound, message, True)

            user_message = Message.objects.filter(
                id=bound.get('user_message_id'),
                session=session,
                role=Message.Role.USER,
            ).first()
            if user_message is None:
                raise LifecycleError(
                    409,
                    'TERMINAL_BINDING_MISMATCH',
                    'terminal 결과의 원 요청 메시지가 상담과 다릅니다.',
                )

            message = None
            if payload['status'] == AITerminalReceipt.Status.COMPLETED:
                message = create_v2_assistant_message(
                    session,
                    payload,
                    bound['step_id'],
                )
            receipt = AITerminalReceipt.objects.create(
                request_id=payload['request_id'],
                session=session,
                thread_id=payload['thread_id'],
                step_id=bound['step_id'],
                user_message=user_message,
                assistant_message=message,
                callback_digest=digest,
                status=payload['status'],
                completed_at=datetime.fromisoformat(
                    payload['completed_at'].replace('Z', '+00:00')
                ),
                error=payload.get('error'),
            )
            return TerminalFinalization(
                _receipt_state(receipt, bound),
                message,
                False,
            )
    except LifecycleError as error:
        _audit_denial(payload, error)
        raise
