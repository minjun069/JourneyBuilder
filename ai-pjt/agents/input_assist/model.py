"""Structured extraction and optional batch review for form changes."""

from typing import Literal

from pydantic import BaseModel, JsonValue
from core.json_response import json_response_chain
from core.llm import llm
from prompts import get_prompt


class ExtractedChange(BaseModel):
    field_ref: str
    item_ref: str | None = None
    operation: Literal["set", "add", "update", "remove"]
    value: JsonValue = None


class ExtractResult(BaseModel):
    request: Literal["change", "fill_defaults", "none"]
    changes: list[ExtractedChange]
    questions: list[str]


class ChangeReview(BaseModel):
    change_ref: str
    accepted: bool
    reason: str = ""


class ReviewResult(BaseModel):
    items: list[ChangeReview]


# Gemini's function schema converter narrows JSON unions to one type. Request
# plain JSON and validate it locally so numbers, booleans and objects survive.
extract_chain = json_response_chain(llm, ExtractResult)
review_chain = llm.with_structured_output(ReviewResult)

EXTRACT_PROMPT = get_prompt("input_assist.extract")
REVIEW_PROMPT = get_prompt("input_assist.review")
RESPOND_PROMPT = get_prompt("input_assist.respond")
