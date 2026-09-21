"""Small model-facing views; persistence and tracing keep their own metadata."""

from collections.abc import Mapping
from copy import deepcopy
from typing import Any


def conversation_for_model(context: Mapping[str, Any]) -> dict[str, Any]:
    """Keep conversation content without storage IDs, scores or budget counters."""
    result: dict[str, Any] = {}
    summary = context.get("summary")
    if isinstance(summary, Mapping):
        summary = summary.get("text")
    if summary:
        result["summary"] = summary
    state = context.get("structured_state")
    if isinstance(state, Mapping):
        result["conversation_notes"] = {
            key: deepcopy(value) for key, value in state.items()
            if value and key in {
                "current_goal", "response_preferences", "decisions",
                "open_questions", "entities",
            }
        }
    for source, target in (("turns", "recent_messages"), ("retrieved_memories", "related_messages")):
        messages = []
        for item in context.get(source, ()):
            if not isinstance(item, Mapping):
                continue
            turn = item.get("turn", item)
            if isinstance(turn, Mapping):
                messages.append({
                    key: turn[key] for key in ("user_message", "assistant_message")
                    if turn.get(key)
                })
        if messages:
            result[target] = messages
    return result
