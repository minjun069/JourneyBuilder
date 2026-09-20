"""Application flow for bounded planning, policy evaluation, and one repair."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from typing import Any

from core.context_window import ContextWindow
from core.observability_masking import exception_identity_fields, masked_exception_fields
from graph.plan_contracts import (
    PLAN_SCHEMA_VERSION,
    PlanIssue,
    RoutePlanValidation,
    context_window_payload,
    issue,
)
from graph.task_catalog import planning_guidance_for


TaskPlanner = Callable[..., Mapping[str, Any]]
PlanEvaluator = Callable[[Any], Any]
logger = logging.getLogger(__name__)


def create_route_plan(
    *,
    message: str,
    step_id: str,
    context_window: ContextWindow,
    planner: TaskPlanner,
    evaluator: PlanEvaluator,
    allowed_task_types: Sequence[str],
    registered_tool_capabilities: Sequence[str] = (),
) -> RoutePlanValidation:
    """Call the model, evaluate raw output, and repair at most once."""

    context = context_window_payload(context_window)
    context["route_constraints"] = planner_route_constraints(
        step_id=step_id,
        allowed_task_types=allowed_task_types,
        registered_tool_capabilities=registered_tool_capabilities,
    )
    diagnostics: list[dict[str, str]] = []
    last_issues: tuple[PlanIssue, ...] = ()
    planner_failed = False

    for attempt in range(2):
        try:
            raw_plan = planner(message=message, step_id=step_id, context=context)
            planner_failed = False
        except Exception as error:
            planner_failed = True
            diagnostic = {
                "component": "planner",
                "attempt": str(attempt + 1),
                **exception_identity_fields(error),
            }
            diagnostics.append(diagnostic)
            logger.error(
                "planner model adapter raised",
                extra={
                    "event": "planner_error",
                    "detail": masked_exception_fields(error, step_id=step_id),
                },
            )
            last_issues = (issue(
                "PLANNER_ERROR",
                "plan",
                "planner가 실행 계획을 만들지 못했습니다.",
            ),)
            if attempt == 0:
                context["validation_feedback"] = [item.to_dict() for item in last_issues]
                continue
            break

        decision = evaluator(raw_plan)
        if decision.allowed:
            return RoutePlanValidation(
                valid=True,
                plan=deepcopy(decision.plan),
                issues=(),
                diagnostics=tuple(diagnostics),
                retries_used=attempt,
            )
        last_issues = tuple(
            issue(item.code, item.path, item.message)
            for item in decision.violations
        )
        if attempt == 0:
            diagnostics.append({
                "component": "planner_repair",
                "attempt": "1",
                "issue_codes": ",".join(item.code for item in last_issues),
            })
            context["validation_feedback"] = [item.to_dict() for item in last_issues]
            continue
        break

    if planner_failed and _looks_like_explanatory_question(message):
        recovery_plan = {
            "status": "executable",
            "tasks": [{
                "id": "answer",
                "type": "answer_question",
                "description": message,
                "depends_on": [],
                "bindings": {},
            }],
        }
        decision = evaluator(recovery_plan)
        if decision.allowed:
            diagnostics.append({"component": "planner", "recovery": "single_qa_plan"})
            return RoutePlanValidation(
                valid=True,
                plan=deepcopy(decision.plan),
                issues=(),
                diagnostics=tuple(diagnostics),
                retries_used=1,
            )

    return RoutePlanValidation(
        valid=False,
        plan={"schema_version": PLAN_SCHEMA_VERSION, "tasks": []},
        issues=last_issues or (
            issue("PLAN_INVALID", "plan", "실행 계획을 검증하지 못했습니다."),
        ),
        diagnostics=tuple(diagnostics),
        retries_used=1,
    )


def planner_route_constraints(
    *,
    step_id: str,
    allowed_task_types: Sequence[str],
    registered_tool_capabilities: Sequence[str] = (),
) -> dict[str, Any]:
    ordered = sorted(set(allowed_task_types))
    return {
        "step_id": step_id,
        "available_tool_capabilities": sorted(set(registered_tool_capabilities)),
        "allowed_task_types": ordered,
        "task_type_guidance": planning_guidance_for(ordered),
    }


def _looks_like_explanatory_question(message: str) -> bool:
    compact = message.strip().replace(" ", "")
    if not compact:
        return False
    return (
        "?" in compact
        or compact.endswith(("뭐야", "무엇인가요", "뜻이야", "말하는거야", "인가요", "나요", "까요"))
        or any(token in compact for token in ("설명해줘", "알려줘", "무슨뜻", "어느정도기간"))
    )
