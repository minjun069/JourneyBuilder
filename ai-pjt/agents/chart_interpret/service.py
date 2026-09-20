"""ChatState adapter for Chart Interpret."""

from __future__ import annotations

from collections.abc import Mapping

from graph.state import ChatState

from .model import explain_chart
from .policy import (
    fallback_response,
    recalculation_claims,
    response_is_allowed,
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
    response = await explain_chart(state, payload, entry=entry)
    if not response_is_allowed(response, payload, entry=entry):
        response = fallback_response(payload, entry=entry)
    return {
        "output": {
            "response": response,
            "step_id": state["step_id"],
            "action": None,
        }
    }
