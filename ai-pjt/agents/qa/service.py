from __future__ import annotations

from collections.abc import Mapping
from math import isfinite
from time import monotonic
from typing import Any, NotRequired, TypedDict

from core.llm import llm
from core.model_boundary import ModelPurpose, prompt_messages, secure_ainvoke
from core.monitoring import OperationStage, OperationStatus
from core.observability_masking import exception_identity_fields
from core.operation_observation import record_operation_observation
from core.prompt_inputs import conversation_for_model, report_for_model
from rag.retriever import format_docs, get_retriever
from tools.agent_tools import lookup_reference, reference_options
from .model import (
    ANSWER_PROMPT as _ANSWER_PROMPT,
    JUDGE_PROMPT as _JUDGE_PROMPT,
    PLAN_PROMPT as _PLAN_PROMPT,
    JudgeResult,
    PlanResult,
    QuestionPlan,
    judge_chain as _judge_chain,
    plan_chain as _plan_chain,
)
from .policy import (
    has_reference_value,
    model_facts,
    relevant_customer_info,
    requests_unbound_official_value,
    strip_retrieval_source_disclosure,
)


MAX_RETRIES = 1
OFFICIAL_VALUE_UNAVAILABLE = (
    "현재 자료로는 요청하신 정확한 공식 금액이나 기준을 확인할 수 없습니다. "
    "확인된 공식 자료가 필요합니다."
)


class QAState(TypedDict):
    message: str
    user_message: NotRequired[str]
    step_id: str
    customer_info: dict
    conversation_context: NotRequired[dict[str, Any]]
    chart_payload: NotRequired[dict[str, Any] | None]
    conversation: dict[str, Any]
    report: dict[str, Any]
    task_input: NotRequired[dict[str, Any]]
    reference_results: NotRequired[dict[str, dict[str, Any]]]
    questions: list[dict[str, Any]]
    customer_context: dict[str, Any]
    evidence: list[dict[str, str]]
    official_only: bool
    current_answer: str
    feedback: str
    retries: int
    review_status: str
    next_step: str
    output: dict[str, Any]


def _bindings(state: Mapping[str, Any]) -> dict[str, Any]:
    task_input = state.get("task_input")
    bindings = task_input.get("bindings") if isinstance(task_input, Mapping) else None
    values = model_facts(bindings) if isinstance(bindings, Mapping) else {}
    return {**values, **{
        key: {"question": item["question"], **model_facts(item["reference"])}
        for key, item in state.get("reference_results", {}).items()
    }}


def _init(state: QAState) -> dict:
    return {
        "questions": [],
        "reference_results": {},
        "conversation": conversation_for_model(state.get("conversation_context") or {}),
        "report": report_for_model(state.get("chart_payload") or {}),
        "customer_context": relevant_customer_info(
            state["message"], state.get("customer_info", {}), state["step_id"]
        ),
        "evidence": [],
        "official_only": False,
        "current_answer": "",
        "feedback": "",
        "retries": 0,
        "review_status": "",
        "next_step": "",
        "output": {},
    }


async def _plan(state: QAState) -> dict:
    result = await secure_ainvoke(
        _plan_chain,
        prompt_messages(_PLAN_PROMPT, {
            **_question_context(state),
            "step_id": state["step_id"],
            "feedback": state["feedback"],
            "reference_options": reference_options(),
        }),
        purpose=ModelPurpose.QA,
        operation_name="qa.plan",
        sensitive_payload=_model_sensitive_payload(state),
        prompt=_PLAN_PROMPT,
    )
    return {"questions": [question.model_dump() for question in result.questions]}


async def _retrieve(state: QAState) -> dict:
    evidence: list[dict[str, str]] = []
    cache: dict[str, str] = {}
    unavailable = 0
    task_input = state.get("task_input", {})
    bindings = task_input.get("bindings", {})
    has_reference = isinstance(bindings, Mapping) and has_reference_value(bindings)
    references = dict(state.get("reference_results", {}))
    for question in state["questions"]:
        text = question["question"]
        reference_id = question.get("reference_id")
        question_has_reference = has_reference
        if reference_id:
            key = f"{reference_id}:{question.get('reference_age')}"
            if key not in references:
                reference = await lookup_reference(reference_id, question.get("reference_age"))
                if reference is not None:
                    references[key] = {"question": text, "reference": reference}
            question_has_reference = key in references
            if not question_has_reference:
                unavailable += 1
                evidence.append({"question": text, "reference": OFFICIAL_VALUE_UNAVAILABLE})
                continue
        if requests_unbound_official_value(
            message=text,
            current_query=question.get("query") or "",
            has_bindings=question_has_reference,
        ):
            unavailable += 1
            evidence.append({"question": text, "reference": OFFICIAL_VALUE_UNAVAILABLE})
            continue
        if not question["need_retrieval"]:
            continue
        query = question["query"]
        if query not in cache:
            cache[query] = await _search(query)
        evidence.append({"question": text, "reference": cache[query]})
    return {
        "evidence": evidence,
        "reference_results": references,
        "official_only": unavailable == len(state["questions"]),
    }


async def _search(query: str) -> str:
    started = monotonic()
    try:
        nodes = await get_retriever().aretrieve(query)
    except RuntimeError as error:
        nodes = []
        record_operation_observation(
            stage=OperationStage.SEARCH,
            status=OperationStatus.FAILED,
            operation_name="qa.search",
            duration_ms=max(0, int((monotonic() - started) * 1000)),
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
            duration_ms=max(0, int((monotonic() - started) * 1000)),
            agent="qa",
            resource_ids=resource_ids,
            scores=scores,
            result_count=len(nodes),
            output={"result_count": len(nodes)},
        )
    return format_docs(nodes)


def _question_context(state: QAState) -> dict[str, Any]:
    return {
        "question": state["message"],
        "user_message": state.get("user_message") or state["message"],
        "conversation": state.get("conversation", {}),
        "report": state.get("report", {}),
    }


def _answer_context(state: QAState) -> dict[str, Any]:
    return {
        **_question_context(state),
        "customer_info": state["customer_context"],
        "bindings": _bindings(state),
        "evidence": state["evidence"],
    }


async def _generate(state: QAState) -> dict:
    if state["official_only"]:
        return {"current_answer": OFFICIAL_VALUE_UNAVAILABLE}
    result = await secure_ainvoke(
        llm,
        prompt_messages(_ANSWER_PROMPT, {
            **_answer_context(state), "feedback": state["feedback"],
        }, step_id=state["step_id"]),
        purpose=ModelPurpose.QA,
        operation_name="qa.generate",
        sensitive_payload=_model_sensitive_payload(state),
        prompt=_ANSWER_PROMPT,
    )
    return {"current_answer": strip_retrieval_source_disclosure(result.content)}


async def _judge(state: QAState) -> dict:
    if state["official_only"]:
        return {"review_status": "good", "next_step": "finish"}
    result = await secure_ainvoke(
        _judge_chain,
        prompt_messages(_JUDGE_PROMPT, {
            **_answer_context(state), "answer": state["current_answer"],
        }, step_id=state["step_id"]),
        purpose=ModelPurpose.QA,
        operation_name="qa.judge",
        sensitive_payload=_model_sensitive_payload(state),
        prompt=_JUDGE_PROMPT,
    )
    if result.status == "good" or state["retries"] >= MAX_RETRIES:
        return {"review_status": result.status, "next_step": "finish"}
    refresh = result.status == "evidence_insufficient" and any(
        question["need_retrieval"] for question in state["questions"]
    )
    return {
        "review_status": result.status,
        "feedback": result.reason,
        "retries": state["retries"] + 1,
        "next_step": "plan" if refresh else "generate",
    }


def _route_after_judge(state: QAState) -> str:
    return state["next_step"]


def _finish(state: QAState) -> dict:
    response = state["current_answer"] if state["review_status"] == "good" else (
        "현재 자료로는 정확한 답변을 확인하기 어렵습니다."
    )
    return {"output": {"response": response, "step_id": state["step_id"], "action": None}}


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
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not isfinite(score):
            all_scores_available = False
            continue
        scores.append(float(score))
    return tuple(resource_ids), tuple(scores) if all_scores_available else ()


def _model_sensitive_payload(state: QAState) -> dict[str, Any]:
    return {
        "message": state.get("message", ""),
        "user_message": state.get("user_message", ""),
        "conversation": state.get("conversation", {}),
        "report": state.get("report", {}),
        "customer_info": state.get("customer_info", {}),
        "bindings": _bindings(state),
        "evidence": state.get("evidence", []),
        "current_answer": state.get("current_answer", ""),
    }
