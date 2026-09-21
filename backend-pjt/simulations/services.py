"""시뮬레이션 실행 오케스트레이션 (S15P21B109-137).

ORM 접근은 이 파일까지다. views 는 세션에서 status·id·is_ready_for_reports() 만 읽고,
나머지 조회는 전부 load_consistent_snapshot() 한 곳에 가둔다. 그래야 계약 테스트가
가짜 세션 객체로 DB 없이 전 경로를 검증할 수 있다.
"""

from __future__ import annotations

from importlib import metadata
import time
from typing import Any

from django.db import transaction

from reports.snapshots import build_calculation_input_snapshot, digest_snapshot

from .adapter import build_simulation_input
from .errors import SimulationError
from .policy import (
    SimulationPolicyError,
    active_simulation_policy_version,
    simulation_assumption_values,
)

DISTRIBUTION = 'retirewise-retirement-simulation'
# 정상 실행은 1 CPU 에서 0.4~1.0초다(2026-09-11 실측). 3초를 넘으면 뭔가 잘못된 것이다.
SLOW_EXECUTION_MS = 3000


def load_consistent_snapshot(session) -> tuple[dict, str]:
    """계산과 기준 검증이 같은 시점을 쓰도록 스냅샷을 한 번만 뜬다.

    build_calculation_input_snapshot() 와 calculation_input_digest() 를 각각 부르면
    DB 를 두 번 조회해 시점이 섞인다.

    한계: PostgreSQL 기본 READ COMMITTED 에서 atomic() 은 진짜 스냅샷 격리를 주지
    않는다. 같은 사용자가 자기 세션을 동시에 수정하는 좁은 경우는 남는다. 보장한다고
    쓰지 않는다.
    """
    with transaction.atomic():
        snapshot = build_calculation_input_snapshot(session)
    return snapshot, digest_snapshot(snapshot)


# 세션 플래그 → 어댑터 섹션 이름. 모델에 플래그가 있는 섹션만 적는다.
# 어댑터의 _CONFIRMABLE_SECTIONS 에는 pension_assets 도 있지만 대응하는 모델 필드가
# 없다. 없는 플래그를 추측해서 채우지 않는다 — 없으면 확인하지 않은 것이다.
_CONFIRMED_EMPTY_FLAGS = (
    ('financial_assets', 'financial_assets_confirmed_empty'),
)


def confirmed_empty_sections(session, snapshot: dict) -> tuple[str, ...]:
    """"정말 없음" 을 사용자가 확인했고 스냅샷도 실제로 비어 있는 섹션 이름.

    플래그만 보고 넘기면 안 된다. 모듈 input_adapter._confirm_empty() 는 이름만 받고
    해당 섹션에 행이 남아 있으면 InputMappingError('invalid_confirmation') 를 던진다.
    그러면 사용자가 고칠 수 있는 422 INPUT_NOT_READY 가 ANALYSIS_REJECTED 로 둔갑한다.
    그래서 플래그와 실제 공백을 **둘 다** 만족할 때만 확인으로 친다.

    스냅샷을 다시 조회하지 않고 인자로 받는다. load_consistent_snapshot() 이 뜬 것과
    다른 시점을 보면 같은 불일치가 생긴다.
    """
    return tuple(
        section
        for section, flag in _CONFIRMED_EMPTY_FLAGS
        # 계약 테스트의 가짜 세션처럼 플래그가 없는 객체는 "확인 안 함" 으로 둔다.
        if getattr(session, flag, False) and not snapshot.get(section)
    )


def package_version() -> str:
    return metadata.version(DISTRIBUTION)


def load_policy() -> tuple[dict[str, Any], str]:
    """정책이 없거나 깨졌으면 실행하지 않는다. 시연값 폴백 금지."""
    try:
        return simulation_assumption_values(), active_simulation_policy_version()
    except SimulationPolicyError as error:
        raise SimulationError(
            503,
            'POLICY_UNAVAILABLE',
            '시뮬레이션 계산 정책을 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.',
            retryable=True,
        ) from error


def prepare_input(snapshot: dict, *, confirmed_empty_sections: tuple[str, ...] = ()):
    """어댑터를 돌리고 규격 위반이 있으면 모듈을 부르지 않는다.

    confirmed_empty_sections 는 호출부가 세션에서 읽어 넘긴다(services 의 동명 헬퍼).
    빠뜨리면 "금융자산 없음" 을 확인한 세션이 항상 422 INPUT_NOT_READY 가 된다.
    """
    prepared = build_simulation_input(
        snapshot, confirmed_empty_sections=confirmed_empty_sections
    )
    if prepared.issues:
        raise SimulationError(
            422,
            'INPUT_NOT_READY',
            '시뮬레이션에 필요한 정보를 먼저 보완해 주세요.',
            details={
                'issues': [
                    {
                        'code': issue.code,
                        'field': issue.field,
                        'message': issue.message,
                        **({'hint': issue.hint} if issue.hint is not None else {}),
                    }
                    for issue in prepared.issues
                ]
            },
        )
    return prepared


def run_analysis(prepared, assumptions: dict[str, Any], scenarios=()) -> tuple[dict, int]:
    """모듈을 실행하고 결과 dict 와 소요 시간을 돌려준다.

    RetirementAnalysisError 는 422 로 바꾼다. 이건 **안전망**이다 — 정상 흐름에서는
    어댑터가 먼저 422 를 내므로, 여기 걸리면 어댑터 사전 점검에 구멍이 있다는 뜻이다.

    그 밖의 예외는 잡지 않는다. except Exception 으로 분석 오류에 섞으면 버그가
    "계산 실패" 로 위장된다.
    """
    from retirement_simulation import (  # noqa: PLC0415
        RetirementAnalysisError,
        analysis_error_to_dict,
        analysis_to_dict,
        analyze_retirement,
    )

    started = time.perf_counter()
    try:
        result = analyze_retirement(
            prepared.saved_data,
            assumptions,
            scenarios=scenarios,
            confirmed_empty_sections=prepared.confirmed_empty_sections,
        )
    except RetirementAnalysisError as error:
        raise SimulationError(
            422,
            'ANALYSIS_REJECTED',
            '입력값으로 시뮬레이션을 계산할 수 없습니다.',
            details={'analysis_error': analysis_error_to_dict(error)['error']},
        ) from None
    payload = analysis_to_dict(result)
    return payload, int((time.perf_counter() - started) * 1000)
