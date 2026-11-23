"""Small model-facing views; persistence and tracing keep their own metadata."""

from collections.abc import Mapping
from copy import deepcopy
from typing import Any


_REPORT_FACT_KEYS = (
    "title", "status", "period", "units", "summary", "series", "highlights",
    "events", "warnings", "details", "connections", "assumption_details",
)
_REPORT_METADATA_KEYS = frozenset({
    "id", "chart_id", "schema_version", "source", "source_id", "source_ids",
    "provenance", "path", "code", "reason_code", "field_name", "compatibility", "read_only",
    "input_snapshot_digest", "calculation_policy_version", "simulation_policy_version",
    "source_report", "report_version_key", "generated_at", "knowledge_scope",
})


def _report_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        if value.get("display") is not None:
            return {
                key: _report_value(value[key])
                for key in ("label", "display", "cause", "explanation") if key in value
            }
        return {
            key: _report_value(child) for key, child in value.items()
            if key not in _REPORT_METADATA_KEYS
        }
    if isinstance(value, list):
        return [_report_value(child) for child in value]
    return deepcopy(value)


def report_for_model(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Expose verified report facts to Chart and QA without storage metadata."""
    keys = _REPORT_FACT_KEYS if payload.get("status") == "ready" else ("title", "status", "warnings")
    return {key: _report_value(payload[key]) for key in keys if key in payload}


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
