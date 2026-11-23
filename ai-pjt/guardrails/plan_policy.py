"""Single deterministic entry point for plan validation and canonical routing."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from core.step_registry import load_step_candidates
from graph.plan_contracts import PLAN_SCHEMA_VERSION
from graph.plan_validation import validate_plan_structure
from graph.task_catalog import (
    TASK_TYPE_DEFAULT_AGENTS,
    TASK_TYPE_CONTRACTS,
    allowed_task_types_for,
)


PLAN_GUARDRAIL_ALLOW_REASON = "PLAN_GUARDRAIL_ALLOWED"


@dataclass(frozen=True)
class PlanGuardrailViolation:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


@dataclass(frozen=True)
class PlanGuardrailDecision:
    decision: str
    reason_code: str
    violations: tuple[PlanGuardrailViolation, ...]
    plan: dict[str, Any]

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "reason_code": self.reason_code,
            "violations": [violation.to_dict() for violation in self.violations],
        }


def evaluate_and_route(
    raw_plan: Any,
    *,
    step_id: str,
    step_candidates: Mapping[str, Sequence[str]] | None = None,
    registered_tool_capabilities: Sequence[str] = (),
    validation_enabled: bool = True,
) -> PlanGuardrailDecision:
    """Validate raw planner output and attach the only executable route metadata."""

    if not validation_enabled:
        return _route_without_validation(raw_plan, step_id=step_id)

    active_steps = load_step_candidates() if step_candidates is None else step_candidates
    active_tools = tuple(registered_tool_capabilities)
    structural = validate_plan_structure(
        raw_plan,
        step_id=step_id,
        allowed_task_types=allowed_task_types_for(
            step_id=step_id,
            step_candidates=active_steps,
            registered_tool_capabilities=active_tools,
        ),
    )
    if not structural.valid:
        return _blocked(
            PlanGuardrailViolation(item.code, item.path, item.message)
            for item in structural.issues
        )

    allowed_agents = set(active_steps.get(step_id, ()))
    active_tool_set = set(active_tools)
    routed_tasks: list[dict[str, Any]] = []
    violations: list[PlanGuardrailViolation] = []
    for index, trusted in enumerate(structural.plan["tasks"]):
        path = f"plan.tasks[{index}]"
        expected_kind = trusted["executor_kind"]
        expected_reason = "intent_match"
        if expected_kind == "agent":
            executor = TASK_TYPE_DEFAULT_AGENTS.get(str(trusted.get("type")))
            if executor is None:
                violations.append(_violation(
                    "ROUTE_EXECUTOR_UNRESOLVED",
                    f"{path}.type",
                    "task type의 Agent 실행자를 결정할 수 없습니다.",
                ))
                continue
            if executor not in allowed_agents:
                violations.append(_violation(
                    "AGENT_NOT_ALLOWLISTED",
                    f"{path}.type",
                    "현재 step에서 허용되지 않은 Agent입니다.",
                ))
        else:
            executor = str(trusted.get("capability", ""))
            expected_reason = "capability_match"
            if executor not in active_tool_set:
                violations.append(_violation(
                    "TOOL_NOT_ALLOWLISTED",
                    f"{path}.type",
                    "현재 runtime에 등록되지 않은 tool capability입니다.",
                ))
        routed = deepcopy(trusted)
        routed["route"] = {
            "task_id": trusted["id"],
            "executor_kind": expected_kind,
            "executor": executor,
            "reason_code": expected_reason,
        }
        routed_tasks.append(routed)

    if violations:
        return _blocked(violations)
    plan = deepcopy(structural.plan)
    plan["tasks"] = routed_tasks
    return PlanGuardrailDecision(
        decision="allow",
        reason_code=PLAN_GUARDRAIL_ALLOW_REASON,
        violations=(),
        plan=plan,
    )


def _route_without_validation(raw_plan: Mapping[str, Any], *, step_id: str) -> PlanGuardrailDecision:
    """Temporarily route compiled plans without structural or step policy checks."""
    plan: dict[str, Any] = {"schema_version": PLAN_SCHEMA_VERSION, "tasks": []}
    status = raw_plan.get("status", "executable")
    if status in {"ambiguous", "unsupported"}:
        plan["outcome_reason"] = f"{status}_intent"
    else:
        raw_tasks = raw_plan["tasks"]
        aliases = {task["id"]: f"task-{index + 1}" for index, task in enumerate(raw_tasks)}
        for raw in raw_tasks:
            contract = TASK_TYPE_CONTRACTS[raw["type"]]
            kind = contract["executor_kind"]
            task = {
                "id": aliases[raw["id"]], "type": raw["type"],
                "description": raw["description"], "step_id": step_id,
                "depends_on": [aliases[item] for item in raw.get("depends_on", [])],
                "bindings": {
                    name: {"task_id": aliases[binding["task_id"]], "path": binding["path"]}
                    for name, binding in raw.get("bindings", {}).items()
                },
                "executor_kind": kind, "produces": contract["produces"],
            }
            if kind == "tool":
                task.update({name: contract[name] for name in ("capability", "tool_name", "authorized_agent")})
                task["arguments"] = deepcopy(contract.get("fixed_arguments", raw.get("arguments", {})))
            task["route"] = {
                "task_id": task["id"], "executor_kind": kind,
                "executor": TASK_TYPE_DEFAULT_AGENTS[raw["type"]] if kind == "agent" else contract["capability"],
                "reason_code": "intent_match" if kind == "agent" else "capability_match",
            }
            plan["tasks"].append(task)
    return PlanGuardrailDecision("allow", "PLAN_VALIDATION_DISABLED", (), plan)


# Temporary import-compatible name. It now accepts raw planner output directly.
evaluate_route_plan = evaluate_and_route


def _blocked(
    violations: Iterable[PlanGuardrailViolation],
) -> PlanGuardrailDecision:
    collected = tuple(violations)
    primary = collected[0].code if collected else "PLAN_GUARDRAIL_FAILED"
    return PlanGuardrailDecision(
        decision="block",
        reason_code=primary,
        violations=collected,
        plan={"schema_version": PLAN_SCHEMA_VERSION, "tasks": []},
    )


def _violation(code: str, path: str, message: str) -> PlanGuardrailViolation:
    return PlanGuardrailViolation(code=code, path=path, message=message)
