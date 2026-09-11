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
from .baseline import issue_baseline_key
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


def prepare_input(snapshot: dict):
    """어댑터를 돌리고 규격 위반이 있으면 모듈을 부르지 않는다."""
    prepared = build_simulation_input(snapshot)
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
