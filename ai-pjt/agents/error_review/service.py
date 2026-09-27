"""Application service and ChatState adapter for Error Review."""

from __future__ import annotations

from typing import Any

from agents.error_review.manuals import load_error_review_manual
from core.field_registry import load_field_registry
from graph.state import ChatState

from .model import REVIEW_PROMPT, ReviewItem, ReviewResult, review_draft
from .policy import build_review_fields, decode_review_item, validated_item


# Compatibility for existing callers while policy ownership remains explicit.
_decode_review_item = decode_review_item
_validated_item = validated_item


async def analyze_review_items(state: ChatState) -> tuple[Any, list[dict[str, Any]]]:
    """Review compact field references and restore the external field contract."""

    manual = load_error_review_manual(state["step_id"])
    current_form = state.get("customer_info", {}).get("current_form", {})
    registry = [
        field for field in load_field_registry(state["step_id"])
        if isinstance(field, dict) and field.get("step_id") == state["step_id"]
        and field.get("sensitivity") != "operational"
    ]
    fields, references = build_review_fields(current_form, registry)
    if not fields:
        return manual, []
    prompt_payload = {
        "fields": fields,
        "review_rules": [
            "같은 항목의 시작 날짜·나이가 종료 날짜·나이보다 늦은지 확인합니다.",
            "같은 의미와 단위의 값끼리 직접 모순되는지 확인합니다. 서로 다른 항목의 금액 차이만으로 오류라고 판단하지 않습니다.",
            "원·만원 또는 비율·소수의 착오는 입력 내용에 단위를 판별할 근거가 있을 때만 지적합니다.",
        ],
    }
    result = await review_draft(
        prompt_payload=prompt_payload,
        current_form=current_form,
        step_id=state["step_id"],
    )
    validated_items = [
        item for raw in result.items
        if (normalized := decode_review_item(raw.model_dump(), references)) is not None
        if (item := validated_item(
            normalized,
            step_id=state["step_id"],
            current_form=current_form,
            field_registry=registry,
        )) is not None
    ]
    return manual, validated_items


async def _run(state: ChatState) -> dict:
    manual, items = await analyze_review_items(state)
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
