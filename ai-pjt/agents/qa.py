from __future__ import annotations

import json
from typing import Any, Literal, NotRequired, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from core.llm import llm
from rag.retriever import format_docs, get_retriever

MAX_RETRIES = 2


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class QAState(TypedDict):
    # ChatState 와 공유 (subgraph 입출력)
    message: str
    step_id: str
    customer_info: dict
    next: str
    # executor가 검증한 선행 typed artifact의 명시적 binding
    bound_inputs: NotRequired[dict[str, Any]]
    # specify 출력
    search_queries: list[str]
    query_index: int
    # 쿼리별 작업 필드
    current_query: str
    current_context: str  # 첫 시도에서 캐시, retry 시 재사용
    current_answer: str
    need_retrieval: bool
    retry_hint: str      # judge 실패 이유 → generate 재시도 시 참고
    retries: int         # 현재 쿼리 재시도 횟수
    retrieval_refreshes: int  # 근거 부족으로 검색어를 보정한 횟수(쿼리당 최대 1)
    official_value_unavailable: bool
    # judge 통과한 답변 수집
    partial_answers: list[str]
    # 최종 출력 — ChatState 의 output 필드로 전파
    output: dict  # {"response": str, "update": {}}


# ---------------------------------------------------------------------------
# 구조화 출력 스키마
# ---------------------------------------------------------------------------

class SpecifyResult(BaseModel):
    queries: list[str]


class RetrievalDecision(BaseModel):
    need_retrieval: bool
    requests_new_official_value: bool = False

class JudgeResult(BaseModel):
    verdict: Literal["good", "bad"]
    failure_kind: Literal["none", "answer_quality", "evidence_insufficient"] = "none"
    reason: str


class RefineResult(BaseModel):
    query: str


_specify_chain = llm.with_structured_output(SpecifyResult)
_decision_chain = llm.with_structured_output(RetrievalDecision)
_judge_chain = llm.with_structured_output(JudgeResult)
_refine_chain = llm.with_structured_output(RefineResult)


# ---------------------------------------------------------------------------
# 프롬프트
# ---------------------------------------------------------------------------

_SPECIFY_SYSTEM = """\
당신은 사용자 질문에 답하기 위해 필요한 핵심 정보를 추출하는 에이전트입니다.
사용자 질문을 분석하여, 질문에서 요구하는 정보를 명세하세요.

[작성 규칙]
- 과도한 확장 금지: 사용자의 질문 범위를 넘어선 구체적인 하위 사례(예: 특정 상품명, 개별 공제 항목 등)를 자의적으로 생성하지 마세요.
- 핵심 개념 위주: 질문의 뼈대가 되는 상위 개념과 구조적 키워드에 집중하여 최소화하세요.

[예시]
사용자 질문: "세액 공제 한도 구조 알려주세요"
나쁜 예시: 세액 공제 한도, 자녀 세액 공제, 월세 세액 공제, 연금계좌 한도
좋은 예시: 세액 공제 한도 산출 구조"""

_DECIDE_SYSTEM = """\
아래 정보를 보고, 고객 정보와 검증된 선행 값만으로 답변 가능한지 판단하세요.

- direct: 고객 정보(나이·자산·연금잔액 등)로 직접 답변 가능
- retrieve: 선행 값의 의미·배경·제도 또는 전문 설명을 위해 승인 문서 검색 필요

검증된 선행 값이 있다는 이유만으로 retrieve를 금지하지 마세요. 검색 문서는 그 값을
바꾸거나 다른 공식 숫자를 만들기 위한 근거가 아니라 보충 설명에만 사용합니다.
requests_new_official_value는 binding에 없는 새 공식 수치·한도·비율을 묻는 경우 true입니다."""

_ANSWER_SYSTEM = """\
당신은 은퇴 설계 전문 AI 상담사입니다.
제공된 승인 문서 근거만 사용해 사용자 질문에 정확하고 이해하기 쉽게 답변하세요.
문서에 없는 내용은 추측하지 말고 현재 근거로 확인할 수 없다고 밝히세요.
사실을 설명할 때는 참고 문서에 표시된 문서 제목과 원문 위치를 함께 인용하세요.
검증된 선행 task 입력이 있으면 그 값·단위·출처를 그대로 사용하고 다시 계산하거나 바꾸지 마세요."""

_DIRECT_ANSWER_SYSTEM = """\
당신은 은퇴 설계 전문 AI 상담사입니다.
사용자 질문에 정확하고 이해하기 쉽게 답변하세요.
검증된 선행 task 입력이 있으면 그 값·단위·출처를 그대로 사용하고 다시 조회·추정·계산하지 마세요."""

_MERGE_SYSTEM = """\
아래 부분 답변들을 하나의 완결된 답변으로 통합하세요.
두괄식으로 작성하세요: 핵심 결론을 먼저 제시하고, 이후 근거와 세부 내용을 자연스럽게 설명하세요.
중복 내용은 제거하세요. 검증된 선행 값·단위·출처·기준일은 그대로 보존하고
다른 값으로 바꾸거나 재계산하지 마세요."""

_JUDGE_SYSTEM = """\
답변의 품질을 평가하세요.

판정 기준:
- good: 질문에 충실하고 완결된 답변
- bad + answer_quality: 근거는 충분하지만 표현·구성·질문 충실도가 부족함
- bad + evidence_insufficient: 현재 승인 문서 context만으로 질문의 근거를 뒷받침할 수 없음

검증된 선행 값 자체는 다시 평가하거나 변경하지 마세요."""

_REFINE_SYSTEM = """\
현재 검색 결과가 질문의 설명 근거로 부족합니다. 사용자 질문 범위를 넓히지 말고,
같은 개념의 승인 문서를 찾을 수 있도록 검색어 하나만 보정하세요.
검증된 선행 공식 값과 다른 숫자를 찾도록 유도하지 마세요."""


# ---------------------------------------------------------------------------
# 노드
# ---------------------------------------------------------------------------

def _init(_: QAState) -> dict:
    return {
        "search_queries": [],
        "query_index": 0,
        "current_query": "",
        "current_context": "",
        "current_answer": "",
        "need_retrieval": False,
        "retry_hint": "",
        "retries": 0,
        "retrieval_refreshes": 0,
        "official_value_unavailable": False,
        "partial_answers": [],
        "output": {},
    }


async def _specify(state: QAState) -> dict:
    result = await _specify_chain.ainvoke([
        SystemMessage(content=_SPECIFY_SYSTEM),
        HumanMessage(content=state["message"]),
    ])
    return {"search_queries": result.queries or [state["message"]], "query_index": 0}


def _next_query(state: QAState) -> dict:
    i = state["query_index"]
    return {
        "current_query": state["search_queries"][i],
        "current_context": "",
        "current_answer": "",
        "retry_hint": "",
        "retries": 0,
        "retrieval_refreshes": 0,
        "official_value_unavailable": False,
    }


async def _decide(state: QAState) -> dict:
    if _requests_unbound_official_value(state):
        return {
            "need_retrieval": False,
            "official_value_unavailable": True,
        }
    decision = await _decision_chain.ainvoke([
        SystemMessage(content=_DECIDE_SYSTEM),
        HumanMessage(content=(
            f"고객 정보: {state.get('customer_info', {})}\n"
            f"검증된 선행 입력: {json.dumps(state.get('bound_inputs', {}), ensure_ascii=False, sort_keys=True)}\n"
            f"필요한 정보 명세: {state['current_query']}"
        )),
    ])
    if getattr(decision, "requests_new_official_value", False):
        return {
            "need_retrieval": False,
            "official_value_unavailable": True,
        }
    return {"need_retrieval": decision.need_retrieval}


async def _generate(state: QAState) -> dict:
    if state.get("official_value_unavailable"):
        return {
            "current_answer": (
                "요청하신 새 공식 값은 현재 연결된 승인 기준값 조회 결과가 없어 "
                "RAG 문서에서 추정해 답하지 않겠습니다. 기준값 조회 기능이 연결된 뒤 "
                "다시 확인하거나, 확인 가능한 공식 출처의 값을 직접 알려 주세요."
            )
        }
    update = {}
    if state["need_retrieval"]:
        context = state["current_context"]
        if not context:
            try:
                nodes = await get_retriever().aretrieve(state["current_query"])
            except RuntimeError:
                nodes = []
            context = format_docs(nodes)
            update["current_context"] = context
        human_parts = [
            f"현재 단계: {state['step_id']}",
            f"고객 정보: {state['customer_info']}",
            f"참고 문서:\n{context}",
            f"질문: {state['message']}",
        ]
        system = _ANSWER_SYSTEM
    else:
        human_parts = [
            f"현재 단계: {state['step_id']}",
            f"고객 정보: {state['customer_info']}",
            f"질문: {state['message']}",
        ]
        system = _DIRECT_ANSWER_SYSTEM
    bound_inputs = state.get("bound_inputs", {})
    if bound_inputs:
        human_parts.append(
            "검증된 선행 task 입력:\n"
            + json.dumps(bound_inputs, ensure_ascii=False, sort_keys=True)
        )
    if state["retry_hint"]:
        human_parts.append(f"이전 답변 실패 이유: {state['retry_hint']}")
    result = await llm.ainvoke([
        SystemMessage(content=system),
        HumanMessage(content="\n\n".join(human_parts)),
    ])
    return {**update, "current_answer": result.content}


async def _judge(state: QAState) -> dict:
    if state.get("official_value_unavailable"):
        return {
            "partial_answers": state["partial_answers"] + [state["current_answer"]],
            "query_index": state["query_index"] + 1,
            "retry_hint": "",
            "retries": 0,
        }
    bound_inputs = state.get("bound_inputs", {})
    result = await _judge_chain.ainvoke([
        SystemMessage(content=_JUDGE_SYSTEM),
        HumanMessage(content=(
            f"질문: {state['message']}\n"
            f"검증된 선행 task 입력: {json.dumps(bound_inputs, ensure_ascii=False, sort_keys=True)}\n"
            f"사용한 승인 문서 context: {state['current_context']}\n"
            f"답변: {state['current_answer']}"
        )),
    ])
    if result.verdict == "good":
        return {
            "partial_answers": state["partial_answers"] + [state["current_answer"]],
            "query_index": state["query_index"] + 1,
            "retry_hint": "",
            "retries": 0,
        }
    if state["retries"] >= MAX_RETRIES:
        return {
            "partial_answers": state["partial_answers"] + ["해당 질문에 대한 답변 제공에 실패했습니다."],
            "query_index": state["query_index"] + 1,
            "retry_hint": "",
            "retries": 0,
        }
    if (
        result.failure_kind == "evidence_insufficient"
        and state["need_retrieval"]
        and state["retrieval_refreshes"] == 0
    ):
        refined = await _refine_chain.ainvoke([
            SystemMessage(content=_REFINE_SYSTEM),
            HumanMessage(content=(
                f"사용자 질문: {state['message']}\n"
                f"현재 검색어: {state['current_query']}\n"
                f"부족한 이유: {result.reason}"
            )),
        ])
        query = refined.query.strip() or state["current_query"]
        return {
            "current_query": query,
            "current_context": "",
            "retry_hint": result.reason,
            "retrieval_refreshes": 1,
            "retries": state["retries"] + 1,
        }
    return {
        "retry_hint": result.reason,
        "retries": state["retries"] + 1,
    }


def _route_after_judge(state: QAState) -> str:
    if state["retry_hint"]:
        return "generate"
    if state["query_index"] < len(state["search_queries"]):
        return "next_query"
    return "merge"


async def _merge(state: QAState) -> dict:
    if len(state["partial_answers"]) == 1:
        return {
            "output": {
                "response": state["partial_answers"][0],
                "step_id": state["step_id"],
                "action": None,
            }
        }
    combined = "\n\n".join(f"[{i+1}] {a}" for i, a in enumerate(state["partial_answers"]))
    result = await llm.ainvoke([
        SystemMessage(content=_MERGE_SYSTEM),
        HumanMessage(content=(
            f"질문: {state['message']}\n"
            f"검증된 선행 task 입력: {json.dumps(state.get('bound_inputs', {}), ensure_ascii=False, sort_keys=True)}\n\n"
            f"부분 답변:\n{combined}"
        )),
    ])
    return {"output": {"response": result.content, "update": {}}}


def _requests_unbound_official_value(state: QAState) -> bool:
    """Fail closed only for a *new* official numeric value without a tool binding."""

    if state.get("bound_inputs"):
        return False
    text = f"{state.get('message', '')} {state.get('current_query', '')}".replace(" ", "")
    official_tokens = ("공식값", "기준값", "한도", "법정", "공단", "세율", "평균금액", "최신값")
    value_tokens = ("얼마", "수치", "금액", "비율", "몇", "%", "원")
    return any(token in text for token in official_tokens) and any(
        token in text for token in value_tokens
    )


# ---------------------------------------------------------------------------
# 그래프 빌드
# ---------------------------------------------------------------------------

def _build():
    g = StateGraph(QAState)

    g.add_node("init", _init)
    g.add_node("specify", _specify)
    g.add_node("next_query", _next_query)
    g.add_node("decide", _decide)
    g.add_node("generate", _generate)
    g.add_node("judge", _judge)
    g.add_node("merge", _merge)

    g.add_edge(START, "init")
    g.add_edge("init", "specify")
    g.add_edge("specify", "next_query")
    g.add_edge("next_query", "decide")
    g.add_edge("decide", "generate")
    g.add_edge("generate", "judge")
    g.add_conditional_edges("judge", _route_after_judge)
    g.add_edge("merge", END)

    return g.compile()


graph = _build()
