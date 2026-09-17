from __future__ import annotations

import json
import re
from math import isfinite
from time import monotonic
from typing import Any, Literal, NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from core.llm import llm
from core.model_boundary import (
    ModelPurpose,
    prompt_messages,
    secure_ainvoke,
)
from core.monitoring import OperationStage, OperationStatus
from core.observability_masking import exception_identity_fields
from core.operation_observation import record_operation_observation
from prompts import get_prompt
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
    retrieval_used: bool
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

_SPECIFY_PROMPT = get_prompt("qa.specify")
_DECIDE_PROMPT = get_prompt("qa.decide_retrieval")
_ANSWER_PROMPT = get_prompt("qa.answer_with_evidence")
_DIRECT_ANSWER_PROMPT = get_prompt("qa.answer_direct")
_MERGE_PROMPT = get_prompt("qa.merge")
_JUDGE_PROMPT = get_prompt("qa.judge")
_REFINE_PROMPT = get_prompt("qa.refine_query")


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
        "retrieval_used": False,
        "partial_answers": [],
        "output": {},
    }


async def _specify(state: QAState) -> dict:
    result = await secure_ainvoke(
        _specify_chain,
        prompt_messages(_SPECIFY_PROMPT, {"message": state["message"]}),
        purpose=ModelPurpose.QA,
        operation_name="qa.specify",
        sensitive_payload=_model_sensitive_payload(state),
        prompt=_SPECIFY_PROMPT,
    )
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
    decision = await secure_ainvoke(
        _decision_chain,
        prompt_messages(_DECIDE_PROMPT, {
            "customer_info": state.get("customer_info", {}),
            "bound_inputs": state.get("bound_inputs", {}),
            "current_query": state["current_query"],
        }),
        purpose=ModelPurpose.QA,
        operation_name="qa.decide",
        sensitive_payload=_model_sensitive_payload(state),
        prompt=_DECIDE_PROMPT,
    )
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
        update["retrieval_used"] = True
        context = state["current_context"]
        if not context:
            search_started = monotonic()
            try:
                nodes = await get_retriever().aretrieve(state["current_query"])
            except RuntimeError as error:
                nodes = []
                record_operation_observation(
                    stage=OperationStage.SEARCH,
                    status=OperationStatus.FAILED,
                    operation_name="qa.search",
                    duration_ms=max(
                        0,
                        int((monotonic() - search_started) * 1000),
                    ),
                    agent="qa",
                    error_code="retrieval_unavailable",
                    result_count=0,
                    output={"result_count": 0},
                    **exception_identity_fields(error),
                )
            else:
                resource_ids, scores = _search_result_metadata(nodes)
                record_operation_observation(
                    stage=OperationStage.SEARCH,
                    status=OperationStatus.SUCCEEDED,
                    operation_name="qa.search",
                    duration_ms=max(
                        0,
                        int((monotonic() - search_started) * 1000),
                    ),
                    agent="qa",
                    resource_ids=resource_ids,
                    scores=scores,
                    result_count=len(nodes),
                    output={"result_count": len(nodes)},
                )
            context = format_docs(nodes)
            update["current_context"] = context
        prompt = _ANSWER_PROMPT
        payload = {
            "step_id": state["step_id"],
            "customer_info": state["customer_info"],
            "evidence": context,
            "question": state["message"],
            "bound_inputs": state.get("bound_inputs", {}),
            "retry_hint": state["retry_hint"],
        }
    else:
        prompt = _DIRECT_ANSWER_PROMPT
        payload = {
            "step_id": state["step_id"],
            "customer_info": state["customer_info"],
            "question": state["message"],
            "bound_inputs": state.get("bound_inputs", {}),
            "retry_hint": state["retry_hint"],
        }
    result = await secure_ainvoke(
        llm,
        prompt_messages(prompt, payload),
        purpose=ModelPurpose.QA,
        operation_name="qa.generate",
        sensitive_payload=_model_sensitive_payload(state),
        prompt=prompt,
    )
    answer = (
        _strip_retrieval_source_disclosure(result.content)
        if state["need_retrieval"]
        else result.content
    )
    return {**update, "current_answer": answer}


async def _judge(state: QAState) -> dict:
    if state.get("official_value_unavailable"):
        return {
            "partial_answers": state["partial_answers"] + [state["current_answer"]],
            "query_index": state["query_index"] + 1,
            "retry_hint": "",
            "retries": 0,
        }
    bound_inputs = state.get("bound_inputs", {})
    result = await secure_ainvoke(
        _judge_chain,
        prompt_messages(_JUDGE_PROMPT, {
            "question": state["message"],
            "bound_inputs": bound_inputs,
            "evidence": state["current_context"],
            "answer": state["current_answer"],
        }),
        purpose=ModelPurpose.QA,
        operation_name="qa.judge",
        sensitive_payload=_model_sensitive_payload(state),
        prompt=_JUDGE_PROMPT,
    )
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
        refined = await secure_ainvoke(
            _refine_chain,
            prompt_messages(_REFINE_PROMPT, {
                "question": state["message"],
                "current_query": state["current_query"],
                "reason": result.reason,
            }),
            purpose=ModelPurpose.QA,
            operation_name="qa.refine",
            sensitive_payload=_model_sensitive_payload(state),
            prompt=_REFINE_PROMPT,
        )
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
    result = await secure_ainvoke(
        llm,
        prompt_messages(_MERGE_PROMPT, {
            "question": state["message"],
            "bound_inputs": state.get("bound_inputs", {}),
            "partial_answers": state["partial_answers"],
        }),
        purpose=ModelPurpose.QA,
        operation_name="qa.merge",
        sensitive_payload=_model_sensitive_payload(state),
        prompt=_MERGE_PROMPT,
    )
    response = result.content
    if state.get("retrieval_used"):
        response = _strip_retrieval_source_disclosure(response)
    return {
        "output": {
            "response": response,
            "update": {},
        }
    }


def _search_result_metadata(
    results: list[Any],
) -> tuple[tuple[str, ...], tuple[float, ...]]:
    resource_ids: list[str] = []
    scores: list[float] = []
    all_scores_available = True
    for result in results:
        chunk = getattr(result, "chunk", None)
        metadata = getattr(chunk, "metadata", None)
        source_id = getattr(metadata, "source_id", None)
        if not isinstance(source_id, str) or not source_id:
            continue
        resource_ids.append(source_id)
        score = getattr(result, "score", None)
        if (
            isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not isfinite(score)
        ):
            all_scores_available = False
            continue
        scores.append(float(score))
    return (
        tuple(resource_ids),
        tuple(scores) if all_scores_available else (),
    )


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


def _model_sensitive_payload(state: QAState) -> dict[str, Any]:
    return {
        "message": state.get("message", ""),
        "customer_info": state.get("customer_info", {}),
        "bound_inputs": state.get("bound_inputs", {}),
        "evidence": state.get("current_context", ""),
        "current_answer": state.get("current_answer", ""),
    }


_SOURCE_BLOCK_HEADING = re.compile(
    r"^\s{0,3}(?:#{1,6}\s*)?(?:\*\*|__)?"
    r"(?:출처|참고\s*문서|검색\s*문서|sources?)"
    r"\s*:?\s*(?:\*\*|__)?\s*:?\s*$",
    re.IGNORECASE,
)
_SOURCE_DISCLOSURE_LINE = re.compile(
    r"^\s*(?:[-*+]\s+)?(?:#{1,6}\s*)?(?:\*\*|__)?"
    r"(?:출처|참고\s*문서|검색\s*문서|원문\s*위치|source_id|문서\s*제목|sources?)"
    r"\s*:?\s*(?:\*\*|__)?\s*:?\s*.+$",
    re.IGNORECASE,
)
_CONTEXT_METADATA_LINE = re.compile(
    r"^\s*(?:\*\*|__)?\[(?:근거|source_id|원문\s*위치)\s*:.*\]"
    r"(?:\*\*|__)?\s*$",
    re.IGNORECASE,
)
_SOURCE_LIST_ITEM = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)")
_INLINE_SOURCE_NOTE = re.compile(
    r"\s*\((?:출처|원문\s*위치|source_id)\s*:[^)]*\)",
    re.IGNORECASE,
)


def _strip_retrieval_source_disclosure(answer: str) -> str:
    """Keep grounded prose while removing retrieved-document source disclosure."""

    kept: list[str] = []
    dropping_source_list = False
    for line in answer.splitlines():
        stripped = line.strip()
        if _SOURCE_BLOCK_HEADING.fullmatch(line):
            dropping_source_list = True
            continue
        if dropping_source_list:
            if not stripped or _SOURCE_LIST_ITEM.match(line):
                continue
            dropping_source_list = False
        if (
            _SOURCE_DISCLOSURE_LINE.fullmatch(line)
            or _CONTEXT_METADATA_LINE.fullmatch(line)
        ):
            continue
        kept.append(_INLINE_SOURCE_NOTE.sub("", line).rstrip())
    return "\n".join(kept).strip()


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
