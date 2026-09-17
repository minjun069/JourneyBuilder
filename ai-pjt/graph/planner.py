"""Context-aware, bounded task planning for the F03 execution graph."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from core.context import ContextWindow
from core.model_boundary import (
    ModelPurpose,
    secure_structured_invoke,
    secure_system_prompt,
)
from core.observability_masking import (
    exception_identity_fields,
    masked_exception_fields,
)
from core.step_registry import load_step_candidates
from graph.elicit_context import ELICIT_TURN_KINDS, FIELD_ID_SET
from graph.router import TaskClassifier, select_route
from tools.financial_policy import REFERENCE_VALUE_INPUT_SCHEMA
from tools.registry import validate_json_schema


MAX_PLAN_TASKS = 3
PLAN_SCHEMA_VERSION = "1.0"
HOUSEHOLD_WRITABLE_REFERENCE_PREFIX = "household_monthly_expense_"

TaskPlanner = Callable[..., Mapping[str, Any]]
logger = logging.getLogger(__name__)


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
            "reference_id": {
                "artifact_type": "reference_value",
                "path": "reference_id",
                "value_type": "string",
            },
            "age": {
                "artifact_type": "reference_value",
                "path": "age",
                "value_type": "number",
            },
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
            "comparator": {
                "artifact_type": "reference_value",
                "path": "comparator",
                "value_type": "string",
            },
            "source_id": {
                "artifact_type": "reference_value",
                "path": "source_id",
                "value_type": "string",
            },
            "source_version": {
                "artifact_type": "reference_value",
                "path": "source_version",
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
        "tool_name": "get_approved_reference_value",
        "authorized_agent": "qa",
        "produces": "reference_value",
        "binding_expectations": {},
        "argument_schema": REFERENCE_VALUE_INPUT_SCHEMA,
    },
    "lookup_retirement_gap": {
        "executor_kind": "tool",
        "capability": "get_dashboard_metric",
        "tool_name": "get_dashboard_metric",
        "authorized_agent": "qa",
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


PLANNER_PROMPT = secure_system_prompt("""\
사용자 메시지를 실행 가능한 task plan으로 분해하세요.
지정된 구조화 출력 스키마에 맞는
{{"elicit_routing": {{...}}, "tasks": [...]}} object를 반환하세요.

제약:
- task는 1개 이상 {max_tasks}개 이하
- 원문에 나타난 독립적인 사용자 목표만 task로 작성
- task type은 다음 중 하나만 사용: {task_types}
- task id는 plan 안에서 참조할 임시 이름이며 중복 금지
- depends_on은 반드시 앞에 선언한 task만 참조
- 선행 결과 사용은 bindings의 task_id와 path로 명시
- 실행 결과 값, Agent 이름, 도구 handler 이름은 생성하지 않음
- 용어의 뜻·기준·기간·선택지 설명만 묻는 요청은 answer_question 하나로 계획
- 예: "단기 지향이면 어느정도 기간을 말하지?", "안정추구란 정확히 뭘 말하는거야?"
  는 모두 answer_question 하나이며 입력 변경 task가 아님
- 제한된 대화 문맥의 elicit_context_index를 보고 현재 발화가 정성정보 수집과
  관련되는지, 대상 field, new|answer|correction|confirmation 발화 종류, 판단
  신뢰도가 충분한지를 elicit_routing에 항상 명시
- 정성정보와 무관하면 is_related=false, field=null, turn_kind=null,
  confidence_sufficient=false
- 관련되지만 대상이나 발화 종류가 불확실하면 is_related=true,
  field 또는 turn_kind를 null로 둘 수 있으며 confidence_sufficient=false로 두고
  collect_qualitative_info task를 만들지 않음
- is_related=true이고 confidence_sufficient=true일 때만 대상 field 하나에 대한
  collect_qualitative_info task를 정확히 하나 생성
- 제한된 대화 문맥은 confirmed_facts, structured_state, summary,
  retrieved_memories, turns 순서의 권위 계층임
- 금액·소득·자산·부채·연금·폼 값은 confirmed_facts만 현재 권위 원천이며,
  summary·retrieved_memories·turns의 과거 값으로 복원하거나 덮어쓰지 않음
- retrieved_memories는 현재 요청 해석을 돕는 참고 자료이며 사실 권위 원천이 아님

각 task의 허용 field:
- id: plan 안에서만 쓰는 고유 문자열
- type: 허용 task type 중 하나
- description: 원문 중 이 task가 수행할 구체적인 요청
- depends_on: 앞서 선언한 task id 문자열 배열
- bindings: 입력 이름을 {{"task_id": "선행 id", "path": "artifact field"}}에 연결한 object
- arguments: lookup_reference_value에만 필요한 object. reference_id, age 정수, period(monthly|annual|current), population을 정확히 지정

의존 요청 예시:
{{"elicit_routing": {{"is_related": false, "field": null, "turn_kind": null, "confidence_sufficient": false}}, "tasks": [
  {{"id": "lookup", "type": "lookup_reference_value", "description": "승인된 기준값 조회", "depends_on": [], "bindings": {{}}, "arguments": {{"reference_id": "debt_service_ratio_max", "age": 40, "period": "annual", "population": "retirewise_kr_households"}}}},
  {{"id": "fill", "type": "propose_form_change", "description": "사용자가 요청한 조회 값으로 기존 빈 항목 변경안 제안", "depends_on": ["lookup"], "bindings": {{"reference_id": {{"task_id": "lookup", "path": "reference_id"}}, "age": {{"task_id": "lookup", "path": "age"}}, "value": {{"task_id": "lookup", "path": "value"}}, "unit": {{"task_id": "lookup", "path": "unit"}}, "period": {{"task_id": "lookup", "path": "period"}}, "population": {{"task_id": "lookup", "path": "population"}}, "comparator": {{"task_id": "lookup", "path": "comparator"}}, "source_id": {{"task_id": "lookup", "path": "source_id"}}, "source_version": {{"task_id": "lookup", "path": "source_version"}}, "as_of": {{"task_id": "lookup", "path": "as_of"}}, "calculation_policy_version": {{"task_id": "lookup", "path": "calculation_policy_version"}}}}}}
]}}

현재 단계: {step_id}
제한된 대화 문맥: {context}
사용자 메시지: {message}
""")


class PlannerBindingOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    path: str


class PlannerTaskOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    type: str
    description: str
    depends_on: list[str] = Field(default_factory=list)
    bindings: dict[str, PlannerBindingOutput] = Field(default_factory=dict)
    arguments: dict[str, Any] | None = None


class PlannerElicitRoutingOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_related: bool
    field: str | None = None
    turn_kind: str | None = None
    confidence_sufficient: bool


class PlannerModelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    elicit_routing: PlannerElicitRoutingOutput = Field(
        default_factory=lambda: PlannerElicitRoutingOutput(
            is_related=False,
            field=None,
            turn_kind=None,
            confidence_sufficient=False,
        )
    )
    tasks: list[PlannerTaskOutput]

    def to_route_plan(self) -> dict[str, Any]:
        """Remove provider-added empty optionals before policy validation."""

        payload = self.model_dump(mode="json")
        for task in payload["tasks"]:
            if task.get("arguments") in ({}, None):
                task.pop("arguments")
        return payload


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
    diagnostics: tuple[dict[str, str], ...] = ()


def context_window_payload(window: ContextWindow) -> dict[str, Any]:
    """Expose only F02's bounded model projection to the planner."""

    return {
        "confirmed_facts": deepcopy(dict(window.confirmed_facts)),
        "structured_state": window.structured_state.to_state(),
        "summary": window.summary.to_state() if window.summary else {},
        "retrieved_memories": [
            memory.to_state() for memory in window.retrieved_memories
        ],
        "turns": [turn.to_state() for turn in window.turns],
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
    if set(raw_plan) - {"tasks", "elicit_routing"}:
        issues.append(
            _issue(
                "INVALID_PLAN_FIELDS",
                "plan",
                "route plan root에는 tasks와 elicit_routing만 허용됩니다.",
            )
        )
    normalized_elicit_routing = _validate_elicit_routing(
        raw_plan.get("elicit_routing"),
        issues,
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

        if task_type == "propose_form_change":
            household_sources = [
                dependency
                for dependency in raw_dependencies
                if dependency in earlier_aliases
                and _raw_reference_id(
                    raw_tasks[aliases.index(dependency)]
                ).startswith(HOUSEHOLD_WRITABLE_REFERENCE_PREFIX)
            ]
            complete = set(normalized_bindings) == set(expectations)
            same_source = (
                len(household_sources) == 1
                and all(
                    isinstance(raw_bindings.get(field), Mapping)
                    and raw_bindings[field].get("task_id") == household_sources[0]
                    for field in expectations
                )
            )
            if household_sources and (not complete or not same_source):
                issues.append(_issue(
                    "INCOMPLETE_REFERENCE_BINDING",
                    f"{path}.bindings",
                    "쓰기 가능한 가구 기준값은 봉인 artifact의 전체 provenance를 같은 dependency에서 binding해야 합니다.",
                ))

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
            normalized["tool_name"] = contract["tool_name"]
            normalized["authorized_agent"] = contract["authorized_agent"]
            normalized["arguments"] = normalized_arguments or {}
        normalized_tasks.append(normalized)

    collect_task_count = sum(
        task.get("type") == "collect_qualitative_info"
        for task in normalized_tasks
    )
    executable_elicit = bool(
        normalized_elicit_routing
        and normalized_elicit_routing["is_related"]
        and normalized_elicit_routing["confidence_sufficient"]
    )
    if collect_task_count and not executable_elicit:
        issues.append(_issue(
            "ELICIT_ROUTING_REQUIRED",
            "plan.elicit_routing",
            "정성정보 task에는 신뢰도 충분한 대상 field와 발화 종류가 필요합니다.",
        ))
    if executable_elicit and collect_task_count != 1:
        issues.append(_issue(
            "ELICIT_TASK_MISMATCH",
            "plan.tasks",
            "실행 가능한 Elicit 판단에는 정성정보 task가 정확히 하나 필요합니다.",
        ))

    if issues:
        return _invalid_plan(issues)
    normalized_plan = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "tasks": normalized_tasks,
    }
    if normalized_elicit_routing is not None:
        normalized_plan["elicit_routing"] = normalized_elicit_routing
    return RoutePlanValidation(
        valid=True,
        plan=normalized_plan,
        issues=(),
    )


def _validate_elicit_routing(
    raw: Any,
    issues: list[PlanIssue],
) -> dict[str, Any] | None:
    if raw is None:
        return None
    path = "plan.elicit_routing"
    required = {
        "is_related",
        "field",
        "turn_kind",
        "confidence_sufficient",
    }
    if not isinstance(raw, Mapping) or set(raw) != required:
        issues.append(_issue(
            "INVALID_ELICIT_ROUTING",
            path,
            "elicit_routing은 관련성·field·발화 종류·신뢰도 충분 여부만 포함해야 합니다.",
        ))
        return None
    is_related = raw.get("is_related")
    confidence_sufficient = raw.get("confidence_sufficient")
    field_id = raw.get("field")
    turn_kind = raw.get("turn_kind")
    if not isinstance(is_related, bool) or not isinstance(confidence_sufficient, bool):
        issues.append(_issue(
            "INVALID_ELICIT_ROUTING_FLAGS",
            path,
            "Elicit 관련성과 신뢰도 충분 여부는 boolean이어야 합니다.",
        ))
        return None
    if not is_related:
        if field_id is not None or turn_kind is not None or confidence_sufficient:
            issues.append(_issue(
                "INCONSISTENT_ELICIT_ROUTING",
                path,
                "무관한 발화에는 Elicit 대상·발화 종류·충분 신뢰도를 지정할 수 없습니다.",
            ))
            return None
    else:
        field_is_valid = field_id is None or field_id in FIELD_ID_SET
        turn_kind_is_valid = turn_kind is None or turn_kind in ELICIT_TURN_KINDS
        if not field_is_valid or not turn_kind_is_valid:
            issues.append(_issue(
                "UNKNOWN_ELICIT_TARGET",
                path,
                "Elicit 대상과 발화 종류는 null 또는 허용된 값이어야 합니다.",
            ))
            return None
        if confidence_sufficient and (field_id is None or turn_kind is None):
            issues.append(_issue(
                "INCOMPLETE_ELICIT_TARGET",
                path,
                "충분한 신뢰도에는 대상 field와 발화 종류가 모두 필요합니다.",
            ))
            return None
    return {
        "is_related": is_related,
        "field": field_id,
        "turn_kind": turn_kind,
        "confidence_sufficient": confidence_sufficient,
    }


def _raw_reference_id(task: Any) -> str:
    if not isinstance(task, Mapping) or task.get("type") != "lookup_reference_value":
        return ""
    arguments = task.get("arguments")
    if not isinstance(arguments, Mapping):
        return ""
    value = arguments.get("reference_id")
    return value if isinstance(value, str) else ""


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
    elicit_context_index: Mapping[str, Any] | None = None,
) -> RoutePlanValidation:
    """Plan with bounded context, validate, then route every canonical task."""

    context = context_window_payload(context_window)
    context["elicit_context_index"] = deepcopy(dict(elicit_context_index or {}))
    diagnostics: list[dict[str, str]] = []
    try:
        raw_plan = planner(message=message, step_id=step_id, context=context)
    except Exception as error:
        diagnostic = {
            "component": "planner",
            **exception_identity_fields(error),
        }
        logger.error(
            "planner model adapter raised",
            extra={
                "event": "planner_error",
                "detail": masked_exception_fields(
                    error,
                    step_id=step_id,
                ),
            },
        )
        if not _looks_like_explanatory_question(message):
            return _invalid_plan(
                [
                    _issue(
                        "PLANNER_ERROR",
                        "plan",
                        "planner가 실행 계획을 만들지 못했습니다.",
                    )
                ],
                diagnostics=(diagnostic,),
            )
        diagnostic["recovery"] = "single_qa_plan"
        diagnostics.append(diagnostic)
        raw_plan = {
            "tasks": [{
                "id": "answer",
                "type": "answer_question",
                "description": message,
                "depends_on": [],
                "bindings": {},
            }],
        }

    validation = validate_route_plan(raw_plan, step_id=step_id)
    if not validation.valid:
        return RoutePlanValidation(
            valid=False,
            plan=validation.plan,
            issues=validation.issues,
            diagnostics=tuple(diagnostics),
        )

    active_candidates = step_candidates or load_step_candidates()
    planned = deepcopy(validation.plan)
    planned["plan_id"] = plan_id or f"plan-{uuid4()}"
    for task in planned["tasks"]:
        task_id = str(task.get("id", "task-unknown"))

        def collect_router_diagnostic(
            diagnostic: Mapping[str, str],
            *,
            current_task_id: str = task_id,
        ) -> None:
            diagnostics.append({
                "component": "router_classifier",
                "task_id": current_task_id,
                **dict(diagnostic),
            })

        task["route"] = select_route(
            task=task,
            step_id=step_id,
            step_candidates=active_candidates,
            classifier=classifier,
            registered_tool_capabilities=registered_tool_capabilities,
            diagnostic_sink=collect_router_diagnostic,
        )
    return RoutePlanValidation(
        valid=True,
        plan=planned,
        issues=(),
        diagnostics=tuple(diagnostics),
    )


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
        # context_window_payload insertion order is the authority precedence:
        # Backend snapshot → structured state → summary → retrieval → recent raw.
        context=json.dumps(context, ensure_ascii=False),
        message=message,
    ))
    response = secure_structured_invoke(
        llm,
        PlannerModelOutput,
        [system, HumanMessage(content=message)],
        purpose=ModelPurpose.PLANNER,
        operation_name="planner.plan",
        sensitive_payload={"message": message, "context": dict(context)},
    )
    return response.to_route_plan()


def binding_expectations_for(task: Mapping[str, Any]) -> Mapping[str, Mapping[str, Any]]:
    contract = TASK_TYPE_CONTRACTS.get(str(task.get("type")))
    if contract is None:
        return {}
    return deepcopy(contract["binding_expectations"])


def _invalid_plan(
    issues: list[PlanIssue],
    *,
    diagnostics: tuple[dict[str, str], ...] = (),
) -> RoutePlanValidation:
    return RoutePlanValidation(
        valid=False,
        plan={"schema_version": PLAN_SCHEMA_VERSION, "tasks": []},
        issues=tuple(issues),
        diagnostics=diagnostics,
    )


def _issue(code: str, path: str, message: str) -> PlanIssue:
    return PlanIssue(code=code, path=path, message=message)


def _looks_like_explanatory_question(message: str) -> bool:
    compact = message.strip().replace(" ", "")
    if not compact:
        return False
    return (
        "?" in compact
        or compact.endswith(
            (
                "뭐야",
                "무엇인가요",
                "뜻이야",
                "말하는거야",
                "인가요",
                "나요",
                "까요",
            )
        )
        or any(
            token in compact
            for token in ("설명해줘", "알려줘", "무슨뜻", "어느정도기간")
        )
    )
