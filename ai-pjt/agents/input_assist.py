from __future__ import annotations

import json
from typing import Any, Literal, NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from agents.input_assist_defaults import (
    ReferenceTargetNotAllowed,
    build_bound_reference_proposals,
    build_default_proposals,
)
from core.llm import llm
from core.model_boundary import (
    ModelPurpose,
    prompt_messages,
    secure_ainvoke,
)
from prompts import get_prompt

MAX_RETRIES = 2


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class InputAssistState(TypedDict):
    # ChatState 와 공유 (subgraph 입출력)
    message: str
    step_id: str
    customer_info: dict
    next: str
    field_registry: list[dict]
    # executor가 검증한 선행 typed artifact의 명시적 binding
    bound_inputs: NotRequired[dict[str, Any]]
    # identify 출력 (Django canonical registry)
    guideline: str
    field_spec: str
    # classify 출력
    classification: str          # "fill_avg" | "find_field" | "organize" | "other"
    # identify 출력
    user_requested_change: bool
    identification: list[dict]
    cur_index: int             # 현재 처리 중인 항목 인덱스
    # resolve 출력 / 재시도 관리
    resolution: Any
    resolution_retries: int
    resolution_confidence: float
    # error_check_2 출력
    validation_type: str
    validation_reason: str
    # 항목별 결과 누적 (qa.py 의 partial_answers 와 동일 역할)
    partial_results: list[dict]  # 성공·실패한 canonical field 처리 결과
    # 최종 출력 — ChatState 의 output 필드로 전파
    output: dict                 # 자연어 응답과 FormChangeAction
    reason_code: NotRequired[str]
# ---------------------------------------------------------------------------
# 구조화 출력 스키마
# ---------------------------------------------------------------------------

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


_classify_chain      = llm.with_structured_output(ClassifyResult)
_identify_chain      = llm.with_structured_output(IdentifyResult)
_resolve_chain       = llm.with_structured_output(ResolveResult)
_error_check_2_chain = llm.with_structured_output(ErrorCheck2Result)


_CLASSIFY_PROMPT = get_prompt("input_assist.classify")
_IDENTIFY_PROMPT = get_prompt("input_assist.identify")
_RESOLVE_PROMPT = get_prompt("input_assist.resolve")
_ERROR_CHECK_2_PROMPT = get_prompt("input_assist.validate_resolution")
_MERGE_PROMPT = get_prompt("input_assist.merge")

_RESOLVE_INTENT_INSTRUCTIONS: dict[str, str] = {
    "fill_avg": "executor가 검증해 전달한 binding 값만 사용하세요. binding 값이 없으면 평균값·추천값을 임의로 생성하지 마세요.",
    "find_field": "발췌 내용에서 이 항목에 해당하는 값을 직접 추출하여 반환하세요.",
    "organize": "발췌 내용을 이 항목의 type·unit·constraints 에 맞게 정리하여 반환하세요.",
}

# ---------------------------------------------------------------------------
# 노드
# ---------------------------------------------------------------------------

def _init(_: InputAssistState) -> dict:
    return {
        "guideline": "",
        "field_spec": "",
        "classification": "",
        "user_requested_change": False,
        "identification": [],
        "cur_index": 0,
        "resolution": None,
        "resolution_confidence": 0,
        "resolution_retries": 0,
        "validation_type": "",
        "validation_reason": "",
        "partial_results": [],
        "output": {},
        "reason_code": "",
    }


async def _classify(state: InputAssistState) -> dict:
    result = await secure_ainvoke(
        _classify_chain,
        prompt_messages(_CLASSIFY_PROMPT, {"message": state["message"]}),
        purpose=ModelPurpose.INPUT_ASSIST,
        operation_name="input_assist.classify",
        sensitive_payload=_model_sensitive_payload(state),
        prompt=_CLASSIFY_PROMPT,
    )
    return {
        "classification": result.intent,
        "user_requested_change": result.user_requested_change,
    }


async def _identify(state: InputAssistState) -> dict:
    writable_registry = [
        field for field in state.get("field_registry", [])
        if field.get("writable_by_ai")
    ]
    guideline = json.dumps(writable_registry, ensure_ascii=False, indent=2)
    current_form = state.get("customer_info", {}).get("current_form", {})
    if state["classification"] == "fill_avg":
        try:
            if "reference_id" in state.get("bound_inputs", {}):
                proposals = build_bound_reference_proposals(
                    step_id=state["step_id"],
                    message=state["message"],
                    current_form=current_form if isinstance(current_form, dict) else {},
                    bound_inputs=state["bound_inputs"],
                    user_requested_change=state["user_requested_change"],
                )
            else:
                proposals = build_default_proposals(
                    step_id=state["step_id"],
                    message=state["message"],
                    current_form=current_form if isinstance(current_form, dict) else {},
                    saved_data=state.get("customer_info", {}).get("saved_data", {}),
                )
        except ReferenceTargetNotAllowed:
            return {
                "guideline": guideline,
                "identification": [],
                "resolution_confidence": 0,
                "cur_index": 0,
                "reason_code": "reference_target_not_allowed",
            }
        return {
            "guideline": guideline,
            "identification": proposals,
            "resolution_confidence": 0,
            "cur_index": 0,
        }
    result = await secure_ainvoke(
        _identify_chain,
        prompt_messages(_IDENTIFY_PROMPT, {
            "field_registry": writable_registry,
            "current_form": current_form,
            "message": state["message"],
        }),
        purpose=ModelPurpose.INPUT_ASSIST,
        operation_name="input_assist.identify",
        sensitive_payload=_model_sensitive_payload(state),
        prompt=_IDENTIFY_PROMPT,
    )
    return {
        "guideline": guideline,
        "identification": [item.model_dump() for item in result.items],
        "resolution_confidence": 0,
        "cur_index": 0,
    }

def _route_after_identify(state: InputAssistState) -> str:
    return "next_field" if state["identification"] else "merge"


def _next_field(state: InputAssistState) -> dict:
    return {
        "field_spec": "",
        "resolution": None,
        "validation_reason": "",
        "resolution_retries": 0,
        "validation_type": "",
    }


async def _error_check_1(state: InputAssistState) -> dict:
    item = state["identification"][state["cur_index"]]
    field_spec = next((
        field
        for field in state.get("field_registry", [])
        if field.get("section") == item["section"]
        and field.get("path") == item["path"]
    ), None)

    if field_spec is None:
        reason = "등록되지 않은 canonical 필드"
    elif not field_spec.get("writable_by_ai"):
        reason = "AI 쓰기가 허용되지 않은 canonical 필드"
    elif item["operation"] not in field_spec.get("operations", []):
        reason = "허용되지 않은 form operation"
    elif (
        state.get("bound_inputs", {}).get("unit") is not None
        and state["bound_inputs"]["unit"] != field_spec.get("unit", "none")
    ):
        reason = "선행 조회 결과와 canonical 필드의 단위 불일치"
    else:
        reason = ""

    if not reason:
        return {"validation_reason": ""}

    return {
        "partial_results": state["partial_results"] + [{
            "section": item["section"],
            "path": item["path"],
            "operation": item["operation"],
            "excerpt": item["excerpt"],
            "resolution": None,
            "success": False,
            "reason": reason,
        }],
        "cur_index": state["cur_index"] + 1,
        "validation_type": "registry",
        "validation_reason": reason,
    }


def _route_after_error_check_1(state: InputAssistState) -> str:
    if not state["validation_reason"]:
        return "resolve"
    if state["cur_index"] < len(state["identification"]):
        return "next_field"
    return "merge"


async def _resolve(state: InputAssistState) -> dict:
    item = state["identification"][state["cur_index"]]
    hint = (
        f"\n\n[이전 작업 실패 이유]: {state['validation_reason']}"
        if state["validation_reason"]
        else ""
    )
    
    field_spec = next((
        field for field in state.get("field_registry", [])
        if field.get("section") == item["section"] and field.get("path") == item["path"]
    ), {})
    field_spec = json.dumps(field_spec, ensure_ascii=False, indent=2)

    bound_inputs = state.get("bound_inputs", {})
    if "value" in bound_inputs:
        return {
            "resolution": bound_inputs["value"],
            "resolution_confidence": 1.0,
            "field_spec": field_spec,
        }
    if "policy_value" in item:
        return {
            "resolution": item["policy_value"],
            "resolution_confidence": 1.0,
            "field_spec": field_spec,
        }
    if state["classification"] == "fill_avg":
        return {
            "resolution": None,
            "resolution_confidence": 0.0,
            "field_spec": field_spec,
            "validation_reason": "검증된 선행 기준값 binding 없음",
        }

    result = await secure_ainvoke(
        _resolve_chain,
        prompt_messages(_RESOLVE_PROMPT, {
            "intent_instruction": _RESOLVE_INTENT_INSTRUCTIONS.get(
                state["classification"], ""
            ),
            "field_spec": field_spec,
            "hint": hint,
            "user_excerpt": item["excerpt"],
        }),
        purpose=ModelPurpose.INPUT_ASSIST,
        operation_name="input_assist.resolve",
        sensitive_payload=_model_sensitive_payload(state),
        prompt=_RESOLVE_PROMPT,
    )
    try:
        resolution = json.loads(result.value_json)
    except (TypeError, json.JSONDecodeError):
        resolution = None
    return {
        "resolution": resolution,
        "resolution_confidence": (
            max(0.0, min(1.0, result.confidence)) if resolution is not None else 0.0
        ),
        "field_spec": field_spec,
    }


async def _error_check_2(state: InputAssistState) -> dict:
    item = state["identification"][state["cur_index"]]

    bound_inputs = state.get("bound_inputs", {})
    if "value" in bound_inputs:
        source_id = bound_inputs.get("source_id")
        reason = "검증된 선행 task artifact binding"
        if source_id:
            reason = f"{reason} ({source_id})"
        return {
            "partial_results": state["partial_results"] + [{
                "section": item["section"],
                "path": item["path"],
                "operation": item["operation"],
                "item_id": item.get("item_id"),
                "client_item_id": item.get("client_item_id"),
                "excerpt": item["excerpt"],
                "resolution": bound_inputs["value"],
                "parent_item_id": item.get("parent_item_id"),
                "parent_client_item_id": item.get("parent_client_item_id"),
                "success": True,
                "reason": reason,
                "confidence": 1.0,
                "bound_unit": bound_inputs.get("unit"),
                "policy_metadata": item.get("policy_metadata"),
            }],
            "cur_index": state["cur_index"] + 1,
            "resolution_retries": 0,
            "validation_type": "ok",
            "validation_reason": "",
        }
    if "policy_value" in item:
        metadata = item.get("policy_metadata", {})
        source = metadata.get("source", {}) if isinstance(metadata, dict) else {}
        source_title = source.get("title", "승인된 기본값 정책")
        return {
            "partial_results": state["partial_results"] + [{
                "section": item["section"],
                "path": item["path"],
                "operation": item["operation"],
                "item_id": item.get("item_id"),
                "client_item_id": item.get("client_item_id"),
                "excerpt": item["excerpt"],
                "resolution": item["policy_value"],
                "parent_item_id": item.get("parent_item_id"),
                "parent_client_item_id": item.get("parent_client_item_id"),
                "success": True,
                "reason": f"승인된 기본값 정책: {source_title}",
                "confidence": 1.0,
                "policy_metadata": metadata,
            }],
            "cur_index": state["cur_index"] + 1,
            "resolution_retries": 0,
            "validation_type": "ok",
            "validation_reason": "",
        }
    if state["classification"] == "fill_avg":
        reason = "승인된 조회 결과가 없어 평균값을 생성하지 않음"
        return {
            "partial_results": state["partial_results"] + [{
                "section": item["section"],
                "path": item["path"],
                "operation": item["operation"],
                "excerpt": item["excerpt"],
                "resolution": None,
                "success": False,
                "reason": reason,
            }],
            "cur_index": state["cur_index"] + 1,
            "resolution_retries": 0,
            "validation_type": "hallucination",
            "validation_reason": reason,
        }

    result = await secure_ainvoke(
        _error_check_2_chain,
        prompt_messages(_ERROR_CHECK_2_PROMPT, {
            "field_spec": state["field_spec"],
            "user_input": item["excerpt"],
            "resolution": state["resolution"],
        }),
        purpose=ModelPurpose.INPUT_ASSIST,
        operation_name="input_assist.validate",
        sensitive_payload=_model_sensitive_payload(state),
        prompt=_ERROR_CHECK_2_PROMPT,
    )
    if result.error_type == "ok":
        return {
            "partial_results": state["partial_results"] + [{
                "section": item["section"],
                "path": item["path"],
                "operation": item["operation"],
                "item_id": item.get("item_id"),
                "client_item_id": item.get("client_item_id"),
                "excerpt": item["excerpt"],
                "resolution": state["resolution"],
                "parent_item_id": item.get("parent_item_id"),
                "parent_client_item_id": item.get("parent_client_item_id"),
                "success": True,
                "reason": result.reason,
                "confidence": state.get("resolution_confidence", 0),
            }],
            "cur_index": state["cur_index"] + 1,
            "resolution_retries": 0,
            "validation_type": "ok",
            "validation_reason": "",
        }
    if state["resolution_retries"] >= MAX_RETRIES:
        return {
            "partial_results": state["partial_results"] + [{
                "section": item["section"],
                "path": item["path"],
                "operation": item["operation"],
                "item_id": item.get("item_id"),
                "client_item_id": item.get("client_item_id"),
                "excerpt": item["excerpt"],
                "resolution": None,
                "success": False,
                "parent_item_id": item.get("parent_item_id"),
                "parent_client_item_id": item.get("parent_client_item_id"),
                "reason": result.reason,
            }],
            "cur_index": state["cur_index"] + 1,
            "resolution_retries": 0,
            "validation_type": result.error_type,
            "validation_reason": result.reason,
        }
    return {
        "validation_type": result.error_type,
        "validation_reason": result.reason,
        "resolution_retries": state["resolution_retries"] + 1,
    }


def _route_after_error_check_2(state: InputAssistState) -> str:
    if state["resolution_retries"]:
        return "resolve"
    if state["cur_index"] < len(state["identification"]):
        return "next_field"
    return "merge"


async def _merge(state: InputAssistState) -> dict:
    if state.get("reason_code") == "reference_target_not_allowed":
        return {
            "output": {
                "response": "조회 기준값을 현재 폼의 승인된 기존 항목에 안전하게 연결할 수 없어 변경안을 만들지 않았습니다.",
                "step_id": state["step_id"],
                "action": None,
            }
        }
    if state["classification"] == "fill_avg" and not state["partial_results"]:
        return {
            "output": {
                "response": (
                    "현재 단계에는 승인된 기본값 정책으로 안전하게 매핑할 수 있는 "
                    "빈 항목이 없어 자동 채우지 않았습니다."
                ),
                "step_id": state["step_id"],
                "action": None,
            }
        }
    parts = []
    changes = []
    confidences = []
    for item in state["partial_results"]:
        field_name = f"{item['section']}.{item['path']}"
        if item["success"]:
            parts.append(f"[{field_name}] 입력값: {item['resolution']}")
            change = {
                "section": item["section"],
                "path": item["path"],
                "operation": item["operation"],
                "value": item["resolution"],
                "reason": item["reason"],
            }
            if item.get("bound_unit") is not None:
                change["unit"] = item["bound_unit"]
            metadata = item.get("policy_metadata")
            if isinstance(metadata, dict):
                change.update(metadata)
            if item.get("item_id") is not None:
                change["item_id"] = item["item_id"]
            if item.get("client_item_id"):
                change["client_item_id"] = item["client_item_id"]
            if item.get("parent_item_id") is not None:
                change["parent_item_id"] = item["parent_item_id"]
            if item.get("parent_client_item_id"):
                change["parent_client_item_id"] = item["parent_client_item_id"]
            changes.append(change)
            confidences.append(item.get("confidence", 0))
        else:
            parts.append(f"[{field_name}] 입력 실패 — {item['reason']}")

    if changes and all(isinstance(change.get("reference"), dict) for change in changes):
        return {
            "output": {
                "response": "승인된 가구 기준값으로 기존 빈 항목의 변경안을 만들었습니다. 항목별 승인 후 폼을 저장해 주세요.",
                "step_id": state["step_id"],
                "action": {
                    "type": "form_change",
                    "target": "draft_form",
                    "user_requested": state["user_requested_change"],
                    "confidence": min(confidences),
                    "changes": changes,
                },
            }
        }

    result = await secure_ainvoke(
        llm,
        prompt_messages(_MERGE_PROMPT, {
            "message": state["message"],
            "results": parts,
        }),
        purpose=ModelPurpose.INPUT_ASSIST,
        operation_name="input_assist.merge",
        sensitive_payload=_model_sensitive_payload(state),
        prompt=_MERGE_PROMPT,
    )
    return {
        "output": {
            "response": result.content,
            "step_id": state["step_id"],
            "action": {
                "type": "form_change",
                "target": "draft_form",
                "user_requested": state["user_requested_change"],
                "confidence": min(confidences) if confidences else 0,
                "changes": changes,
            } if changes else None,
        }
    }


def _model_sensitive_payload(state: InputAssistState) -> dict[str, Any]:
    return {
        "message": state.get("message", ""),
        "customer_info": state.get("customer_info", {}),
        "bound_inputs": state.get("bound_inputs", {}),
        "identification": state.get("identification", []),
        "resolution": state.get("resolution"),
        "partial_results": state.get("partial_results", []),
    }


# ---------------------------------------------------------------------------
# 그래프 빌드
# ---------------------------------------------------------------------------

def _build():
    g = StateGraph(InputAssistState)

    g.add_node("init", _init)
    g.add_node("classify", _classify)
    g.add_node("identify", _identify)
    g.add_node("next_field", _next_field)
    g.add_node("error_check_1", _error_check_1)
    g.add_node("resolve", _resolve)
    g.add_node("error_check_2", _error_check_2)
    g.add_node("merge", _merge)

    g.add_edge(START, "init")
    g.add_edge("init", "classify")
    g.add_edge("classify", "identify")
    g.add_conditional_edges("identify", _route_after_identify)
    g.add_edge("next_field", "error_check_1")
    g.add_conditional_edges("error_check_1", _route_after_error_check_1)
    g.add_edge("resolve", "error_check_2")
    g.add_conditional_edges("error_check_2", _route_after_error_check_2)
    g.add_edge("merge", END)

    return g.compile()


graph = _build()
