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
from .sse import SSEProtocolError, aiter_sse_events, encode_sse


logger = logging.getLogger(__name__)
_ALLOWED_EVENTS = {'start', 'delta', 'done', 'error'}


def ai_v2_stream_url():
    base = settings.AI_SERVICE_URL.rstrip('/')
    if base.endswith('/chat'):
        base = base[:-5]
    return f'{base}/v2/chat/stream'


async def stream_v2_ai_response(payload):
    import httpx

    timeout = httpx.Timeout(
        connect=settings.AI_SERVICE_TIMEOUT_SECONDS,
        read=settings.AI_STREAM_READ_TIMEOUT_SECONDS,
        write=settings.AI_SERVICE_TIMEOUT_SECONDS,
        pool=settings.AI_SERVICE_TIMEOUT_SECONDS,
    )
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream(
            'POST',
            ai_v2_stream_url(),
            json=payload,
            headers={
                'Authorization': f'Bearer {settings.AI_SERVICE_BEARER_TOKEN}',
                'Accept': 'text/event-stream',
            },
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


async def proxy_v2_ai_stream(session, payload, state, store):
    yield encode_sse('start', _browser_start(state))

    chunks: list[str] = []
    terminal = False
    try:
        async for event_name, data in aiter_sse_events(stream_v2_ai_response(payload)):
            if event_name not in _ALLOWED_EVENTS:
                raise SSEProtocolError(f'unsupported SSE event: {event_name}')
            _validate_identity(data, payload)

            if event_name == 'start':
                continue
            if event_name == 'delta':
                content = data.get('content')
                if not isinstance(content, str):
                    raise SSEProtocolError('delta content must be a string')
                if content:
                    chunks.append(content)
                    yield encode_sse('delta', {
                        'contract_version': CONTRACT_VERSION,
                        'request_id': state['request_id'],
                        'content': content,
                    })
                continue

            validated = validate_terminal_payload(data)
            if event_name == 'error':
                if validated['status'] != 'failed':
                    raise SSEProtocolError('error event must have failed status')
                finalized = await _finalize(state, validated)
                terminal = True
                await _save_terminal(store, finalized.state)
                yield encode_sse('error', _browser_error(state, validated['error']))
                return

            if validated['status'] != 'completed':
                raise SSEProtocolError('done event must have completed status')
            output = validated['output']
            if not output.get('response'):
                response = ''.join(chunks)
                if not response:
                    raise SSEProtocolError('done output has no response')
                output = {**output, 'response': response}
                validated = {**validated, 'output': output}

            finalized = await _finalize(state, validated)
            terminal = True
            await _save_terminal(store, finalized.state)
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
        logger.info('Django v2 SSE 클라이언트 연결 종료', extra={'request_id': state['request_id']})
        if not terminal:
            failed = _failed_terminal(state, {
                'code': 'UPSTREAM_UNAVAILABLE',
                'message': 'AI 스트림 연결이 종료되었습니다.',
                'retryable': True,
            })
            finalized = await _finalize(state, failed)
            terminal = True
            await _save_terminal(store, finalized.state)
        raise
    except Exception:
        logger.exception('Django v2 SSE 프록시 실패', extra={'request_id': state['request_id']})
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
