"""Backend-owned v2 SSE request, terminal receipt, and decision lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from typing import Any
import uuid

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder

from .ai_services import (
    build_customer_info,
    build_thread_id,
    create_assistant_message_from_terminal,
    generate_request_id,
)
from .ai_tokenization import ReferenceTokenVault, TOKEN_VAULT_TTL_SECONDS
from .field_registry import get_step_field_registry
from .chart_payload import build_chart_payload


CONTRACT_VERSION = '2.0'
AI_EXECUTION_DEADLINE_SECONDS = 60
ACTIVE_STATE_TTL_SECONDS = 600
TERMINAL_STATE_TTL_SECONDS = 86400
AI_FAILURE_CODES = {
    'LLM_TIMEOUT',
    'UPSTREAM_UNAVAILABLE',
    'OUTPUT_SCHEMA_INVALID',
    'POLICY_BLOCKED',
    'INTERNAL_ERROR',
}


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


def validate_browser_message(payload):
    if not isinstance(payload, dict):
        raise LifecycleError(400, 'REQUEST_INVALID', '요청 본문은 객체여야 합니다.')
    if payload.get('contract_version') != CONTRACT_VERSION:
        raise LifecycleError(
            422,
            'CONTRACT_VERSION_UNSUPPORTED',
            '지원하지 않는 계약 버전입니다.',
        )
    allowed_fields = {
        'contract_version', 'content', 'step_id', 'form_data',
        'request_mode',
    }
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
    request_mode = payload.get('request_mode', 'chat')
    if request_mode not in ('chat', 'error_review', 'presentation_entry'):
        raise LifecycleError(400, 'REQUEST_INVALID', 'request_mode이 올바르지 않습니다.')
    normalized = {
        'contract_version': CONTRACT_VERSION,
        'content': content,
        'step_id': step_id,
        'form_data': form_data,
    }
    if 'request_mode' in payload:
        normalized['request_mode'] = request_mode
    return normalized


def require_idempotency_key(request):
    key = request.headers.get('Idempotency-Key', '')
    if not key or len(key) > 128:
        raise LifecycleError(400, 'REQUEST_INVALID', 'Idempotency-Key는 필수입니다.')
    return key


def validate_terminal_payload(payload):
    if not isinstance(payload, dict):
        raise LifecycleError(422, 'REQUEST_SCHEMA_INVALID', 'terminal 본문은 객체여야 합니다.')
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
        _validate_error_review_output(output)
        if 'ui_action' in output:
            raise LifecycleError(
                422,
                'REQUEST_SCHEMA_INVALID',
                'ui_action output은 더 이상 지원하지 않습니다.',
            )
    else:
        error = payload.get('error')
        if not isinstance(error, dict):
            raise LifecycleError(422, 'REQUEST_SCHEMA_INVALID', 'failed error가 올바르지 않습니다.')
        if error.get('code') not in AI_FAILURE_CODES:
            raise LifecycleError(422, 'REQUEST_SCHEMA_INVALID', 'failed error code가 올바르지 않습니다.')
        if not isinstance(error.get('message'), str) or not isinstance(error.get('retryable'), bool):
            raise LifecycleError(422, 'REQUEST_SCHEMA_INVALID', 'failed error가 올바르지 않습니다.')
    return payload


def _validate_error_review_output(output):
    review = output.get('review')
    if review is None:
        return
    if not isinstance(review, dict) or set(review) != {'type', 'manual', 'items'}:
        raise LifecycleError(422, 'REQUEST_SCHEMA_INVALID', 'review output이 올바르지 않습니다.')
    if review.get('type') != 'error_review':
        raise LifecycleError(422, 'REQUEST_SCHEMA_INVALID', 'review output이 올바르지 않습니다.')
    manual = review.get('manual')
    if (
        not isinstance(manual, dict)
        or set(manual) != {'document_id', 'version'}
        or not all(isinstance(manual.get(key), str) and manual.get(key) for key in manual)
    ):
        raise LifecycleError(422, 'REQUEST_SCHEMA_INVALID', 'review manual이 올바르지 않습니다.')
    items = review.get('items')
    if not isinstance(items, list):
        raise LifecycleError(422, 'REQUEST_SCHEMA_INVALID', 'review items가 올바르지 않습니다.')
    allowed = {
        'section', 'path', 'item_id', 'client_item_id', 'parent_item_id',
        'parent_client_item_id', 'current_value', 'reason', 'suggested_value',
    }
    for item in items:
        if not isinstance(item, dict) or set(item) - allowed:
            raise LifecycleError(422, 'REQUEST_SCHEMA_INVALID', 'review item이 올바르지 않습니다.')
        if 'current_value' not in item:
            raise LifecycleError(422, 'REQUEST_SCHEMA_INVALID', 'review item에 현재 값이 필요합니다.')
        if not all(isinstance(item.get(key), str) and item.get(key) for key in ('section', 'path', 'reason')):
            raise LifecycleError(422, 'REQUEST_SCHEMA_INVALID', 'review item이 올바르지 않습니다.')


def _validate_error_review_fields(output, step_id):
    review = output.get('review')
    if not isinstance(review, dict):
        return
    registry = {
        (field.get('section'), field.get('path')): field
        for field in get_step_field_registry(step_id)
        if isinstance(field, dict)
    }
    for item in review.get('items', []):
        spec = registry.get((item.get('section'), item.get('path')))
        if spec is None:
            raise LifecycleError(422, 'REQUEST_SCHEMA_INVALID', 'review field가 현재 단계와 일치하지 않습니다.')
        if spec.get('cardinality') in ('list', 'nested_list') and not (
            item.get('item_id') is not None or item.get('client_item_id')
        ):
            raise LifecycleError(422, 'REQUEST_SCHEMA_INVALID', 'review 반복 항목 식별자가 필요합니다.')
        if (
            spec.get('cardinality') == 'nested_list'
            and str(item.get('path', '')).startswith('items[].')
            and not (
                item.get('parent_item_id') is not None
                or item.get('parent_client_item_id')
            )
        ):
            raise LifecycleError(422, 'REQUEST_SCHEMA_INVALID', 'review 부모 항목 식별자가 필요합니다.')


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

    def token_vault_key(self, request_id):
        return f'{self._namespace}ai:v2:token-vault:{request_id}'

    def get_request(self, request_id):
        raw = self.client.get(self.request_key(request_id))
        return json.loads(raw) if raw else None

    def save_active(self, state):
        self._save_request(state, ACTIVE_STATE_TTL_SECONDS)

    def save_terminal(self, state):
        self._save_request(state, TERMINAL_STATE_TTL_SECONDS)

    def save_token_vault(self, vault: ReferenceTokenVault):
        self.client.setex(
            self.token_vault_key(vault.request_id),
            TOKEN_VAULT_TTL_SECONDS,
            json.dumps(vault.to_record(), ensure_ascii=False, cls=DjangoJSONEncoder),
        )

    def get_token_vault(self, request_id):
        raw = self.client.get(self.token_vault_key(request_id))
        if not raw:
            return None
        return ReferenceTokenVault.from_record(json.loads(raw))

    def delete_token_vault(self, request_id):
        self.client.delete(self.token_vault_key(request_id))

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


def build_v2_ai_payload(
    *, request_id, session, user, message, step_id, form_data,
    request_mode='chat', now=None,
):
    current = now or utc_now()
    chart_result = build_chart_payload(
        session=session,
        user=user,
        step_id=step_id,
    )
    payload = {
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
        'chart_payload': chart_result.payload,
    }
    if request_mode != 'chat':
        payload['request_mode'] = request_mode
    return payload


def make_processing_state(
    *, request_id, session, user, user_message_id, thread_id, step_id,
    payload_digest, now=None,
):
    return {
        'contract_version': CONTRACT_VERSION,
        'request_id': request_id,
        'status': 'processing',
        'session_id': session.id,
        'user_id': user.id,
        'user_message_id': user_message_id,
        'thread_id': thread_id,
        'step_id': step_id,
        'payload_digest': payload_digest,
    }


def bind_terminal_result(state, payload):
    if (
        state.get('request_id') != payload['request_id']
        or state.get('session_id') != payload['session_id']
        or state.get('thread_id') != payload['thread_id']
    ):
        raise LifecycleError(
            409,
            'TERMINAL_BINDING_MISMATCH',
            'terminal 식별자 결합이 원래 요청과 다릅니다.',
        )
    digest = canonical_digest(payload)
    current_status = state.get('status')
    if current_status == 'expired':
        raise LifecycleError(
            409,
            'TERMINAL_CONFLICT',
            '만료된 AI 작업에는 terminal 결과를 저장할 수 없습니다.',
        )
    if current_status in ('completed', 'failed'):
        if state.get('callback_digest') == digest:
            return state, True
        raise LifecycleError(
            409,
            'TERMINAL_CONFLICT',
            '이미 종결된 AI 작업과 terminal 결과가 충돌합니다.',
        )
    if current_status != 'processing':
        raise LifecycleError(
            409,
            'TERMINAL_CONFLICT',
            '종결할 수 없는 AI 작업 상태입니다.',
        )
    return {**state, 'callback_digest': digest}, False


def create_v2_assistant_message(session, payload, step_id):
    output = payload.get('output') if isinstance(payload.get('output'), dict) else {}
    _validate_error_review_fields(output, step_id)
    message = create_assistant_message_from_terminal(session, payload, step_id=step_id)
    extracted = message.extracted_data or {}
    extracted_changed = False
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
        extracted_changed = True
    if isinstance(output.get('review'), dict):
        extracted['review'] = output['review']
        extracted_changed = True
    if extracted_changed:
        message.extracted_data = extracted
        message.save(update_fields=['extracted_data'])
    return message


def new_request_id():
    return generate_request_id()
