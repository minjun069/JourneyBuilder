"""Advisory Error Review over a valid draft and a directly mapped manual."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from agents.error_review_confidence import confidence_level, visible_review_items
from agents.error_review_manuals import load_error_review_manual
from core.llm import llm
from core.model_boundary import (
    ModelPurpose,
    secure_ainvoke,
    secure_system_prompt,
)
from graph.state import ChatState
from tools.output_validation import validate_form_update_contract


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

SYSTEM_PROMPT = secure_system_prompt("""\
당신은 저장을 막지 않는 입력값 검토 자문 Agent입니다.
Frontend의 필수값·자료형·범위 검증을 이미 통과한 현재 draft만 검토합니다.

[역할]
- 형식상 유효하지만 오기, 단위 착오, 다른 필드와의 문맥 불일치가 의심되는 값만 반환합니다.
- 정상값 또는 단순히 드문 값이라는 이유만으로 경고하지 않습니다.
- section/path는 canonical registry에 있는 현재 단계 field만 사용합니다.
- current_value_json은 현재 draft 값을 JSON으로 직렬화한 문자열입니다.
- suggested_value_json은 한 가지 수정 후보가 명확할 때만 그 값을 JSON
  문자열로 넣고, 추측이면 null로 둡니다.
- 반복 항목에는 현재 draft의 id 또는 client_item_id를 반드시 그대로 넣습니다.
- 중첩 목표 항목에는 부모 identity도 그대로 넣습니다.
- 결정론적 필수값·자료형·범위 오류를 다시 설명하지 않습니다.
- 매뉴얼에 없는 규칙이나 값을 만들지 않습니다.
- confidence는 다음 의미로 보수적으로 부여합니다.
  - low: 단지 가능성만 있거나 드문 값이라는 점 외에 근거가 부족함
  - medium: 현재 폼이나 매뉴얼과 구체적인 불일치가 있으나 수정값은 확정하기 어려움
  - high: 단위 착오 또는 다른 입력과의 직접 모순이 명확함
""")


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
        [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=(
                f"검토 trigger: {state.get('review_trigger', 'user_request')}\n"
                f"step_id: {state['step_id']}\n"
                f"canonical registry: {json.dumps(registry, ensure_ascii=False)}\n"
                f"현재 draft: {json.dumps(current_form, ensure_ascii=False)}\n\n"
                f"직접 매핑된 승인 매뉴얼 [{manual.document_id}@{manual.version}]\n"
                f"{manual.text}"
            )),
        ],
        purpose=ModelPurpose.ERROR_REVIEW,
        operation_name="error_review.review",
        sensitive_payload={"current_form": current_form},
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
