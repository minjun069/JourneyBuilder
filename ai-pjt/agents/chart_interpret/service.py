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
    started = monotonic()
    try:
        response = await explain_chart(state, payload, entry=entry)
    except Exception as error:
        record_operation_observation(
            stage=OperationStage.OUTPUT_VALIDATION,
            status=OperationStatus.FAILED,
            operation_name="chart_interpret.deliver",
            duration_ms=max(0, int((monotonic() - started) * 1000)),
            agent="chart_interpret",
            error_code=type(error).__name__,
            output={"delivery": "blocked_before_output"},
        )
        raise

    # Diagnostic heuristics are deliberately non-blocking. The model boundary
    # and terminal masker still enforce the common security policy.
    try:
        facts = project_chart_facts(payload)
        diagnostics: dict[str, int | str] = {
            "unmatched_numeric_count": len(unauthorized_numeric_claims(response, facts)),
            "causal_heuristic_count": len(unsupported_causal_claims(response, facts)),
            "recalculation_marker_count": len(recalculation_claims(response)),
        }
    except Exception:
        diagnostics = {"diagnostics": "unavailable"}
    record_operation_observation(
        stage=OperationStage.OUTPUT_VALIDATION,
        status=OperationStatus.SUCCEEDED,
        operation_name="chart_interpret.deliver",
        duration_ms=max(0, int((monotonic() - started) * 1000)),
        agent="chart_interpret",
        input={"report_status": payload["status"], "response_mode": "initial_entry" if entry else "detail"},
        output={"response": response, "delivery": "model_output", **diagnostics},
    )
    return {
        "output": {
            "response": response,
            "step_id": state["step_id"],
            "action": None,
        }
    }
