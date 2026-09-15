from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from .services import (
    build_customer_detail,
    build_customer_row,
    build_segments,
    build_summary,
    customers_queryset,
)

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

# ordering 파라미터 화이트리스트 (앞에 '-' 를 붙이면 내림차순)
ORDERABLE_FIELDS = {
    'name', 'age', 'net_worth', 'monthly_surplus',
    'risk_count', 'progress_pct', 'last_activity',
}
# 동등 비교 필터: 쿼리 파라미터 → row 키. 값 'unknown' 은 미입력(None) 과 일치
EQUALITY_FILTERS = {
    'status': 'session_status',
    'need': 'need',
    'age_band': 'age_band',
    'net_worth_band': 'net_worth_band',
    'tendency': 'tendency_group',
    'health': 'health',
}


class AdminAPIView(APIView):
    """/api/admin/v1/* 공통 베이스 — is_staff가 아니면 403 (NFR-01).

    v1 관리자 API는 조회 전용이므로 하위 뷰는 GET만 구현한다 (NFR-05).
    """

    permission_classes = [IsAdminUser]

    @staticmethod
    def envelope(data, meta=None):
        meta = dict(meta or {})
        # FR-09: 프론트가 "마지막 갱신 시각"을 표시할 기준
        meta['generated_at'] = timezone.now()
        return Response({'success': True, 'data': data, 'meta': meta})


class PingView(AdminAPIView):
    def get(self, request):
        return Response({'success': True, 'data': {'status': 'ok'}})


class CustomerListView(AdminAPIView):
    """FR-03 고객 리스트 — 검색·필터·정렬·페이지네이션.

    행 수가 수십 명 규모라 파생 값(순자산·위험지표 수)의 필터·정렬은
    스냅샷 계산 후 파이썬에서 수행한다 (4.2절 성능 규칙).
    """

    def get(self, request):
        params = request.query_params
        queryset = customers_queryset()

        search = params.get('search')
        if search:
            queryset = queryset.filter(Q(name__icontains=search) | Q(email__icontains=search))

        rows = [build_customer_row(user) for user in queryset]
        rows = self._filter(rows, params)
        rows = self._order(rows, params.get('ordering'))
        page_rows, meta = self._paginate(rows, params)
        return self.envelope(page_rows, meta)

    @staticmethod
    def _filter(rows, params):
        for param, row_key in EQUALITY_FILTERS.items():
            value = params.get(param)
            if not value:
                continue
            if value == 'unknown':
                rows = [row for row in rows if row[row_key] is None]
            else:
                rows = [row for row in rows if row[row_key] == value]
        return rows

    @staticmethod
    def _order(rows, ordering):
        if not ordering:
            return rows
        reverse = ordering.startswith('-')
        field = ordering.lstrip('-')
        if field not in ORDERABLE_FIELDS:
            return rows
        # None 은 방향과 무관하게 항상 마지막으로 보낸다 (NFR-04)
        present = [row for row in rows if row[field] is not None]
        missing = [row for row in rows if row[field] is None]
        present.sort(key=lambda row: row[field], reverse=reverse)
        return present + missing

    @staticmethod
    def _paginate(rows, params):
        def positive_int(name, default, maximum):
            try:
                value = int(params.get(name, default))
            except (TypeError, ValueError):
                value = default
            return max(1, min(value, maximum))

        page_size = positive_int('page_size', DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE)
        total = len(rows)
        total_pages = max(1, -(-total // page_size))
        page = positive_int('page', 1, total_pages)
        start = (page - 1) * page_size
        meta = {
            'total': total,
            'page': page,
            'page_size': page_size,
            'total_pages': total_pages,
        }
        return rows[start:start + page_size], meta


class CustomerDetailView(AdminAPIView):
    """FR-04·05 고객 상세 — 대표 세션 전 섹션 + 파생 스냅샷."""

    def get(self, request, user_id):
        user = customers_queryset().filter(pk=user_id).first()
        if not user:
            return Response(
                {'success': False, 'error': '고객을 찾을 수 없습니다.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return self.envelope(build_customer_detail(user))


class SummaryView(AdminAPIView):
    """FR-06 개요 KPI + 최근 상담 활동."""

    def get(self, request):
        return self.envelope(build_summary())


class SegmentsView(AdminAPIView):
    """FR-07 세그먼트 분포(6축) + 지표 등급 스택 + 연령×순자산 산점도."""

    def get(self, request):
        return self.envelope(build_segments())
