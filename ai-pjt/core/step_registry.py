"""Load and validate the shared AI lifecycle step registry."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any


FIXTURE_RELATIVE_PATH = Path(
    "contract-fixtures/ai-lifecycle/v2/step-registry.json"
)
EXPECTED_FIXTURE_KIND = "ai-step-registry"
EXPECTED_CONTRACT_VERSION = "2.0"


class StepRegistryError(ValueError):
    """Raised when the shared step registry is missing or invalid."""


def resolve_step_registry_path(environ: Mapping[str, str] | None = None) -> Path:
    """Resolve the repository or container copy of the shared fixture."""

    active_environ = os.environ if environ is None else environ
    configured = active_environ.get("STEP_REGISTRY_PATH", "").strip()
    if configured:
        return Path(configured)

    module_path = Path(__file__).resolve()
    candidates = (
        module_path.parents[2] / FIXTURE_RELATIVE_PATH,
        module_path.parents[1] / FIXTURE_RELATIVE_PATH,
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def _require_non_empty_strings(values: Any, field: str) -> list[str]:
    if not isinstance(values, list) or not values:
        raise StepRegistryError(f"{field} must be a non-empty array")
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise StepRegistryError(f"{field} must contain non-empty strings")
    if len(values) != len(set(values)):
        raise StepRegistryError(f"{field} must not contain duplicates")
    return values


def load_step_registry(path: Path | None = None) -> dict[str, Any]:
    """Read the registry and reject malformed or unsafe routing contracts."""

    fixture_path = resolve_step_registry_path() if path is None else path
    try:
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StepRegistryError("step registry could not be loaded") from exc

    if not isinstance(payload, dict):
        raise StepRegistryError("step registry root must be an object")
    if payload.get("fixture_kind") != EXPECTED_FIXTURE_KIND:
        raise StepRegistryError("step registry fixture_kind is unsupported")
    if payload.get("contract_version") != EXPECTED_CONTRACT_VERSION:
        raise StepRegistryError("step registry contract_version is unsupported")

    agents = _require_non_empty_strings(payload.get("agents"), "agents")
    agent_descriptions = payload.get("agent_descriptions")
    if not isinstance(agent_descriptions, dict):
        raise StepRegistryError("agent_descriptions must be an object")
    if set(agent_descriptions) != set(agents):
        raise StepRegistryError("agent_descriptions must describe every registered agent")
    if any(
        not isinstance(description, str) or not description.strip()
        for description in agent_descriptions.values()
    ):
        raise StepRegistryError("agent_descriptions must contain non-empty strings")
    transport_fallback_step_ids = _require_non_empty_strings(
        payload.get("transport_fallback_step_ids"),
        "transport_fallback_step_ids",
    )
    transport_fallback_allowed_agents = _require_non_empty_strings(
        payload.get("transport_fallback_allowed_agents"),
        "transport_fallback_allowed_agents",
    )
    if set(transport_fallback_allowed_agents) - set(agents):
        raise StepRegistryError(
            "transport_fallback_allowed_agents contains unregistered agents"
        )

    steps = payload.get("steps")
    if not isinstance(steps, list) or not steps:
        raise StepRegistryError("steps must be a non-empty array")

    step_ids: list[str] = []
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            raise StepRegistryError(f"steps[{index}] must be an object")
        step_id = step.get("step_id")
        if not isinstance(step_id, str) or not step_id.strip():
            raise StepRegistryError(f"steps[{index}].step_id must be non-empty")
        allowed_agents = _require_non_empty_strings(
            step.get("allowed_agents"),
            f"steps[{index}].allowed_agents",
        )
        if set(allowed_agents) - set(agents):
            raise StepRegistryError(
                f"steps[{index}].allowed_agents contains unregistered agents"
            )
        step_ids.append(step_id)

    if len(step_ids) != len(set(step_ids)):
        raise StepRegistryError("steps must not contain duplicate step_id values")
    if set(step_ids) & set(transport_fallback_step_ids):
        raise StepRegistryError(
            "transport_fallback_step_ids must not duplicate supported steps"
        )
    return payload


def load_step_candidates(path: Path | None = None) -> dict[str, list[str]]:
    registry = load_step_registry(path)
    candidates = {
        step["step_id"]: list(step["allowed_agents"])
        for step in registry["steps"]
    }
    for step_id in registry["transport_fallback_step_ids"]:
        candidates[step_id] = list(registry["transport_fallback_allowed_agents"])
    return candidates


def load_agent_descriptions(path: Path | None = None) -> dict[str, str]:
    registry = load_step_registry(path)
    return dict(registry["agent_descriptions"])
