"""Deterministic task routing with a provider adapter at the boundary."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from core.model_boundary import ModelPurpose, secure_invoke, secure_system_prompt
from core.step_registry import load_agent_descriptions, load_step_candidates
from graph.state import ChatState


STEP_CANDIDATES = load_step_candidates()
AGENT_DESCRIPTIONS = load_agent_descriptions()
FALLBACK_AGENT = "fallback"
CLASSIFIER_STATUSES = {"matched", "ambiguous", "unsupported"}

TaskClassifier = Callable[..., Mapping[str, Any]]


ROUTER_PROMPT = secure_system_prompt("""\
사용자 요청 하나를 분석해 후보 에이전트 중 하나를 선택하세요.
반드시 JSON object만 반환하세요.

허용 status: matched, ambiguous, unsupported
matched이면 agent에 후보 중 하나를 넣고, 그 외에는 null을 넣으세요.

현재 단계: {step_id}
요청 종류: {task_type}
요청 설명: {description}

후보 에이전트:
{candidates}
""")


def _decision(
    *,
    task_id: str,
    executor_kind: str,
    executor: str,
    reason_code: str,
) -> dict[str, str]:
    return {
        "task_id": task_id,
        "executor_kind": executor_kind,
        "executor": executor,
        "reason_code": reason_code,
    }


def _task_id(task: Mapping[str, Any]) -> str:
    value = task.get("id")
    return value if isinstance(value, str) and value.strip() else "task-unknown"


def select_route(
    *,
    task: Mapping[str, Any],
    step_id: str,
    step_candidates: Mapping[str, Sequence[str]],
    classifier: TaskClassifier,
    registered_tool_capabilities: Sequence[str] = (),
) -> dict[str, str]:
    """Select one safe executor for one validated task.

    The classifier proposes an Agent. This function owns the final policy
    decision, including the step allowlist and all fail-closed outcomes. Tool
    tasks name a capability rather than a concrete handler; F09 injects the
    production capability registry.
    """

    task_id = _task_id(task)
    executor_kind = task.get("executor_kind", "agent")
    if executor_kind == "tool":
        capability = task.get("capability")
        if (
            isinstance(capability, str)
            and capability
            and capability in registered_tool_capabilities
        ):
            return _decision(
                task_id=task_id,
                executor_kind="tool",
                executor=capability,
                reason_code="capability_match",
            )
        return _decision(
            task_id=task_id,
            executor_kind="tool",
            executor=str(capability or "unavailable"),
            reason_code="capability_unavailable",
        )

    if executor_kind != "agent":
        return _decision(
            task_id=task_id,
            executor_kind="agent",
            executor=FALLBACK_AGENT,
            reason_code="plan_invalid",
        )

    candidates = step_candidates.get(step_id)
    if candidates is None:
        return _decision(
            task_id=task_id,
            executor_kind="agent",
            executor=FALLBACK_AGENT,
            reason_code="plan_invalid",
        )

    candidate_tuple = tuple(candidates)
    try:
        classified = classifier(task=task, candidates=candidate_tuple)
    except Exception:
        return _decision(
            task_id=task_id,
            executor_kind="agent",
            executor=FALLBACK_AGENT,
            reason_code="plan_invalid",
        )

    if not isinstance(classified, Mapping):
        status = None
        chosen = None
    else:
        status = classified.get("status")
        chosen = classified.get("agent")

    if status not in CLASSIFIER_STATUSES:
        reason_code = "plan_invalid"
    elif status == "ambiguous":
        reason_code = "ambiguous_intent"
    elif status == "unsupported":
        reason_code = "unsupported_intent"
    elif not isinstance(chosen, str) or not chosen:
        reason_code = "plan_invalid"
    elif chosen not in candidate_tuple:
        reason_code = "plan_invalid"
    else:
        return _decision(
            task_id=task_id,
            executor_kind="agent",
            executor=chosen,
            reason_code="intent_match",
        )

    return _decision(
        task_id=task_id,
        executor_kind="agent",
        executor=FALLBACK_AGENT,
        reason_code=reason_code,
    )


def classify_task_with_model(
    *,
    task: Mapping[str, Any],
    candidates: tuple[str, ...],
) -> Mapping[str, Any]:
    """Normalize the model response into the small classifier contract."""

    from langchain_core.messages import HumanMessage, SystemMessage

    from core.llm import llm

    candidate_lines = "\n".join(
        f"- {name}: {AGENT_DESCRIPTIONS[name]}" for name in candidates
    )
    system = SystemMessage(content=ROUTER_PROMPT.format(
        step_id=task.get("step_id", ""),
        task_type=task.get("type", "user_request"),
        description=task.get("description", ""),
        candidates=candidate_lines,
    ))
    result = secure_invoke(
        llm,
        [
            system,
            HumanMessage(content=str(task.get("description", ""))),
        ],
        purpose=ModelPurpose.ROUTER,
        sensitive_payload={"task": dict(task)},
    )
    content = result.content if isinstance(result.content, str) else ""
    payload = json.loads(content)
    if not isinstance(payload, dict):
        raise ValueError("router classifier response must be an object")
    return payload


def router(state: ChatState) -> dict[str, Any]:
    """Compatibility node for a single task until the T03 planner is bound."""

    task = state.get("current_task") or {
        "id": "task-1",
        "type": "user_request",
        "description": state["message"],
        "executor_kind": "agent",
        "step_id": state["step_id"],
    }
    decision = select_route(
        task=task,
        step_id=state["step_id"],
        step_candidates=STEP_CANDIDATES,
        classifier=classify_task_with_model,
    )
    return {"route": decision, "next": decision["executor"]}
