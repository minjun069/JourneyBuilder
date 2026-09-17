"""Advisory Error Review over a valid draft and a directly mapped manual."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from agents.error_review_confidence import confidence_level, visible_review_items
from agents.error_review_manuals import load_error_review_manual
from core.llm import llm
from core.model_boundary import (
    ModelPurpose,
    prompt_messages,
    secure_ainvoke,
)
from graph.state import ChatState
from tools.output_validation import validate_form_update_contract
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


async def analyze_review_items(state: ChatState) -> tuple[Any, list[dict[str, Any]]]:
    """Run the production review once and retain low-confidence items for evals."""

    manual = load_error_review_manual(state["step_id"])
    current_form = state.get("customer_info", {}).get("current_form", {})
    registry = [
        field for field in state.get("field_registry", [])
        if isinstance(field, dict) and field.get("step_id") == state["step_id"]
        and field.get("sensitivity") != "operational"
    ]
    result = await secure_ainvoke(
        _review_chain,
        prompt_messages(REVIEW_PROMPT, {
            "trigger": state.get("review_trigger", "user_request"),
            "step_id": state["step_id"],
            "field_registry": registry,
            "current_form": current_form,
            "manual_id": manual.document_id,
            "manual_version": manual.version,
            "manual_text": manual.text,
        }),
        purpose=ModelPurpose.ERROR_REVIEW,
        sensitive_payload={"current_form": current_form},
        prompt=REVIEW_PROMPT,
    )
    validated_items = [
        item for raw in result.items
        if (normalized := _decode_review_item(raw.model_dump())) is not None
        if (item := _validated_item(
            normalized,
            step_id=state["step_id"],
            current_form=current_form,
            field_registry=registry,
        )) is not None
    ]
    return manual, validated_items


def _decode_review_item(item: Mapping[str, Any]) -> dict[str, Any] | None:
    try:
        current_value = json.loads(item["current_value_json"])
        suggested_raw = item.get("suggested_value_json")
        suggested_value = (
            json.loads(suggested_raw) if suggested_raw is not None else None
        )
    except (KeyError, TypeError, json.JSONDecodeError):
        return None
    return {
        **{
            key: value for key, value in item.items()
            if key not in {"current_value_json", "suggested_value_json"}
        },
        "current_value": current_value,
        "suggested_value": suggested_value,
    }


async def _run(state: ChatState) -> dict:
    manual, all_items = await analyze_review_items(state)
    validated_items = visible_review_items(all_items)
    if validated_items:
        response = f"입력값에서 확인이 필요한 항목 {len(validated_items)}개를 찾았습니다."
    else:
        response = "현재 입력값에서 별도로 확인을 권할 만한 항목을 찾지 못했습니다."
    return {
        "output": {
            "response": response,
            "step_id": state["step_id"],
            "action": None,
            "review": {
                "type": "error_review",
                "trigger": state.get("review_trigger", "user_request"),
                "manual": {
                    "document_id": manual.document_id,
                    "version": manual.version,
                },
                "items": validated_items,
            },
        }
    }


def _validated_item(
    item: Mapping[str, Any],
    *,
    step_id: str,
    current_form: Any,
    field_registry: list[dict[str, Any]],
) -> dict[str, Any] | None:
    registry_spec = next((
        field for field in field_registry
        if field.get("section") == item.get("section")
        and field.get("path") == item.get("path")
    ), None)
    if registry_spec is None:
        return None
    cardinality = registry_spec.get("cardinality")
    if cardinality in {"list", "nested_list"} and not (
        item.get("item_id") is not None or item.get("client_item_id")
    ):
        return None
    if (
        cardinality == "nested_list"
        and str(item.get("path", "")).startswith("items[].")
        and not (
            item.get("parent_item_id") is not None
            or item.get("parent_client_item_id")
        )
    ):
        return None
    actual, found = _current_value(current_form, item, registry_spec)
    if not found or actual != item.get("current_value"):
        return None
    candidate = item.get("suggested_value")
    if candidate is not None:
        change = {
            "section": item["section"],
            "path": item["path"],
            "operation": "set" if registry_spec.get("cardinality") == "single" else "update",
            "value": candidate,
            "reason": item["reason"],
        }
        for key in (
            "item_id", "client_item_id", "parent_item_id", "parent_client_item_id"
        ):
            if item.get(key) is not None:
                change[key] = item[key]
        validation = validate_form_update_contract({
            "step_id": step_id,
            "field_registry": field_registry,
            "output": {
                "response": "후보 검증",
                "step_id": step_id,
                "action": {
                    "type": "form_change",
                    "target": "draft_form",
                    "user_requested": False,
                    "confidence": item["confidence"],
                    "changes": [change],
                },
            },
        })
        if not validation.valid:
            candidate = None
    return {
        "section": item["section"],
        "path": item["path"],
        **{
            key: item[key] for key in (
                "item_id", "client_item_id", "parent_item_id", "parent_client_item_id"
            ) if item.get(key) is not None
        },
        "current_value": actual,
        "reason": item["reason"],
        "suggested_value": candidate,
        "confidence": item["confidence"],
        "confidence_level": confidence_level(float(item["confidence"])),
    }


def _current_value(
    current_form: Any,
    item: Mapping[str, Any],
    spec: Mapping[str, Any],
) -> tuple[Any, bool]:
    if not isinstance(current_form, Mapping):
        return None, False
    if spec.get("cardinality") == "single":
        path = str(item.get("path", ""))
        return (current_form.get(path), path in current_form)
    section = item.get("section")
    rows = (
        current_form.get(section, [])
        if section in {"goal_periods", "goal_milestones"}
        else current_form.get("assets", [])
    )
    if not isinstance(rows, list):
        return None, False
    if section == "goal_periods" and str(item.get("path", "")).startswith("items[]."):
        for parent in rows:
            if not isinstance(parent, Mapping):
                continue
            if not _parent_identity_matches(parent, item):
                continue
            for child in parent.get("items", []) if isinstance(parent.get("items"), list) else []:
                if isinstance(child, Mapping) and _identity_matches(child, item):
                    key = str(item["path"]).split(".", 1)[1]
                    return child.get(key), key in child
        return None, False
    for row in rows:
        if isinstance(row, Mapping) and _identity_matches(row, item):
            path = str(item.get("path", ""))
            return row.get(path), path in row
    return None, False


def _identity_matches(row: Mapping[str, Any], item: Mapping[str, Any]) -> bool:
    if item.get("item_id") is not None:
        return str(row.get("id")) == str(item["item_id"])
    client_id = item.get("client_item_id")
    return isinstance(client_id, str) and row.get("client_item_id") == client_id


def _parent_identity_matches(row: Mapping[str, Any], item: Mapping[str, Any]) -> bool:
    if item.get("parent_item_id") is not None:
        return str(row.get("id")) == str(item["parent_item_id"])
    client_id = item.get("parent_client_item_id")
    return isinstance(client_id, str) and row.get("client_item_id") == client_id


def _build():
    g = StateGraph(ChatState)
    g.add_node("run", _run)
    g.add_edge(START, "run")
    g.add_edge("run", END)
    return g.compile()


graph = _build()
