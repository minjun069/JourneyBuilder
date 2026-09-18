"""Human-readable rendering for validated model inputs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from core.prompt_registry import PromptSpec


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == {} or value == [] or value == ()


def _scalar(value: Any) -> str:
    if value is None:
        return "없음"
    if value is True:
        return "true"
    if value is False:
        return "false"
    return str(value)


def _render(value: Any, *, indent: int = 0) -> list[str]:
    prefix = "  " * indent
    if isinstance(value, Mapping):
        lines: list[str] = []
        for key, child in value.items():
            if _empty(child):
                continue
            if isinstance(child, (Mapping, list, tuple)):
                lines.append(f"{prefix}- {key}")
                lines.extend(_render(child, indent=indent + 1))
            else:
                lines.append(f"{prefix}- {key}: {_scalar(child)}")
        return lines or [f"{prefix}- 없음"]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        lines = []
        for child in value:
            if isinstance(child, (Mapping, list, tuple)):
                lines.append(f"{prefix}-")
                lines.extend(_render(child, indent=indent + 1))
            else:
                lines.append(f"{prefix}- {_scalar(child)}")
        return lines or [f"{prefix}- 없음"]
    return [f"{prefix}{_scalar(value)}"]


def render_prompt_payload(prompt: PromptSpec, payload: Mapping[str, Any]) -> str:
    """Validate a payload and render only non-empty declared sections."""

    validated = prompt.validate_payload(payload)
    sections: list[str] = []
    for key, label in prompt.input_sections:
        value = validated.get(key)
        if _empty(value):
            continue
        content = "\n".join(_render(value))
        sections.append(f"[{label}]\n{content}")
    if not sections:
        raise ValueError(f"{prompt.prompt_id} has no non-empty model input")
    return "\n\n".join(sections)
