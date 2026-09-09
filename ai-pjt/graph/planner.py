"""Context-aware, bounded task planning for the F03 execution graph."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from core.context import ContextWindow
from core.step_registry import load_step_candidates
from graph.router import TaskClassifier, select_route
from tools.financial_policy import REFERENCE_VALUE_INPUT_SCHEMA
from tools.registry import validate_json_schema


MAX_PLAN_TASKS = 3
PLAN_SCHEMA_VERSION = "1.0"

TaskPlanner = Callable[..., Mapping[str, Any]]


TASK_TYPE_CONTRACTS: dict[str, dict[str, Any]] = {
    "answer_question": {
        "executor_kind": "agent",
        "produces": "text_response",
        "binding_expectations": {
            "value": {
                "artifact_type": "reference_value",
                "path": "value",
                "value_type": "number",
            },
            "unit": {
                "artifact_type": "reference_value",
                "path": "unit",
                "value_type": "string",
            },
            "period": {
                "artifact_type": "reference_value",
                "path": "period",
                "value_type": "string",
            },
            "population": {
                "artifact_type": "reference_value",
                "path": "population",
                "value_type": "string",
            },
            "source_id": {
                "artifact_type": "reference_value",
                "path": "source_id",
                "value_type": "string",
            },
            "as_of": {
                "artifact_type": "reference_value",
                "path": "as_of",
                "value_type": "string",
            },
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
            "value": {
                "artifact_type": "reference_value",
                "path": "value",
                "value_type": "number",
            },
            "unit": {
                "artifact_type": "reference_value",
                "path": "unit",
                "value_type": "string",
            },
            "source_id": {
                "artifact_type": "reference_value",
                "path": "source_id",
                "value_type": "string",
            },
            "as_of": {
                "artifact_type": "reference_value",
                "path": "as_of",
                "value_type": "string",
            },
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
    "fallback": {
        "executor_kind": "agent",
        "produces": "text_response",
        "binding_expectations": {},
    },
    "lookup_reference_value": {
        "executor_kind": "tool",
        "capability": "lookup_reference_value",
        "produces": "reference_value",
        "binding_expectations": {},
        "argument_schema": REFERENCE_VALUE_INPUT_SCHEMA,
    },
    "lookup_retirement_gap": {
        "executor_kind": "tool",
        "capability": "get_dashboard_metric",
        "produces": "dashboard_metric",
        "binding_expectations": {},
        "fixed_arguments": {"metric_id": "retirement_gap"},
    },
    "answer_dashboard_metric": {
        "executor_kind": "agent",
        "produces": "text_response",
        "binding_expectations": {
            "metric_id": {
                "artifact_type": "dashboard_metric",
                "path": "metric_id",
                "value_type": "string",
            },
            "value": {
                "artifact_type": "dashboard_metric",
                "path": "value",
                "value_type": "number",
                "unit": "KRW",
            },
            "unit": {
                "artifact_type": "dashboard_metric",
                "path": "unit",
                "value_type": "string",
            },
            "report_id": {
                "artifact_type": "dashboard_metric",
                "path": "report_id",
                "value_type": "number",
            },
            "report_version": {
                "artifact_type": "dashboard_metric",
                "path": "report_version",
                "value_type": "number",
            },
            "calculation_policy_version": {
                "artifact_type": "dashboard_metric",
                "path": "calculation_policy_version",
                "value_type": "string",
            },
            "generated_at": {
                "artifact_type": "dashboard_metric",
                "path": "generated_at",
                "value_type": "string",
            },
        },
    },
}

TASK_FIELDS = {"id", "type", "description", "depends_on", "bindings", "arguments"}
FORBIDDEN_PLANNER_FIELDS = {
    "agent",
    "executor",
    "executor_kind",
    "handler",
    "produces",
    "tool",
}


PLANNER_PROMPT = """\
사용자 메시지를 실행 가능한 task plan으로 분해하세요.
반드시 {{"tasks": [...]}} JSON object만 반환하세요.

제약:
- task는 1개 이상 {max_tasks}개 이하
- 원문에 나타난 독립적인 사용자 목표만 task로 작성
- task type은 다음 중 하나만 사용: {task_types}
- task id는 plan 안에서 참조할 임시 이름이며 중복 금지
- depends_on은 반드시 앞에 선언한 task만 참조
- 선행 결과 사용은 bindings의 task_id와 path로 명시
- 실행 결과 값, Agent 이름, 도구 handler 이름은 생성하지 않음

각 task의 허용 field:
- id: plan 안에서만 쓰는 고유 문자열
- type: 허용 task type 중 하나
- description: 원문 중 이 task가 수행할 구체적인 요청
- depends_on: 앞서 선언한 task id 문자열 배열
- bindings: 입력 이름을 {{"task_id": "선행 id", "path": "artifact field"}}에 연결한 object
- arguments: lookup_reference_value에만 필요한 object. reference_id, age 정수, period(monthly|annual|current), population을 정확히 지정

의존 요청 예시:
{{"tasks": [
  {{"id": "lookup", "type": "lookup_reference_value", "description": "승인된 기준값 조회", "depends_on": [], "bindings": {{}}, "arguments": {{"reference_id": "debt_service_ratio_max", "age": 40, "period": "annual", "population": "retirewise_kr_households"}}}},
  {{"id": "fill", "type": "propose_form_change", "description": "조회 값으로 변경안 제안", "depends_on": ["lookup"], "bindings": {{"value": {{"task_id": "lookup", "path": "value"}}}}}}
]}}

현재 단계: {step_id}
제한된 대화 문맥: {context}
사용자 메시지: {message}
"""


@dataclass(frozen=True)
class PlanIssue:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


@dataclass(frozen=True)
class RoutePlanValidation:
    valid: bool
    plan: dict[str, Any]
    issues: tuple[PlanIssue, ...]


def context_window_payload(window: ContextWindow) -> dict[str, Any]:
    """Expose only F02's bounded model projection to the planner."""

    return {
        "turns": [turn.to_state() for turn in window.turns],
        "summary": window.summary.to_state() if window.summary else {},
        "confirmed_facts": deepcopy(dict(window.confirmed_facts)),
        "input_units": window.input_units,
    }


def validate_route_plan(
    raw_plan: Mapping[str, Any],
    *,
    step_id: str,
) -> RoutePlanValidation:
    """Normalize a model plan into canonical ids and reject unsafe topology."""

    issues: list[PlanIssue] = []
    if not isinstance(raw_plan, Mapping):
        return _invalid_plan(
            [_issue("INVALID_PLAN", "plan", "route plan은 object여야 합니다.")]
        )
    if set(raw_plan) != {"tasks"}:
        issues.append(
            _issue(
                "INVALID_PLAN_FIELDS",
                "plan",
                "route plan root에는 tasks만 허용됩니다.",
            )
        )

    raw_tasks = raw_plan.get("tasks")
    if not isinstance(raw_tasks, list) or not 1 <= len(raw_tasks) <= MAX_PLAN_TASKS:
        issues.append(
            _issue(
                "INVALID_TASK_COUNT",
                "plan.tasks",
                f"task는 1개 이상 {MAX_PLAN_TASKS}개 이하여야 합니다.",
            )
        )
        return _invalid_plan(issues)

    aliases: list[str] = []
    for index, raw_task in enumerate(raw_tasks):
        if not isinstance(raw_task, Mapping):
            issues.append(
                _issue(
                    "INVALID_TASK",
                    f"plan.tasks[{index}]",
                    "task는 object여야 합니다.",
                )
            )
            aliases.append(f"invalid-{index}")
            continue
        alias = raw_task.get("id")
        if not isinstance(alias, str) or not alias.strip():
            issues.append(
                _issue(
                    "INVALID_TASK_ALIAS",
                    f"plan.tasks[{index}].id",
                    "planner task에는 참조용 id가 필요합니다.",
                )
            )
            alias = f"invalid-{index}"
        aliases.append(alias)
    if len(aliases) != len(set(aliases)):
        issues.append(
            _issue(
                "DUPLICATE_TASK_ALIAS",
                "plan.tasks",
                "planner task id는 중복될 수 없습니다.",
            )
        )

    alias_to_id = {alias: f"task-{index + 1}" for index, alias in enumerate(aliases)}
    normalized_tasks: list[dict[str, Any]] = []
    for index, raw_task in enumerate(raw_tasks):
        if not isinstance(raw_task, Mapping):
            continue
        path = f"plan.tasks[{index}]"
        unknown = set(raw_task) - TASK_FIELDS
        forbidden = set(raw_task) & FORBIDDEN_PLANNER_FIELDS
        if unknown:
            issues.append(
                _issue(
                    "UNKNOWN_TASK_FIELDS",
                    path,
                    f"허용되지 않은 task field입니다: {', '.join(sorted(unknown))}",
                )
            )
        if forbidden:
            issues.append(
                _issue(
                    "PLANNER_EXECUTOR_INJECTION",
                    path,
                    "planner는 executor나 produces를 지정할 수 없습니다.",
                )
            )

        task_type = raw_task.get("type")
        contract = TASK_TYPE_CONTRACTS.get(str(task_type))
        if contract is None:
            issues.append(
                _issue(
                    "UNSUPPORTED_TASK_TYPE",
                    f"{path}.type",
                    "지원하지 않는 task type입니다.",
                )
            )
            continue
        description = raw_task.get("description")
        if not isinstance(description, str) or not description.strip():
            issues.append(
                _issue(
                    "INVALID_TASK_DESCRIPTION",
                    f"{path}.description",
                    "task description은 비어 있지 않아야 합니다.",
                )
            )

        argument_schema = contract.get("argument_schema")
        fixed_arguments = contract.get("fixed_arguments")
        raw_arguments = raw_task.get("arguments")
        normalized_arguments: dict[str, Any] | None = None
        if isinstance(argument_schema, Mapping):
            argument_errors = validate_json_schema(argument_schema, raw_arguments)
            if argument_errors:
                issues.append(
                    _issue(
                        "INVALID_TOOL_ARGUMENTS",
                        f"{path}.arguments",
                        "tool task arguments가 입력 계약과 일치하지 않습니다.",
                    )
                )
            elif isinstance(raw_arguments, Mapping):
                normalized_arguments = deepcopy(dict(raw_arguments))
        elif isinstance(fixed_arguments, Mapping):
            if "arguments" in raw_task:
                issues.append(
                    _issue(
                        "PLANNER_FIXED_ARGUMENT_INJECTION",
                        f"{path}.arguments",
                        "이 tool task의 인자는 runtime 정책으로 고정됩니다.",
                    )
                )
            normalized_arguments = deepcopy(dict(fixed_arguments))
        elif "arguments" in raw_task:
            issues.append(
                _issue(
                    "UNEXPECTED_TASK_ARGUMENTS",
                    f"{path}.arguments",
                    "이 task type은 arguments를 허용하지 않습니다.",
                )
            )

        raw_dependencies = raw_task.get("depends_on", [])
        if not isinstance(raw_dependencies, list) or any(
            not isinstance(item, str) for item in raw_dependencies
        ):
            issues.append(
                _issue(
                    "INVALID_DEPENDENCIES",
                    f"{path}.depends_on",
                    "depends_on은 task id 문자열 배열이어야 합니다.",
                )
            )
            raw_dependencies = []
        if len(raw_dependencies) != len(set(raw_dependencies)):
            issues.append(
                _issue(
                    "DUPLICATE_DEPENDENCY",
                    f"{path}.depends_on",
                    "같은 dependency를 중복 선언할 수 없습니다.",
                )
            )

        earlier_aliases = set(aliases[:index])
        for dependency in raw_dependencies:
            if dependency not in alias_to_id:
                code = "UNKNOWN_DEPENDENCY"
                message = "plan에 없는 task를 참조했습니다."
            elif dependency not in earlier_aliases:
                code = "FORWARD_OR_CYCLIC_DEPENDENCY"
                message = "dependency는 앞에 선언한 task만 참조할 수 있습니다."
            else:
                continue
            issues.append(_issue(code, f"{path}.depends_on", message))

        raw_bindings = raw_task.get("bindings", {})
        if not isinstance(raw_bindings, Mapping):
            issues.append(
                _issue(
                    "INVALID_BINDINGS",
                    f"{path}.bindings",
                    "bindings는 object여야 합니다.",
                )
            )
            raw_bindings = {}

        normalized_bindings: dict[str, dict[str, str]] = {}
        expectations = contract["binding_expectations"]
        for input_name, binding in raw_bindings.items():
            if input_name not in expectations:
                issues.append(
                    _issue(
                        "UNKNOWN_BINDING_INPUT",
                        f"{path}.bindings.{input_name}",
                        "task type이 허용하지 않는 binding 입력입니다.",
                    )
                )
                continue
            if not isinstance(binding, Mapping) or set(binding) != {"task_id", "path"}:
                issues.append(
                    _issue(
                        "INVALID_BINDING",
                        f"{path}.bindings.{input_name}",
                        "binding은 task_id와 path만 포함해야 합니다.",
                    )
                )
                continue
            source_alias = binding.get("task_id")
            source_path = binding.get("path")
            if source_alias not in raw_dependencies:
                issues.append(
                    _issue(
                        "UNDECLARED_BINDING_DEPENDENCY",
                        f"{path}.bindings.{input_name}.task_id",
                        "binding source는 depends_on에도 선언해야 합니다.",
                    )
                )
                continue
            if source_alias not in earlier_aliases:
                continue
            source_index = aliases.index(str(source_alias))
            source_raw = raw_tasks[source_index]
            source_contract = (
                TASK_TYPE_CONTRACTS.get(str(source_raw.get("type")))
                if isinstance(source_raw, Mapping)
                else None
            )
            expectation = expectations[input_name]
            if source_contract is None or (
                source_contract["produces"] != expectation["artifact_type"]
            ):
                issues.append(
                    _issue(
                        "BINDING_ARTIFACT_MISMATCH",
                        f"{path}.bindings.{input_name}",
                        "source task artifact가 consumer 입력과 맞지 않습니다.",
                    )
                )
                continue
            if source_path != expectation["path"]:
                issues.append(
                    _issue(
                        "BINDING_PATH_MISMATCH",
                        f"{path}.bindings.{input_name}.path",
                        "binding path가 task 입력 계약과 맞지 않습니다.",
                    )
                )
                continue
            normalized_bindings[input_name] = {
                "task_id": alias_to_id[str(source_alias)],
                "path": str(source_path),
            }

        normalized = {
            "id": f"task-{index + 1}",
            "type": task_type,
            "description": description,
            "depends_on": [
                alias_to_id[item]
                for item in raw_dependencies
                if item in earlier_aliases
            ],
            "bindings": normalized_bindings,
            "executor_kind": contract["executor_kind"],
            "produces": contract["produces"],
            "step_id": step_id,
        }
        if contract["executor_kind"] == "tool":
            normalized["capability"] = contract["capability"]
            normalized["arguments"] = normalized_arguments or {}
        normalized_tasks.append(normalized)

    if issues:
        return _invalid_plan(issues)
    return RoutePlanValidation(
        valid=True,
        plan={"schema_version": PLAN_SCHEMA_VERSION, "tasks": normalized_tasks},
        issues=(),
    )


def create_route_plan(
    *,
    message: str,
    step_id: str,
    context_window: ContextWindow,
    planner: TaskPlanner,
    classifier: TaskClassifier,
    step_candidates: Mapping[str, Sequence[str]] | None = None,
    registered_tool_capabilities: Sequence[str] = (),
    plan_id: str | None = None,
) -> RoutePlanValidation:
    """Plan with bounded context, validate, then route every canonical task."""

    context = context_window_payload(context_window)
    try:
        raw_plan = planner(message=message, step_id=step_id, context=context)
    except Exception:
        return _invalid_plan([
            _issue("PLANNER_ERROR", "plan", "planner가 실행 계획을 만들지 못했습니다.")
        ])

    validation = validate_route_plan(raw_plan, step_id=step_id)
    if not validation.valid:
        return validation

    active_candidates = step_candidates or load_step_candidates()
    planned = deepcopy(validation.plan)
    planned["plan_id"] = plan_id or f"plan-{uuid4()}"
    for task in planned["tasks"]:
        task["route"] = select_route(
            task=task,
            step_id=step_id,
            step_candidates=active_candidates,
            classifier=classifier,
            registered_tool_capabilities=registered_tool_capabilities,
        )
    return RoutePlanValidation(valid=True, plan=planned, issues=())


def plan_tasks_with_model(
    *,
    message: str,
    step_id: str,
    context: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Production planner adapter; deterministic validation remains separate."""

    from langchain_core.messages import HumanMessage, SystemMessage

    from core.llm import llm

    system = SystemMessage(content=PLANNER_PROMPT.format(
        max_tasks=MAX_PLAN_TASKS,
        task_types=", ".join(sorted(TASK_TYPE_CONTRACTS)),
        step_id=step_id,
        context=json.dumps(context, ensure_ascii=False, sort_keys=True),
        message=message,
    ))
    response = llm.invoke([system, HumanMessage(content=message)])
    content = response.content if isinstance(response.content, str) else ""
    payload = json.loads(content)
    if not isinstance(payload, dict):
        raise ValueError("planner response must be an object")
    return payload


def binding_expectations_for(task: Mapping[str, Any]) -> Mapping[str, Mapping[str, Any]]:
    contract = TASK_TYPE_CONTRACTS.get(str(task.get("type")))
    if contract is None:
        return {}
    return deepcopy(contract["binding_expectations"])


def _invalid_plan(issues: list[PlanIssue]) -> RoutePlanValidation:
    return RoutePlanValidation(
        valid=False,
        plan={"schema_version": PLAN_SCHEMA_VERSION, "tasks": []},
        issues=tuple(issues),
    )


def _issue(code: str, path: str, message: str) -> PlanIssue:
    return PlanIssue(code=code, path=path, message=message)
