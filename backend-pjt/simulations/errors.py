"""시뮬레이션 API 오류 계약 (S15P21B109-137).

구조는 consulting.ai_lifecycle.LifecycleError 를 그대로 따른다 — 저장소에서 이미
검증된 패턴의 두 번째 인스턴스이지 새 추상화가 아니다.

다만 **v2 계약을 재사용하지는 않는다.** CONTRACT_VERSION='2.0' 은
contract-fixtures/ai-lifecycle/v2 로 동결된 AI 라이프사이클 계약이다. 시뮬레이션
응답에 그 버전을 박으면 계산 계약이 AI 계약 버전에 묶인다. 모듈 인수인계 문서도
"기존 채팅 오류 코드나 timeout 을 새 계산 계약에 임의로 재사용하지 않는다" 고 못박았다.

봉투는 /api/v1 계열(success/data/error)을 따르되 error 를 객체로 둔다. 문자열 error 는
기계가 판별할 수 없다 — reports 의 session_not_found_response() 를 재사용하지 않는 이유다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rest_framework.exceptions import AuthenticationFailed, NotAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView


# frozen=True 를 쓰지 않는다. 예외는 Python 이 __traceback__ 을 대입해야 하는데
# frozen dataclass 의 __setattr__ 이 그것을 막아 FrozenInstanceError 로 바뀐다
# (unittest 가 오류를 포매팅할 때 실제로 터졌다). consulting.ai_lifecycle.LifecycleError
# 는 frozen 이지만 같은 함정을 안고 있다.
@dataclass
class SimulationError(Exception):
    status_code: int
    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] | None = field(default=None)

    def response_body(self) -> dict[str, Any]:
        error: dict[str, Any] = {
            'code': self.code,
            'message': self.message,
            'retryable': self.retryable,
        }
        if self.details is not None:
            error['details'] = self.details
        return {'success': False, 'error': error}


def error_response(error: SimulationError) -> Response:
    return Response(error.response_body(), status=error.status_code)


def success_response(data: dict[str, Any], meta: dict[str, Any]) -> Response:
    return Response({'success': True, 'data': data, 'meta': meta})


class SimulationAPIView(APIView):
    def handle_exception(self, exc):
        if isinstance(exc, (AuthenticationFailed, NotAuthenticated)):
            return error_response(
                SimulationError(401, 'AUTH_REQUIRED', '사용자 인증이 필요합니다.')
            )
        if isinstance(exc, SimulationError):
            return error_response(exc)
        # 예상 밖 예외를 분석 오류로 섞지 않는다. DRF 기본 500(비식별)로 보낸다 —
        # traceback·금액 원문이 응답에 새면 안 된다.
        return super().handle_exception(exc)
