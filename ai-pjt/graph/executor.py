"""Sequential execution and aggregation for validated F03 route plans."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
import logging
from typing import Any

from core.monitoring import OperationStage, OperationStatus
from core.observability_masking import masked_exception_fields
from core.runtime_instrumentation import RuntimeStage, build_artifact_id
from core.runtime_instrumentation_adapter import BoundRuntimeInstrumentation
from core.step_registry import load_agent_descriptions
from graph.planner import binding_expectations_for
from graph.task_contracts import (
    BindingResolutionError,
    blocked_task_result,
    failed_task_result,
    resolve_task_bindings,
    seal_validated_artifact,
    validate_task_result,
)
from tools.output_validation import validate_form_update_contract


logger = logging.getLogger(__name__)

RuntimeExecutor = Callable[[dict[str, Any]], Awaitable[Mapping[str, Any]]]


@dataclass(frozen=True)
class ExecutionReport:
    task_results: dict[str, dict[str, Any]]
    output: dict[str, Any]
    tasks_used: int
    tool_calls_used: int = 0
    state_update: dict[str, Any] | None = None


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
    instrumentation: BoundRuntimeInstrumentation,
) -> ExecutionReport:
    """Run validated tasks in source order and pass only explicit bindings.

    Each executor receives its own task description, the current form context,
    and resolved binding values. It never receives the whole plan or another
    task's raw result.
    """

    task_results: dict[str, dict[str, Any]] = {}
    artifact_seals = {}
    tasks_used = 0
    if (
        isinstance(task_limit, bool)
        or not isinstance(task_limit, int)
        or task_limit <= 0
    ):
        raise ValueError("task_limit must be a positive integer")
    raw_tasks = plan.get("tasks", [])
    tasks = raw_tasks if isinstance(raw_tasks, list) else []
    aggregate_tasks = list(tasks)

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
            instrumentation.record_block(
                task_id=task_id,
                depends_on=tuple(str(item) for item in dependencies),
                blocked_by=tuple(str(item) for item in blockers),
            )
            continue

        binding_span = instrumentation.start(
            RuntimeStage.BINDING_RESOLUTION,
            task_id=task_id,
            depends_on=tuple(str(item) for item in dependencies),
        )
        try:
            bound_inputs = resolve_task_bindings(
                task=task,
                task_results=task_results,
                expectations=binding_expectations_for(task),
                artifact_seals=artifact_seals,
            )
        except BindingResolutionError as exc:
            instrumentation.fail(
                binding_span,
                reason_code=exc.code.lower(),
            )
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
        instrumentation.succeed(binding_span)

        route = task.get("route")
        if not isinstance(route, Mapping):
            task_results[task_id] = failed_task_result(
                task_id,
                "route_missing",
            )
            task_span = instrumentation.start(
                RuntimeStage.TASK_EXECUTION,
                task_id=task_id,
                depends_on=tuple(str(item) for item in dependencies),
            )
            instrumentation.fail(task_span, reason_code="route_missing")
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
            task_span = instrumentation.start(
                RuntimeStage.TASK_EXECUTION,
                task_id=task_id,
                depends_on=tuple(str(item) for item in dependencies),
                executor_kind="tool",
                executor_name=str(executor_name or "unavailable"),
            )
            instrumentation.fail(task_span, reason_code="capability_unavailable")
            continue

        registry = tool_executors if executor_kind == "tool" else agent_executors
        executor = registry.get(str(executor_name))
        if executor is None:
            task_results[task_id] = failed_task_result(
                task_id,
                "executor_unavailable",
            )
            task_span = instrumentation.start(
                RuntimeStage.TASK_EXECUTION,
                task_id=task_id,
                depends_on=tuple(str(item) for item in dependencies),
                executor_kind=str(executor_kind or "agent"),
                executor_name=str(executor_name or "unavailable"),
            )
            instrumentation.fail(task_span, reason_code="executor_unavailable")
            continue

        if tasks_used >= task_limit:
            task_results[task_id] = failed_task_result(
                task_id,
                "task_budget_exhausted",
            )
            task_span = instrumentation.start(
                RuntimeStage.TASK_EXECUTION,
                task_id=task_id,
                depends_on=tuple(str(item) for item in dependencies),
                executor_kind=str(executor_kind),
                executor_name=str(executor_name),
            )
            instrumentation.fail(task_span, reason_code="task_budget_exhausted")
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
            "chart_payload": deepcopy(plan.get("chart_payload")),
            "request_mode": plan.get("request_mode", "chat"),
            "review_trigger": plan.get("review_trigger"),
            "elicit_action": plan.get("elicit_action"),
            "elicit_state": deepcopy(plan.get("elicit_state", {})),
            "context_scope": deepcopy(plan.get("context_scope", {})),
            "conversation_context": deepcopy(plan.get("conversation_context", {})),
        }
        if executor_kind == "tool":
            execution_input["tool_audit_sink"] = instrumentation.record_tool_audit
        task_span = instrumentation.start(
            RuntimeStage.TASK_EXECUTION,
            task_id=task_id,
            depends_on=tuple(str(item) for item in dependencies),
            executor_kind=str(executor_kind),
            executor_name=str(executor_name),
        )
        operation_started = instrumentation.elapsed_ms(task_span)
        try:
            raw_result = await executor(execution_input)
        except Exception as error:
            # 이 except 는 사용자에게 executor_error 안내문만 남기므로, 여기서 기록하지
            # 않으면 배포본에서 원인을 되짚을 수 있는 흔적이 전혀 남지 않는다.
            logger.error(
                "task executor raised",
                extra={
                    "event": "executor_error",
                    "detail": masked_exception_fields(
                        error,
                        request_id=request_id,
                        plan_id=str(plan.get("plan_id", "plan-unknown")),
                        step_id=step_id,
                        task_id=task_id,
                        executor_kind=str(executor_kind),
                        executor_name=str(executor_name),
                    ),
                },
            )
            operation_duration = max(
                0,
                instrumentation.elapsed_ms(task_span) - operation_started,
            )
            instrumentation.record_operation(
                stage=(
                    OperationStage.TOOL
                    if executor_kind == "tool"
                    else OperationStage.MODEL
                ),
                status=OperationStatus.FAILED,
                operation_name=str(executor_name),
                duration_ms=operation_duration,
                agent=str(executor_name) if executor_kind != "tool" else None,
                error_code="executor_error",
            )
            task_results[task_id] = failed_task_result(
                task_id,
                "executor_error",
            )
            instrumentation.fail(task_span, reason_code="executor_error")
            continue

        operation_duration = max(
            0,
            instrumentation.elapsed_ms(task_span) - operation_started,
        )
        instrumentation.record_operation(
            stage=(
                OperationStage.TOOL
                if executor_kind == "tool"
                else OperationStage.MODEL
            ),
            status=OperationStatus.SUCCEEDED,
            operation_name=str(executor_name),
            duration_ms=operation_duration,
            agent=str(executor_name) if executor_kind != "tool" else None,
        )

        artifact_type = str(task.get("produces", "unknown"))
        artifact_span = instrumentation.start(
            RuntimeStage.ARTIFACT_VALIDATION,
            task_id=task_id,
            depends_on=tuple(str(item) for item in dependencies),
            artifact_id=build_artifact_id(
                plan_id=str(plan.get("plan_id", "plan-unknown")),
                producer_task_id=task_id,
                artifact_type=artifact_type,
            ),
            artifact_type=artifact_type,
            producer_task_id=task_id,
        )
        validation = validate_task_result(
            task=task,
            result=raw_result,
            step_id=step_id,
            field_registry=field_registry,
        )
        result = validation.result
        if validation.valid and not _uses_bound_values(result, bound_inputs):
            result = failed_task_result(task_id, "binding_not_consumed")
            instrumentation.fail(
                artifact_span,
                reason_code="binding_not_consumed",
            )
        elif validation.valid:
            instrumentation.succeed(artifact_span)
        else:
            instrumentation.fail(artifact_span, reason_code="artifact_invalid")
        task_results[task_id] = result
        if validation.valid and result.get("status") == "completed":
            artifact_seals[task_id] = seal_validated_artifact(validation)
        if result.get("status") == "completed":
            response_owner = (
                "fallback"
                if str(executor_name) == "fallback"
                else "aggregator"
                if executor_kind == "tool"
                else "agent"
            )
            instrumentation.succeed(task_span, response_owner=response_owner)
        else:
            instrumentation.fail(
                task_span,
                reason_code=str(result.get("reason_code", "artifact_invalid")),
            )

    has_completed = any(
        result.get("status") == "completed" for result in task_results.values()
    )
    has_planned_fallback = any(
        isinstance(task, Mapping)
        and isinstance(task.get("route"), Mapping)
        and task["route"].get("executor") == "fallback"
        for task in tasks
    )
    if not has_completed and tasks and not has_planned_fallback:
        fallback_task = {
            "id": "task-fallback-all-failed",
            "type": "fallback",
            "description": "검증된 task 결과 없음",
            "depends_on": [],
            "bindings": {},
            "executor_kind": "agent",
            "produces": "text_response",
            "step_id": step_id,
            "route": {
                "task_id": "task-fallback-all-failed",
                "executor_kind": "agent",
                "executor": "fallback",
                "reason_code": "all_tasks_failed",
            },
        }
        fallback_executor = agent_executors.get("fallback")
        if fallback_executor is not None:
            fallback_payload = {
                "request_id": request_id,
                "plan_id": str(plan.get("plan_id", "plan-unknown")),
                "task": fallback_task,
                "task_input": {"description": "검증된 task 결과 없음", "bindings": {}},
                "message": "검증된 task 결과 없음",
                "step_id": step_id,
                "customer_info": {},
                "field_registry": [],
                "bound_inputs": {},
                "next": "fallback",
                "deadline_at": deadline_at,
                "tool_context": {},
                "tool_call_budget": tool_call_budget,
            }
            fallback_span = instrumentation.start(
                RuntimeStage.TASK_EXECUTION,
                task_id=fallback_task["id"],
                executor_kind="agent",
                executor_name="fallback",
            )
            try:
                operation_started = instrumentation.elapsed_ms(fallback_span)
                fallback_result = await fallback_executor(fallback_payload)
                operation_duration = max(
                    0,
                    instrumentation.elapsed_ms(fallback_span) - operation_started,
                )
                instrumentation.record_operation(
                    stage=OperationStage.MODEL,
                    status=OperationStatus.SUCCEEDED,
                    operation_name="fallback",
                    duration_ms=operation_duration,
                    agent="fallback",
                )
                fallback_artifact_span = instrumentation.start(
                    RuntimeStage.ARTIFACT_VALIDATION,
                    task_id=fallback_task["id"],
                    artifact_id=build_artifact_id(
                        plan_id=str(plan.get("plan_id", "plan-unknown")),
                        producer_task_id=fallback_task["id"],
                        artifact_type="text_response",
                    ),
                    artifact_type="text_response",
                    producer_task_id=fallback_task["id"],
                )
                fallback_validation = validate_task_result(
                    task=fallback_task,
                    result=fallback_result,
                    step_id=step_id,
                    field_registry=field_registry,
                )
                if fallback_validation.valid:
                    instrumentation.succeed(fallback_artifact_span)
                    instrumentation.succeed(
                        fallback_span,
                        response_owner="fallback",
                    )
                else:
                    instrumentation.fail(
                        fallback_artifact_span,
                        reason_code="artifact_invalid",
                    )
                    instrumentation.fail(
                        fallback_span,
                        reason_code="artifact_invalid",
                    )
                task_results[fallback_task["id"]] = fallback_validation.result
                aggregate_tasks.append(fallback_task)
                tasks_used += 1
            except Exception as error:
                logger.error(
                    "fallback executor raised",
                    extra={
                        "event": "executor_error",
                        "detail": masked_exception_fields(
                            error,
                            request_id=request_id,
                            plan_id=str(plan.get("plan_id", "plan-unknown")),
                            step_id=step_id,
                            task_id=str(fallback_task["id"]),
                            executor_kind="agent",
                            executor_name="fallback",
                        ),
                    },
                )
                operation_duration = max(
                    0,
                    instrumentation.elapsed_ms(fallback_span) - operation_started,
                )
                instrumentation.record_operation(
                    stage=OperationStage.MODEL,
                    status=OperationStatus.FAILED,
                    operation_name="fallback",
                    duration_ms=operation_duration,
                    agent="fallback",
                    error_code="executor_error",
                )
                instrumentation.fail(
                    fallback_span,
                    reason_code="executor_error",
                )

    aggregation_span = instrumentation.start(RuntimeStage.AGGREGATION)
    try:
        output = aggregate_task_results(
            tasks=aggregate_tasks,
            task_results=task_results,
            step_id=step_id,
            field_registry=field_registry,
        )
    except Exception as error:
        logger.error(
            "aggregation raised",
            extra={
                "event": "aggregation_error",
                "detail": masked_exception_fields(
                    error,
                    request_id=request_id,
                    plan_id=str(plan.get("plan_id", "plan-unknown")),
                    step_id=step_id,
                ),
            },
        )
        instrumentation.fail(aggregation_span, reason_code="aggregation_error")
        raise
    instrumentation.succeed(aggregation_span)
    return ExecutionReport(
        task_results=task_results,
        output=output,
        tasks_used=tasks_used,
        tool_calls_used=(
            tool_call_budget.used
            if hasattr(tool_call_budget, "used")
            else 0
        ),
        state_update=_collect_state_update(aggregate_tasks, task_results),
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
    reviews: list[dict[str, Any]] = []
    ui_actions: list[dict[str, Any]] = []

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

        if isinstance(result.get("review"), Mapping):
            reviews.append(deepcopy(dict(result["review"])))
        if isinstance(result.get("ui_action"), Mapping):
            ui_actions.append(deepcopy(dict(result["ui_action"])))

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

        form_artifact = (
            artifact
            if isinstance(artifact, Mapping) and artifact.get("type") == "form_change"
            else result.get("elicit_capture")
        )
        if not isinstance(form_artifact, Mapping) or form_artifact.get("type") != "form_change":
            continue
        user_requested_values.append(form_artifact.get("user_requested") is True)
        confidence = form_artifact.get("confidence")
        if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
            confidences.append(float(confidence))
        for change_index, raw_change in enumerate(form_artifact.get("changes", [])):
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
    if len(reviews) == 1:
        output["review"] = reviews[0]
    if len(ui_actions) == 1:
        output["ui_action"] = ui_actions[0]
    validation = validate_form_update_contract({
        "step_id": step_id,
        "field_registry": field_registry,
        "output": output,
    })
    return validation.output


def _collect_state_update(
    tasks: list[Any],
    task_results: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    update: dict[str, Any] = {}
    for task in tasks:
        if not isinstance(task, Mapping):
            continue
        result = task_results.get(str(task.get("id")))
        if isinstance(result, Mapping) and isinstance(result.get("state_update"), Mapping):
            update.update(deepcopy(dict(result["state_update"])))
    return update


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
        "reference_target_not_allowed": (
            "조회 기준값을 현재 폼의 승인된 기존 항목에 안전하게 연결할 수 없어 변경 제안을 제외했습니다."
        ),
        "reference_contract_mismatch": (
            "요청한 기준값과 조회 결과의 식별 조건이 일치하지 않아 결과를 폐기했습니다."
        ),
        "output_schema_invalid": (
            "작업 결과가 안전성 검증을 통과하지 못해 응답에서 제외했습니다."
        ),
        "artifact_invalid": (
            "작업 결과가 안전성 검증을 통과하지 못해 응답에서 제외했습니다."
        ),
        "dependency_failed": "선행 작업이 실패해 연결된 요청은 실행하지 않았습니다.",
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
