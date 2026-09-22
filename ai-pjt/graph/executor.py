"""Sequential execution for validated route plans."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from functools import partial
import logging
from typing import Any

from core.model_boundary import bind_model_call_recorder
from core.field_registry import load_field_registry
from core.monitoring import OperationStage, OperationStatus
from core.operation_observation import bind_operation_recorder
from core.prompt_registry import bind_prompt_trace_sink
from core.observability_masking import (
    exception_identity_fields,
    masked_exception_fields,
)
from core.runtime_instrumentation import RuntimeStage, build_artifact_id
from core.runtime_instrumentation_adapter import BoundRuntimeInstrumentation
from core.step_registry import load_agent_descriptions
from graph.aggregation import AggregateComposer, aggregate_task_results
from graph.binding_policy import (
    BindingResolutionError,
    resolve_task_bindings,
    seal_validated_artifact,
)
from graph.runtime_types import RuntimeExecutor
from graph.task_catalog import binding_expectations_for
from graph.task_contracts import (
    blocked_task_result,
    failed_task_result,
    validate_task_result,
)
from tools.runtime import ToolCallBudget


logger = logging.getLogger(__name__)
TASK_LIMIT = 3
TOOL_CALL_LIMIT = 3

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
    agent_executors: Mapping[str, RuntimeExecutor],
    tool_executors: Mapping[str, RuntimeExecutor],
    request_id: str,
    instrumentation: BoundRuntimeInstrumentation,
    response_composer: AggregateComposer | None = None,
    counseling_enricher: AggregateComposer | None = None,
    question: str = "",
    conversation_context: Mapping[str, Any] | None = None,
) -> ExecutionReport:
    """Run validated tasks in source order and pass only explicit bindings.

    Each executor receives its own task description, the current form context,
    and resolved binding values. It never receives the whole plan or another
    task's raw result.
    """

    task_results: dict[str, dict[str, Any]] = {}
    artifact_seals = {}
    tasks_used = 0
    field_registry = load_field_registry(step_id)
    tool_call_budget = ToolCallBudget(limit=TOOL_CALL_LIMIT)
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
        route = task.get("route")
        executor_kind = (
            route.get("executor_kind") if isinstance(route, Mapping) else None
        )
        executor_name = (
            route.get("executor") if isinstance(route, Mapping) else None
        )
        task_span = instrumentation.start(
            RuntimeStage.TASK_EXECUTION,
            task_id=task_id,
            depends_on=tuple(str(item) for item in dependencies),
            executor_kind=str(executor_kind or "unknown"),
            executor_name=str(executor_name or "unknown"),
            input={
                "task_type": str(task.get("type", "unknown")),
                "dependency_count": len(dependencies),
                "message": str(task.get("description", "")),
                "step_id": step_id,
                "customer_info": deepcopy(dict(customer_info)),
            },
        )

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
            instrumentation.fail(
                task_span,
                reason_code="dependency_blocked",
                output=task_results[task_id],
            )
            continue

        binding_span = instrumentation.start(
            RuntimeStage.BINDING_RESOLUTION,
            task_id=task_id,
            depends_on=tuple(str(item) for item in dependencies),
        )
        try:
            task_bindings = resolve_task_bindings(
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
            instrumentation.fail(
                task_span,
                reason_code=exc.code.lower(),
                output=task_results[task_id],
            )
            continue
        instrumentation.succeed(binding_span)

        if not isinstance(route, Mapping):
            task_results[task_id] = failed_task_result(
                task_id,
                "route_missing",
            )
            instrumentation.fail(task_span, reason_code="route_missing")
            continue

        if (
            executor_kind == "tool"
            and route.get("reason_code") == "capability_unavailable"
        ):
            task_results[task_id] = failed_task_result(
                task_id,
                "capability_unavailable",
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
            instrumentation.fail(task_span, reason_code="executor_unavailable")
            continue

        if tasks_used >= TASK_LIMIT:
            task_results[task_id] = failed_task_result(
                task_id,
                "task_budget_exhausted",
            )
            instrumentation.fail(task_span, reason_code="task_budget_exhausted")
            continue
        tasks_used += 1

        execution_input = {
            "request_id": request_id,
            "task": deepcopy(dict(task)),
            "task_input": {
                "description": str(task.get("description", "")),
                "bindings": deepcopy(task_bindings),
            },
            "message": str(task.get("description", "")),
            "step_id": step_id,
            "customer_info": deepcopy(dict(customer_info)),
            "chart_payload": deepcopy(plan.get("chart_payload")),
            "request_mode": plan.get("request_mode", "chat"),
            "conversation_context": deepcopy(plan.get("conversation_context", {})),
        }
        observable_execution_input = {
            "task": deepcopy(dict(task)),
            "message": str(task.get("description", "")),
            "step_id": step_id,
            "customer_info": deepcopy(dict(customer_info)),
            "task_input": deepcopy(execution_input["task_input"]),
        }
        if executor_kind == "tool":
            execution_input["tool_call_budget"] = tool_call_budget
            execution_input["tool_audit_sink"] = instrumentation.record_tool_audit
        operation_started = instrumentation.elapsed_ms(task_span)
        try:
            if executor_kind == "tool":
                with bind_prompt_trace_sink(
                    instrumentation.record_prompt_invocation
                ):
                    raw_result = await executor(execution_input)
            else:
                with bind_operation_recorder(
                    partial(
                        instrumentation.record_operation,
                        task_id=task_id,
                    )
                ):
                    with bind_model_call_recorder(
                        partial(
                            instrumentation.record_model_call,
                            task_id=task_id,
                        )
                    ):
                        with bind_prompt_trace_sink(
                            instrumentation.record_prompt_invocation
                        ):
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
                        step_id=step_id,
                        task_id=task_id,
                        executor_kind=str(executor_kind),
                        executor_name=str(executor_name),
                    ),
                },
            )
            error_identity = exception_identity_fields(error)
            failure_output = {
                "status": "failed",
                "reason_code": "executor_error",
            }
            operation_duration = max(
                0,
                instrumentation.elapsed_ms(task_span) - operation_started,
            )
            if executor_kind == "tool":
                instrumentation.record_operation(
                    stage=OperationStage.TOOL,
                    status=OperationStatus.FAILED,
                    operation_name=str(executor_name),
                    duration_ms=operation_duration,
                    task_id=task_id,
                    error_code="executor_error",
                    input=observable_execution_input,
                    output=failure_output,
                    **error_identity,
                )
            task_results[task_id] = failed_task_result(
                task_id,
                "executor_error",
            )
            instrumentation.fail(
                task_span,
                reason_code="executor_error",
                output=failure_output,
                **error_identity,
            )
            continue

        operation_duration = max(
            0,
            instrumentation.elapsed_ms(task_span) - operation_started,
        )
        if executor_kind == "tool":
            instrumentation.record_operation(
                stage=OperationStage.TOOL,
                status=OperationStatus.SUCCEEDED,
                operation_name=str(executor_name),
                duration_ms=operation_duration,
                task_id=task_id,
                input=observable_execution_input,
                output=raw_result,
            )

        artifact_type = str(task.get("produces", "unknown"))
        artifact_span = instrumentation.start(
            RuntimeStage.ARTIFACT_VALIDATION,
            task_id=task_id,
            depends_on=tuple(str(item) for item in dependencies),
            artifact_id=build_artifact_id(
                request_id=request_id,
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
        if validation.valid and not _uses_bound_values(result, task_bindings):
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
                "aggregator" if executor_kind == "tool" else "agent"
            )
            instrumentation.succeed(
                task_span,
                response_owner=response_owner,
                output=result,
            )
        else:
            instrumentation.fail(
                task_span,
                reason_code=str(result.get("reason_code", "artifact_invalid")),
                output=result,
            )

    aggregation_span = instrumentation.start(RuntimeStage.AGGREGATION)
    try:
        output = aggregate_task_results(
            tasks=aggregate_tasks,
            task_results=task_results,
            step_id=step_id,
            field_registry=field_registry,
            outcome_reason=(
                str(plan["outcome_reason"])
                if plan.get("outcome_reason") is not None
                else None
            ),
        )
        has_execution_failure = any(
            result.get("status") != "completed"
            for result in task_results.values()
        )
        if (
            response_composer is not None
            and not has_execution_failure
            and plan.get("outcome_reason") is None
        ):
            with bind_model_call_recorder(instrumentation.record_model_call):
                with bind_prompt_trace_sink(
                    instrumentation.record_prompt_invocation
                ):
                    output = await response_composer(
                        output=output,
                        step_id=step_id,
                        field_registry=field_registry,
                    )
        if (
            counseling_enricher is not None
            and task_results
            and not has_execution_failure
            and plan.get("outcome_reason") is None
        ):
            # Optional counseling never owns task values, actions or request success.
            verified_output = deepcopy(output)
            try:
                with (
                    bind_operation_recorder(instrumentation.record_operation),
                    bind_model_call_recorder(instrumentation.record_model_call),
                    bind_prompt_trace_sink(instrumentation.record_prompt_invocation),
                ):
                    enriched = await counseling_enricher(
                        output=deepcopy(verified_output),
                        step_id=step_id,
                        question=question,
                        conversation_context=deepcopy(dict(conversation_context or {})),
                        task_types=tuple(
                            str(task.get("type", "")) for task in aggregate_tasks
                        ),
                    )
                original_response = str(verified_output.get("response", ""))
                enriched_response = enriched.get("response")
                if not isinstance(enriched_response, str) or not (
                    enriched_response == original_response
                    or enriched_response.startswith(original_response + "\n\n")
                ):
                    raise ValueError("counseling must preserve the verified response")
                output = {**verified_output, "response": enriched_response}
            except Exception as error:  # cancellation must propagate to the request owner
                logger.warning(
                    "counseling enrichment failed; verified response retained",
                    extra={
                        "event": "counseling_enrichment_fallback",
                        "error_type": type(error).__name__,
                    },
                )
                output = verified_output
    except Exception as error:
        logger.error(
            "aggregation raised",
            extra={
                "event": "aggregation_error",
                "detail": masked_exception_fields(
                    error,
                    request_id=request_id,
                    step_id=step_id,
                ),
            },
        )
        instrumentation.fail(aggregation_span, reason_code="aggregation_error")
        raise
    instrumentation.succeed(aggregation_span, output=output)
    return ExecutionReport(
        task_results=task_results,
        output=output,
        tasks_used=tasks_used,
        tool_calls_used=(
            tool_call_budget.used
        ),
    )


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
    bindings: Mapping[str, Any],
) -> bool:
    """Reject a form proposal that substituted a different bound value."""

    if "value" not in bindings:
        return True
    artifact = result.get("artifact")
    if not isinstance(artifact, Mapping) or artifact.get("type") != "form_change":
        return True
    for change in artifact.get("changes", []):
        if isinstance(change, Mapping) and change.get("value") == bindings["value"]:
            if "unit" in bindings and change.get("unit") != bindings["unit"]:
                continue
            return True
    return False
