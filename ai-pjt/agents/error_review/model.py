"""Error Review model adapter and structured output schema."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, Field

from core.llm import llm
from core.model_boundary import ModelPurpose, prompt_messages, secure_ainvoke
from prompts import get_prompt


class ReviewItem(BaseModel):
    section: str
    path: str
    item_id: int | None = None
    client_item_id: str | None = None
    parent_item_id: int | None = None
    parent_client_item_id: str | None = None
    current_value_json: str
    reason: str = Field(min_length=1)
    suggested_value_json: str | None = None
    confidence: float = Field(ge=0, le=1)


class ReviewResult(BaseModel):
    items: list[ReviewItem]


_review_chain = llm.with_structured_output(ReviewResult)
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
