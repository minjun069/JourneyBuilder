"""Deterministic task-to-tool authorization policy."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from tools.registry import FunctionToolDefinition, FunctionToolRegistry


def task_tool_context_is_authorized(
    *,
    task: Mapping,
    step_id: str,
    requested_tool: str,
    requested_agent: str,
    definition: FunctionToolDefinition | None,
) -> bool:
    task_type = task.get("type")
    route = task.get("route")
    if definition is None or not isinstance(route, Mapping):
        return False
    common_matches = (
        task.get("step_id") == step_id
        and requested_agent in definition.allowed_agents
        and task_type in definition.allowed_task_types
    )
    if task.get("executor_kind") == "tool":
        route_matches = (
            task.get("tool_name") == requested_tool
            and task.get("authorized_agent") == requested_agent
            and route.get("executor_kind") == "tool"
            and route.get("executor") == task.get("capability")
        )
    else:
        route_matches = (
            task.get("executor_kind") == "agent"
            and route.get("executor_kind") == "agent"
            and route.get("executor") == requested_agent
        )
    return common_matches and route_matches


def validate_task_tool_authorizations(
    registry: FunctionToolRegistry,
    task_contracts: Mapping[str, Mapping[str, Any]] | None = None,
) -> None:
    """Fail startup when canonical task and tool allowlists drift apart."""

    if task_contracts is None:
        return
    for task_type, contract in task_contracts.items():
        if contract.get("executor_kind") != "tool":
            continue
        tool_name = str(contract.get("tool_name", ""))
        agent = str(contract.get("authorized_agent", ""))
        definition = registry.definition_for(tool_name)
        if definition is None:
            raise ValueError(
                f"tool task authorization references an unknown tool: {task_type}"
            )
        if (
            agent not in definition.allowed_agents
            or task_type not in definition.allowed_task_types
        ):
            raise ValueError(
                f"tool task authorization is inconsistent: {task_type}"
            )
