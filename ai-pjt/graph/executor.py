"""Sequential execution and aggregation for validated F03 route plans."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from core.step_registry import load_agent_descriptions
from graph.planner import binding_expectations_for
from graph.task_contracts import (
    BindingResolutionError,
    blocked_task_result,
    failed_task_result,
    resolve_task_bindings,
    validate_task_result,
)
from tools.output_validation import validate_form_update_contract


RuntimeExecutor = Callable[[dict[str, Any]], Awaitable[Mapping[str, Any]]]


@dataclass(frozen=True)
class ExecutionReport:
    task_results: dict[str, dict[str, Any]]
    output: dict[str, Any]
    tasks_used: int
    tool_calls_used: int = 0


def validate_agent_executor_registry(
    executors: Mapping[str, RuntimeExecutor],
) -> None:
    """Require one runtime binding for every registered Agent role."""

    expected = set(load_agent_descriptions())
    actual = set(executors)
    if actual != expected:
        missing = ", ".join(sorted(expected - actual)) or "none"
        unknown = ", ".join(sorted(actual - expected)) or "none"
        raise ValueError(
            f"Agent executor registry mismatch (missing={missing}; unknown={unknown})"
        )
    if any(not callable(executor) for executor in executors.values()):
        raise TypeError("every Agent executor must be callable")


async def execute_route_plan(
    *,
    plan: Mapping[str, Any],
    step_id: str,
    customer_info: Mapping[str, Any],
    field_registry: list[dict[str, Any]],
    agent_executors: Mapping[str, RuntimeExecutor],
    tool_executors: Mapping[str, RuntimeExecutor],
    task_limit: int = 3,
    request_id: str = "request-unknown",
    deadline_at: str | None = None,
    tool_context: Mapping[str, Any] | None = None,
    tool_call_budget: Any = None,
) -> ExecutionReport:
    """Run validated tasks in source order and pass only explicit bindings.

    Each executor receives its own task description, the current form context,
    and resolved binding values. It never receives the whole plan or another
    task's raw result.
    """

    task_results: dict[str, dict[str, Any]] = {}
    tasks_used = 0
    if (
        isinstance(task_limit, bool)
        or not isinstance(task_limit, int)
        or task_limit <= 0
    ):
        raise ValueError("task_limit must be a positive integer")
    raw_tasks = plan.get("tasks", [])
    tasks = raw_tasks if isinstance(raw_tasks, list) else []

    for task in tasks:
        if not isinstance(task, Mapping):
            continue
        task_id = str(task.get("id", "task-unknown"))
        dependencies = task.get("depends_on", [])
        if not isinstance(dependencies, list):
            dependencies = []

        blockers = [
            dependency
            for dependency in dependencies
            if task_results.get(dependency, {}).get("status") != "completed"
        ]
        if blockers:
            task_results[task_id] = blocked_task_result(task_id, blockers)
            continue

        try:
            bound_inputs = resolve_task_bindings(
                task=task,
                task_results=task_results,
                expectations=binding_expectations_for(task),
            )
        except BindingResolutionError as exc:
            binding_sources = _binding_source_ids(task)
            blockers = binding_sources or dependencies
            if blockers:
                blocked = blocked_task_result(task_id, blockers)
                blocked["dependency_reason_code"] = exc.code.lower()
                task_results[task_id] = blocked
            else:
                task_results[task_id] = failed_task_result(
                    task_id,
                    "binding_resolution_failed",
                )
            continue

        route = task.get("route")
        if not isinstance(route, Mapping):
            task_results[task_id] = failed_task_result(
                task_id,
                "route_missing",
            )
            continue

        executor_kind = route.get("executor_kind")
        executor_name = route.get("executor")
        if (
            executor_kind == "tool"
            and route.get("reason_code") == "capability_unavailable"
        ):
            task_results[task_id] = failed_task_result(
                task_id,
                "capability_unavailable",
            )
            continue

        registry = tool_executors if executor_kind == "tool" else agent_executors
        executor = registry.get(str(executor_name))
        if executor is None:
            task_results[task_id] = failed_task_result(
                task_id,
                "executor_unavailable",
            )
            continue

        if tasks_used >= task_limit:
            task_results[task_id] = failed_task_result(
                task_id,
                "task_budget_exhausted",
            )
            continue
        tasks_used += 1

        execution_input = {
            "request_id": request_id,
            "plan_id": str(plan.get("plan_id", "plan-unknown")),
            "task": deepcopy(dict(task)),
            "task_input": {
                "description": str(task.get("description", "")),
                "bindings": deepcopy(bound_inputs),
            },
            "message": str(task.get("description", "")),
            "step_id": step_id,
            "customer_info": deepcopy(dict(customer_info)),
            "field_registry": deepcopy(field_registry),
            "bound_inputs": deepcopy(bound_inputs),
            "next": str(executor_name),
            "deadline_at": deadline_at,
            "tool_context": deepcopy(dict(tool_context or {})),
            "tool_call_budget": tool_call_budget,
        }
        try:
            raw_result = await executor(execution_input)
        except Exception:
            task_results[task_id] = failed_task_result(
                task_id,
                "executor_error",
            )
            continue

        validation = validate_task_result(
            task=task,
            result=raw_result,
            step_id=step_id,
            field_registry=field_registry,
        )
        result = validation.result
        if validation.valid and not _uses_bound_values(result, bound_inputs):
            result = failed_task_result(task_id, "binding_not_consumed")
        task_results[task_id] = result

    output = aggregate_task_results(
        tasks=tasks,
        task_results=task_results,
        step_id=step_id,
        field_registry=field_registry,
    )
    return ExecutionReport(
        task_results=task_results,
        output=output,
        tasks_used=tasks_used,
        tool_calls_used=(
            tool_call_budget.used
            if hasattr(tool_call_budget, "used")
            else 0
        ),
    )


def aggregate_task_results(
    *,
    tasks: list[Any],
    task_results: Mapping[str, Mapping[str, Any]],
    step_id: str,
    field_registry: list[dict[str, Any]],
) -> dict[str, Any]:
    """Collapse internal results into one public response and at most one action."""

    responses: list[str] = []
    changes_by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    change_order: list[tuple[Any, ...]] = []
    conflicts: set[tuple[Any, ...]] = set()
    user_requested_values: list[bool] = []
    confidences: list[float] = []

    for task_index, task in enumerate(tasks):
        if not isinstance(task, Mapping):
            continue
        result = task_results.get(str(task.get("id")))
        if not isinstance(result, Mapping):
            continue
        status = result.get("status")
        if status == "failed":
            responses.append(_failure_message(str(result.get("reason_code", ""))))
            continue
        if status == "blocked":
            responses.append("선행 작업이 완료되지 않아 연결된 요청은 실행하지 않았습니다.")
            continue
        if status != "completed":
            continue

        response = result.get("response")
        artifact = result.get("artifact")
        if isinstance(response, str) and response.strip():
            responses.append(response.strip())
        elif isinstance(artifact, Mapping) and artifact.get("type") == "text_response":
            text = artifact.get("text")
            if isinstance(text, str) and text.strip():
                responses.append(text.strip())

        if isinstance(artifact, Mapping) and artifact.get("type") == "reference_value":
            responses.append(_reference_value_message(artifact))
        if isinstance(artifact, Mapping) and artifact.get("type") == "dashboard_metric":
            responses.append(_dashboard_metric_message(artifact))

        if not isinstance(artifact, Mapping) or artifact.get("type") != "form_change":
            continue
        user_requested_values.append(artifact.get("user_requested") is True)
        confidence = artifact.get("confidence")
        if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
            confidences.append(float(confidence))
        for change_index, raw_change in enumerate(artifact.get("changes", [])):
            if not isinstance(raw_change, Mapping):
                continue
            change = deepcopy(dict(raw_change))
            key = _change_key(change, task_index, change_index)
            existing = changes_by_key.get(key)
            if existing is None:
                changes_by_key[key] = change
                change_order.append(key)
            elif not _changes_equivalent(existing, change):
                conflicts.add(key)

    if conflicts:
        responses.append(
            "같은 입력 항목에 서로 다른 변경안이 있어 해당 항목은 제안에서 제외했습니다."
        )
    changes = [
        changes_by_key[key]
        for key in change_order
        if key not in conflicts
    ]
    action = None
    if changes:
        action = {
            "type": "form_change",
            "target": "draft_form",
            "user_requested": all(user_requested_values),
            "confidence": min(confidences) if confidences else 0.0,
            "changes": changes,
        }

    output = {
        "response": "\n\n".join(_deduplicate(responses))
        or "요청을 처리할 수 있는 결과를 만들지 못했습니다. 내용을 확인해 다시 요청해 주세요.",
        "step_id": step_id,
        "action": action,
    }
    validation = validate_form_update_contract({
        "step_id": step_id,
        "field_registry": field_registry,
        "output": output,
    })
    return validation.output


def _binding_source_ids(task: Mapping[str, Any]) -> list[str]:
    bindings = task.get("bindings")
    if not isinstance(bindings, Mapping):
        return []
    result: list[str] = []
    for binding in bindings.values():
        if not isinstance(binding, Mapping):
            continue
        source_id = binding.get("task_id")
        if isinstance(source_id, str) and source_id not in result:
            result.append(source_id)
    return result


def _uses_bound_values(
    result: Mapping[str, Any],
    bound_inputs: Mapping[str, Any],
) -> bool:
    """Reject a form proposal that substituted a different bound value."""

    if "value" not in bound_inputs:
        return True
    artifact = result.get("artifact")
    if not isinstance(artifact, Mapping) or artifact.get("type") != "form_change":
        return True
    for change in artifact.get("changes", []):
        if isinstance(change, Mapping) and change.get("value") == bound_inputs["value"]:
            if "unit" in bound_inputs and change.get("unit") != bound_inputs["unit"]:
                continue
            return True
    return False


def _change_key(
    change: Mapping[str, Any],
    task_index: int,
    change_index: int,
) -> tuple[Any, ...]:
    if change.get("operation") == "add":
        return ("add", task_index, change_index)
    return (
        change.get("section"),
        change.get("path"),
        change.get("item_id"),
        change.get("client_item_id"),
    )


def _failure_message(reason_code: str) -> str:
    messages = {
        "capability_unavailable": (
            "승인된 기준값 조회 도구가 아직 연결되지 않아 해당 조회를 완료하지 못했습니다."
        ),
        "binding_not_consumed": (
            "조회 결과와 입력 변경안의 값이 일치하지 않아 변경 제안을 제외했습니다."
        ),
        "output_schema_invalid": (
            "작업 결과가 안전성 검증을 통과하지 못해 응답에서 제외했습니다."
        ),
        "executor_unavailable": "요청을 처리할 실행 기능이 연결되지 않았습니다.",
        "executor_error": "요청 처리 중 오류가 발생해 해당 결과를 제외했습니다.",
        "route_missing": "안전한 실행 경로를 확인하지 못해 해당 요청을 처리하지 않았습니다.",
        "binding_resolution_failed": "선행 결과를 후속 입력으로 안전하게 연결하지 못했습니다.",
        "task_budget_exhausted": "요청당 실행 가능한 작업 수를 초과했습니다.",
        "tool_resource_not_found": "검증된 조회 결과를 찾지 못했습니다.",
        "tool_dependency_unavailable": "검증된 조회 결과를 현재 사용할 수 없습니다.",
        "invalid_tool_arguments": "도구 조회 조건이 안전한 계약과 일치하지 않습니다.",
        "invalid_tool_result": "도구 조회 결과가 artifact 계약을 통과하지 못했습니다.",
        "invalid_tool_task": "도구 task가 허용된 실행 계약과 일치하지 않습니다.",
    }
    return messages.get(reason_code, "요청의 일부를 완료하지 못했습니다.")


def _deduplicate(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _changes_equivalent(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> bool:
    return {
        key: value for key, value in left.items() if key != "reason"
    } == {
        key: value for key, value in right.items() if key != "reason"
    }


def _reference_value_message(artifact: Mapping[str, Any]) -> str:
    value = artifact.get("value")
    rendered_value = f"{value:,}" if isinstance(value, (int, float)) else str(value)
    return (
        f"{artifact.get('population')} 기준 {artifact.get('period')} 값은 "
        f"{rendered_value} {artifact.get('unit')}입니다. "
        f"출처는 {artifact.get('source_id')}, 기준일은 {artifact.get('as_of')}입니다."
    )


def _dashboard_metric_message(artifact: Mapping[str, Any]) -> str:
    value = artifact.get("value")
    rendered_value = f"{value:,}" if isinstance(value, int) else str(value)
    return (
        f"최신 검증 보고서의 은퇴 부족액은 {rendered_value} {artifact.get('unit')}입니다. "
        f"보고서 ID/version은 {artifact.get('report_id')}/{artifact.get('report_version')}, "
        f"계산 정책은 {artifact.get('calculation_policy_version')}, "
        f"생성 시각은 {artifact.get('generated_at')}입니다."
    )
