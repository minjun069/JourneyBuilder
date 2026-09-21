"""Fail-closed loader for the approved retirement simulation policy.

reports/policy.py 와 같은 패턴이다. 별도 파일인 이유는 두 정책의 수명이 다르기
때문이다 — 공통 재무 정책은 보고서 계산 전체가 쓰고, 이 정책은 몬테카를로 가정
다섯 개만 담는다. 하나로 합치면 시뮬레이션 가정을 바꿀 때마다 보고서 계약이 흔들린다.

`contract-fixtures/simulation/v1/policy.json`이 runtime 값과 승인 상태의
기계 단일 원천이다. 패키지 문서는 근거와 과거 인수인계 기록이며
runtime 정책 값을 중복 소유하지 않는다. 모듈 wheel 에는 정책 JSON 이 없으므로
서버가 fixture를 fail-closed로 읽어 값을 명시적으로 전달한다.
"""

from __future__ import annotations

from functools import lru_cache
import json
from math import isfinite
from pathlib import Path
from typing import Any


POLICY_RELATIVE_PATH = Path("contract-fixtures/simulation/v1/policy.json")

# 모듈 map_simulation_assumptions() 가 정확히 이 다섯 개를 요구한다. 추가·누락 모두 거부된다.
ASSUMPTION_FIELDS = (
    "annual_return_mean",
    "annual_return_volatility",
    "annual_inflation_rate",
    "num_paths",
    "seed",
)


class SimulationPolicyError(RuntimeError):
    pass


def _policy_path() -> Path:
    candidates = (
        Path(__file__).resolve().parents[2] / POLICY_RELATIVE_PATH,
        Path(__file__).resolve().parents[1] / POLICY_RELATIVE_PATH,
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise SimulationPolicyError("approved simulation policy is missing")


@lru_cache(maxsize=1)
def load_simulation_policy() -> dict[str, Any]:
    try:
        payload = json.loads(_policy_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SimulationPolicyError("approved simulation policy cannot be loaded") from exc

    required = {
        "schema_version",
        "policy_version",
        "status",
        "approved_at",
        "rationale_document",
        "assumption_values",
        "max_comparison_scenarios",
    }
    if set(payload) != required:
        raise SimulationPolicyError("simulation policy schema mismatch")
    if payload["schema_version"] != "1.0" or payload["status"] != "approved":
        raise SimulationPolicyError("simulation policy is not approved")
    if not isinstance(payload["policy_version"], str) or not payload["policy_version"]:
        raise SimulationPolicyError("simulation policy version is missing")

    values = payload["assumption_values"]
    if not isinstance(values, dict) or set(values) != set(ASSUMPTION_FIELDS):
        raise SimulationPolicyError("simulation assumption values are invalid")
    for field in ("annual_return_mean", "annual_return_volatility", "annual_inflation_rate"):
        rate = values[field]
        # bool 은 int 의 서브클래스라 따로 막는다. 모듈도 같은 이유로 bool 을 거부한다.
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not isfinite(rate):
            raise SimulationPolicyError(f"simulation assumption {field} is invalid")
    for field in ("num_paths", "seed"):
        count = values[field]
        if isinstance(count, bool) or not isinstance(count, int):
            raise SimulationPolicyError(f"simulation assumption {field} is invalid")
    if values["num_paths"] < 1:
        raise SimulationPolicyError("simulation assumption num_paths is invalid")
    if values["seed"] < 0:
        raise SimulationPolicyError("simulation assumption seed is invalid")

    scenarios = payload["max_comparison_scenarios"]
    if isinstance(scenarios, bool) or not isinstance(scenarios, int) or scenarios < 1:
        raise SimulationPolicyError("simulation comparison limit is invalid")

    return payload


def active_simulation_policy_version() -> str:
    return str(load_simulation_policy()["policy_version"])


def simulation_assumption_values() -> dict[str, Any]:
    """호출할 때마다 새 dict 를 준다.

    lru_cache 가 같은 객체를 돌려주므로 사본을 만들지 않으면 호출부가 캐시를
    직접 수정할 수 있다. 이 값은 모듈로 그대로 전달되는 계산 가정이라 공유하면 안 된다.
    """
    return dict(load_simulation_policy()["assumption_values"])
