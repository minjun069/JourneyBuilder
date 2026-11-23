"""Convert Function tool results into validated graph task artifacts."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from graph.runtime_types import RuntimeExecutor
from graph.task_contracts import TOOL_ARTIFACT_SCHEMA_VERSION, failed_task_result

from .contracts import ToolCallResult, ToolErrorCode
from .runtime import FunctionToolRuntime, RuntimeToolRequest


REFERENCE_CAPABILITY = "lookup_reference_value"


def build_graph_tool_executors(
    runtime: FunctionToolRuntime,
) -> dict[str, RuntimeExecutor]:
    return {
        REFERENCE_CAPABILITY: _reference_executor(runtime),
    }


def _reference_executor(runtime: FunctionToolRuntime) -> RuntimeExecutor:
    async def execute(payload: dict[str, Any]) -> Mapping[str, Any]:
        task = payload.get("task")
        if not isinstance(task, Mapping) or task.get("type") != "lookup_reference_value":
            return failed_task_result("task-unknown", "invalid_tool_task")
        task_id = str(task.get("id", "task-unknown"))
        arguments = task.get("arguments")
        if not isinstance(arguments, Mapping):
            return failed_task_result(task_id, "invalid_tool_arguments")
        result = await runtime.invoke(
            RuntimeToolRequest(
                tool_name=str(task.get("tool_name")),
                arguments=deepcopy(dict(arguments)),
                agent=str(task.get("authorized_agent")),
            ),
            payload,
        )
        if not result.ok:
            return _tool_failure(task_id, result)
        reference = result.data.get("reference") if result.data else None
        if not isinstance(reference, Mapping):
            return failed_task_result(task_id, "invalid_tool_result")
        if any(
            reference.get(field) != arguments.get(field)
            for field in ("reference_id", "age", "period", "population")
        ):
            return failed_task_result(task_id, "reference_contract_mismatch")
        return {
            "task_id": task_id,
            "status": "completed",
            "artifact": {
                "type": "reference_value",
                "schema_version": TOOL_ARTIFACT_SCHEMA_VERSION,
                "producer_task_id": task_id,
                **deepcopy(dict(reference)),
            },
        }

    return execute


def _tool_failure(task_id: str, result: ToolCallResult) -> dict[str, Any]:
    error = result.error
    reason_code = (
        error.code.value.lower()
        if error is not None
        else ToolErrorCode.INTERNAL_ERROR.value.lower()
    )
    failed = failed_task_result(
        task_id,
        reason_code,
        message=error.message if error is not None else None,
    )
    if error is not None:
        failed["tool_error"] = error.to_dict()
    return failed
