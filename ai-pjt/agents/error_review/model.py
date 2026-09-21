"""Error Review model adapter and structured output schema."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from core.llm import llm
from core.json_response import json_response_chain
from core.model_boundary import ModelPurpose, prompt_messages, secure_ainvoke
from prompts import get_prompt


class ReviewItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    field_ref: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    suggested_value: JsonValue = None


class ReviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    items: list[ReviewItem]


_review_chain = json_response_chain(llm, ReviewResult)
REVIEW_PROMPT = get_prompt("error_review.analyze")


async def review_draft(
    *,
    prompt_payload: Mapping[str, Any],
    current_form: Any,
) -> ReviewResult:
    result = await secure_ainvoke(
        _review_chain,
        prompt_messages(REVIEW_PROMPT, prompt_payload),
        purpose=ModelPurpose.ERROR_REVIEW,
        operation_name="error_review.review",
        sensitive_payload={"current_form": current_form},
        prompt=REVIEW_PROMPT,
    )
    return result
