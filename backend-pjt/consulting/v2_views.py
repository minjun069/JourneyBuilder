"""Thin DRF views for the frozen asynchronous AI v2 contract."""

from __future__ import annotations

from django.db import transaction
from django.http import StreamingHttpResponse
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed, NotAuthenticated
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from .ai_lifecycle import (
    LifecycleError,
    RedisLifecycleStore,
    bind_terminal_callback,
    build_v2_ai_payload,
    canonical_digest,
    create_v2_assistant_message,
    decide_change,
    dispatch_v2_ai_request,
    expire_if_due,
    find_action_message,
    make_processing_state,
    new_request_id,
    require_idempotency_key,
    validate_browser_message,
    validate_terminal_callback,
    v2_success,
    verify_service_bearer,
)
from .models import ConsultingSession, Message
from .sse import EventStreamRenderer
from .v2_sse import proxy_v2_ai_stream, replay_v2_stream


def error_response(error):
    return Response(error.response_body(), status=error.status_code)


def get_owned_session(session_id, user):
    return ConsultingSession.objects.filter(id=session_id, user=user).first()


def serialize_v2_message(message):
    return {
        'id': message.id,
        'role': message.role,
        'content': message.content,
        'extracted_data': message.extracted_data,
    }


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


class V2MessageCreateView(V2APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, session_id):
        session = get_owned_session(session_id, request.user)
        if session is None:
            return error_response(
                LifecycleError(404, 'SESSION_NOT_FOUND', '상담 세션을 찾을 수 없습니다.')
            )
        if session.status in (
            ConsultingSession.Status.COMPLETED,
            ConsultingSession.Status.ABANDONED,
        ):
            return error_response(
                LifecycleError(409, 'SESSION_CLOSED', '종료된 상담입니다.')
            )
        try:
            body = validate_browser_message(request.data)
            key = require_idempotency_key(request)
            store = RedisLifecycleStore()
            digest = canonical_digest(body)
            replay = store.get_idempotency(request.user.id, session.id, key)
            if replay is not None:
                if replay.get('payload_digest') != digest:
                    raise LifecycleError(
                        409,
                        'IDEMPOTENCY_CONFLICT',
                        '같은 idempotency key의 요청 내용이 다릅니다.',
                    )
                return Response(replay['body'], status=replay['status_code'])

            user_message = Message.objects.create(
                session=session,
                role=Message.Role.USER,
                content=body['content'],
            )
            request_id = new_request_id()
            ai_payload = build_v2_ai_payload(
                request_id=request_id,
                session=session,
                user=request.user,
                message=body['content'],
                step_id=body['step_id'],
                form_data=body['form_data'],
                request_mode=body.get('request_mode', 'chat'),
                review_trigger=body.get('review_trigger'),
                elicit_action=body.get('elicit_action'),
            )
            state = make_processing_state(
                request_id=request_id,
                session=session,
                user=request.user,
                user_message_id=user_message.id,
                thread_id=ai_payload['thread_id'],
                step_id=body['step_id'],
            )
            store.save_active(state)
            try:
                dispatch_v2_ai_request(ai_payload)
            except Exception:
                state = {
                    **state,
                    'status': 'failed',
                    'error': {
                        'code': 'UPSTREAM_UNAVAILABLE',
                        'message': 'AI 작업을 접수하지 못했습니다.',
                        'retryable': True,
                    },
                }
                store.save_terminal(state)
                error = LifecycleError(
                    502,
                    'AI_DISPATCH_FAILED',
                    'AI 작업을 접수하지 못했습니다.',
                    True,
                    {
                        'user_message_id': user_message.id,
                        'request_id': request_id,
                    },
                )
                response_body = error.response_body()
                response_status = error.status_code
            else:
                response_body = v2_success({
                    'user_message_id': user_message.id,
                    'request_id': request_id,
                    'status': 'processing',
                    'processing_deadline_at': state['processing_deadline_at'],
                })
                response_status = status.HTTP_202_ACCEPTED
            store.save_idempotency(
                request.user.id,
                session.id,
                key,
                {
                    'payload_digest': digest,
                    'body': response_body,
                    'status_code': response_status,
                },
            )
            return Response(response_body, status=response_status)
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
        if session.status in (
            ConsultingSession.Status.COMPLETED,
            ConsultingSession.Status.ABANDONED,
        ):
            return error_response(
                LifecycleError(409, 'SESSION_CLOSED', '종료된 상담입니다.')
            )

        try:
            body = validate_browser_message(request.data)
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
                state = store.get_request(replay.get('request_id'))
                if state is None:
                    raise LifecycleError(
                        503,
                        'AI_STATE_UNAVAILABLE',
                        'AI 작업 상태를 사용할 수 없습니다.',
                        True,
                    )
                if state['status'] == 'processing':
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

            user_message = Message.objects.create(
                session=session,
                role=Message.Role.USER,
                content=body['content'],
            )
            request_id = new_request_id()
            ai_payload = build_v2_ai_payload(
                request_id=request_id,
                session=session,
                user=request.user,
                message=body['content'],
                step_id=body['step_id'],
                form_data=body['form_data'],
                request_mode=body.get('request_mode', 'chat'),
                review_trigger=body.get('review_trigger'),
                elicit_action=body.get('elicit_action'),
            )
            state = make_processing_state(
                request_id=request_id,
                session=session,
                user=request.user,
                user_message_id=user_message.id,
                thread_id=ai_payload['thread_id'],
                step_id=body['step_id'],
            )
            store.save_active(state)
            store.save_idempotency(
                request.user.id,
                session.id,
                key,
                {
                    'transport': 'sse',
                    'payload_digest': digest,
                    'request_id': request_id,
                },
            )
            return self._stream_response(
                proxy_v2_ai_stream(session, ai_payload, state, store)
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


class V2PollingView(V2APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, session_id, request_id):
        session = get_owned_session(session_id, request.user)
        if session is None:
            return error_response(
                LifecycleError(404, 'SESSION_NOT_FOUND', '상담 세션을 찾을 수 없습니다.')
            )
        try:
            store = RedisLifecycleStore()
            state = store.get_request(request_id)
            if (
                state is None
                or state.get('session_id') != session.id
                or state.get('user_id') != request.user.id
            ):
                raise LifecycleError(
                    404,
                    'AI_REQUEST_NOT_FOUND',
                    'AI 작업을 찾을 수 없습니다.',
                )
            state, expired = expire_if_due(state)
            if expired:
                store.save_terminal(state)
            data = {'request_id': request_id, 'status': state['status']}
            if state['status'] == 'processing':
                data['processing_deadline_at'] = state['processing_deadline_at']
            elif state['status'] == 'completed':
                message = Message.objects.filter(
                    id=state.get('assistant_message_id'),
                    session=session,
                ).first()
                if message is not None:
                    data['message'] = serialize_v2_message(message)
            elif state['status'] in ('failed', 'expired'):
                data['error'] = state['error']
            return Response(v2_success(data))
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


class V2CallbackView(V2APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        try:
            verify_service_bearer(request)
            payload = validate_terminal_callback(request.data)
            store = RedisLifecycleStore()
            state = store.get_request(payload['request_id'])
            if state is None:
                raise LifecycleError(
                    404,
                    'CALLBACK_REQUEST_NOT_FOUND',
                    'callback 대상 AI 작업을 찾을 수 없습니다.',
                )
            state, replay = bind_terminal_callback(state, payload)
            if replay:
                return Response(v2_success({
                    'request_id': state['request_id'],
                    'status': state['status'],
                    **(
                        {'message_id': state['assistant_message_id']}
                        if state.get('assistant_message_id') else {}
                    ),
                }))
            if payload['status'] == 'completed':
                session = ConsultingSession.objects.filter(
                    id=payload['session_id']
                ).first()
                if session is None:
                    raise LifecycleError(
                        404,
                        'CALLBACK_REQUEST_NOT_FOUND',
                        'callback 대상 AI 작업을 찾을 수 없습니다.',
                    )
                message = create_v2_assistant_message(
                    session,
                    payload,
                    state['step_id'],
                )
                state = {
                    **state,
                    'status': 'completed',
                    'assistant_message_id': message.id,
                }
            else:
                state = {
                    **state,
                    'status': 'failed',
                    'error': payload['error'],
                }
            store.save_terminal(state)
            return Response(v2_success({
                'request_id': state['request_id'],
                'status': state['status'],
                **(
                    {'message_id': state['assistant_message_id']}
                    if state.get('assistant_message_id') else {}
                ),
            }))
        except LifecycleError as exc:
            return error_response(exc)
        except ValueError:
            return error_response(
                LifecycleError(422, 'REQUEST_SCHEMA_INVALID', 'callback output이 올바르지 않습니다.')
            )
        except Exception:
            return error_response(
                LifecycleError(
                    503,
                    'AI_STATE_UNAVAILABLE',
                    'AI 작업 상태 저장소를 사용할 수 없습니다.',
                    True,
                )
            )


class V2ChangeDecisionView(V2APIView):
    permission_classes = [IsAuthenticated]
    decision = None

    def post(self, request, session_id, action_id, change_id):
        session = get_owned_session(session_id, request.user)
        if session is None:
            return error_response(
                LifecycleError(404, 'SESSION_NOT_FOUND', '상담 세션을 찾을 수 없습니다.')
            )
        try:
            key = require_idempotency_key(request)
            store = RedisLifecycleStore()
            decision_request = {
                'action_id': action_id,
                'change_id': change_id,
                'decision': self.decision,
            }
            digest = canonical_digest(decision_request)
            replay = store.get_idempotency(request.user.id, session.id, key)
            if replay is not None:
                if replay.get('payload_digest') != digest:
                    raise LifecycleError(
                        409,
                        'IDEMPOTENCY_CONFLICT',
                        '같은 idempotency key의 요청 내용이 다릅니다.',
                    )
                return Response(replay['body'], status=replay['status_code'])
            with transaction.atomic():
                message = find_action_message(session, action_id, for_update=True)
                if message is None:
                    raise LifecycleError(
                        404,
                        'ACTION_NOT_FOUND',
                        'AI 변경 제안을 찾을 수 없습니다.',
                    )
                data = decide_change(
                    message,
                    change_id=change_id,
                    decision=self.decision,
                )
            response_body = v2_success(data)
            store.save_idempotency(
                request.user.id,
                session.id,
                key,
                {
                    'payload_digest': digest,
                    'body': response_body,
                    'status_code': status.HTTP_200_OK,
                },
            )
            return Response(response_body)
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


class V2ChangeApproveView(V2ChangeDecisionView):
    decision = 'approved'


class V2ChangeRejectView(V2ChangeDecisionView):
    decision = 'rejected'
