"""Single validation entry point for enriched planner candidates."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from core.step_registry import load_step_candidates
from graph.planner import _validate_plan_structure
from graph.router import TASK_TYPE_DEFAULT_AGENTS, allowed_task_types_for


PLAN_GUARDRAIL_ALLOW_REASON = "PLAN_GUARDRAIL_ALLOWED"
ENRICHMENT_FIELDS = {
    "source_task_id",
    "executor_kind",
    "executor",
    "reason_code",
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


def evaluate_route_plan(
    candidate: Any,
    *,
    step_id: str,
    step_candidates: Mapping[str, Sequence[str]] | None = None,
    registered_tool_capabilities: Sequence[str] = (),
) -> PlanGuardrailDecision:
    """Validate planner structure first, then validate route enrichment.

    The deterministic router is intentionally not trusted.  This function
    recomputes the step task menu and execution contract before returning the
    only canonical plan that the executor may consume.
    """

    if not isinstance(candidate, Mapping):
        return _blocked((
            _violation("INVALID_PLAN_CANDIDATE", "candidate", "plan candidate는 object여야 합니다."),
        ))
    if set(candidate) != {
        "plan_id",
        "step_id",
        "planner_plan",
        "route_enrichments",
    }:
        return _blocked((
            _violation(
                "INVALID_PLAN_CANDIDATE_FIELDS",
                "candidate",
                "plan candidate의 trusted field 구성이 올바르지 않습니다.",
            ),
        ))
    if candidate.get("step_id") != step_id:
        return _blocked((
            _violation(
                "PLAN_STEP_MISMATCH",
                "candidate.step_id",
                "plan candidate의 step이 현재 요청과 일치하지 않습니다.",
            ),
        ))
    plan_id = candidate.get("plan_id")
    if not isinstance(plan_id, str) or not plan_id.strip():
        return _blocked((
            _violation("INVALID_PLAN_ID", "candidate.plan_id", "runtime plan ID가 필요합니다."),
        ))

    active_steps = load_step_candidates() if step_candidates is None else step_candidates
    active_tools = tuple(registered_tool_capabilities)
    allowed_task_types = allowed_task_types_for(
        step_id=step_id,
        step_candidates=active_steps,
        registered_tool_capabilities=active_tools,
    )
    structural = _validate_plan_structure(
        candidate.get("planner_plan"),
        step_id=step_id,
        allowed_task_types=allowed_task_types,
    )
    if not structural.valid:
        return _blocked(
            PlanGuardrailViolation(issue.code, issue.path, issue.message)
            for issue in structural.issues
        )

    enrichments = candidate.get("route_enrichments")
    if not isinstance(enrichments, list):
        return _blocked((
            _violation(
                "INVALID_ROUTE_ENRICHMENTS",
                "candidate.route_enrichments",
                "route enrichment는 array여야 합니다.",
            ),
        ))
    trusted_tasks = structural.plan["tasks"]
    if len(enrichments) != len(trusted_tasks):
        return _blocked((
            _violation(
                "ROUTE_COUNT_MISMATCH",
                "candidate.route_enrichments",
                "route enrichment 수가 검증된 task 수와 일치하지 않습니다.",
            ),
        ))

    planner_plan = candidate.get("planner_plan")
    raw_tasks = (
        planner_plan.get("tasks", [])
        if isinstance(planner_plan, Mapping)
        else []
    )
    violations: list[PlanGuardrailViolation] = []
    routed_tasks: list[dict[str, Any]] = []
    allowed_agents = set(active_steps.get(step_id, ()))
    active_tool_set = set(active_tools)

    for index, (trusted, enrichment) in enumerate(
        zip(trusted_tasks, enrichments, strict=True)
    ):
        path = f"candidate.route_enrichments[{index}]"
        if not isinstance(enrichment, Mapping):
            violations.append(_violation(
                "INVALID_ROUTE_ENRICHMENT",
                path,
                "route enrichment는 object여야 합니다.",
            ))
            continue
        if set(enrichment) != ENRICHMENT_FIELDS:
            violations.append(_violation(
                "INVALID_ROUTE_FIELDS",
                path,
                "route enrichment field 구성이 올바르지 않습니다.",
            ))
        raw_task = raw_tasks[index] if index < len(raw_tasks) else None
        source_task_id = raw_task.get("id") if isinstance(raw_task, Mapping) else None
        if enrichment.get("source_task_id") != source_task_id:
            violations.append(_violation(
                "ROUTE_SOURCE_TASK_MISMATCH",
                f"{path}.source_task_id",
                "route source가 planner task와 일치하지 않습니다.",
            ))

        expected_kind = trusted["executor_kind"]
        executor = enrichment.get("executor")
        expected_reason = "intent_match"
        if enrichment.get("executor_kind") != expected_kind:
            violations.append(_violation(
                "ROUTE_EXECUTOR_KIND_MISMATCH",
                f"{path}.executor_kind",
                "route executor kind가 task 계약과 일치하지 않습니다.",
            ))

        if expected_kind == "agent":
            expected_executor = TASK_TYPE_DEFAULT_AGENTS.get(str(trusted.get("type")))
            if executor != expected_executor:
                violations.append(_violation(
                    "ROUTE_EXECUTOR_MISMATCH",
                    f"{path}.executor",
                    "Agent route가 task type의 결정론적 매핑과 일치하지 않습니다.",
                ))
            if executor not in allowed_agents:
                violations.append(_violation(
                    "AGENT_NOT_ALLOWLISTED",
                    f"{path}.executor",
                    "현재 step에서 허용되지 않은 Agent입니다.",
                ))
        else:
            expected_executor = trusted.get("capability")
            expected_reason = "capability_match"
            if executor != expected_executor:
                violations.append(_violation(
                    "TOOL_CAPABILITY_MISMATCH",
                    f"{path}.executor",
                    "route tool이 task type의 capability와 일치하지 않습니다.",
                ))
            if executor not in active_tool_set:
                violations.append(_violation(
                    "TOOL_NOT_ALLOWLISTED",
                    f"{path}.executor",
                    "현재 runtime에 등록되지 않은 tool capability입니다.",
                ))
        if enrichment.get("reason_code") != expected_reason:
            violations.append(_violation(
                "ROUTE_REASON_MISMATCH",
                f"{path}.reason_code",
                "route reason이 검증된 실행 결정과 일치하지 않습니다.",
            ))

        routed = deepcopy(trusted)
        routed["route"] = {
            "task_id": trusted["id"],
            "executor_kind": expected_kind,
            "executor": expected_executor,
            "reason_code": expected_reason,
        }
        routed_tasks.append(routed)

    if violations:
        return _blocked(violations)

    plan = deepcopy(structural.plan)
    plan["plan_id"] = plan_id
    plan["tasks"] = routed_tasks
    return PlanGuardrailDecision(
        decision="allow",
        reason_code=PLAN_GUARDRAIL_ALLOW_REASON,
        violations=(),
        plan=plan,
    )


def _blocked(
    violations: Iterable[PlanGuardrailViolation],
) -> PlanGuardrailDecision:
    collected = tuple(violations)
    primary = collected[0].code if collected else "PLAN_GUARDRAIL_FAILED"
    return PlanGuardrailDecision(
        decision="block",
        reason_code=primary,
        violations=collected,
        plan={"schema_version": "1.0", "tasks": []},
    )


def _violation(code: str, path: str, message: str) -> PlanGuardrailViolation:
    return PlanGuardrailViolation(code=code, path=path, message=message)
