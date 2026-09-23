"""ChatState adapter for Chart Interpret."""

from __future__ import annotations

from collections.abc import Mapping
from time import monotonic

from core.monitoring import OperationStage, OperationStatus
from core.operation_observation import record_operation_observation
from graph.state import ChatState

from .model import explain_chart
from .context import project_chart_facts
from .policy import (
    fallback_response,
    recalculation_claims,
    response_is_allowed,
    response_validation_issues,
    unauthorized_numeric_claims,
    unsupported_causal_claims,
)


# Compatibility for evaluation code; new policy callers use fallback_response.
_fallback_response = fallback_response


async def _run(state: ChatState) -> dict:
    payload = state.get("chart_payload")
    if payload is None:
        return {
            "output": {
                "response": (
                    "현재 단계는 Chart Agent 설명 대상이 아닙니다. "
                    "해당 화면의 일반 질문으로 다시 요청해 주세요."
                ),
                "step_id": state["step_id"],
                "action": None,
            }
        }
    if (
        not isinstance(payload, Mapping)
        or payload.get("chart_id") != state.get("step_id")
        or payload.get("status") not in {"ready", "guidance_only"}
        or payload.get("read_only") is not True
    ):
        raise ValueError("invalid chart payload contract")

    entry = state.get("request_mode") == "presentation_entry"
    # The model sees the complete verified report. Keep metadata out of the
    # numeric evidence used by the deterministic response policy.
    facts = project_chart_facts(payload)
    feedback: tuple[str, ...] = ()
    response = ""
    for attempt in range(2):
        try:
            response = await explain_chart(
                state, payload, entry=entry, previous_response=response, feedback=feedback,
            )
        except Exception as error:
            if attempt == 0:
                raise
            record_operation_observation(
                stage=OperationStage.OUTPUT_VALIDATION,
                status=OperationStatus.FAILED,
                operation_name="chart_interpret.validate",
                duration_ms=0,
                agent="chart_interpret",
                error_code=type(error).__name__,
                output={"attempt": 2, "issues": ["repair_failed"], "fallback": True},
            )
            break
        started = monotonic()
        feedback = response_validation_issues(response, facts, entry=entry)
        record_operation_observation(
            stage=OperationStage.OUTPUT_VALIDATION,
            status=OperationStatus.FAILED if feedback else OperationStatus.SUCCEEDED,
            operation_name="chart_interpret.validate",
            duration_ms=max(0, int((monotonic() - started) * 1000)),
            agent="chart_interpret",
            error_code=feedback[0] if feedback else None,
            output={
                "attempt": attempt + 1,
                "issues": list(feedback),
                "fallback": bool(feedback) and attempt == 1,
            },
        )
        if not feedback:
            break
    if feedback:
        response = fallback_response(facts, entry=entry)
    return {
        "output": {
            "response": response,
            "step_id": state["step_id"],
            "action": None,
        }
    }
