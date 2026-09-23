"""Project report data into the facts used for both explanation and validation."""

from collections.abc import Mapping
from typing import Any


_FACT_KEYS = (
    "title", "status", "period", "units", "summary", "series", "highlights",
    "events", "warnings", "details", "connections", "assumption_details",
)
_METADATA_KEYS = frozenset({
    "id", "chart_id", "schema_version", "source", "source_id", "source_ids",
    "provenance", "path", "code", "reason_code", "compatibility", "read_only",
    "input_snapshot_digest", "calculation_policy_version", "simulation_policy_version",
    "source_report", "report_version_key", "generated_at", "knowledge_scope",
})


def _explanation_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        if value.get("display") is not None:
            return {
                key: _explanation_value(value[key])
                for key in ("label", "display", "cause", "explanation") if key in value
            }
        return {
            key: _explanation_value(child) for key, child in value.items()
            if key not in _METADATA_KEYS
        }
    if isinstance(value, list):
        return [_explanation_value(child) for child in value]
    return value


def project_chart_facts(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Select report facts, excluding metadata, for response validation."""
    if payload.get("status") != "ready":
        return {
            key: _explanation_value(payload[key])
            for key in ("title", "status", "warnings") if key in payload
        }
    return {
        key: _explanation_value(payload[key])
        for key in _FACT_KEYS if key in payload
    }
