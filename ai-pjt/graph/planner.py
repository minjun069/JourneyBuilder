"""Planner model schema, prompt binding, and provider adapter only."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.model_boundary import ModelPurpose, prompt_messages, secure_structured_invoke
from graph.plan_contracts import MAX_PLAN_TASKS
from core.prompt_inputs import conversation_for_model
from prompts import get_prompt


PLANNER_PROMPT = get_prompt("planner.plan")


class PlannerTaskOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    description: str = Field(min_length=1)
    reference_id: str | None = None
    reference_age: int | None = Field(default=None, ge=0, le=120)


class PlannerModelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["executable", "ambiguous", "unsupported"] = "executable"
    requests: list[PlannerTaskOutput] = Field(default_factory=list, max_length=MAX_PLAN_TASKS)

    @model_validator(mode="after")
    def validate_status_contract(self) -> "PlannerModelOutput":
        if self.status == "executable" and not self.requests:
            raise ValueError("executable planner output requires requests")
        if self.status != "executable" and self.requests:
            raise ValueError("non-executable planner output must not include requests")
        return self

    def to_request_plan(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


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
            "tasks": route_constraints["task_type_guidance"],
            "references": route_constraints.get("reference_options", []),
            "step_id": step_id,
            "context": {
                **conversation_for_model(context),
                "current_age": next((
                    value for path, value in context.get("confirmed_facts", {}).items()
                    if path in {"age", "basic_info.age"}
                ), None),
            },
            "feedback": context.get("validation_feedback", []),
            "message": message,
        }),
        purpose=ModelPurpose.PLANNER,
        operation_name="planner.plan",
        sensitive_payload={"message": message, "context": dict(context)},
        prompt=PLANNER_PROMPT,
        max_attempts=1,
    )
    return response.to_request_plan()


# Existing injection sites keep this callable name while the module owns only LLM work.
plan_tasks_with_model = invoke_planner_model
