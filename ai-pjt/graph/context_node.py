"""Conversation-context persistence node."""

from __future__ import annotations

from collections.abc import Mapping

from core.context_domain import ConversationContext, ConversationTurn
from core.context_service import advance_context
from graph.state import ChatState


def persist_context(state: ChatState) -> dict:
    issues = state.get("plan_issues", [])
    if any(
        isinstance(issue, Mapping) and issue.get("security_blocked") is True
        for issue in issues
    ):
        return {}

    context_payload = state.get("conversation_context")
    if not isinstance(context_payload, Mapping):
        return {}
    request_id = state.get("request_id")
    if not isinstance(request_id, str) or not request_id.strip():
        return {}

    output = state.get("output", {})
    response = output.get("response", "") if isinstance(output, Mapping) else ""
    updated = advance_context(
        context=ConversationContext.from_state(context_payload),
        new_turn=ConversationTurn(
            turn_id=request_id,
            user_message=state["message"],
            assistant_message=response if isinstance(response, str) else "",
        ),
    )
    return {"conversation_context": updated.to_state()}
