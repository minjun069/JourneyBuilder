"""Planner model schema, prompt binding, and provider adapter only."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.model_boundary import ModelPurpose, prompt_messages, secure_structured_invoke
from graph.plan_contracts import MAX_PLAN_TASKS
from prompts import get_prompt


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
        payload = self.model_dump(mode="json")
        for task in payload["tasks"]:
            if task.get("arguments") in ({}, None):
                task.pop("arguments")
        return payload


def invoke_planner_model(
    *,
    message: str,
    step_id: str,
    context: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Invoke the production planner and return untrusted raw plan data."""

    from core.llm import llm

    route_constraints = context.get("route_constraints")
    if not isinstance(route_constraints, Mapping):
        raise ValueError("planner route_constraints are required")
    allowed_task_types = route_constraints.get("allowed_task_types")
    if not isinstance(allowed_task_types, list) or not allowed_task_types:
        raise ValueError("planner allowed_task_types are required")
    response = secure_structured_invoke(
        llm,
        PlannerModelOutput,
        prompt_messages(PLANNER_PROMPT, {
            "max_tasks": MAX_PLAN_TASKS,
            "task_types": list(allowed_task_types),
            "route_constraints": deepcopy(dict(route_constraints)),
            "step_id": step_id,
            "context": dict(context),
            "message": message,
        }),
        purpose=ModelPurpose.PLANNER,
        operation_name="planner.plan",
        sensitive_payload={"message": message, "context": dict(context)},
        prompt=PLANNER_PROMPT,
        max_attempts=1,
    )
    return response.to_route_plan()


# Existing injection sites keep this callable name while the module owns only LLM work.
plan_tasks_with_model = invoke_planner_model
