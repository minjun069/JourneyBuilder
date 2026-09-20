"""Context-aware, bounded task planning for the F03 execution graph."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.context import ContextWindow
from core.model_boundary import (
    ModelPurpose,
    prompt_messages,
    secure_structured_invoke,
)
from core.observability_masking import (
    exception_identity_fields,
    masked_exception_fields,
)
from tools.financial_policy import REFERENCE_VALUE_INPUT_SCHEMA
from tools.registry import validate_json_schema
from prompts import get_prompt


MAX_PLAN_TASKS = 3
PLAN_SCHEMA_VERSION = "1.0"
HOUSEHOLD_WRITABLE_REFERENCE_PREFIX = "household_monthly_expense_"

TaskPlanner = Callable[..., Mapping[str, Any]]
PlanEnricher = Callable[..., Mapping[str, Any]]
PlanEvaluator = Callable[..., Any]
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

TASK_PLANNING_GUIDANCE: dict[str, str] = {
    "answer_question": "현재 화면·용어·제도·선택지 또는 확인된 값의 의미를 설명한다.",
    "propose_form_change": "사용자가 명시적으로 요청한 draft 입력 변경안만 만든다.",
    "review_validation_error": "유효한 draft의 오기·단위 착오·문맥 불일치를 자문한다.",
    "interpret_chart": "Backend가 검증한 Presentation chart payload만 설명한다.",
    "collect_qualitative_info": "과거 대화 문맥을 참고해 정성 질문에 자연어로 답한다.",
    "lookup_reference_value": "승인된 공식 기준값이 필요한 경우에만 등록된 조회 도구를 호출한다.",
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


PLANNER_PROMPT = get_prompt("planner.plan")


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


class PlannerModelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["executable", "ambiguous", "unsupported"] = "executable"
    tasks: list[PlannerTaskOutput] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_status_contract(self) -> "PlannerModelOutput":
        if self.status == "executable" and not self.tasks:
            raise ValueError("executable planner output requires tasks")
        if self.status != "executable" and self.tasks:
            raise ValueError("non-executable planner output must not include tasks")
        return self

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
    retries_used: int = 0


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


def _validate_plan_structure(
    raw_plan: Any,
    *,
    step_id: str,
    allowed_task_types: Sequence[str] | None = None,
) -> RoutePlanValidation:
    """Normalize a model plan into canonical ids and reject unsafe topology."""

    issues: list[PlanIssue] = []
    if not isinstance(raw_plan, Mapping):
        return _invalid_plan(
            [_issue("INVALID_PLAN", "plan", "route plan은 object여야 합니다.")]
        )
    if set(raw_plan) - {"status", "tasks"}:
        issues.append(
            _issue(
                "INVALID_PLAN_FIELDS",
                "plan",
                "route plan root에는 status와 tasks만 허용됩니다.",
            )
        )
    status = raw_plan.get("status", "executable")
    if status not in {"executable", "ambiguous", "unsupported"}:
        issues.append(_issue(
            "INVALID_PLAN_STATUS",
            "plan.status",
            "planner status가 지원 계약과 일치하지 않습니다.",
        ))
    raw_tasks = raw_plan.get("tasks")
    if status in {"ambiguous", "unsupported"}:
        if raw_tasks != []:
            issues.append(_issue(
                "NON_EXECUTABLE_TASKS",
                "plan.tasks",
                "실행하지 않는 planner 결과에는 task가 없어야 합니다.",
            ))
        if issues:
            return _invalid_plan(issues)
        normalized_plan = {
            "schema_version": PLAN_SCHEMA_VERSION,
            "tasks": [],
            "outcome_reason": (
                "ambiguous_intent" if status == "ambiguous" else "unsupported_intent"
            ),
        }
        return RoutePlanValidation(valid=True, plan=normalized_plan, issues=())

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
        if allowed_task_types is not None and task_type not in set(allowed_task_types):
            issues.append(_issue(
                "TASK_TYPE_NOT_ALLOWED_FOR_STEP",
                f"{path}.type",
                "현재 단계와 실행 capability에서 허용되지 않은 task type입니다.",
            ))
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

    if issues:
        return _invalid_plan(issues)
    normalized_plan = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "tasks": normalized_tasks,
    }
    return RoutePlanValidation(
        valid=True,
        plan=normalized_plan,
        issues=(),
    )


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
    enricher: PlanEnricher,
    evaluator: PlanEvaluator,
    allowed_task_types: Sequence[str],
    registered_tool_capabilities: Sequence[str] = (),
) -> RoutePlanValidation:
    """Plan, enrich, and enter validation only through the supplied evaluator."""

    route_constraints = planner_route_constraints(
        step_id=step_id,
        allowed_task_types=allowed_task_types,
        registered_tool_capabilities=registered_tool_capabilities,
    )
    context = context_window_payload(context_window)
    context["route_constraints"] = route_constraints
    diagnostics: list[dict[str, str]] = []
    last_issues: tuple[PlanIssue, ...] = ()
    planner_failed = False

    for attempt in range(2):
        try:
            raw_plan = planner(message=message, step_id=step_id, context=context)
            planner_failed = False
        except Exception as error:
            planner_failed = True
            diagnostic = {
                "component": "planner",
                "attempt": str(attempt + 1),
                **exception_identity_fields(error),
            }
            diagnostics.append(diagnostic)
            logger.error(
                "planner model adapter raised",
                extra={
                    "event": "planner_error",
                    "detail": masked_exception_fields(error, step_id=step_id),
                },
            )
            last_issues = (
                _issue(
                    "PLANNER_ERROR",
                    "plan",
                    "planner가 실행 계획을 만들지 못했습니다.",
                ),
            )
            if attempt == 0:
                context["validation_feedback"] = [
                    issue.to_dict() for issue in last_issues
                ]
                continue
            break

        candidate = enricher(
            raw_plan=raw_plan,
            step_id=step_id,
            registered_tool_capabilities=registered_tool_capabilities,
        )
        decision = evaluator(candidate)
        if decision.allowed:
            return RoutePlanValidation(
                valid=True,
                plan=deepcopy(decision.plan),
                issues=(),
                diagnostics=tuple(diagnostics),
                retries_used=attempt,
            )

        last_issues = tuple(
            _issue(violation.code, violation.path, violation.message)
            for violation in decision.violations
        )
        if attempt == 0:
            diagnostics.append({
                "component": "planner_repair",
                "attempt": "1",
                "issue_codes": ",".join(issue.code for issue in last_issues),
            })
            context["validation_feedback"] = [
                issue.to_dict() for issue in last_issues
            ]
            continue
        break

    if planner_failed and _looks_like_explanatory_question(message):
        recovery_plan = {
            "status": "executable",
            "tasks": [{
                "id": "answer",
                "type": "answer_question",
                "description": message,
                "depends_on": [],
                "bindings": {},
            }],
        }
        candidate = enricher(
            raw_plan=recovery_plan,
            step_id=step_id,
            registered_tool_capabilities=registered_tool_capabilities,
        )
        decision = evaluator(candidate)
        if decision.allowed:
            diagnostics.append({
                "component": "planner",
                "recovery": "single_qa_plan",
            })
            return RoutePlanValidation(
                valid=True,
                plan=deepcopy(decision.plan),
                issues=(),
                diagnostics=tuple(diagnostics),
                retries_used=1,
            )

    return RoutePlanValidation(
        valid=False,
        plan={"schema_version": PLAN_SCHEMA_VERSION, "tasks": []},
        issues=last_issues or (
            _issue("PLAN_INVALID", "plan", "실행 계획을 검증하지 못했습니다."),
        ),
        diagnostics=tuple(diagnostics),
        retries_used=1,
    )


def plan_tasks_with_model(
    *,
    message: str,
    step_id: str,
    context: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Production planner adapter; deterministic validation remains separate."""

    from core.llm import llm

    route_constraints = context.get("route_constraints")
    if not isinstance(route_constraints, Mapping):
        raise ValueError("planner route_constraints are required")
    allowed_task_types = route_constraints.get("allowed_task_types")
    if not isinstance(allowed_task_types, list) or not allowed_task_types:
        raise ValueError("planner allowed_task_types are required")

    messages = prompt_messages(PLANNER_PROMPT, {
        "max_tasks": MAX_PLAN_TASKS,
        "task_types": list(allowed_task_types),
        "route_constraints": deepcopy(dict(route_constraints)),
        "step_id": step_id,
        # context_window_payload insertion order is the authority precedence:
        # Backend snapshot → structured state → summary → retrieval → recent raw.
        "context": dict(context),
        "message": message,
    })
    response = secure_structured_invoke(
        llm,
        PlannerModelOutput,
        messages,
        purpose=ModelPurpose.PLANNER,
        operation_name="planner.plan",
        sensitive_payload={"message": message, "context": dict(context)},
        prompt=PLANNER_PROMPT,
        max_attempts=1,
    )
    return response.to_route_plan()


def planner_route_constraints(
    *,
    step_id: str,
    allowed_task_types: Sequence[str],
    registered_tool_capabilities: Sequence[str] = (),
) -> dict[str, Any]:
    """Describe the deterministic task menu without selecting an executor."""

    ordered_task_types = sorted(set(allowed_task_types))
    return {
        "step_id": step_id,
        "available_tool_capabilities": sorted(set(registered_tool_capabilities)),
        "allowed_task_types": ordered_task_types,
        "task_type_guidance": {
            task_type: TASK_PLANNING_GUIDANCE[task_type]
            for task_type in ordered_task_types
        },
    }


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
