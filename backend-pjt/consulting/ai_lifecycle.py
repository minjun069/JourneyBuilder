"""Backend-owned v2 AI request, callback, polling, and decision lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import secrets
from typing import Any
import uuid

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder

from .ai_services import (
    build_customer_info,
    build_thread_id,
    create_assistant_message_from_callback,
    generate_request_id,
)
from .field_registry import get_step_field_registry
from .tool_context import build_tool_context


CONTRACT_VERSION = '2.0'
AI_EXECUTION_DEADLINE_SECONDS = 60
PROCESSING_DEADLINE_SECONDS = 90
ACTIVE_STATE_TTL_SECONDS = 600
TERMINAL_STATE_TTL_SECONDS = 86400
RETRYABLE_HTTP_STATUSES = {408, 429, 502, 503, 504}
AI_FAILURE_CODES = {
    'LLM_TIMEOUT',
    'UPSTREAM_UNAVAILABLE',
    'OUTPUT_SCHEMA_INVALID',
    'POLICY_BLOCKED',
    'INTERNAL_ERROR',
}


class RetryableAIDispatchError(RuntimeError):
    pass


def utc_now():
    return datetime.now(timezone.utc)


def isoformat_utc(value):
    return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def canonical_digest(payload):
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
        cls=DjangoJSONEncoder,
    ).encode('utf-8')
    return sha256(encoded).hexdigest()


@dataclass(frozen=True)
class LifecycleError(Exception):
    status_code: int
    code: str
    message: str
    retryable: bool = False
    data: dict[str, Any] | None = None

    def response_body(self):
        body = {
            'success': False,
            'error': {
                'code': self.code,
                'message': self.message,
                'retryable': self.retryable,
            },
        }
        if self.data is not None:
            body['contract_version'] = CONTRACT_VERSION
            body['data'] = self.data
        return body


def v2_success(data):
    return {
        'contract_version': CONTRACT_VERSION,
        'success': True,
        'data': data,
    }


def validate_browser_message(payload):
    if not isinstance(payload, dict):
        raise LifecycleError(400, 'REQUEST_INVALID', '요청 본문은 객체여야 합니다.')
    if payload.get('contract_version') != CONTRACT_VERSION:
        raise LifecycleError(
            422,
            'CONTRACT_VERSION_UNSUPPORTED',
            '지원하지 않는 계약 버전입니다.',
        )
    allowed_fields = {'contract_version', 'content', 'step_id', 'form_data'}
    if set(payload) - allowed_fields:
        raise LifecycleError(400, 'REQUEST_INVALID', '지원하지 않는 요청 필드가 있습니다.')
    if 'content' not in payload:
        raise LifecycleError(400, 'REQUEST_INVALID', 'content는 필수입니다.')
    content = payload['content']
    if not isinstance(content, str):
        raise LifecycleError(400, 'REQUEST_INVALID', 'content는 문자열이어야 합니다.')
    if not (1 <= len(content) <= 2000):
        raise LifecycleError(400, 'REQUEST_INVALID', 'content는 1~2000자여야 합니다.')
    if 'step_id' not in payload:
        raise LifecycleError(400, 'REQUEST_INVALID', 'step_id는 필수입니다.')
    step_id = payload['step_id']
    if not isinstance(step_id, str) or not step_id:
        raise LifecycleError(400, 'REQUEST_INVALID', 'step_id는 비어 있지 않은 문자열이어야 합니다.')
    form_data = payload.get('form_data', {})
    if not isinstance(form_data, dict):
        raise LifecycleError(400, 'REQUEST_INVALID', 'form_data는 객체여야 합니다.')
    return {
        'contract_version': CONTRACT_VERSION,
        'content': content,
        'step_id': step_id,
        'form_data': form_data,
    }


def require_idempotency_key(request):
    key = request.headers.get('Idempotency-Key', '')
    if not key or len(key) > 128:
        raise LifecycleError(400, 'REQUEST_INVALID', 'Idempotency-Key는 필수입니다.')
    return key


def verify_service_bearer(request):
    value = request.headers.get('Authorization', '')
    scheme, separator, supplied = value.partition(' ')
    expected = settings.AI_SERVICE_BEARER_TOKEN
    if not (
        separator == ' '
        and scheme == 'Bearer'
        and expected
        and secrets.compare_digest(supplied, expected)
    ):
        raise LifecycleError(401, 'SERVICE_UNAUTHORIZED', '서비스 인증에 실패했습니다.')


def validate_terminal_callback(payload):
    if not isinstance(payload, dict):
        raise LifecycleError(422, 'REQUEST_SCHEMA_INVALID', 'callback 본문은 객체여야 합니다.')
    if payload.get('contract_version') != CONTRACT_VERSION:
        raise LifecycleError(
            422,
            'CONTRACT_VERSION_UNSUPPORTED',
            '지원하지 않는 계약 버전입니다.',
        )
    required = ('request_id', 'session_id', 'thread_id', 'status', 'completed_at')
    for field in required:
        if field not in payload:
            raise LifecycleError(422, 'REQUEST_SCHEMA_INVALID', f'{field}는 필수입니다.')
    if not isinstance(payload['request_id'], str) or not payload['request_id']:
        raise LifecycleError(422, 'REQUEST_SCHEMA_INVALID', 'request_id가 올바르지 않습니다.')
    if isinstance(payload['session_id'], bool) or not isinstance(payload['session_id'], int):
        raise LifecycleError(422, 'REQUEST_SCHEMA_INVALID', 'session_id는 정수여야 합니다.')
    if not isinstance(payload['thread_id'], str) or not payload['thread_id']:
        raise LifecycleError(422, 'REQUEST_SCHEMA_INVALID', 'thread_id가 올바르지 않습니다.')
    status = payload['status']
    if status not in ('completed', 'failed'):
        raise LifecycleError(422, 'REQUEST_SCHEMA_INVALID', 'status가 올바르지 않습니다.')
    if not isinstance(payload['completed_at'], str):
        raise LifecycleError(422, 'REQUEST_SCHEMA_INVALID', 'completed_at이 올바르지 않습니다.')
    try:
        completed_at = datetime.fromisoformat(
            payload['completed_at'].replace('Z', '+00:00')
        )
    except ValueError as exc:
        raise LifecycleError(
            422,
            'REQUEST_SCHEMA_INVALID',
            'completed_at이 올바르지 않습니다.',
        ) from exc
    if completed_at.tzinfo is None:
        raise LifecycleError(422, 'REQUEST_SCHEMA_INVALID', 'completed_at이 올바르지 않습니다.')
    if status == 'completed':
        output = payload.get('output')
        if not isinstance(output, dict) or not isinstance(output.get('response'), str):
            raise LifecycleError(422, 'REQUEST_SCHEMA_INVALID', 'completed output이 올바르지 않습니다.')
    else:
        error = payload.get('error')
        if not isinstance(error, dict):
            raise LifecycleError(422, 'REQUEST_SCHEMA_INVALID', 'failed error가 올바르지 않습니다.')
        if error.get('code') not in AI_FAILURE_CODES:
            raise LifecycleError(422, 'REQUEST_SCHEMA_INVALID', 'failed error code가 올바르지 않습니다.')
        if not isinstance(error.get('message'), str) or not isinstance(error.get('retryable'), bool):
            raise LifecycleError(422, 'REQUEST_SCHEMA_INVALID', 'failed error가 올바르지 않습니다.')
    return payload


class RedisLifecycleStore:
    def __init__(self, client=None, namespace=None):
        self._client = client
        self._namespace = (
            settings.AI_REDIS_NAMESPACE
            if namespace is None
            else namespace
        )

    @property
    def client(self):
        if self._client is None:
            import redis
            self._client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
        return self._client

    def request_key(self, request_id):
        return f'{self._namespace}ai:v2:request:{request_id}'

    def idempotency_key(self, user_id, session_id, key):
        return f'{self._namespace}ai:v2:idempotency:{user_id}:{session_id}:{key}'

    def get_request(self, request_id):
        raw = self.client.get(self.request_key(request_id))
        return json.loads(raw) if raw else None

    def save_active(self, state):
        self._save_request(state, ACTIVE_STATE_TTL_SECONDS)

    def save_terminal(self, state):
        self._save_request(state, TERMINAL_STATE_TTL_SECONDS)

    def _save_request(self, state, ttl):
        self.client.setex(
            self.request_key(state['request_id']),
            ttl,
            json.dumps(state, ensure_ascii=False, cls=DjangoJSONEncoder),
        )

    def get_idempotency(self, user_id, session_id, key):
        raw = self.client.get(self.idempotency_key(user_id, session_id, key))
        return json.loads(raw) if raw else None

    def save_idempotency(self, user_id, session_id, key, record):
        self.client.setex(
            self.idempotency_key(user_id, session_id, key),
            TERMINAL_STATE_TTL_SECONDS,
            json.dumps(record, ensure_ascii=False, cls=DjangoJSONEncoder),
        )


def build_v2_ai_payload(*, request_id, session, user, message, step_id, form_data, now=None):
    current = now or utc_now()
    return {
        'contract_version': CONTRACT_VERSION,
        'request_id': request_id,
        'session_id': session.id,
        'thread_id': build_thread_id(user, session),
        'step_id': step_id,
        'message': message,
        'deadline_at': isoformat_utc(
            current + timedelta(seconds=AI_EXECUTION_DEADLINE_SECONDS)
        ),
        'customer_info': build_customer_info(session, form_data),
        'field_registry': get_step_field_registry(step_id),
        'tool_context': build_tool_context(
            session=session,
            user=user,
            step_id=step_id,
            generated_at=current,
        ),
        'chart_payload': None,
    }


def ai_v2_url():
    base = settings.AI_SERVICE_URL.rstrip('/')
    if base.endswith('/chat'):
        base = base[:-5]
    return f'{base}/v2/chat'


def dispatch_v2_ai_request(payload, *, post=None):
    if post is None:
        import httpx
        post = httpx.post
        transport_errors = (httpx.TransportError,)
    else:
        transport_errors = (TimeoutError, ConnectionError)
    last_error = None
    for attempt in range(2):
        try:
            response = post(
                ai_v2_url(),
                json=payload,
                headers={
                    'Authorization': f'Bearer {settings.AI_SERVICE_BEARER_TOKEN}',
                },
                timeout=5,
            )
            if response.status_code in RETRYABLE_HTTP_STATUSES:
                raise RetryableAIDispatchError(
                    f'retryable AI response: {response.status_code}'
                )
            response.raise_for_status()
            body = response.json()
            if (
                body.get('contract_version') != CONTRACT_VERSION
                or body.get('request_id') != payload['request_id']
                or body.get('status') != 'accepted'
            ):
                raise RuntimeError('AI accepted response contract mismatch')
            return body
        except transport_errors + (RetryableAIDispatchError,) as exc:
            last_error = exc
    raise last_error or RuntimeError('AI dispatch failed')


def make_processing_state(*, request_id, session, user, user_message_id, thread_id, step_id, now=None):
    current = now or utc_now()
    return {
        'contract_version': CONTRACT_VERSION,
        'request_id': request_id,
        'status': 'processing',
        'session_id': session.id,
        'user_id': user.id,
        'user_message_id': user_message_id,
        'thread_id': thread_id,
        'step_id': step_id,
        'processing_deadline_at': isoformat_utc(
            current + timedelta(seconds=PROCESSING_DEADLINE_SECONDS)
        ),
    }


def expire_if_due(state, *, now=None):
    if state.get('status') != 'processing':
        return state, False
    deadline = datetime.fromisoformat(state['processing_deadline_at'].replace('Z', '+00:00'))
    if (now or utc_now()) < deadline:
        return state, False
    expired = {
        **state,
        'status': 'expired',
        'error': {
            'code': 'AI_REQUEST_EXPIRED',
            'message': 'AI 작업 시간이 만료되었습니다. 다시 요청해주세요.',
            'retryable': True,
        },
    }
    return expired, True


def bind_terminal_callback(state, payload):
    if (
        state.get('session_id') != payload['session_id']
        or state.get('thread_id') != payload['thread_id']
    ):
        raise LifecycleError(
            409,
            'CALLBACK_BINDING_MISMATCH',
            'callback 식별자 결합이 원래 요청과 다릅니다.',
        )
    digest = canonical_digest(payload)
    current_status = state.get('status')
    if current_status == 'expired':
        raise LifecycleError(
            409,
            'CALLBACK_CONFLICT',
            '만료된 AI 작업에는 callback을 저장할 수 없습니다.',
        )
    if current_status in ('completed', 'failed'):
        if state.get('callback_digest') == digest:
            return state, True
        raise LifecycleError(
            409,
            'CALLBACK_CONFLICT',
            '이미 종결된 AI 작업과 callback이 충돌합니다.',
        )
    return {**state, 'callback_digest': digest}, False


def create_v2_assistant_message(session, payload, step_id):
    message = create_assistant_message_from_callback(session, payload, step_id=step_id)
    extracted = message.extracted_data or {}
    action = extracted.get('action')
    if isinstance(action, dict):
        action_id = f'act_{uuid.uuid4().hex}'
        changes = [
            {
                **change,
                'change_id': f'chg_{uuid.uuid4().hex}',
                'decision_status': 'pending',
            }
            for change in action.get('changes', [])
        ]
        extracted = {'action': {
            **action,
            'action_id': action_id,
            'status': 'pending',
            'changes': changes,
        }}
        message.extracted_data = extracted
        message.save(update_fields=['extracted_data'])
    return message


def find_action_message(session, action_id, *, for_update=False):
    queryset = session.messages.filter(role='assistant').order_by('-id')
    if for_update:
        queryset = queryset.select_for_update()
    for message in queryset:
        action = (message.extracted_data or {}).get('action')
        if isinstance(action, dict) and action.get('action_id') == action_id:
            return message
    return None


def decide_change(message, *, change_id, decision):
    extracted = message.extracted_data or {}
    action = extracted.get('action')
    if not isinstance(action, dict):
        raise LifecycleError(404, 'ACTION_NOT_FOUND', 'AI 변경 제안을 찾을 수 없습니다.')
    changes = action.get('changes') or []
    target = next((item for item in changes if item.get('change_id') == change_id), None)
    if target is None:
        raise LifecycleError(404, 'ACTION_NOT_FOUND', 'AI 변경 제안을 찾을 수 없습니다.')
    current = target.get('decision_status', 'pending')
    if current != 'pending' and current != decision:
        raise LifecycleError(
            409,
            'ACTION_DECISION_CONFLICT',
            '이미 반대 결정으로 처리된 변경입니다.',
        )
    target['decision_status'] = decision
    pending = sum(item.get('decision_status') == 'pending' for item in changes)
    action['status'] = 'resolved' if pending == 0 else (
        'pending' if pending == len(changes) else 'partially_resolved'
    )
    extracted['action'] = action
    message.extracted_data = extracted
    message.save(update_fields=['extracted_data'])
    response = {
        'action_id': action['action_id'],
        'change_id': change_id,
        'decision_status': decision,
        'action_status': action['status'],
    }
    if decision == 'approved':
        response['change'] = {
            key: value
            for key, value in target.items()
            if key not in {'change_id', 'decision_status', 'label', 'reason'}
        }
    return response


def new_request_id():
    return generate_request_id()
