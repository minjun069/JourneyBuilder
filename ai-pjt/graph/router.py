"""Untrusted deterministic enrichment for planner output.

The router never validates or authorizes a plan. It only derives proposed
execution metadata from the planner task type registry. ``evaluate_route_plan``
is the single entry point that validates both the original planner structure
and this enrichment before execution.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from core.step_registry import load_step_candidates
from graph.planner import TASK_TYPE_CONTRACTS


TASK_TYPE_DEFAULT_AGENTS = {
    "answer_question": "qa",
    "propose_form_change": "input_assist",
    "review_validation_error": "error_review",
    "interpret_chart": "chart_interpret",
    "collect_qualitative_info": "elicit",
}


def allowed_task_types_for(
    *,
    step_id: str,
    step_candidates: Mapping[str, Sequence[str]] | None = None,
    registered_tool_capabilities: Sequence[str] = (),
) -> tuple[str, ...]:
    """Return the trusted task menu for one runtime step."""

    active_steps = load_step_candidates() if step_candidates is None else step_candidates
    candidates = active_steps.get(step_id, ())
    allowed_agents = set(candidates)
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


def select_route(
    *,
    task: Mapping[str, Any],
    registered_tool_capabilities: Sequence[str] = (),
) -> dict[str, str]:
    """Derive one untrusted route proposal without consulting a model."""

    source_task_id = task.get("id")
    task_id = (
        source_task_id
        if isinstance(source_task_id, str) and source_task_id.strip()
        else "task-unknown"
    )
    task_type = str(task.get("type", ""))
    contract = TASK_TYPE_CONTRACTS.get(task_type)
    if contract is None:
        return {
            "source_task_id": task_id,
            "executor_kind": "agent",
            "executor": "unresolved",
            "reason_code": "plan_invalid",
        }

    executor_kind = str(contract["executor_kind"])
    if executor_kind == "tool":
        capability = str(contract["capability"])
        return {
            "source_task_id": task_id,
            "executor_kind": "tool",
            "executor": capability,
            "reason_code": (
                "capability_match"
                if capability in set(registered_tool_capabilities)
                else "capability_unavailable"
            ),
        }

    executor = TASK_TYPE_DEFAULT_AGENTS.get(task_type, "unresolved")
    return {
        "source_task_id": task_id,
        "executor_kind": "agent",
        "executor": executor,
        "reason_code": (
            "intent_match" if executor != "unresolved" else "plan_invalid"
        ),
    }


def enrich_route_plan(
    *,
    raw_plan: Any,
    step_id: str,
    registered_tool_capabilities: Sequence[str] = (),
) -> dict[str, Any]:
    """Attach untrusted route proposals while preserving raw planner output."""

    routes: list[dict[str, str]] = []
    if isinstance(raw_plan, Mapping):
        tasks = raw_plan.get("tasks")
        if isinstance(tasks, list):
            routes = [
                select_route(
                    task=task,
                    registered_tool_capabilities=registered_tool_capabilities,
                )
                for task in tasks
                if isinstance(task, Mapping)
            ]
    return {
        "step_id": step_id,
        "planner_plan": deepcopy(raw_plan),
        "route_enrichments": routes,
    }
