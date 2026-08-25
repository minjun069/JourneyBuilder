from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from consulting.models import ConsultingSession
from .models import Report
from .serializers import ReportSerializer, ReportListSerializer
from .services import generate_sync_report



# 공통 Mixin
# session 조회 + 소유권 검증 공통 처리
# Mixin: 상속받아서 공통 기능만 재사용하는 클래스
class SessionMixin:

    # 세션 조회 함수
    def get_session(self, session_id, user):
        try:
            return ConsultingSession.objects.get(id=session_id, user=user)
        except ConsultingSession.DoesNotExist:
            return None

    # 404 응답 공통화
    def session_not_found_response(self):
        return Response(
            {'success': False, 'error': '세션을 찾을 수 없습니다.'},
            status=status.HTTP_404_NOT_FOUND
        )

    def require_completed_session_response(self, session, action):
        if session.status == ConsultingSession.Status.ABANDONED:
            return Response(
                {'success': False, 'error': f'중도 이탈한 상담은 보고서를 {action}할 수 없습니다.'},
                status=status.HTTP_409_CONFLICT
            )

        if session.is_ready_for_reports():
            if session.status != ConsultingSession.Status.COMPLETED:
                session.refresh_completion_status()
            return None

        return Response(
            {'success': False, 'error': f'전체 정보를 입력한 후 {action}할 수 있습니다.'},
            status=status.HTTP_409_CONFLICT
        )


# 보고서 목록
class ReportListView(SessionMixin, APIView):
    # 로그인 필수
    permission_classes = [IsAuthenticated]

    # GET /api/v1/consultations/{id}/reports/
    # : 세션의 전체 보고서 목록 (최신 버전만)
    def get(self, request, session_id):
        session = self.get_session(session_id, request.user)
        if not session:
            return self.session_not_found_response()

        reports = Report.objects.filter(
            session=session,
            is_latest=True,
        ).order_by('created_at')

        serializer = ReportListSerializer(reports, many=True)
        return Response({'success': True, 'data': serializer.data})


# 보고서 상세/생성 공통 엔드포인트
class ReportDetailView(SessionMixin, APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, session_id, report_type):
        session = self.get_session(session_id, request.user)
        if not session:
            return self.session_not_found_response()

        report_type = self._normalize_report_type(report_type)
        if not report_type:
            return self.report_type_not_found_response()

        blocked_response = self.require_completed_session_response(session, '조회')
        if blocked_response:
            return blocked_response

        report = Report.objects.filter(
            session=session,
            report_type=report_type,
            is_latest=True
        ).first()

        if not report:
            return Response(
                {'success': False, 'error': '보고서가 아직 생성되지 않았습니다.'},
                status=status.HTTP_404_NOT_FOUND
            )

        return Response({'success': True, 'data': ReportSerializer(report).data})

    def post(self, request, session_id, report_type):
        session = self.get_session(session_id, request.user)
        if not session:
            return self.session_not_found_response()

        report_type = self._normalize_report_type(report_type)
        if not report_type:
            return self.report_type_not_found_response()

        blocked_response = self.require_completed_session_response(session, '생성')
        if blocked_response:
            return blocked_response

        report = generate_sync_report(session, report_type)
        return Response(
            {'success': True, 'data': ReportSerializer(report).data},
            status=status.HTTP_201_CREATED
        )

    def _normalize_report_type(self, report_type):
        legacy_url_map = {
            'balance-sheet': Report.ReportType.ASSET_STATEMENT,
            'asset-statement': Report.ReportType.ASSET_STATEMENT,
            'cash-flow': Report.ReportType.CASH_FLOW,
        }
        report_type = legacy_url_map.get(report_type, report_type)

        if report_type in Report.ReportType.values:
            return report_type
        return None

    def report_type_not_found_response(self):
        return Response(
            {'success': False, 'error': '지원하지 않는 보고서 타입입니다.'},
            status=status.HTTP_404_NOT_FOUND
        )
