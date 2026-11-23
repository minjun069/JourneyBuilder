"""Pure structural normalization for untrusted planner output."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from graph.plan_contracts import (
    MAX_PLAN_TASKS,
    PLAN_SCHEMA_VERSION,
    RoutePlanValidation,
    invalid_plan,
    issue,
)
from graph.task_catalog import TASK_TYPE_CONTRACTS
from tools.registry import validate_json_schema


HOUSEHOLD_WRITABLE_REFERENCE_PREFIX = "household_monthly_expense_"
TASK_FIELDS = {"id", "type", "description", "depends_on", "bindings", "arguments"}
FORBIDDEN_PLANNER_FIELDS = {
    "agent",
    "executor",
    "executor_kind",
    "handler",
    "produces",
    "route",
    "tool",
}


def validate_plan_structure(
    raw_plan: Any,
    *,
    step_id: str,
    allowed_task_types: Sequence[str] | None = None,
) -> RoutePlanValidation:
    """Normalize a model plan into canonical ids and reject unsafe topology."""

    issues = []
    if not isinstance(raw_plan, Mapping):
        return invalid_plan([issue("INVALID_PLAN", "plan", "route plan은 object여야 합니다.")])
    if set(raw_plan) - {"status", "tasks"}:
        issues.append(issue(
            "INVALID_PLAN_FIELDS",
            "plan",
            "route plan root에는 status와 tasks만 허용됩니다.",
        ))
    status = raw_plan.get("status", "executable")
    if status not in {"executable", "ambiguous", "unsupported"}:
        issues.append(issue(
            "INVALID_PLAN_STATUS",
            "plan.status",
            "planner status가 지원 계약과 일치하지 않습니다.",
        ))
    raw_tasks = raw_plan.get("tasks")
    if status in {"ambiguous", "unsupported"}:
        if raw_tasks != []:
            issues.append(issue(
                "NON_EXECUTABLE_TASKS",
                "plan.tasks",
                "실행하지 않는 planner 결과에는 task가 없어야 합니다.",
            ))
        if issues:
            return invalid_plan(issues)
        return RoutePlanValidation(
            valid=True,
            plan={
                "schema_version": PLAN_SCHEMA_VERSION,
                "tasks": [],
                "outcome_reason": (
                    "ambiguous_intent"
                    if status == "ambiguous"
                    else "unsupported_intent"
                ),
            },
            issues=(),
        )

    if not isinstance(raw_tasks, list) or not 1 <= len(raw_tasks) <= MAX_PLAN_TASKS:
        issues.append(issue(
            "INVALID_TASK_COUNT",
            "plan.tasks",
            f"task는 1개 이상 {MAX_PLAN_TASKS}개 이하여야 합니다.",
        ))
        return invalid_plan(issues)

    aliases: list[str] = []
    for index, raw_task in enumerate(raw_tasks):
        if not isinstance(raw_task, Mapping):
            issues.append(issue(
                "INVALID_TASK",
                f"plan.tasks[{index}]",
                "task는 object여야 합니다.",
            ))
            aliases.append(f"invalid-{index}")
            continue
        alias = raw_task.get("id")
        if not isinstance(alias, str) or not alias.strip():
            issues.append(issue(
                "INVALID_TASK_ALIAS",
                f"plan.tasks[{index}].id",
                "planner task에는 참조용 id가 필요합니다.",
            ))
            alias = f"invalid-{index}"
        aliases.append(alias)
    if len(aliases) != len(set(aliases)):
        issues.append(issue(
            "DUPLICATE_TASK_ALIAS",
            "plan.tasks",
            "planner task id는 중복될 수 없습니다.",
        ))

    alias_to_id = {alias: f"task-{index + 1}" for index, alias in enumerate(aliases)}
    normalized_tasks: list[dict[str, Any]] = []
    allowed = set(allowed_task_types) if allowed_task_types is not None else None
    for index, raw_task in enumerate(raw_tasks):
        if not isinstance(raw_task, Mapping):
            continue
        path = f"plan.tasks[{index}]"
        unknown = set(raw_task) - TASK_FIELDS
        forbidden = set(raw_task) & FORBIDDEN_PLANNER_FIELDS
        if unknown:
            issues.append(issue(
                "UNKNOWN_TASK_FIELDS",
                path,
                f"허용되지 않은 task field입니다: {', '.join(sorted(unknown))}",
            ))
        if forbidden:
            issues.append(issue(
                "PLANNER_EXECUTOR_INJECTION",
                path,
                "planner는 executor나 produces를 지정할 수 없습니다.",
            ))

        task_type = raw_task.get("type")
        contract = TASK_TYPE_CONTRACTS.get(str(task_type))
        if contract is None:
            issues.append(issue(
                "UNSUPPORTED_TASK_TYPE",
                f"{path}.type",
                "지원하지 않는 task type입니다.",
            ))
            continue
        if allowed is not None and task_type not in allowed:
            issues.append(issue(
                "TASK_TYPE_NOT_ALLOWED_FOR_STEP",
                f"{path}.type",
                "현재 단계와 실행 capability에서 허용되지 않은 task type입니다.",
            ))
        description = raw_task.get("description")
        if not isinstance(description, str) or not description.strip():
            issues.append(issue(
                "INVALID_TASK_DESCRIPTION",
                f"{path}.description",
                "task description은 비어 있지 않아야 합니다.",
            ))

        argument_schema = contract.get("argument_schema")
        fixed_arguments = contract.get("fixed_arguments")
        raw_arguments = raw_task.get("arguments")
        normalized_arguments: dict[str, Any] | None = None
        if isinstance(argument_schema, Mapping):
            argument_errors = validate_json_schema(argument_schema, raw_arguments)
            if argument_errors:
                issues.append(issue(
                    "INVALID_TOOL_ARGUMENTS",
                    f"{path}.arguments",
                    "tool task arguments가 입력 계약과 일치하지 않습니다.",
                ))
            elif isinstance(raw_arguments, Mapping):
                normalized_arguments = deepcopy(dict(raw_arguments))
        elif isinstance(fixed_arguments, Mapping):
            if "arguments" in raw_task:
                issues.append(issue(
                    "PLANNER_FIXED_ARGUMENT_INJECTION",
                    f"{path}.arguments",
                    "이 tool task의 인자는 runtime 정책으로 고정됩니다.",
                ))
            normalized_arguments = deepcopy(dict(fixed_arguments))
        elif "arguments" in raw_task:
            issues.append(issue(
                "UNEXPECTED_TASK_ARGUMENTS",
                f"{path}.arguments",
                "이 task type은 arguments를 허용하지 않습니다.",
            ))

        raw_dependencies = raw_task.get("depends_on", [])
        if not isinstance(raw_dependencies, list) or any(
            not isinstance(item, str) for item in raw_dependencies
        ):
            issues.append(issue(
                "INVALID_DEPENDENCIES",
                f"{path}.depends_on",
                "depends_on은 task id 문자열 배열이어야 합니다.",
            ))
            raw_dependencies = []
        if len(raw_dependencies) != len(set(raw_dependencies)):
            issues.append(issue(
                "DUPLICATE_DEPENDENCY",
                f"{path}.depends_on",
                "같은 dependency를 중복 선언할 수 없습니다.",
            ))
        earlier_aliases = set(aliases[:index])
        for dependency in raw_dependencies:
            if dependency not in alias_to_id:
                code, message = "UNKNOWN_DEPENDENCY", "plan에 없는 task를 참조했습니다."
            elif dependency not in earlier_aliases:
                code = "FORWARD_OR_CYCLIC_DEPENDENCY"
                message = "dependency는 앞에 선언한 task만 참조할 수 있습니다."
            else:
                continue
            issues.append(issue(code, f"{path}.depends_on", message))

        raw_bindings = raw_task.get("bindings", {})
        if not isinstance(raw_bindings, Mapping):
            issues.append(issue(
                "INVALID_BINDINGS",
                f"{path}.bindings",
                "bindings는 object여야 합니다.",
            ))
            raw_bindings = {}
        normalized_bindings: dict[str, dict[str, str]] = {}
        expectations = contract["binding_expectations"]
        for input_name, binding in raw_bindings.items():
            if input_name not in expectations:
                issues.append(issue(
                    "UNKNOWN_BINDING_INPUT",
                    f"{path}.bindings.{input_name}",
                    "task type이 허용하지 않는 binding 입력입니다.",
                ))
                continue
            if not isinstance(binding, Mapping) or set(binding) != {"task_id", "path"}:
                issues.append(issue(
                    "INVALID_BINDING",
                    f"{path}.bindings.{input_name}",
                    "binding은 task_id와 path만 포함해야 합니다.",
                ))
                continue
            source_alias = binding.get("task_id")
            source_path = binding.get("path")
            if source_alias not in raw_dependencies:
                issues.append(issue(
                    "UNDECLARED_BINDING_DEPENDENCY",
                    f"{path}.bindings.{input_name}.task_id",
                    "binding source는 depends_on에도 선언해야 합니다.",
                ))
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
                issues.append(issue(
                    "BINDING_ARTIFACT_MISMATCH",
                    f"{path}.bindings.{input_name}",
                    "source task artifact가 consumer 입력과 맞지 않습니다.",
                ))
                continue
            if source_path != expectation["path"]:
                issues.append(issue(
                    "BINDING_PATH_MISMATCH",
                    f"{path}.bindings.{input_name}.path",
                    "binding path가 task 입력 계약과 맞지 않습니다.",
                ))
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
                issues.append(issue(
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
        return invalid_plan(issues)
    return RoutePlanValidation(
        valid=True,
        plan={"schema_version": PLAN_SCHEMA_VERSION, "tasks": normalized_tasks},
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
