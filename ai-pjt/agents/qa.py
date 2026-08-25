from __future__ import annotations

from typing import Literal, TypedDict

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

class JudgeResult(BaseModel):
    verdict: Literal["good", "bad"]
    reason: str


_specify_chain = llm.with_structured_output(SpecifyResult)
_decision_chain = llm.with_structured_output(RetrievalDecision)
_judge_chain = llm.with_structured_output(JudgeResult)


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
아래 정보를 보고, 고객 정보만으로 답변 가능한지 판단하세요.

- direct: 고객 정보(나이·자산·연금잔액 등)로 직접 답변 가능
- retrieve: 연금 제도, 세액공제 등 전문 문서 검색 필요"""

_ANSWER_SYSTEM = """\
당신은 은퇴 설계 전문 AI 상담사입니다.
참고 문서를 바탕으로 사용자 질문에 정확하고 이해하기 쉽게 답변하세요.
문서에 없는 내용은 추측하지 말고 모른다고 하세요."""

_DIRECT_ANSWER_SYSTEM = """\
당신은 은퇴 설계 전문 AI 상담사입니다.
사용자 질문에 정확하고 이해하기 쉽게 답변하세요."""

_MERGE_SYSTEM = """\
아래 부분 답변들을 하나의 완결된 답변으로 통합하세요.
두괄식으로 작성하세요: 핵심 결론을 먼저 제시하고, 이후 근거와 세부 내용을 자연스럽게 설명하세요.
중복 내용은 제거하세요."""

_JUDGE_SYSTEM = """\
답변의 품질을 평가하세요.

판정 기준:
- good: 질문에 충실하고 완결된 답변
- bad: 사실이 아닌 내용을 단정적으로 답변 / 답변이 불완전하거나 핵심을 빠뜨림 / 질문과 무관한 답변"""


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
    }


async def _decide(state: QAState) -> dict:
    decision = await _decision_chain.ainvoke([
        SystemMessage(content=_DECIDE_SYSTEM),
        HumanMessage(content=(
            f"고객 정보: {state['customer_info']}\n"
            f"필요한 정보 명세: {state['current_query']}"
        )),
    ])
    return {"need_retrieval": decision.need_retrieval}


async def _generate(state: QAState) -> dict:
    update = {}
    if state["need_retrieval"]:
        context = state["current_context"]
        if not context:
            nodes = await get_retriever().aretrieve(state["current_query"])
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
    if state["retry_hint"]:
        human_parts.append(f"이전 답변 실패 이유: {state['retry_hint']}")
    result = await llm.ainvoke([
        SystemMessage(content=system),
        HumanMessage(content="\n\n".join(human_parts)),
    ])
    return {**update, "current_answer": result.content}


async def _judge(state: QAState) -> dict:
    result = await _judge_chain.ainvoke([
        SystemMessage(content=_JUDGE_SYSTEM),
        HumanMessage(content=(
            f"질문: {state['message']}\n"
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
    combined = "\n\n".join(f"[{i+1}] {a}" for i, a in enumerate(state["partial_answers"]))
    result = await llm.ainvoke([
        SystemMessage(content=_MERGE_SYSTEM),
        HumanMessage(content=f"질문: {state['message']}\n\n부분 답변:\n{combined}"),
    ])
    return {"output": {"response": result.content, "update": {}}}


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
