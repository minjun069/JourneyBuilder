"""Compile semantic requests into validated execution plans without model-made wiring."""

from collections.abc import Mapping
from typing import Any

from graph.plan_contracts import MAX_PLAN_TASKS
from graph.task_catalog import TASK_TYPE_CONTRACTS, binding_expectations_for
from tools.financial_policy import load_approved_financial_policy


_REFERENCE_LABELS = {
    "household_balance_max": "가계수지 비율 상한",
    "emergency_reserve_min": "비상예비자금 최소 개월 수",
    "debt_service_ratio_max": "부채상환 비율 상한",
    "total_savings_propensity_min": "총저축 비율 하한",
    "retirement_savings_share_min": "은퇴저축 비중 하한",
    "financial_asset_ratio_min": "금융자산 비율 하한",
    "insurance_preparation_range_min": "보험 준비 비율 하한",
    "insurance_preparation_range_max": "보험 준비 비율 상한",
    **{
        f"household_monthly_expense_{key}_avg": f"가구 월평균 {label}"
        for key, label in {
            "housing": "주거비", "food_living": "식비·생활비", "transportation": "교통비",
            "telecom": "통신비", "education": "교육비", "medical": "의료비",
            "leisure_culture": "여가·문화비", "dining_out": "외식비", "shopping": "쇼핑비",
            "insurance_premium": "보험료",
        }.items()
    },
}


def reference_options() -> list[dict[str, Any]]:
    """Only selection metadata, never reference values or provenance."""
    options: dict[str, dict[str, Any]] = {}
    for item in load_approved_financial_policy()["reference_values"]:
        key = item["reference_id"]
        options.setdefault(key, {
            "reference_id": key, "label": _REFERENCE_LABELS.get(key, key.replace("_", " ")),
            "period": item["period"],
        })
    return list(options.values())


def compile_request_plan(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Each request has at most one reference; the compiler owns every edge and path."""
    if set(raw) - {"status", "requests"}:
        raise ValueError("요청 계획에 실행 정보가 포함되어 있습니다.")
    status = raw.get("status")
    requests = raw.get("requests")
    if status not in {"executable", "ambiguous", "unsupported"} or not isinstance(requests, list):
        raise ValueError("요청 상태와 목록을 확인해 주세요.")
    if status != "executable":
        if requests:
            raise ValueError("실행하지 않는 요청에는 작업을 넣지 않습니다.")
        return {"status": status, "tasks": []}
    if not 1 <= len(requests) <= MAX_PLAN_TASKS:
        raise ValueError("같은 종류의 요청을 모아 최대 세 작업으로 정리해 주세요.")
    tasks: list[dict[str, Any]] = []
    lookups: dict[tuple[str, int, str, str], str] = {}
    for request in requests:
        if not isinstance(request, Mapping) or set(request) - {
            "type", "description", "reference_id", "reference_age",
        }:
            raise ValueError("작업 종류와 설명, 필요한 기준만 선택해 주세요.")
        kind, description = request.get("type"), request.get("description")
        contract = TASK_TYPE_CONTRACTS.get(kind)
        if not contract or contract["executor_kind"] != "agent":
            raise ValueError("제공된 상담 작업 중에서 선택해 주세요.")
        if not isinstance(description, str) or not description.strip():
            raise ValueError("처리할 요청의 조건을 description에 보존해 주세요.")
        dependency = None
        reference_id = request.get("reference_id")
        if reference_id:
            if kind not in {"answer_question", "propose_form_change"}:
                raise ValueError("이 작업에는 기준값 조회를 연결할 수 없습니다.")
            age = request.get("reference_age")
            matches = [
                item for item in load_approved_financial_policy()["reference_values"]
                if item["reference_id"] == reference_id
            ]
            if not matches:
                raise ValueError("제공된 기준 목록에 없는 항목입니다.")
            if age is None and all(item["age_min"] == 0 and item["age_max"] is None for item in matches):
                age = 0
            if isinstance(age, bool) or not isinstance(age, int) or not 0 <= age <= 120:
                raise ValueError("기준에 필요한 나이를 알 수 없으면 조회를 비우고 확인 질문을 남겨 주세요.")
            matches = [item for item in matches if item["age_min"] <= age and (
                item["age_max"] is None or age <= item["age_max"]
            )]
            if len(matches) != 1:
                raise ValueError("해당 나이에 적용할 기준을 하나로 확인할 수 없습니다.")
            match = matches[0]
            lookup_key = (reference_id, age, match["period"], match["population"])
            dependency = lookups.get(lookup_key)
            if dependency is None:
                dependency = f"task-{len(tasks) + 1}"
                tasks.append({
                    "id": dependency, "type": "lookup_reference_value",
                    "description": "요청한 공식 기준 확인", "depends_on": [], "bindings": {},
                    "arguments": dict(zip(("reference_id", "age", "period", "population"), lookup_key)),
                })
                lookups[lookup_key] = dependency
        task = {
            "id": f"task-{len(tasks) + 1}", "type": kind,
            "description": description.strip(),
            "depends_on": [dependency] if dependency else [],
            "bindings": {},
        }
        if dependency:
            task["bindings"] = {
                name: {"task_id": dependency, "path": spec["path"]}
                for name, spec in binding_expectations_for(task).items()
            }
        tasks.append(task)
    if len(tasks) > MAX_PLAN_TASKS:
        raise ValueError("기준 조회를 포함해 세 작업을 넘습니다. 같은 종류의 요청을 합쳐 주세요.")
    return {"status": status, "tasks": tasks}
