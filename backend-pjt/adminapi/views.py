from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView


class AdminAPIView(APIView):
    """/api/v1/admin/* 공통 베이스 — is_staff가 아니면 403 (NFR-01).

    v1 관리자 API는 조회 전용이므로 하위 뷰는 GET만 구현한다 (NFR-05).
    """

    permission_classes = [IsAdminUser]


class PingView(AdminAPIView):
    def get(self, request):
        return Response({'success': True, 'data': {'status': 'ok'}})
