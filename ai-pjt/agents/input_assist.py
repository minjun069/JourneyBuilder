from __future__ import annotations

from typing import Any, Literal, TypedDict, Union

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from core.llm import llm
from rag.markdown_retriever import get_field_spec, get_step_guideline

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
    # identify 출력 (RAG)
    guideline: str
    field_spec: str
    # classify 출력
    classification: str          # "fill_avg" | "find_field" | "organize" | "other"
    # identify 출력
    identification: list[dict]   # [{"field_name": str, "excerpt": str}, ...]
    cur_index: int             # 현재 처리 중인 항목 인덱스
    # resolve 출력 / 재시도 관리
    resolution: Any
    resolution_retries: int
    # error_check_2 출력
    validation_type: str
    validation_reason: str
    # 항목별 결과 누적 (qa.py 의 partial_answers 와 동일 역할)
    partial_results: list[dict]  # [{"field_name": str, "excerpt": str, "resolution": Any, "success": bool, "reason": str}]
    # 최종 출력 — ChatState 의 output 필드로 전파
    output: dict                 # {"response": str, "update": {field_name: value}}


# ---------------------------------------------------------------------------
# 구조화 출력 스키마
# ---------------------------------------------------------------------------

class ClassifyResult(BaseModel):
    intent: Literal["fill_avg", "find_field", "organize", "other"]


class IdentificationItem(BaseModel):
    field_name: str
    excerpt: str


class IdentifyResult(BaseModel):
    items: list[IdentificationItem]


class ErrorCheck1Result(BaseModel):
    error_analysis: str      # CoT — 배정 오류 분석 (생성하되 이후 노드에 전달하지 않음)
    corrected_field_name: str


class ResolveResult(BaseModel):
    value: Union[str, int, float]


class ErrorCheck2Result(BaseModel):
    error_type: Literal["ok", "hallucination", "unorganized", "type_unit", "unreasonable"]
    reason: str


_classify_chain      = llm.with_structured_output(ClassifyResult)
_identify_chain      = llm.with_structured_output(IdentifyResult)
_error_check_1_chain = llm.with_structured_output(ErrorCheck1Result)
_resolve_chain       = llm.with_structured_output(ResolveResult)
_error_check_2_chain = llm.with_structured_output(ErrorCheck2Result)


# ---------------------------------------------------------------------------
# 프롬프트
# ---------------------------------------------------------------------------

_CLASSIFY_SYSTEM = """\
당신은 은퇴 설계 전문 AI 상담사입니다.

사용자 발화의 의도를 다음 중 하나로 분류하세요.
- fill_avg : 특정 항목에 적절한 평균값·추천값으로 채워달라는 요청
- find_field: 값은 알지만 어느 항목에 입력해야 할지 모르는 경우
- organize  : 입력할 내용이 있으나 정리가 필요한 경우"""

_IDENTIFY_SYSTEM = """\
당신은 은퇴 설계 전문 AI 상담사입니다.

[프로세스 가이드라인]
{guideline}

사용자 발화에서 입력 대상이 되는 폼 항목과 해당 발췌 내용을 추출하세요.
- field_name 은 프로세스 가이드라인의 field_name 중 하나여야 합니다.
- excerpt 는 사용자 발화에서 해당 항목과 관련된 내용을 그대로 발췌하세요.
- 하나의 발화에서 여러 항목을 동시에 특정할 수 있습니다."""

_ERROR_CHECK_1_SYSTEM = """\
당신은 은퇴 설계 전문 AI 상담사입니다.

[프로세스 가이드라인]
{guideline}

아래 검토 대상의 항목 배정이 올바른지 검토하세요.

[검토 절차]
1. error_analysis: 배정된 항목이 발췌 내용과 맞지 않는 이유를 서술하세요. 올바른 배정이면 "오류 없음"이라고 적으세요.
2. corrected_field_name: 발췌 내용이 실제로 해당하는 올바른 field_name 을 반환하세요. 배정이 옳다면 원래 field_name 을 그대로 반환하세요."""

_RESOLVE_SYSTEM = """\
당신은 은퇴 설계 전문 AI 상담사입니다.
{intent_instruction}

[항목 명세서]
{field_spec}

[공통 작성 규칙]
- 항목의 type 과 unit 에 맞는 형식으로 반환하세요.
- constraints 범위를 벗어나면 가장 가까운 경계값으로 조정하세요.{hint}"""

_RESOLVE_INTENT_INSTRUCTIONS: dict[str, str] = {
    "fill_avg": "참고 자료를 바탕으로 이 항목에 적절한 평균값·추천값을 찾아 반환하세요. 참고 자료에 없는 값을 임의로 생성하지 마세요.",
    "find_field": "발췌 내용에서 이 항목에 해당하는 값을 직접 추출하여 반환하세요.",
    "organize": "발췌 내용을 이 항목의 type·unit·constraints 에 맞게 정리하여 반환하세요.",
}

_ERROR_CHECK_2_SYSTEM = """\
분류 기준에 따라 확정된 값의 오류 여부와 판단 근거를 작성하세요.

오류 유형:
- ok           : 오류 없음
- hallucination: 사용자 발화 발췌나 참고 자료에 근거 없는 값
- unorganized  : 값이 정리되지 않아 항목에 입력하기 부적합한 형태
- type_unit    : 자료형 또는 단위 이상
- unreasonable : 값의 합리성 의심 (참고 자료 미반영 또는 범위 이탈)

[항목 명세서]
{field_spec}"""

_MERGE_SYSTEM = """\
당신은 은퇴 설계 전문 AI 상담사입니다.

아래 항목별 입력 처리 결과를 바탕으로 사용자에게 전달할 자연스러운 한국어 안내 멘트를 작성하세요.
- 항목별로 두괄식으로 작성하세요.
- 입력에 성공한 항목은 어떤 값을 왜 선택했는지 설명하세요.
- 실패한 항목은 실패 이유와 사용자가 직접 확인해야 할 사항을 안내하세요. 실패한 항목이 없다면 생략하세요."""


# ---------------------------------------------------------------------------
# 노드
# ---------------------------------------------------------------------------

def _init(_: InputAssistState) -> dict:
    return {
        "guideline": "",
        "field_spec": "",
        "classification": "",
        "identification": [],
        "cur_index": 0,
        "resolution": None,
        "resolution_retries": 0,
        "validation_type": "",
        "validation_reason": "",
        "partial_results": [],
        "output": {},
    }


async def _classify(state: InputAssistState) -> dict:
    result = await _classify_chain.ainvoke([
        SystemMessage(content=_CLASSIFY_SYSTEM),
        HumanMessage(content=state["message"]),
    ])
    return {"classification": result.intent}


async def _identify(state: InputAssistState) -> dict:
    guideline = get_step_guideline(state["step_id"])

    result = await _identify_chain.ainvoke([
        SystemMessage(content=_IDENTIFY_SYSTEM.format(
            guideline=guideline,
        )),
        HumanMessage(content=state["message"]),
    ])
    return {
        "guideline": guideline,
        "identification": [{"field_name": i.field_name, "excerpt": i.excerpt} for i in result.items],
        "cur_index": 0,
    }


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

    result = await _error_check_1_chain.ainvoke([
        SystemMessage(content=_ERROR_CHECK_1_SYSTEM.format(
            guideline=state["guideline"]
        )),
        HumanMessage(content=(
            f"[검토 대상]\n"
            f"-사용자 입력: {item['excerpt']}\n"
            f"-배정된 항목: {item['field_name']}"
        )),
    ])
    # error_analysis 는 CoT 역할만 하며 이후 노드에 전달하지 않음
    identification = list(state["identification"])
    identification[state["cur_index"]] = {**identification[state["cur_index"]], "field_name": result.corrected_field_name}
    return {"identification": identification}


async def _resolve(state: InputAssistState) -> dict:
    item = state["identification"][state["cur_index"]]
    hint = (
        f"\n\n[이전 작업 실패 이유]: {state['validation_reason']}"
        if state["validation_reason"]
        else ""
    )
    
    if not state["field_spec"]:
        field_spec = get_field_spec(state["step_id"], item["field_name"])
    else:
        field_spec = state["field_spec"]

    result = await _resolve_chain.ainvoke([
        SystemMessage(content=_RESOLVE_SYSTEM.format(
            field_spec=field_spec,
            intent_instruction=_RESOLVE_INTENT_INSTRUCTIONS.get(state["classification"], ""),
            hint=hint,
        )),
        HumanMessage(content=f"[사용자 입력]\n{item['excerpt']}"),
    ])
    return {"resolution": result.value, "field_spec": field_spec}


async def _error_check_2(state: InputAssistState) -> dict:
    item = state["identification"][state["cur_index"]]

    result = await _error_check_2_chain.ainvoke([
        SystemMessage(content=_ERROR_CHECK_2_SYSTEM.format(
            field_spec=state["field_spec"]
        )),
        HumanMessage(content=(
            f"사용자 입력: {item['excerpt']}\n"
            f"해석된 값: {state['resolution']}\n"
        )),
    ])
    if result.error_type == "ok":
        return {
            "partial_results": state["partial_results"] + [{
                "field_name": item["field_name"],
                "excerpt": item["excerpt"],
                "resolution": state["resolution"],
                "success": True,
                "reason": result.reason,
            }],
            "cur_index": state["cur_index"] + 1,
            "resolution_retries": 0,
            "validation_type": "ok",
            "validation_reason": "",
        }
    if state["resolution_retries"] >= MAX_RETRIES:
        return {
            "partial_results": state["partial_results"] + [{
                "field_name": item["field_name"],
                "excerpt": item["excerpt"],
                "resolution": None,
                "success": False,
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
    parts = []
    update = {}
    for r in state["partial_results"]:
        field_name = r["field_name"]
        if r["success"]:
            parts.append(f"[{field_name}] 입력값: {r['resolution']}")
            update[field_name] = r["resolution"]
        else:
            parts.append(f"[{field_name}] 입력 실패 — {r['reason']}")

    result = await llm.ainvoke([
        SystemMessage(content=_MERGE_SYSTEM),
        HumanMessage(content=(
            f"사용자 입력: {state['message']}\n\n"
            f"항목별 처리 결과:\n" + "\n".join(parts)
        )),
    ])
    return {"output": {"response": result.content, "update": update}}


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
    g.add_edge("identify", "next_field")
    g.add_edge("next_field", "error_check_1")
    g.add_edge("error_check_1", "resolve")
    g.add_edge("resolve", "error_check_2")
    g.add_conditional_edges("error_check_2", _route_after_error_check_2)
    g.add_edge("merge", END)

    return g.compile()


graph = _build()
