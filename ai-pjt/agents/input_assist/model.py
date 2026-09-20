"""Input Assist model schemas, prompt contracts, and structured runnables."""

from typing import Literal

from pydantic import BaseModel

from core.llm import llm
from prompts import get_prompt


class ClassifyResult(BaseModel):
    intent: Literal["fill_avg", "find_field", "organize", "other"]
    user_requested_change: bool


class IdentificationItem(BaseModel):
    section: str
    path: str
    operation: Literal["set", "add", "update", "remove"]
    item_id: int | None = None
    client_item_id: str | None = None
    parent_item_id: int | None = None
    parent_client_item_id: str | None = None
    label: str | None = None
    excerpt: str


class IdentifyResult(BaseModel):
    items: list[IdentificationItem]


class ResolveResult(BaseModel):
    value_json: str
    confidence: float


class ErrorCheck2Result(BaseModel):
    error_type: Literal["ok", "hallucination", "unorganized", "type_unit", "unreasonable"]
    reason: str


classify_chain = llm.with_structured_output(ClassifyResult)
identify_chain = llm.with_structured_output(IdentifyResult)
resolve_chain = llm.with_structured_output(ResolveResult)
error_check_2_chain = llm.with_structured_output(ErrorCheck2Result)

CLASSIFY_PROMPT = get_prompt("input_assist.classify")
IDENTIFY_PROMPT = get_prompt("input_assist.identify")
RESOLVE_PROMPT = get_prompt("input_assist.resolve")
ERROR_CHECK_2_PROMPT = get_prompt("input_assist.validate_resolution")
MERGE_PROMPT = get_prompt("input_assist.merge")
