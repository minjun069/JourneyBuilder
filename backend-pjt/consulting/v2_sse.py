from __future__ import annotations

import asyncio
import logging

from asgiref.sync import sync_to_async
from django.conf import settings

from .ai_lifecycle import (
    CONTRACT_VERSION,
    isoformat_utc,
    utc_now,
    validate_terminal_payload,
)
from .ai_security import finalize_terminal_result
from .ai_tokenization import ReferenceTokenValidationError, ReferenceTokenVault
from .sse import SSEProtocolError, aiter_sse_events, encode_sse


logger = logging.getLogger(__name__)
PROGRESS_EVENTS_HEADER = 'X-AI-Progress-Events'
PROGRESS_EVENTS_VERSION = 'v1'
_ALLOWED_EVENTS = {'start', 'progress', 'heartbeat', 'done', 'error'}
_TRANSIENT_EVENTS = {'progress', 'heartbeat'}
_PROGRESS_PHASES = {
    'preparing',
    'planning',
    'executing',
    'reviewing',
    'composing',
    'validating',
    'saving',
}


def ai_v2_stream_url():
    base = settings.AI_SERVICE_URL.rstrip('/')
    return f'{base}/api/ai/v2/chat/stream'


async def stream_v2_ai_response(payload, *, progress_events=False):
    import httpx

    timeout = httpx.Timeout(
        connect=settings.AI_SERVICE_TIMEOUT_SECONDS,
        read=settings.AI_STREAM_READ_TIMEOUT_SECONDS,
        write=settings.AI_SERVICE_TIMEOUT_SECONDS,
        pool=settings.AI_SERVICE_TIMEOUT_SECONDS,
    )
    headers = {
        'Authorization': f'Bearer {settings.AI_SERVICE_BEARER_TOKEN}',
        'Accept': 'text/event-stream',
    }
    if progress_events:
        headers[PROGRESS_EVENTS_HEADER] = PROGRESS_EVENTS_VERSION
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream(
            'POST',
            ai_v2_stream_url(),
            json=payload,
            headers=headers,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                yield line


def _validate_identity(data, payload):
    if data.get('contract_version') != CONTRACT_VERSION:
        raise SSEProtocolError('contract_version does not match v2')
    if data.get('request_id') != payload['request_id']:
        raise SSEProtocolError('request_id does not match the stream request')
    expected = {
        'session_id': payload['session_id'],
        'thread_id': payload['thread_id'],
    }
    for field, expected_value in expected.items():
        if field in data and data[field] != expected_value:
            raise SSEProtocolError(f'{field} does not match the stream request')


def _validate_transient_event(event_name, data, previous_sequence):
    event_sequence = data.get('event_seq')
    if (
        isinstance(event_sequence, bool)
        or not isinstance(event_sequence, int)
        or event_sequence <= previous_sequence
    ):
        raise SSEProtocolError('transient SSE event_seq must increase')
    identity_fields = {
        'contract_version', 'request_id', 'session_id', 'thread_id', 'event_seq',
    }
    if event_name == 'heartbeat':
        if set(data) - identity_fields:
            raise SSEProtocolError('heartbeat contains unsupported fields')
        return event_sequence
    if set(data) - (identity_fields | {'phase', 'status'}):
        raise SSEProtocolError('progress contains unsupported fields')
    if data.get('phase') not in _PROGRESS_PHASES or data.get('status') != 'active':
        raise SSEProtocolError('progress event is invalid')
    return event_sequence


def _serialize_message(message):
    return {
        'id': message.id,
        'role': message.role,
        'content': message.content,
        'extracted_data': message.extracted_data,
    }


def _browser_start(state):
    return {
        'contract_version': CONTRACT_VERSION,
        'request_id': state['request_id'],
        'session_id': state['session_id'],
        'thread_id': state['thread_id'],
        'user_message_id': state['user_message_id'],
    }


def _browser_transient(event_name, state, data):
    payload = {
        'contract_version': CONTRACT_VERSION,
        'request_id': state['request_id'],
        'event_seq': data['event_seq'],
    }
    if event_name == 'progress':
        payload.update({
            'phase': data['phase'],
            'status': 'active',
        })
    return payload


def _browser_error(state, error):
    return {
        'contract_version': CONTRACT_VERSION,
        'request_id': state['request_id'],
        'status': 'failed',
        'error': error,
    }


def _failed_terminal(state, error):
    return {
        'contract_version': CONTRACT_VERSION,
        'request_id': state['request_id'],
        'session_id': state['session_id'],
        'thread_id': state['thread_id'],
        'status': 'failed',
        'completed_at': isoformat_utc(utc_now()),
        'error': error,
    }


async def _finalize(state, payload):
    return await sync_to_async(
        finalize_terminal_result,
        thread_sensitive=True,
    )(state, payload)


async def _save_terminal(store, state):
    await sync_to_async(store.save_terminal, thread_sensitive=True)(state)


async def _delete_token_vault(store, request_id):
    try:
        await sync_to_async(
            store.delete_token_vault,
            thread_sensitive=True,
        )(request_id)
    except Exception:
        logger.exception(
            'Django AI 참조 token vault 삭제 실패',
            extra={'request_id': request_id},
        )


async def proxy_v2_ai_stream(
    session,
    payload,
    state,
    store,
    *,
    token_vault: ReferenceTokenVault,
    progress_events=False,
):
    yield encode_sse('start', _browser_start(state))

    terminal = False
    event_sequence = 0
    try:
        token_vault.assert_scope(
            user_id=state['user_id'],
            session_id=state['session_id'],
            request_id=state['request_id'],
        )
        upstream = stream_v2_ai_response(payload, progress_events=progress_events)
        async for event_name, data in aiter_sse_events(upstream):
            if event_name not in _ALLOWED_EVENTS:
                raise SSEProtocolError(f'unsupported SSE event: {event_name}')
            _validate_identity(data, payload)

            if event_name == 'start':
                continue
            if event_name in _TRANSIENT_EVENTS:
                if not progress_events:
                    raise SSEProtocolError('transient event was not negotiated')
                event_sequence = _validate_transient_event(
                    event_name,
                    data,
                    event_sequence,
                )
                yield encode_sse(
                    event_name,
                    _browser_transient(event_name, state, data),
                )
                continue
            validated = validate_terminal_payload(data)
            if event_name == 'error':
                if validated['status'] != 'failed':
                    raise SSEProtocolError('error event must have failed status')
                restored = validate_terminal_payload(
                    token_vault.restore_terminal(validated, step_id=state['step_id'])
                )
                finalized = await _finalize(state, restored)
                terminal = True
                await _save_terminal(store, finalized.state)
                await _delete_token_vault(store, state['request_id'])
                yield encode_sse('error', _browser_error(state, restored['error']))
                return

            if validated['status'] != 'completed':
                raise SSEProtocolError('done event must have completed status')
            output = validated['output']
            if not output.get('response'):
                raise SSEProtocolError('done output has no response')

            restored = validate_terminal_payload(
                token_vault.restore_terminal(validated, step_id=state['step_id'])
            )

            finalized = await _finalize(state, restored)
            terminal = True
            await _save_terminal(store, finalized.state)
            await _delete_token_vault(store, state['request_id'])
            message = finalized.message
            if message is None:
                raise RuntimeError('completed terminal receipt has no assistant message')
            yield encode_sse('done', {
                'contract_version': CONTRACT_VERSION,
                'request_id': state['request_id'],
                'status': 'completed',
                'message': _serialize_message(message),
            })
            return

        raise SSEProtocolError('SSE stream ended without a terminal event')
    except asyncio.CancelledError:
        logger.info(
            'Django v2 SSE 클라이언트 연결 종료',
            extra={
                'request_id': state['request_id'],
                'reason_code': 'browser_stream_disconnected',
            },
        )
        if not terminal:
            # The AI may already have atomically committed context and its
            # terminal while delivery to Django was interrupted. Keep the
            # original request identity and vault so an idempotent retry can
            # query/replay that exact terminal instead of creating a new turn.
            try:
                await asyncio.shield(
                    sync_to_async(store.save_active, thread_sensitive=True)({
                        **state,
                        'retry_after_disconnect': True,
                    })
                )
            except Exception:
                logger.exception(
                    'Django v2 SSE 재연결 표식 저장 실패',
                    extra={
                        'request_id': state['request_id'],
                        'reason_code': 'disconnect_resume_marker_failed',
                    },
                )
        raise
    except Exception as exc:
        if isinstance(exc, ReferenceTokenValidationError):
            logger.warning(
                'Django v2 SSE 참조 token 검증 실패',
                extra={'request_id': state['request_id']},
            )
            error = {
                'code': 'POLICY_BLOCKED',
                'message': 'AI 응답 데이터 검증에 실패했습니다.',
                'retryable': False,
            }
        else:
            logger.exception(
                'Django v2 SSE 프록시 실패',
                extra={'request_id': state['request_id']},
            )
            error = {
                'code': 'INTERNAL_ERROR',
                'message': 'AI 응답 생성에 실패했습니다.',
                'retryable': True,
            }
        if not terminal:
            try:
                finalized = await _finalize(state, _failed_terminal(state, error))
                terminal = True
                await _save_terminal(store, finalized.state)
                await _delete_token_vault(store, state['request_id'])
            except Exception:
                logger.exception(
                    'Django v2 SSE 실패 상태 확정 실패',
                    extra={'request_id': state['request_id']},
                )
        yield encode_sse('error', _browser_error(state, error))


async def replay_v2_stream(state, message=None):
    yield encode_sse('start', _browser_start(state))
    if state['status'] == 'completed' and message is not None:
        yield encode_sse('done', {
            'contract_version': CONTRACT_VERSION,
            'request_id': state['request_id'],
            'status': 'completed',
            'message': _serialize_message(message),
        })
        return
    error = state.get('error') or {
        'code': 'INTERNAL_ERROR',
        'message': 'AI 응답 결과를 복구할 수 없습니다.',
        'retryable': True,
    }
    yield encode_sse('error', _browser_error(state, error))
