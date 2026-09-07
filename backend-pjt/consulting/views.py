from django.db import transaction
from django.http import StreamingHttpResponse
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
import logging

from django.conf import settings

from .ai_services import (
    AI_STATUS_COMPLETED,
    AI_STATUS_FAILED,
    AI_STATUS_PROCESSING,
    apply_pending_changes,
    build_ai_request_payload,
    create_assistant_message_from_callback,
    dispatch_ai_request,
    generate_request_id,
    get_ai_request_state,
    get_ai_request_state_by_thread_id,
    mark_message_action,
    save_ai_request_state,
    stream_ai_response,
)
from .models import (
    ConsultingSession, Message,
    BasicInfo, FinancialMindset, FinancialSatisfaction,
    FinancialAsset, PensionAsset, RealEstateAsset,
    Liability, Income, Expense,
    FinancialGoalPeriod, FinancialGoalPeriodItem, FinancialGoalMilestone,
)
from .serializers import (
    ConsultingSessionSerializer,
    ConsultingSessionListSerializer,
    ConsultingSessionCreateSerializer,
    MessageSerializer,
    MessageCreateSerializer,
    BasicInfoSerializer,
    BasicInfoWriteSerializer,
    FinancialMindsetSerializer,
    FinancialSatisfactionSerializer,
    FinancialAssetSerializer,
    PensionAssetSerializer,
    RealEstateAssetSerializer,
    LiabilitySerializer,
    IncomeSerializer,
    ExpenseSerializer,
    FinancialGoalPeriodSerializer,
    FinancialGoalMilestoneSerializer,
)
from .sse import SSEProtocolError, encode_sse, iter_sse_events


logger = logging.getLogger(__name__)

_AI_STREAM_EVENTS = {'start', 'delta', 'done', 'error'}


def _validate_stream_identity(data, ai_payload):
    expected = {
        'request_id': ai_payload['request_id'],
        'session_id': ai_payload['session_id'],
        'thread_id': ai_payload['thread_id'],
    }
    for field, expected_value in expected.items():
        if field in data and data[field] != expected_value:
            raise SSEProtocolError(f'{field} does not match the stream request')


def _stream_error(request_id, code='AI_STREAM_FAILED'):
    return encode_sse('error', {
        'request_id': request_id,
        'code': code,
        'message': 'AI 응답 생성에 실패했습니다.',
    })


def _proxy_ai_stream(session, ai_payload, step_id, user_message_id):
    request_id = ai_payload['request_id']
    yield encode_sse('start', {
        'request_id': request_id,
        'session_id': session.id,
        'user_message_id': user_message_id,
    })

    chunks: list[str] = []
    try:
        for event_name, data in iter_sse_events(stream_ai_response(ai_payload)):
            if event_name not in _AI_STREAM_EVENTS:
                raise SSEProtocolError(f'unsupported SSE event: {event_name}')
            _validate_stream_identity(data, ai_payload)

            if event_name == 'start':
                continue

            if event_name == 'delta':
                content = data.get('content')
                if not isinstance(content, str):
                    raise SSEProtocolError('delta content must be a string')
                if content:
                    chunks.append(content)
                    yield encode_sse('delta', {
                        'request_id': request_id,
                        'content': content,
                    })
                continue

            if event_name == 'error':
                code = data.get('code')
                if not isinstance(code, str) or not code:
                    code = 'AI_STREAM_FAILED'
                yield _stream_error(request_id, code=code)
                return

            output = data.get('output')
            if not isinstance(output, dict):
                raise SSEProtocolError('done output must be an object')
            response_text = output.get('response')
            if not isinstance(response_text, str) or not response_text:
                response_text = ''.join(chunks)
                if not response_text:
                    raise SSEProtocolError('done output has no response')
                output = {**output, 'response': response_text}

            assistant_message = create_assistant_message_from_callback(
                session,
                {
                    'request_id': request_id,
                    'session_id': session.id,
                    'thread_id': ai_payload['thread_id'],
                    'status': AI_STATUS_COMPLETED,
                    'output': output,
                },
                step_id=step_id,
            )
            stored_action = (assistant_message.extracted_data or {}).get('action')
            client_output = {
                **output,
                **({'action': stored_action} if stored_action else {}),
            }
            yield encode_sse('done', {
                'request_id': request_id,
                'session_id': session.id,
                'thread_id': ai_payload['thread_id'],
                'message_id': assistant_message.id,
                'output': client_output,
            })
            return

        yield _stream_error(request_id, code='AI_STREAM_INCOMPLETE')
    except GeneratorExit:
        logger.info('Django SSE 클라이언트 연결 종료', extra={'request_id': request_id})
        raise
    except Exception:
        logger.exception('Django AI stream 프록시 실패', extra={'request_id': request_id})
        yield _stream_error(request_id)


# 공통 유틸
def get_session_or_404(session_id, user):
    try:
        return ConsultingSession.objects.get(id=session_id, user=user)
    except ConsultingSession.DoesNotExist:
        return None


def session_not_found():
    return Response({'success': False, 'error': '세션을 찾을 수 없습니다.'}, status=status.HTTP_404_NOT_FOUND)


# 세션 목록 / 생성
class ConsultationListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        sessions = ConsultingSession.objects.filter(user=request.user)
        serializer = ConsultingSessionListSerializer(sessions, many=True)
        return Response({
            'success': True,
            'data': serializer.data,
            'meta': {'total': sessions.count()}
        })

    def post(self, request):
        serializer = ConsultingSessionCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({'success': False, 'error': serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            session = ConsultingSession.objects.create(
                user=request.user,
                status=ConsultingSession.Status.IN_PROGRESS,
            )

        return Response({
            'success': True,
            'data': ConsultingSessionSerializer(session).data
        }, status=status.HTTP_201_CREATED)


# 세션 상세
class ConsultationDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, session_id):
        session = get_session_or_404(session_id, request.user)
        if not session:
            return session_not_found()
        return Response({'success': True, 'data': ConsultingSessionSerializer(session).data})

    def delete(self, request, session_id):
        session = get_session_or_404(session_id, request.user)
        if not session:
            return session_not_found()

        if session.status in (ConsultingSession.Status.COMPLETED, ConsultingSession.Status.ABANDONED):
            return Response({'success': False, 'error': '이미 종료된 세션입니다.'}, status=status.HTTP_409_CONFLICT)

        session.status = ConsultingSession.Status.ABANDONED
        session.save(update_fields=['status'])
        return Response(status=status.HTTP_204_NO_CONTENT)


# 메시지 목록 / 전송
class MessageListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, session_id):
        session = get_session_or_404(session_id, request.user)
        if not session:
            return session_not_found()

        messages = session.messages.all()
        serializer = MessageSerializer(messages, many=True)
        return Response({'success': True, 'data': serializer.data, 'meta': {'total': messages.count()}})

    # 기존 POST 확장: 사용자 메시지 저장 후 AI 작업 요청 생성
    def post(self, request, session_id):
        session = get_session_or_404(session_id, request.user)
        if not session:
            return session_not_found()

        if session.status in (ConsultingSession.Status.COMPLETED, ConsultingSession.Status.ABANDONED):
            return Response({'success': False, 'error': '종료된 세션에는 메시지를 보낼 수 없습니다.'}, status=status.HTTP_409_CONFLICT)

        serializer = MessageCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({'success': False, 'error': serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        user_message = Message.objects.create(
            session=session,
            role=Message.Role.USER,
            content=serializer.validated_data['content'],
        )

        request_id = generate_request_id()
        step_id = serializer.validated_data.get('step_id') or 'general'
        form_data = serializer.validated_data.get('form_data') or {}
        ai_payload = build_ai_request_payload(
            request_id=request_id,
            session=session,
            user=request.user,
            message=user_message.content,
            step_id=step_id,
            form_data=form_data,
        )

        state_payload = {
            'request_id': request_id,
            'status': AI_STATUS_PROCESSING,
            'session_id': session.id,
            'user_id': request.user.id,
            'user_message_id': user_message.id,
            'thread_id': ai_payload['thread_id'],
            'step_id': step_id,
        }

        try:
            save_ai_request_state(request_id, state_payload)
            dispatch_ai_request(ai_payload)
        except Exception as exc:
            logger.exception('AI 요청 전송 실패', extra={'request_id': request_id})
            state_payload.update({
                'status': AI_STATUS_FAILED,
                'error_code': 'AI_SERVICE_UNAVAILABLE',
                'error': 'AI 서비스에 일시적으로 연결할 수 없습니다.',
            })
            try:
                save_ai_request_state(request_id, state_payload)
            except Exception:
                pass
            return Response(
                {
                    'success': False,
                    'error': 'AI 서버 요청에 실패했습니다.',
                    'data': {
                        'user_message_id': user_message.id,
                        'request_id': request_id,
                    },
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response(
            {
                'success': True,
                'data': {
                    'message': MessageSerializer(user_message).data,
                    'ai_request': {
                        'request_id': request_id,
                        'status': AI_STATUS_PROCESSING,
                    },
                    'user_message_id': user_message.id,
                    'request_id': request_id,
                    'status': AI_STATUS_PROCESSING,
                },
            },
            status=status.HTTP_202_ACCEPTED,
        )


# Polling API
class AIResponsePollingView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, session_id, request_id):
        session = get_session_or_404(session_id, request.user)
        if not session:
            return session_not_found()

        try:
            request_state = get_ai_request_state(request_id)
        except Exception as exc:
            logger.exception('AI 작업 상태 조회 실패', extra={'request_id': request_id})
            return Response(
                {'success': False, 'error': 'AI 작업 상태를 조회할 수 없습니다.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        if not request_state:
            return Response(
                {'success': False, 'error': 'AI 작업 상태를 찾을 수 없습니다.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        if request_state.get('session_id') != session.id or request_state.get('user_id') != request.user.id:
            return Response(
                {'success': False, 'error': 'AI 작업에 접근할 수 없습니다.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        response_data = {
            'request_id': request_id,
            'status': request_state.get('status'),
        }

        if request_state.get('status') == AI_STATUS_COMPLETED:
            message = Message.objects.filter(
                id=request_state.get('assistant_message_id'),
                session=session,
            ).first()
            if message:
                response_data['message'] = MessageSerializer(message).data
        elif request_state.get('status') == AI_STATUS_FAILED:
            response_data['error'] = request_state.get('error', 'AI 응답 생성에 실패했습니다.')

        return Response({'success': True, 'data': response_data})


# AI 수신 API
class AICallbackView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        if settings.AI_CALLBACK_SECRET:
            secret = request.headers.get('X-Internal-Secret')
            if secret != settings.AI_CALLBACK_SECRET:
                return Response(
                    {'success': False, 'error': '유효하지 않은 callback secret입니다.'},
                    status=status.HTTP_403_FORBIDDEN,
                )

        request_id = request.data.get('request_id')
        session_id = request.data.get('session_id')
        callback_status = request.data.get('status')
        thread_id = request.data.get('thread_id')

        if request_id:
            try:
                request_state = get_ai_request_state(request_id)
            except Exception as exc:
                logger.exception('AI callback 상태 조회 실패', extra={'request_id': request_id})
                return Response(
                    {'success': False, 'error': 'AI 작업 상태를 조회할 수 없습니다.'},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )

            if not request_state:
                return Response(
                    {'success': False, 'error': 'AI 작업 상태를 찾을 수 없습니다.'},
                    status=status.HTTP_404_NOT_FOUND,
                )

            session_id = session_id or request_state.get('session_id')
            callback_status = callback_status or AI_STATUS_COMPLETED
        elif thread_id:
            try:
                request_state = get_ai_request_state_by_thread_id(thread_id)
            except Exception as exc:
                logger.exception('AI callback thread 상태 조회 실패', extra={'thread_id': thread_id})
                return Response(
                    {'success': False, 'error': 'AI 작업 상태를 조회할 수 없습니다.'},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )

            if not request_state:
                return Response(
                    {'success': False, 'error': 'AI 작업 상태를 찾을 수 없습니다.'},
                    status=status.HTTP_404_NOT_FOUND,
                )

            request_id = request_state.get('request_id')
            session_id = request_state.get('session_id')
            callback_status = AI_STATUS_COMPLETED
        else:
            request_state = None

        if not request_id or not session_id or not callback_status:
            return Response(
                {'success': False, 'error': 'request_id, session_id, status는 필수입니다.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        session = ConsultingSession.objects.filter(id=session_id).first()
        if not session:
            return Response(
                {'success': False, 'error': '세션을 찾을 수 없습니다.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            request_state = request_state or get_ai_request_state(request_id) or {
                'request_id': request_id,
                'session_id': session.id,
                'user_id': session.user_id,
            }
        except Exception as exc:
            logger.exception('AI callback 상태 복원 실패', extra={'request_id': request_id})
            return Response(
                {'success': False, 'error': 'AI 작업 상태를 조회할 수 없습니다.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        if request_state.get('session_id') != session.id:
            return Response(
                {'success': False, 'error': 'request_id와 session_id가 일치하지 않습니다.'},
                status=status.HTTP_409_CONFLICT,
            )

        if callback_status == AI_STATUS_FAILED:
            logger.warning(
                'AI 처리 실패 callback 수신',
                extra={
                    'request_id': request_id,
                    'error_code': request.data.get('error_code', 'AI_PROCESSING_FAILED'),
                },
            )
            request_state.update({
                'status': AI_STATUS_FAILED,
                'error_code': request.data.get('error_code', 'AI_PROCESSING_FAILED'),
                'error': 'AI 응답 생성에 실패했습니다. 다시 시도해주세요.',
            })
            save_ai_request_state(request_id, request_state)
            return Response({'success': True, 'data': {'request_id': request_id, 'status': AI_STATUS_FAILED}})

        if callback_status != AI_STATUS_COMPLETED:
            return Response(
                {'success': False, 'error': '지원하지 않는 status입니다.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if request_state.get('status') == AI_STATUS_COMPLETED and request_state.get('assistant_message_id'):
            return Response({
                'success': True,
                'data': {
                    'request_id': request_id,
                    'status': AI_STATUS_COMPLETED,
                    'message_id': request_state.get('assistant_message_id'),
                },
            })

        assistant_message = create_assistant_message_from_callback(session, request.data)
        request_state.update({
            'status': AI_STATUS_COMPLETED,
            'assistant_message_id': assistant_message.id,
        })
        save_ai_request_state(request_id, request_state)

        return Response({
            'success': True,
            'data': {
                'request_id': request_id,
                'status': AI_STATUS_COMPLETED,
                'message_id': assistant_message.id,
            },
        })


# AI 제안 사용자 approve/reject API
class AIActionApproveView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, session_id):
        session = get_session_or_404(session_id, request.user)
        if not session:
            return session_not_found()

        message_id = request.data.get('message_id')
        message = Message.objects.filter(
            id=message_id,
            session=session,
            role=Message.Role.ASSISTANT,
        ).first()
        if not message:
            return Response(
                {'success': False, 'error': 'AI 메시지를 찾을 수 없습니다.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        extracted_data = message.extracted_data or {}
        if extracted_data.get('action_status') == 'approved':
            return Response(
                {'success': False, 'error': '이미 승인된 AI 제안입니다.'},
                status=status.HTTP_409_CONFLICT,
            )
        if extracted_data.get('action_status') == 'rejected':
            return Response(
                {'success': False, 'error': '이미 거절된 AI 제안입니다.'},
                status=status.HTTP_409_CONFLICT,
            )

        if not extracted_data.get('requires_approval') or not extracted_data.get('pending_changes'):
            return Response(
                {'success': False, 'error': '승인할 입력 제안이 없습니다.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            applied = apply_pending_changes(session, message)
        except (ValueError, ValidationError) as exc:
            return Response({'success': False, 'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response({'success': True, 'data': {'message_id': message.id, 'applied': applied}})


class AIActionRejectView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, session_id):
        session = get_session_or_404(session_id, request.user)
        if not session:
            return session_not_found()

        message_id = request.data.get('message_id')
        message = Message.objects.filter(
            id=message_id,
            session=session,
            role=Message.Role.ASSISTANT,
        ).first()
        if not message:
            return Response(
                {'success': False, 'error': 'AI 메시지를 찾을 수 없습니다.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        extracted_data = message.extracted_data or {}
        if extracted_data.get('action_status') == 'approved':
            return Response(
                {'success': False, 'error': '이미 승인된 AI 제안입니다.'},
                status=status.HTTP_409_CONFLICT,
            )

        mark_message_action(message, 'rejected')
        return Response({'success': True, 'data': {'message_id': message.id, 'action_status': 'rejected'}})


# SSE 스트리밍
class MessageStreamView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, session_id):
        session = get_session_or_404(session_id, request.user)
        if not session:
            return session_not_found()

        if session.status in (ConsultingSession.Status.COMPLETED, ConsultingSession.Status.ABANDONED):
            return Response({'success': False, 'error': '종료된 세션에는 메시지를 보낼 수 없습니다.'}, status=status.HTTP_409_CONFLICT)

        serializer = MessageCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'error': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user_message = Message.objects.create(
            session=session,
            role=Message.Role.USER,
            content=serializer.validated_data['content'],
        )
        request_id = generate_request_id()
        step_id = serializer.validated_data.get('step_id') or 'general'
        ai_payload = build_ai_request_payload(
            request_id=request_id,
            session=session,
            user=request.user,
            message=user_message.content,
            step_id=step_id,
            form_data=serializer.validated_data.get('form_data') or {},
        )

        response = StreamingHttpResponse(
            _proxy_ai_stream(session, ai_payload, step_id, user_message.id),
            content_type='text/event-stream',
        )
        response['Cache-Control'] = 'no-cache'
        response['X-Accel-Buffering'] = 'no'
        return response


# 기본 정보
class BasicInfoView(APIView):
    permission_classes = [IsAuthenticated]

    # GET /api/v1/consultations/{session_id}/financial-data/basic-info/
    def get(self, request, session_id):
        session = get_session_or_404(session_id, request.user)
        if not session:
            return session_not_found()

        try:
            basic_info = session.basic_info
        except BasicInfo.DoesNotExist:
            return Response({'success': True, 'data': None})

        return Response({'success': True, 'data': BasicInfoSerializer(basic_info).data})

    # PUT /api/v1/consultations/{session_id}/financial-data/basic-info/
    def put(self, request, session_id):
        session = get_session_or_404(session_id, request.user)
        if not session:
            return session_not_found()

        serializer = BasicInfoWriteSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({'success': False, 'error': serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            basic_info = serializer.save_to_session(session)
            session.refresh_completion_status()

        return Response({'success': True, 'data': BasicInfoSerializer(basic_info).data})


# 경제 관념
class FinancialMindsetView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, session_id):
        session = get_session_or_404(session_id, request.user)
        if not session:
            return session_not_found()

        try:
            obj = session.financial_mindset
        except FinancialMindset.DoesNotExist:
            return Response({'success': True, 'data': None})

        return Response({'success': True, 'data': FinancialMindsetSerializer(obj).data})

    def put(self, request, session_id):
        session = get_session_or_404(session_id, request.user)
        if not session:
            return session_not_found()

        serializer = FinancialMindsetSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({'success': False, 'error': serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        obj = serializer.save_to_session(session)
        session.refresh_completion_status()
        return Response({'success': True, 'data': FinancialMindsetSerializer(obj).data})


# 재무 만족도
class FinancialSatisfactionView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, session_id):
        session = get_session_or_404(session_id, request.user)
        if not session:
            return session_not_found()

        try:
            obj = session.financial_satisfaction
        except FinancialSatisfaction.DoesNotExist:
            return Response({'success': True, 'data': None})

        return Response({'success': True, 'data': FinancialSatisfactionSerializer(obj).data})

    def put(self, request, session_id):
        session = get_session_or_404(session_id, request.user)
        if not session:
            return session_not_found()

        serializer = FinancialSatisfactionSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({'success': False, 'error': serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        obj = serializer.save_to_session(session)
        session.refresh_completion_status()
        return Response({'success': True, 'data': FinancialSatisfactionSerializer(obj).data})


# 1:N 폼 공통 뷰 베이스
# GET → 전체 목록, PUT → 전체 교체 (기존 삭제 후 재생성)

class BulkReplaceView(APIView):
    permission_classes = [IsAuthenticated]
    serializer_class   = None   # 서브클래스에서 지정
    related_name       = None   # session의 related_manager 이름

    def get(self, request, session_id):
        session = get_session_or_404(session_id, request.user)
        if not session:
            return session_not_found()

        qs = getattr(session, self.related_name).all()
        return Response({'success': True, 'data': self.serializer_class(qs, many=True).data})

    def put(self, request, session_id):
        session = get_session_or_404(session_id, request.user)
        if not session:
            return session_not_found()

        if not isinstance(request.data, list):
            return Response({'success': False, 'error': '리스트 형식으로 보내주세요.'}, status=status.HTTP_400_BAD_REQUEST)

        existing = {
            obj.id: obj
            for obj in getattr(session, self.related_name).all()
        }
        prepared = []
        kept_ids = set()
        for raw_item in request.data:
            item_id = raw_item.get('id') if isinstance(raw_item, dict) else None
            instance = None
            if item_id is not None:
                try:
                    item_id = int(item_id)
                except (TypeError, ValueError):
                    return Response(
                        {'success': False, 'error': 'id는 정수여야 합니다.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                instance = existing.get(item_id)
                if instance is None:
                    return Response(
                        {'success': False, 'error': f'현재 세션의 항목이 아닙니다: {item_id}'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                kept_ids.add(item_id)
            serializer = self.serializer_class(instance, data=raw_item)
            if not serializer.is_valid():
                return Response(
                    {'success': False, 'error': serializer.errors},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            prepared.append((instance, serializer))

        with transaction.atomic():
            saved = []
            for instance, serializer in prepared:
                saved.append(
                    serializer.save() if instance else serializer.save(session=session)
                )
            stale_ids = set(existing) - kept_ids
            if stale_ids:
                getattr(session, self.related_name).filter(id__in=stale_ids).delete()
            session.refresh_completion_status()

        return Response({'success': True, 'data': self.serializer_class(saved, many=True).data})


class FinancialAssetsView(BulkReplaceView):
    serializer_class = FinancialAssetSerializer
    related_name     = 'financial_assets'


class PensionAssetsView(BulkReplaceView):
    serializer_class = PensionAssetSerializer
    related_name     = 'pension_assets'


class RealEstateAssetsView(BulkReplaceView):
    serializer_class = RealEstateAssetSerializer
    related_name     = 'real_estate_assets'


class LiabilitiesView(BulkReplaceView):
    serializer_class = LiabilitySerializer
    related_name     = 'liabilities'


class IncomesView(BulkReplaceView):
    serializer_class = IncomeSerializer
    related_name     = 'incomes'


class ExpensesView(BulkReplaceView):
    serializer_class = ExpenseSerializer
    related_name     = 'expenses'


class GoalMilestonesView(BulkReplaceView):
    serializer_class = FinancialGoalMilestoneSerializer
    related_name     = 'goal_milestones'


# 재무목표 기간 (중첩 구조라 별도 처리)
class GoalPeriodsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, session_id):
        session = get_session_or_404(session_id, request.user)
        if not session:
            return session_not_found()

        qs = session.goal_periods.prefetch_related('items').all()
        return Response({'success': True, 'data': FinancialGoalPeriodSerializer(qs, many=True).data})

    def put(self, request, session_id):
        session = get_session_or_404(session_id, request.user)
        if not session:
            return session_not_found()

        if not isinstance(request.data, list):
            return Response({'success': False, 'error': '리스트 형식으로 보내주세요.'}, status=status.HTTP_400_BAD_REQUEST)

        serializer = FinancialGoalPeriodSerializer(data=request.data, many=True)
        if not serializer.is_valid():
            return Response({'success': False, 'error': serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        existing_periods = {
            period.id: period
            for period in session.goal_periods.prefetch_related('items').all()
        }

        # 모든 서버 ID의 소유권을 쓰기 전에 확인해 중첩 저장도 전부 성공하거나
        # 전부 실패하도록 한다. 신규 행은 DB id 없이 client_item_id로만 식별한다.
        for raw_period in request.data:
            raw_period = raw_period if isinstance(raw_period, dict) else {}
            period_id = raw_period.get('id')
            period = None
            if period_id is not None:
                try:
                    period_id = int(period_id)
                except (TypeError, ValueError):
                    return Response(
                        {'success': False, 'error': '기간 id는 정수여야 합니다.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                period = existing_periods.get(period_id)
                if period is None:
                    return Response(
                        {'success': False, 'error': f'현재 세션의 기간이 아닙니다: {period_id}'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
            existing_item_ids = {
                item.id for item in period.items.all()
            } if period is not None else set()
            for raw_item in raw_period.get('items', []):
                item_id = raw_item.get('id') if isinstance(raw_item, dict) else None
                if item_id is None:
                    continue
                try:
                    item_id = int(item_id)
                except (TypeError, ValueError):
                    return Response(
                        {'success': False, 'error': '기간 항목 id는 정수여야 합니다.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if item_id not in existing_item_ids:
                    return Response(
                        {'success': False, 'error': f'현재 기간의 항목이 아닙니다: {item_id}'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

        with transaction.atomic():
            saved_periods = []
            kept_period_ids = set()
            for raw_period, period_data in zip(request.data, serializer.validated_data):
                raw_period = raw_period if isinstance(raw_period, dict) else {}
                period_id = raw_period.get('id')
                period = None
                if period_id is not None:
                    try:
                        period_id = int(period_id)
                    except (TypeError, ValueError):
                        return Response(
                            {'success': False, 'error': '기간 id는 정수여야 합니다.'},
                            status=status.HTTP_400_BAD_REQUEST,
                        )
                    period = existing_periods.get(period_id)
                    if period is None:
                        return Response(
                            {'success': False, 'error': f'현재 세션의 기간이 아닙니다: {period_id}'},
                            status=status.HTTP_400_BAD_REQUEST,
                        )
                    kept_period_ids.add(period_id)

                items_data = period_data.pop('items', [])
                if period is None:
                    period = FinancialGoalPeriod.objects.create(session=session, **period_data)
                else:
                    for field, value in period_data.items():
                        setattr(period, field, value)
                    period.save(update_fields=list(period_data))

                existing_items = {item.id: item for item in period.items.all()}
                kept_item_ids = set()
                for raw_item, item_data in zip(raw_period.get('items', []), items_data):
                    item_id = raw_item.get('id') if isinstance(raw_item, dict) else None
                    item = None
                    if item_id is not None:
                        try:
                            item_id = int(item_id)
                        except (TypeError, ValueError):
                            return Response(
                                {'success': False, 'error': '기간 항목 id는 정수여야 합니다.'},
                                status=status.HTTP_400_BAD_REQUEST,
                            )
                        item = existing_items.get(item_id)
                        if item is None:
                            return Response(
                                {'success': False, 'error': f'현재 기간의 항목이 아닙니다: {item_id}'},
                                status=status.HTTP_400_BAD_REQUEST,
                            )
                        kept_item_ids.add(item_id)
                    if item is None:
                        FinancialGoalPeriodItem.objects.create(period=period, **item_data)
                    else:
                        for field, value in item_data.items():
                            setattr(item, field, value)
                        item.save(update_fields=list(item_data))
                stale_item_ids = set(existing_items) - kept_item_ids
                if stale_item_ids:
                    period.items.filter(id__in=stale_item_ids).delete()
                saved_periods.append(period)

            stale_period_ids = set(existing_periods) - kept_period_ids
            if stale_period_ids:
                session.goal_periods.filter(id__in=stale_period_ids).delete()
            session.refresh_completion_status()

        refreshed = {
            period.id: period
            for period in FinancialGoalPeriod.objects.filter(
                id__in=[period.id for period in saved_periods]
            ).prefetch_related('items')
        }
        ordered = [refreshed[period.id] for period in saved_periods]
        return Response({'success': True, 'data': FinancialGoalPeriodSerializer(ordered, many=True).data})
