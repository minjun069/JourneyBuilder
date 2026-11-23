"""Small constructor shared by one-prompt definition modules."""

from __future__ import annotations

from core.prompt_registry import PromptSpec
from prompts.common import assemble_system_text


def define_prompt(
    prompt_id: str,
    purpose: str,
    role_text: str,
    sections: tuple[tuple[str, str], ...],
    *,
    output_schema: str | None = None,
    risk: str = "standard",
    advisor: bool = False,
    version: str = "4.0.0",
) -> PromptSpec:
    return PromptSpec(
        prompt_id=prompt_id,
        version=version,
        purpose=purpose,
        system_text=assemble_system_text(role_text, advisor=advisor),
        required_input_keys=frozenset(key for key, _label in sections),
        output_schema=output_schema,
        risk=risk,
        optional_input_keys=frozenset({"screen_context"}),
        input_sections=(("screen_context", "현재 화면 안내"), *sections),
    )
