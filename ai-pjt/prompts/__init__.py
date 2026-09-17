"""Approved production prompt release registry."""

from prompts.catalog import (
    ACTIVE_PROMPT_RELEASE,
    PREVIOUS_PROMPT_RELEASE,
    SUPPORTED_PROMPT_RELEASES,
    get_prompt,
    get_prompt_manifest,
    prompt_ids,
)

__all__ = (
    "ACTIVE_PROMPT_RELEASE",
    "PREVIOUS_PROMPT_RELEASE",
    "SUPPORTED_PROMPT_RELEASES",
    "get_prompt",
    "get_prompt_manifest",
    "prompt_ids",
)
