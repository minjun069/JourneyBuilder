"""Application service and ChatState adapter for Error Review."""

from __future__ import annotations

from typing import Any

from agents.error_review.confidence import visible_review_items
from agents.error_review.manuals import load_error_review_manual
from core.field_registry import load_field_registry
from graph.state import ChatState

from .model import REVIEW_PROMPT, ReviewItem, ReviewResult, review_draft
from .policy import decode_review_item, validated_item


# Compatibility for existing callers while policy ownership remains explicit.
_decode_review_item = decode_review_item
_validated_item = validated_item


async def analyze_review_items(state: ChatState) -> tuple[Any, list[dict[str, Any]]]:
    """Run the production review once and retain low-confidence items for evals."""

    manual = load_error_review_manual(state["step_id"])
    current_form = state.get("customer_info", {}).get("current_form", {})
    registry = [
        field for field in load_field_registry(state["step_id"])
        if isinstance(field, dict) and field.get("step_id") == state["step_id"]
        and field.get("sensitivity") != "operational"
    ]
    prompt_payload = {
        "step_id": state["step_id"],
        "field_registry": registry,
        "current_form": current_form,
        "manual_id": manual.document_id,
        "manual_version": manual.version,
        "manual_text": manual.text,
    }
    if "trigger" in REVIEW_PROMPT.required_input_keys:
        prompt_payload["trigger"] = "save"
    result = await review_draft(
        prompt_payload=prompt_payload,
        current_form=current_form,
    )
    validated_items = [
        item for raw in result.items
        if (normalized := decode_review_item(raw.model_dump())) is not None
        if (item := validated_item(
            normalized,
            step_id=state["step_id"],
            current_form=current_form,
            field_registry=registry,
        )) is not None
    ]
    return manual, validated_items


async def _run(state: ChatState) -> dict:
    manual, all_items = await analyze_review_items(state)
    items = visible_review_items(all_items)
    response = (
        f"입력값에서 확인이 필요한 항목 {len(items)}개를 찾았습니다."
        if items
        else "현재 입력값에서 별도로 확인을 권할 만한 항목을 찾지 못했습니다."
    )
    return {
        "output": {
            "response": response,
            "step_id": state["step_id"],
            "action": None,
            "review": {
                "type": "error_review",
                "manual": {
                    "document_id": manual.document_id,
                    "version": manual.version,
                },
                "items": items,
            },
        }
    }
