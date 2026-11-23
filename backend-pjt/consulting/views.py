from django.db import transaction
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
import logging

from .models import (
    ConsultingSession,
    BasicInfo, FinancialMindset, FinancialSatisfaction,
    FinancialGoalPeriod, FinancialGoalPeriodItem,
)
from .serializers import (
    ConsultingSessionSerializer,
    ConsultingSessionListSerializer,
    ConsultingSessionCreateSerializer,
    MessageSerializer,
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


logger = logging.getLogger(__name__)


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


# 메시지 목록. AI 전송은 v2 SSE endpoint만 사용한다.
class MessageListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, session_id):
        session = get_session_or_404(session_id, request.user)
        if not session:
            return session_not_found()

        messages = session.messages.all()
        serializer = MessageSerializer(messages, many=True)
        return Response({'success': True, 'data': serializer.data, 'meta': {'total': messages.count()}})

# 기본 정보
class BasicInfoView(APIView):
    permission_classes = [IsAuthenticated]

    # GET /api/consulting/v1/consultations/{session_id}/data/basic-info/
    def get(self, request, session_id):
        session = get_session_or_404(session_id, request.user)
        if not session:
            return session_not_found()

        try:
            basic_info = session.basic_info
        except BasicInfo.DoesNotExist:
            return Response({'success': True, 'data': None})

        return Response({'success': True, 'data': BasicInfoSerializer(basic_info).data})

    # PUT /api/consulting/v1/consultations/{session_id}/data/basic-info/
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

    def after_replace(self, session):
        section = 'financial_goals' if self.related_name == 'goal_milestones' else self.related_name
        session.clear_empty_confirmation(section)

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
            self.after_replace(session)
            session.refresh_completion_status()

        return Response({'success': True, 'data': self.serializer_class(saved, many=True).data})


class FinancialAssetsView(BulkReplaceView):
    serializer_class = FinancialAssetSerializer
    related_name     = 'financial_assets'

    def after_replace(self, session):
        if session.financial_assets_confirmed_empty:
            session.financial_assets_confirmed_empty = False
            session.save(update_fields=['financial_assets_confirmed_empty'])


class FinancialAssetsEmptyConfirmationView(APIView):
    permission_classes = [IsAuthenticated]

    def put(self, request, session_id):
        session = get_session_or_404(session_id, request.user)
        if not session:
            return session_not_found()

        with transaction.atomic():
            session.financial_assets.all().delete()
            session.financial_assets_confirmed_empty = True
            session.save(update_fields=['financial_assets_confirmed_empty'])
            session.refresh_completion_status()

        return Response({
            'success': True,
            'data': ConsultingSessionSerializer(session).data,
        })


class SectionEmptyConfirmationView(APIView):
    permission_classes = [IsAuthenticated]
    sections = {
        'pension-assets': ('pension_assets', ('pension_assets',)),
        'real-estate': ('real_estate_assets', ('real_estate_assets',)),
        'liabilities': ('liabilities', ('liabilities',)),
        'income': ('incomes', ('incomes',)),
        'expenses': ('expenses', ('expenses',)),
        'financial-goals': ('financial_goals', ('goal_periods', 'goal_milestones')),
    }

    def put(self, request, session_id, step_id):
        session = get_session_or_404(session_id, request.user)
        if not session:
            return session_not_found()
        if step_id not in self.sections:
            return Response({'success': False, 'error': '해당 없음 확인을 지원하지 않는 단계입니다.'}, status=400)

        section, relations = self.sections[step_id]
        with transaction.atomic():
            session = ConsultingSession.objects.select_for_update().get(pk=session.pk)
            for relation in relations:
                getattr(session, relation).all().delete()
            session.confirmed_empty_sections = sorted(set(session.confirmed_empty_sections) | {section})
            session.save(update_fields=['confirmed_empty_sections'])
            session.refresh_completion_status()

        return Response({'success': True, 'data': ConsultingSessionSerializer(session).data})


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
            session.clear_empty_confirmation('financial_goals')
            session.refresh_completion_status()

        refreshed = {
            period.id: period
            for period in FinancialGoalPeriod.objects.filter(
                id__in=[period.id for period in saved_periods]
            ).prefetch_related('items')
        }
        ordered = [refreshed[period.id] for period in saved_periods]
        return Response({'success': True, 'data': FinancialGoalPeriodSerializer(ordered, many=True).data})
