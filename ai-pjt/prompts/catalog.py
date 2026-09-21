"""Current production prompt registry.

Historical prompt sets are recovered from Git and their deployment images. This
module deliberately contains no previous-catalog inheritance or runtime release
selector.
"""

from __future__ import annotations

from core.prompt_registry import PromptSpec, prompt_manifest
from prompts.definitions import ALL_PROMPTS


PROMPT_RELEASE = "2026-09-21.1"

_CATALOG = {prompt.prompt_id: prompt for prompt in ALL_PROMPTS}
if len(_CATALOG) != len(ALL_PROMPTS):
    raise RuntimeError("prompt catalog contains duplicate IDs")


def current_prompt_release() -> str:
    """Return the prompt identity compiled into this deployment image."""

    return PROMPT_RELEASE


def get_prompt(prompt_id: str) -> PromptSpec:
    try:
        return _CATALOG[prompt_id]
    except KeyError as error:
        raise KeyError(f"unknown prompt ID: {prompt_id}") from error


def prompt_ids() -> tuple[str, ...]:
    return tuple(sorted(_CATALOG))


def get_prompt_manifest() -> dict[str, object]:
    return prompt_manifest(_CATALOG, release=PROMPT_RELEASE)
