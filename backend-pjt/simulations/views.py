"""기준 시뮬레이션 실행 API (S15P21B109-137).

POST /api/v1/consultations/<id>/simulations/

기준과 What-if 를 한 엔드포인트로 둔다. 모듈 진입점이 analyze_retirement() 하나이고
mode 는 scenarios 의 공백 여부로 파생되며, What-if 응답은 같은 배치의 기준 결과를
포함하므로 리소스가 하나다. 나누면 인증·스냅샷·정책·어댑터가 통째로 중복된다.

이번 단계는 mode="baseline" 만 받는다. What-if 는 S15P21B109-138.
저장하는 것이 없으므로 GET 은 제공하지 않는다.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from rest_framework.permissions import IsAuthenticated

from consulting.models import ConsultingSession
from reports.views import SessionMixin

from .baseline import issue_baseline_key
from .errors import SimulationError, SimulationAPIView, success_response
from .services import (
    SLOW_EXECUTION_MS,
    load_consistent_snapshot,
    load_policy,
    package_version,
    prepare_input,
    run_analysis,
)

logger = logging.getLogger(__name__)

# 이번 단계에서 허용하는 요청 본문 키. 가정값·경로 수·시드·confirmed_empty_sections 는
# 요청으로 받지 않는다 — 브라우저가 계산 가정을 정하게 두지 않는다.
_ALLOWED_FIELDS = {'mode'}
_SUPPORTED_MODES = ('baseline',)


class SimulationRunView(SessionMixin, SimulationAPIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, session_id):
        self._validate_body(request.data)

        session = self.get_session(session_id, request.user)
        if session is None:
            # 타인 세션도 404 로 둔다. 존재 여부가 새지 않는다 (reports 와 같은 관례).
            raise SimulationError(
                404, 'SESSION_NOT_FOUND', '상담을 찾을 수 없습니다.'
            )
        self._require_ready(session)

        assumptions, policy_version = load_policy()
        snapshot, snapshot_digest = load_consistent_snapshot(session)
        prepared = prepare_input(snapshot)
        payload, elapsed_ms = run_analysis(prepared, assumptions)

        if elapsed_ms >= SLOW_EXECUTION_MS:
            # 비식별. 상담 id 와 소요 시간만 남긴다.
            logger.warning(
                'simulation slow: session=%s elapsed_ms=%s', session.id, elapsed_ms
            )

        baseline_key = issue_baseline_key(
            user_id=request.user.id,
            session_id=session.id,
            snapshot_digest=snapshot_digest,
            policy_version=policy_version,
            contract_version=payload['contract_version'],
            mapping_version=payload['mapping_version'],
            package_version=package_version(),
        )
        return success_response(
            # analysis 는 모듈 출력 그대로다. notices·units·contract_version 을 보존해야
            # 안내를 버린 채 성공확률만 표시하는 상황이 구조적으로 막힌다.
            {'baseline_key': baseline_key, 'analysis': payload},
            {
                'simulation_policy_version': policy_version,
                'package_version': package_version(),
                'executed_at': datetime.now(timezone.utc).isoformat(),
                'elapsed_ms': elapsed_ms,
            },
        )

    @staticmethod
    def _validate_body(data: object) -> None:
        if not isinstance(data, dict):
            raise SimulationError(
                400, 'REQUEST_INVALID', '요청 본문은 객체여야 합니다.'
            )
        unknown = set(data) - _ALLOWED_FIELDS
        if unknown:
            raise SimulationError(
                400,
                'REQUEST_INVALID',
                '지원하지 않는 요청 필드가 있습니다.',
                details={'unsupported_fields': sorted(unknown)},
            )
        mode = data.get('mode')
        if mode is None:
            raise SimulationError(400, 'REQUEST_INVALID', 'mode 는 필수입니다.')
        if mode not in _SUPPORTED_MODES:
            # what_if 는 다음 단계다. 지금 조용히 기준으로 처리하면 사용자는 비교가
            # 된 줄 안다.
            raise SimulationError(
                400,
                'REQUEST_INVALID',
                '지원하지 않는 mode 입니다.',
                details={'supported': list(_SUPPORTED_MODES)},
            )

    @staticmethod
    def _require_ready(session) -> None:
        if session.status == ConsultingSession.Status.ABANDONED:
            raise SimulationError(
                409,
                'SESSION_ABANDONED',
                '중도 이탈한 상담은 시뮬레이션을 실행할 수 없습니다.',
            )
        if not session.is_ready_for_reports():
            raise SimulationError(
                409,
                'SESSION_NOT_READY',
                '전체 정보를 입력한 후 시뮬레이션을 실행할 수 있습니다.',
            )
