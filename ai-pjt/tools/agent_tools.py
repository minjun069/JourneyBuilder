"""Request-scoped access to shared tools from authorized agent subgraphs."""

from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from .financial_policy import load_approved_financial_policy
from .runtime import FunctionToolRuntime, RuntimeToolRequest


_BOUND_TOOLS: ContextVar[tuple[FunctionToolRuntime, str, Mapping[str, Any]] | None] = ContextVar(
    "agent_tools", default=None,
)


@contextmanager
def bind_agent_tools(runtime: FunctionToolRuntime | None, agent: str, payload: Mapping[str, Any]):
    """Keep trusted runtime context out of prompts and checkpoint state."""
    token = _BOUND_TOOLS.set((runtime, agent, payload) if runtime is not None else None)
    try:
        yield
    finally:
        _BOUND_TOOLS.reset(token)


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
    """Expose selection metadata only when an authorized agent has a runtime."""
    binding = _BOUND_TOOLS.get()
    if binding is None or binding[1] not in {"qa", "input_assist"}:
        return []
    options: dict[str, dict[str, Any]] = {}
    for item in load_approved_financial_policy()["reference_values"]:
        key = item["reference_id"]
        options.setdefault(key, {
            "reference_id": key,
            "label": _REFERENCE_LABELS.get(key, key.replace("_", " ")),
            "age_required": item["age_min"] != 0 or item["age_max"] is not None,
        })
    return list(options.values())


async def lookup_reference(reference_id: str, age: int | None) -> dict[str, Any] | None:
    """Resolve metadata; canonical runtime still owns authorization, budget and audit."""
    binding = _BOUND_TOOLS.get()
    if binding is None:
        return None
    runtime, agent, payload = binding
    if agent not in {"qa", "input_assist"}:
        return None
    matches = [
        item for item in load_approved_financial_policy()["reference_values"]
        if item["reference_id"] == reference_id
    ]
    if age is None and matches and all(
        item["age_min"] == 0 and item["age_max"] is None for item in matches
    ):
        age = 0
    if isinstance(age, bool) or not isinstance(age, int) or not 0 <= age <= 120:
        return None
    matches = [
        item for item in matches if item["age_min"] <= age
        and (item["age_max"] is None or age <= item["age_max"])
    ]
    if len(matches) != 1:
        return None
    match = matches[0]
    result = await runtime.invoke(RuntimeToolRequest(
        tool_name="get_approved_reference_value",
        arguments={
            "reference_id": reference_id, "age": age,
            "period": match["period"], "population": match["population"],
        },
        agent=agent,
    ), payload)
    if not result.ok or not isinstance(result.data, Mapping):
        return None
    reference = result.data.get("reference")
    return dict(reference) if isinstance(reference, Mapping) else None
