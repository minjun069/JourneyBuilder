"""ChatState adapter for qualitative financial-mindset dialogue."""

from __future__ import annotations

from typing import Any


from graph.state import ChatState
from .model import respond


async def _run(state: ChatState) -> dict[str, Any]:
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
