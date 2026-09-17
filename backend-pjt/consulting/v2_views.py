"""Thin DRF views for the SSE-only AI v2 contract."""

from __future__ import annotations

from django.db import transaction
from django.http import StreamingHttpResponse
from rest_framework.exceptions import AuthenticationFailed, NotAuthenticated
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from .ai_lifecycle import (
    LifecycleError,
    RedisLifecycleStore,
    build_v2_ai_payload,
    canonical_digest,
    make_processing_state,
    new_request_id,
    require_idempotency_key,
    validate_browser_message,
)
from .ai_security import recover_terminal_state
from .ai_tokenization import ReferenceTokenVault
from .chart_payload import PRESENTATION_CHART_STEP_IDS
from .models import AIRequestExecution, ConsultingSession, Message
from .v2_sse import (
    PROGRESS_EVENTS_HEADER,
    PROGRESS_EVENTS_VERSION,
    proxy_v2_ai_stream,
    replay_v2_stream,
)


def error_response(error):
    return Response(error.response_body(), status=error.status_code)


def get_owned_session(session_id, user):
    return ConsultingSession.objects.filter(id=session_id, user=user).first()


def progress_events_requested(request):
    value = request.headers.get(PROGRESS_EVENTS_HEADER)
    if value is None:
        return False
    if value != PROGRESS_EVENTS_VERSION:
        raise LifecycleError(
            400,
            'REQUEST_INVALID',
            '지원하지 않는 진행 이벤트 capability입니다.',
        )
    return True


class EventStreamRenderer(JSONRenderer):
    media_type = 'text/event-stream'
    format = 'event-stream'


class V2APIView(APIView):
    def handle_exception(self, exc):
        if isinstance(exc, (AuthenticationFailed, NotAuthenticated)):
            return error_response(
                LifecycleError(401, 'AUTH_REQUIRED', '사용자 인증이 필요합니다.')
            )
        if isinstance(exc, LifecycleError):
            return error_response(exc)
        return super().handle_exception(exc)


class V2MessageStreamView(V2APIView):
    permission_classes = [IsAuthenticated]
    renderer_classes = [JSONRenderer, EventStreamRenderer]

    @staticmethod
    def _stream_response(iterator):
        response = StreamingHttpResponse(iterator, content_type='text/event-stream')
        response['Cache-Control'] = 'no-cache'
        response['X-Accel-Buffering'] = 'no'
        return response

    def post(self, request, session_id):
        session = get_owned_session(session_id, request.user)
        if session is None:
            return error_response(
                LifecycleError(404, 'SESSION_NOT_FOUND', '상담 세션을 찾을 수 없습니다.')
            )
        if session.status == ConsultingSession.Status.ABANDONED:
            return error_response(
                LifecycleError(409, 'SESSION_CLOSED', '종료된 상담입니다.')
            )

        try:
            body = validate_browser_message(request.data)
            progress_events = progress_events_requested(request)
            if (
                session.status == ConsultingSession.Status.COMPLETED
                and body['step_id'] not in PRESENTATION_CHART_STEP_IDS
            ):
                raise LifecycleError(
                    409,
                    'SESSION_CLOSED',
                    '완료된 상담에서는 진단 결과 질문만 이어갈 수 있습니다.',
                )
            key = require_idempotency_key(request)
            store = RedisLifecycleStore()
            digest = canonical_digest(body)
            replay = store.get_idempotency(request.user.id, session.id, key)
            if replay is not None:
                if replay.get('payload_digest') != digest or replay.get('transport') != 'sse':
                    raise LifecycleError(
                        409,
                        'IDEMPOTENCY_CONFLICT',
                        '같은 idempotency key의 요청 내용 또는 전송 방식이 다릅니다.',
                    )
                cached_state = store.get_request(replay.get('request_id'))
                state = recover_terminal_state(
                    replay.get('request_id'),
                    state=cached_state,
                    expected_session_id=session.id,
                    expected_user_id=request.user.id,
                )
                if state is None:
                    raise LifecycleError(
                        503,
                        'AI_STATE_UNAVAILABLE',
                        'AI 작업 상태를 사용할 수 없습니다.',
                        True,
                    )
                if state is not cached_state:
                    store.save_terminal(state)
                if state['status'] == 'processing':
                    if state.get('retry_after_disconnect') is True:
                        execution = (
                            AIRequestExecution.objects.filter(
                                request_id=state['request_id'],
                                session=session,
                                thread_id=state['thread_id'],
                                payload_digest=state.get('payload_digest'),
                                status=AIRequestExecution.Status.PROCESSING,
                            )
                            .first()
                        )
                        token_vault = store.get_token_vault(state['request_id'])
                        if execution is None or token_vault is None:
                            raise LifecycleError(
                                503,
                                'AI_STATE_UNAVAILABLE',
                                '기존 AI 요청을 안전하게 재개할 수 없습니다.',
                                True,
                            )
                        resumed_state = dict(state)
                        resumed_state.pop('retry_after_disconnect', None)
                        store.save_active(resumed_state)
                        return self._stream_response(
                            proxy_v2_ai_stream(
                                session,
                                execution.request_payload,
                                resumed_state,
                                store,
                                token_vault=token_vault,
                                progress_events=progress_events,
                            )
                        )
                    raise LifecycleError(
                        409,
                        'AI_STREAM_IN_PROGRESS',
                        '같은 AI 스트림 요청이 처리 중입니다.',
                        True,
                    )
                message = None
                if state['status'] == 'completed':
                    message = Message.objects.filter(
                        id=state.get('assistant_message_id'),
                        session=session,
                    ).first()
                return self._stream_response(replay_v2_stream(state, message))

            with transaction.atomic():
                locked_session = (
                    ConsultingSession.objects.select_for_update()
                    .filter(id=session.id, user=request.user)
                    .first()
                )
                if locked_session is None:
                    raise LifecycleError(404, 'SESSION_NOT_FOUND', '상담 세션을 찾을 수 없습니다.')
                if AIRequestExecution.objects.filter(
                    session=locked_session,
                    status=AIRequestExecution.Status.PROCESSING,
                ).exists():
                    raise LifecycleError(
                        409,
                        'AI_STREAM_IN_PROGRESS',
                        '이 상담의 다른 AI 스트림 요청이 처리 중입니다.',
                        True,
                    )

                user_message = Message.objects.create(
                    session=locked_session,
                    role=Message.Role.USER,
                    content=body['content'],
                    extracted_data=(
                        {
                            'system_event': 'presentation_entry',
                            'step_id': body['step_id'],
                        }
                        if body.get('request_mode') == 'presentation_entry'
                        else None
                    ),
                )
                request_id = new_request_id()
                raw_ai_payload = build_v2_ai_payload(
                    request_id=request_id,
                    session=locked_session,
                    user=request.user,
                    message=body['content'],
                    step_id=body['step_id'],
                    form_data=body['form_data'],
                    request_mode=body.get('request_mode', 'chat'),
                    review_trigger=body.get('review_trigger'),
                )
                token_vault = ReferenceTokenVault(
                    user_id=request.user.id,
                    session_id=locked_session.id,
                    request_id=request_id,
                )
                ai_payload = token_vault.tokenize(raw_ai_payload)
                ai_payload.pop('payload_digest', None)
                ai_payload_digest = canonical_digest(ai_payload)
                ai_payload['payload_digest'] = ai_payload_digest
                state = make_processing_state(
                    request_id=request_id,
                    session=locked_session,
                    user=request.user,
                    user_message_id=user_message.id,
                    thread_id=ai_payload['thread_id'],
                    step_id=body['step_id'],
                    payload_digest=ai_payload_digest,
                )
                AIRequestExecution.objects.create(
                    request_id=request_id,
                    session=locked_session,
                    thread_id=ai_payload['thread_id'],
                    step_id=body['step_id'],
                    user_message=user_message,
                    payload_digest=ai_payload_digest,
                    request_payload=ai_payload,
                )
                store.save_token_vault(token_vault)
                store.save_active(state)
                store.save_idempotency(
                    request.user.id,
                    locked_session.id,
                    key,
                    {
                        'transport': 'sse',
                        'payload_digest': digest,
                        'request_id': request_id,
                    },
                )
            return self._stream_response(
                proxy_v2_ai_stream(
                    locked_session,
                    ai_payload,
                    state,
                    store,
                    token_vault=token_vault,
                    progress_events=progress_events,
                )
            )
        except LifecycleError as exc:
            return error_response(exc)
        except Exception:
            return error_response(
                LifecycleError(
                    503,
                    'AI_STATE_UNAVAILABLE',
                    'AI 작업 상태 저장소를 사용할 수 없습니다.',
                    True,
                )
            )
