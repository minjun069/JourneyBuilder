"""Deterministic task routing with a provider adapter at the boundary."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator

from core.model_boundary import (
    ModelPurpose,
    secure_structured_invoke,
    secure_system_prompt,
)
from core.observability_masking import (
    exception_identity_fields,
    masked_exception_fields,
)
from core.step_registry import load_agent_descriptions, load_step_candidates
from graph.state import ChatState


STEP_CANDIDATES = load_step_candidates()
AGENT_DESCRIPTIONS = load_agent_descriptions()
FALLBACK_AGENT = "fallback"
CLASSIFIER_STATUSES = {"matched", "ambiguous", "unsupported"}

TaskClassifier = Callable[..., Mapping[str, Any]]
RouterDiagnosticSink = Callable[[Mapping[str, str]], None]
logger = logging.getLogger(__name__)

TASK_TYPE_DEFAULT_AGENTS = {
    "answer_question": "qa",
    "answer_dashboard_metric": "qa",
    "propose_form_change": "input_assist",
    "review_validation_error": "error_review",
    "interpret_chart": "chart_interpret",
    "collect_qualitative_info": "elicit",
    "fallback": "fallback",
}


ROUTER_PROMPT = secure_system_prompt("""\
사용자 요청 하나를 분석해 후보 에이전트 중 하나를 선택하세요.
지정된 구조화 출력 스키마에 맞는 object를 반환하세요.

허용 status: matched, ambiguous, unsupported
matched이면 agent에 후보 중 하나를 넣고, 그 외에는 null을 넣으세요.
task type이 역할을 명확히 정한 경우 다음 대응을 우선하세요:
- answer_question, answer_dashboard_metric: qa
- propose_form_change: input_assist
- review_validation_error: error_review
- interpret_chart: chart_interpret
- collect_qualitative_info: elicit
- fallback: fallback

현재 단계: {step_id}
요청 종류: {task_type}
요청 설명: {description}

후보 에이전트:
{candidates}
""")


class RouterModelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["matched", "ambiguous", "unsupported"]
    agent: str | None = None

    @model_validator(mode="after")
    def validate_agent_contract(self) -> "RouterModelOutput":
        if self.status == "matched" and not self.agent:
            raise ValueError("matched router output requires an agent")
        if self.status != "matched" and self.agent is not None:
            raise ValueError("non-matched router output requires agent=null")
        return self


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
    diagnostic_sink: RouterDiagnosticSink | None = None,
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
    except Exception as error:
        _report_classifier_error(
            error,
            task=task,
            step_id=step_id,
            diagnostic_sink=diagnostic_sink,
        )
        return _recover_known_route(task, task_id, candidate_tuple)

    if not isinstance(classified, Mapping):
        error = ValueError("router classifier response must be an object")
        _report_classifier_error(
            error,
            task=task,
            step_id=step_id,
            diagnostic_sink=diagnostic_sink,
        )
        return _recover_known_route(task, task_id, candidate_tuple)

    status = classified.get("status")
    chosen = classified.get("agent")

    if status not in CLASSIFIER_STATUSES:
        error = ValueError("router classifier status is invalid")
        _report_classifier_error(
            error,
            task=task,
            step_id=step_id,
            diagnostic_sink=diagnostic_sink,
        )
        return _recover_known_route(task, task_id, candidate_tuple)
    if status == "ambiguous":
        reason_code = "ambiguous_intent"
    elif status == "unsupported":
        reason_code = "unsupported_intent"
    elif not isinstance(chosen, str) or not chosen:
        error = ValueError("matched router classifier omitted agent")
        _report_classifier_error(
            error,
            task=task,
            step_id=step_id,
            diagnostic_sink=diagnostic_sink,
        )
        return _recover_known_route(task, task_id, candidate_tuple)
    elif chosen not in candidate_tuple:
        error = ValueError("router classifier selected a disallowed agent")
        _report_classifier_error(
            error,
            task=task,
            step_id=step_id,
            diagnostic_sink=diagnostic_sink,
        )
        return _recover_known_route(task, task_id, candidate_tuple)
    else:
        expected = TASK_TYPE_DEFAULT_AGENTS.get(str(task.get("type", "")))
        if expected is not None and expected in candidate_tuple and chosen != expected:
            error = ValueError("router classifier contradicted the task type")
            _report_classifier_error(
                error,
                task=task,
                step_id=step_id,
                diagnostic_sink=diagnostic_sink,
            )
            chosen = expected
        return _decision(
            task_id=task_id,
            executor_kind="agent",
            executor=chosen,
            reason_code=(
                "unsupported_intent"
                if chosen == FALLBACK_AGENT and task.get("type") == "fallback"
                else "intent_match"
            ),
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
    result = secure_structured_invoke(
        llm,
        RouterModelOutput,
        [
            system,
            HumanMessage(content=str(task.get("description", ""))),
        ],
        purpose=ModelPurpose.ROUTER,
        operation_name="router.classify",
        sensitive_payload={"task": dict(task)},
    )
    return result.model_dump(mode="json")


def _recover_known_route(
    task: Mapping[str, Any],
    task_id: str,
    candidates: tuple[str, ...],
) -> dict[str, str]:
    expected = TASK_TYPE_DEFAULT_AGENTS.get(str(task.get("type", "")))
    if expected is not None and expected in candidates:
        return _decision(
            task_id=task_id,
            executor_kind="agent",
            executor=expected,
            reason_code=(
                "plan_invalid" if expected == FALLBACK_AGENT else "intent_match"
            ),
        )
    return _decision(
        task_id=task_id,
        executor_kind="agent",
        executor=FALLBACK_AGENT,
        reason_code="plan_invalid",
    )


def _report_classifier_error(
    error: BaseException,
    *,
    task: Mapping[str, Any],
    step_id: str,
    diagnostic_sink: RouterDiagnosticSink | None,
) -> None:
    diagnostic = exception_identity_fields(error)
    if diagnostic_sink is not None:
        diagnostic_sink(diagnostic)
    logger.error(
        "router classifier failed contract",
        extra={
            "event": "router_classifier_error",
            "detail": masked_exception_fields(
                error,
                step_id=step_id,
                task_id=_task_id(task),
                task_type=str(task.get("type", "unknown")),
            ),
        },
    )


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
