"""ChatState adapter for qualitative financial-mindset dialogue."""

from __future__ import annotations

from typing import Any


from graph.state import ChatState
from core.mindset_elicitation import (
    ElicitationResult, ElicitationState, MAX_ANSWERS, completed_response,
    current_summary, next_mindset_field, opening_question,
)
from .model import collect_answer, respond


async def _run(state: ChatState) -> dict[str, Any]:
    if state.get("request_mode") == "mindset_elicitation":
        return await _collect(state)
    context = state.get("conversation_context", {})
    response = await respond(
        message=state["message"],
        conversation_context=context,
    )
    return {
        "output": {
            "response": response,
            "step_id": state["step_id"],
            "action": None,
        }
    }


async def _collect(state: ChatState) -> dict[str, Any]:
    if state["step_id"] != "financial-mindset":
        raise ValueError("mindset collection requires financial-mindset step")
    request = ElicitationState.model_validate(state.get("elicitation_state"))
    customer = state["customer_info"]
    summary = current_summary(customer, request.field)
    count = request.answer_count
    reason = None
    if request.event == "start":
        response = opening_question(request.field, customer)
    else:
        answer = await collect_answer(
            field=request.field, message=state["message"], summary=summary,
            answer_count=count, conversation_context=state.get("conversation_context", {}),
        )
        response = answer.response
        if answer.turn_kind == "answer":
            count += 1
            summary = answer.summary
            if answer.sufficient and summary.strip():
                reason = "sufficient"
            elif count >= MAX_ANSWERS:
                reason = "limit"
        elif answer.turn_kind == "skip":
            reason = "skipped"
            if not summary.strip():
                summary = "이 항목에 대한 답변을 보류함."
        # Clarifications and refusals preserve every existing user-edited summary.
    result = ElicitationResult(
        field=request.field, summary=summary, answer_count=count,
        status="completed" if reason else "in_progress",
        completion_reason=reason,
        next_field=next_mindset_field(request.field) if reason else None,
    )
    if reason:
        response = completed_response(result, customer)
    return {"output": {
        "response": response, "step_id": state["step_id"], "action": None,
        "elicitation": result.model_dump(mode="json"),
    }}
