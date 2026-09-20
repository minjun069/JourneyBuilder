"""Canonical plan-time task, executor, tool, and binding contracts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from core.step_registry import load_step_candidates
from tools.financial_policy import REFERENCE_VALUE_INPUT_SCHEMA


TASK_TYPE_CONTRACTS: dict[str, dict[str, Any]] = {
    "answer_question": {
        "executor_kind": "agent",
        "produces": "text_response",
        "binding_expectations": {
            "value": {"artifact_type": "reference_value", "path": "value", "value_type": "number"},
            "unit": {"artifact_type": "reference_value", "path": "unit", "value_type": "string"},
            "period": {"artifact_type": "reference_value", "path": "period", "value_type": "string"},
            "population": {"artifact_type": "reference_value", "path": "population", "value_type": "string"},
            "source_id": {"artifact_type": "reference_value", "path": "source_id", "value_type": "string"},
            "as_of": {"artifact_type": "reference_value", "path": "as_of", "value_type": "string"},
            "calculation_policy_version": {
                "artifact_type": "reference_value",
                "path": "calculation_policy_version",
                "value_type": "string",
            },
        },
    },
    "propose_form_change": {
        "executor_kind": "agent",
        "produces": "form_change",
        "binding_expectations": {
            "reference_id": {"artifact_type": "reference_value", "path": "reference_id", "value_type": "string"},
            "age": {"artifact_type": "reference_value", "path": "age", "value_type": "number"},
            "value": {"artifact_type": "reference_value", "path": "value", "value_type": "number"},
            "unit": {"artifact_type": "reference_value", "path": "unit", "value_type": "string"},
            "period": {"artifact_type": "reference_value", "path": "period", "value_type": "string"},
            "population": {"artifact_type": "reference_value", "path": "population", "value_type": "string"},
            "comparator": {"artifact_type": "reference_value", "path": "comparator", "value_type": "string"},
            "source_id": {"artifact_type": "reference_value", "path": "source_id", "value_type": "string"},
            "source_version": {"artifact_type": "reference_value", "path": "source_version", "value_type": "string"},
            "as_of": {"artifact_type": "reference_value", "path": "as_of", "value_type": "string"},
            "calculation_policy_version": {
                "artifact_type": "reference_value",
                "path": "calculation_policy_version",
                "value_type": "string",
            },
        },
    },
    "review_validation_error": {
        "executor_kind": "agent",
        "produces": "text_response",
        "binding_expectations": {},
    },
    "interpret_chart": {
        "executor_kind": "agent",
        "produces": "text_response",
        "binding_expectations": {},
    },
    "collect_qualitative_info": {
        "executor_kind": "agent",
        "produces": "text_response",
        "binding_expectations": {},
    },
    "lookup_reference_value": {
        "executor_kind": "tool",
        "capability": "lookup_reference_value",
        "tool_name": "get_approved_reference_value",
        "authorized_agent": "qa",
        "produces": "reference_value",
        "binding_expectations": {},
        "argument_schema": REFERENCE_VALUE_INPUT_SCHEMA,
    },
}

TASK_TYPE_DEFAULT_AGENTS = {
    "answer_question": "qa",
    "propose_form_change": "input_assist",
    "review_validation_error": "error_review",
    "interpret_chart": "chart_interpret",
    "collect_qualitative_info": "elicit",
}

TASK_PLANNING_GUIDANCE: dict[str, str] = {
    "answer_question": "현재 화면·용어·제도·선택지 또는 확인된 값의 의미를 설명한다.",
    "propose_form_change": "사용자가 명시적으로 요청한 draft 입력 변경안만 만든다.",
    "review_validation_error": "유효한 draft의 오기·단위 착오·문맥 불일치를 자문한다.",
    "interpret_chart": "Backend가 검증한 Presentation chart payload만 설명한다.",
    "collect_qualitative_info": "과거 대화 문맥을 참고해 정성 질문에 자연어로 답한다.",
    "lookup_reference_value": "승인된 공식 기준값이 필요한 경우에만 등록된 조회 도구를 호출한다.",
}


def allowed_task_types_for(
    *,
    step_id: str,
    step_candidates: Mapping[str, Sequence[str]] | None = None,
    registered_tool_capabilities: Sequence[str] = (),
) -> tuple[str, ...]:
    """Return the trusted task menu for one runtime step."""

    active_steps = load_step_candidates() if step_candidates is None else step_candidates
    allowed_agents = set(active_steps.get(step_id, ()))
    allowed_tools = set(registered_tool_capabilities)
    allowed: list[str] = []
    for task_type, contract in TASK_TYPE_CONTRACTS.items():
        if contract["executor_kind"] == "tool":
            if contract.get("capability") in allowed_tools:
                allowed.append(task_type)
            continue
        if TASK_TYPE_DEFAULT_AGENTS.get(task_type) in allowed_agents:
            allowed.append(task_type)
    return tuple(sorted(allowed))


def binding_expectations_for(
    task: Mapping[str, Any],
) -> Mapping[str, Mapping[str, Any]]:
    contract = TASK_TYPE_CONTRACTS.get(str(task.get("type")))
    if contract is None:
        return {}
    return deepcopy(contract["binding_expectations"])


def planning_guidance_for(task_types: Sequence[str]) -> dict[str, str]:
    return {
        task_type: TASK_PLANNING_GUIDANCE[task_type]
        for task_type in sorted(set(task_types))
    }
