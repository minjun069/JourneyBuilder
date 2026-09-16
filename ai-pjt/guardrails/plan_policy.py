"""Pure allow/block policy for a fully routed F03 ``RoutePlan``.

The F03 planner remains the source of truth for task, dependency, and binding
normalization.  This module reuses that validator, then verifies that the
routing metadata still agrees with trusted step and tool registries before a
later integration layer may execute the plan.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from core.step_registry import load_step_candidates
from graph.planner import TASK_FIELDS, TASK_TYPE_CONTRACTS, validate_route_plan


PLAN_GUARDRAIL_ALLOW_REASON = "PLAN_GUARDRAIL_ALLOWED"
CANONICAL_TASK_FIELDS = TASK_FIELDS | {
    "executor_kind",
    "produces",
    "step_id",
    "capability",
    "tool_name",
    "authorized_agent",
    "route",
}
ROUTE_FIELDS = {"task_id", "executor_kind", "executor", "reason_code"}
ROUTED_TASK_METADATA_FIELDS = {
    "executor_kind",
    "produces",
    "step_id",
    "capability",
    "tool_name",
    "authorized_agent",
    "route",
}


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

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "reason_code": self.reason_code,
            "violations": [violation.to_dict() for violation in self.violations],
        }


def evaluate_route_plan(
    plan: Any,
    *,
    step_id: str,
    step_candidates: Mapping[str, Sequence[str]] | None = None,
    registered_tool_capabilities: Sequence[str] = (),
) -> PlanGuardrailDecision:
    """Evaluate a routed plan without invoking an Agent, tool, or model.

    Structural issues retain the existing F03 validator reason codes.  Route
    issues are accumulated in deterministic task/field order; the first code is
    the stable primary reason used by the security regression corpus.
    """

    validation = validate_route_plan(_planner_view(plan), step_id=step_id)
    if not validation.valid:
        return _blocked(
            PlanGuardrailViolation(issue.code, issue.path, issue.message)
            for issue in validation.issues
        )

    if not isinstance(plan, Mapping):
        return _blocked((
            _violation("INVALID_PLAN", "plan", "route plan은 object여야 합니다."),
        ))
    candidate_tasks = plan.get("tasks")
    if not isinstance(candidate_tasks, list):
        return _blocked((
            _violation("INVALID_TASKS", "plan.tasks", "tasks는 array여야 합니다."),
        ))

    trusted_tasks = validation.plan["tasks"]
    active_steps = (
        load_step_candidates() if step_candidates is None else step_candidates
    )
    active_tools = set(registered_tool_capabilities)
    violations: list[PlanGuardrailViolation] = []

    for index, (candidate, trusted) in enumerate(
        zip(candidate_tasks, trusted_tasks, strict=True)
    ):
        if not isinstance(candidate, Mapping):
            # The shared planner validator already reports this branch.
            continue
        path = f"plan.tasks[{index}]"
        unknown_fields = set(candidate) - CANONICAL_TASK_FIELDS
        if unknown_fields:
            violations.append(_violation(
                "UNKNOWN_CANONICAL_TASK_FIELDS",
                path,
                f"route task에 허용되지 않은 field가 있습니다: "
                f"{', '.join(sorted(unknown_fields))}",
            ))

        _compare_trusted_field(violations, candidate, trusted, path, "id")
        _compare_trusted_field(violations, candidate, trusted, path, "depends_on")
        _compare_trusted_field(violations, candidate, trusted, path, "bindings")
        _compare_trusted_field(violations, candidate, trusted, path, "executor_kind")
        _compare_trusted_field(violations, candidate, trusted, path, "produces")
        _compare_trusted_field(violations, candidate, trusted, path, "step_id")

        expected_kind = trusted["executor_kind"]
        if expected_kind == "tool":
            _compare_trusted_field(
                violations,
                candidate,
                trusted,
                path,
                "capability",
                code="TASK_TOOL_CAPABILITY_MISMATCH",
            )
            _compare_trusted_field(
                violations,
                candidate,
                trusted,
                path,
                "tool_name",
                code="TASK_TOOL_NAME_MISMATCH",
            )
            _compare_trusted_field(
                violations,
                candidate,
                trusted,
                path,
                "authorized_agent",
                code="TASK_TOOL_AGENT_MISMATCH",
            )
            _compare_trusted_field(
                violations,
                candidate,
                trusted,
                path,
                "arguments",
                code="TRUSTED_TOOL_ARGUMENT_MISMATCH",
            )
        elif "capability" in candidate:
            violations.append(_violation(
                "UNEXPECTED_TASK_CAPABILITY",
                f"{path}.capability",
                "Agent task에는 tool capability를 지정할 수 없습니다.",
            ))

        route = candidate.get("route")
        if not isinstance(route, Mapping):
            violations.append(_violation(
                "ROUTE_MISSING",
                f"{path}.route",
                "검증할 route 결정이 없습니다.",
            ))
            continue
        unknown_route_fields = set(route) - ROUTE_FIELDS
        if unknown_route_fields:
            violations.append(_violation(
                "UNKNOWN_ROUTE_FIELDS",
                f"{path}.route",
                f"route에 허용되지 않은 field가 있습니다: "
                f"{', '.join(sorted(unknown_route_fields))}",
            ))
        if route.get("task_id") != trusted["id"]:
            violations.append(_violation(
                "ROUTE_TASK_ID_MISMATCH",
                f"{path}.route.task_id",
                "route task_id가 검증된 task와 일치하지 않습니다.",
            ))
        if route.get("executor_kind") != expected_kind:
            violations.append(_violation(
                "ROUTE_EXECUTOR_KIND_MISMATCH",
                f"{path}.route.executor_kind",
                "route executor kind가 task 계약과 일치하지 않습니다.",
            ))

        executor = route.get("executor")
        if expected_kind == "agent":
            if trusted.get("type") == "fallback":
                if executor != "fallback":
                    violations.append(_violation(
                        "AGENT_NOT_ALLOWLISTED",
                        f"{path}.route.executor",
                        "Fallback task는 fallback Agent만 실행할 수 있습니다.",
                    ))
                continue
            allowed_agents = active_steps.get(step_id)
            if allowed_agents is None:
                violations.append(_violation(
                    "UNKNOWN_STEP",
                    f"{path}.step_id",
                    "Agent allowlist를 확인할 수 없는 step입니다.",
                ))
            elif not isinstance(executor, str) or executor not in allowed_agents:
                violations.append(_violation(
                    "AGENT_NOT_ALLOWLISTED",
                    f"{path}.route.executor",
                    "현재 step에서 허용되지 않은 Agent입니다.",
                ))
        else:
            trusted_capability = trusted["capability"]
            if executor != trusted_capability:
                violations.append(_violation(
                    "TOOL_CAPABILITY_MISMATCH",
                    f"{path}.route.executor",
                    "route tool이 task type의 trusted capability와 일치하지 않습니다.",
                ))
            if not isinstance(executor, str) or executor not in active_tools:
                violations.append(_violation(
                    "TOOL_NOT_ALLOWLISTED",
                    f"{path}.route.executor",
                    "현재 runtime에 등록되지 않은 tool capability입니다.",
                ))

    if violations:
        return _blocked(violations)
    return PlanGuardrailDecision(
        decision="allow",
        reason_code=PLAN_GUARDRAIL_ALLOW_REASON,
        violations=(),
    )


def _planner_view(plan: Any) -> Any:
    """Project a routed plan back to the exact F03 planner input contract."""

    if not isinstance(plan, Mapping):
        return plan
    tasks = plan.get("tasks")
    if not isinstance(tasks, list):
        return {"tasks": tasks}
    projected: list[Any] = []
    for task in tasks:
        if not isinstance(task, Mapping):
            projected.append(task)
            continue
        contract = TASK_TYPE_CONTRACTS.get(str(task.get("type")), {})
        projected.append({
            key: deepcopy(value)
            for key, value in task.items()
            if key not in ROUTED_TASK_METADATA_FIELDS
            and not (key == "arguments" and "fixed_arguments" in contract)
        })
    planner_view = {"tasks": projected}
    if "elicit_routing" in plan:
        planner_view["elicit_routing"] = deepcopy(plan["elicit_routing"])
    return planner_view


def _compare_trusted_field(
    violations: list[PlanGuardrailViolation],
    candidate: Mapping[str, Any],
    trusted: Mapping[str, Any],
    path: str,
    field: str,
    *,
    code: str = "TRUSTED_TASK_METADATA_MISMATCH",
) -> None:
    if candidate.get(field) == trusted.get(field):
        return
    violations.append(_violation(
        code,
        f"{path}.{field}",
        f"{field} 값이 task type의 trusted 계약과 일치하지 않습니다.",
    ))


def _blocked(
    violations: Iterable[PlanGuardrailViolation],
) -> PlanGuardrailDecision:
    collected = tuple(violations)
    primary = collected[0].code if collected else "PLAN_GUARDRAIL_FAILED"
    return PlanGuardrailDecision(
        decision="block",
        reason_code=primary,
        violations=collected,
    )


def _violation(code: str, path: str, message: str) -> PlanGuardrailViolation:
    return PlanGuardrailViolation(code=code, path=path, message=message)
