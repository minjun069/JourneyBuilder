"""Bounded, topic-only evidence lookup for the Aggregator's single rewrite."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
import json
from math import isfinite
import re
from time import monotonic
from typing import Any

from core.monitoring import OperationStage, OperationStatus
from core.observability_masking import exception_identity_fields
from core.operation_observation import record_operation_observation
from core.prompt_inputs import conversation_for_model
from guardrails.boundary_policy import filter_rag_results
from rag.counseling_retriever import CounselingTopic, get_counseling_retriever


# The upstream window owns relevance/recency selection (3,200 estimated units,
# up to twelve recent turns). ASCII can use four characters per unit; this cap
# allows serialization overhead without introducing another history window.
MAX_CONTEXT_CHARACTERS = 16_000
_ELIGIBLE_TASKS = {"answer_question", "interpret_chart", "collect_qualitative_info"}
INTERNAL_OR_LINK = re.compile(
    r"\b[a-z][a-z0-9+.-]*://|www\.|\]\(|\[\[|(?:source|evidence|chunk)_id|retirewise|"
    r"financial-knowledge|interface-guides|\[(?:자료|출처|근거)|출처\s*:|자료\s*:|"
    r"\b[\w.-]+\.(?:md|json|py)(?![a-z0-9_])|(?:/home/|/tmp/|/etc/|/mnt/|[a-z]:[\\/])",
    re.I,
)
WRITTEN_QUANTITY = re.compile(
    r"(?:[영일이삼사오육칠팔구십백천만억조]+|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열)"
    r"\s*(?:퍼센트|개월|만원|억원|원|년|달|세|배|회)(?=$|[\s.,!?。]|[은는이가을를의에로부까])"
)
EXECUTION_CLAIM = re.compile(
    r"(?:저장|승인|반영|수정|삭제|적용|처리).{0,8}(?:완료|했습니다|했어요|됐습니다|되었|해\s*드렸)|"
    r"(?:매수|매도|매입|매각|가입|해지|대환|상환)(?:를|을)?\s*(?:하세요|하십시오|권합니다|추천합니다|진행하)|"
    r"(?:무조건|반드시|분명|확실히).{0,16}(?:충분|괜찮|안전|보장)|"
    r"(?:충분합니다|안전합니다|괜찮습니다|걱정할\s*필요.{0,4}없|보장됩니다)"
)


def _context_projection(context: Mapping[str, Any]) -> dict[str, Any]:
    """Remove metadata only; reject an oversized window instead of losing corrections."""
    projection = conversation_for_model(context)
    if len(json.dumps(projection, ensure_ascii=False)) > MAX_CONTEXT_CHARACTERS:
        raise ValueError("counseling context exceeds bounded input")
    return projection


_TOPIC_TERMS = (
    (CounselingTopic.DEBT, ("대출", "부채", "상환", "납부", "이자", "빚")),
    (CounselingTopic.PENSION, ("연금", "수령", "개시")),
    (CounselingTopic.CASHFLOW, ("현금흐름", "생활비", "소득", "수입", "휴직", "실직", "입금", "지출", "비상금")),
    (CounselingTopic.ASSETS, ("자산", "유동성", "현금화", "부동산", "처분")),
    (CounselingTopic.GOALS, ("목표", "우선순위", "가족", "배우자", "부모", "목돈")),
    (CounselingTopic.RETIREMENT, ("은퇴", "노후", "진단", "보고서")),
    (CounselingTopic.COMPARISON, ("비교", "평균", "또래", "물가", "금리", "통계")),
    (CounselingTopic.WELLBEING, ("걱정", "불안", "불편", "막막")),
    (CounselingTopic.UNDERSTANDING, ("이해", "해석", "정정", "추정")),
)
_NO_COUNSELING = re.compile(
    r"고마워|감사합니다|안녕|그만|끝내|더\s*묻지\s*마|추가\s*질문.*(?:싫|말아)"
    r"|(?:금액|숫자|나이|입력).*만\s*정정|뜻만\s*짧게|조회만"
)


def select_topic(question: str) -> CounselingTopic:
    """Route only explicit public concepts; never send a customer phrase to search."""
    if _NO_COUNSELING.search(question) and not re.search(r"[?？]|그런데|하지만", question):
        return CounselingTopic.NONE
    for topic, terms in _TOPIC_TERMS:
        if any(term in question for term in terms):
            return topic
    return CounselingTopic.NONE


def _retrieve(topic: CounselingTopic, retriever: Any) -> list[dict[str, Any]]:
    active = retriever if retriever is not None else get_counseling_retriever()
    results = active.retrieve(topic)
    if not isinstance(results, (list, tuple)) or len(results) > 3:
        raise ValueError("invalid counseling retrieval result count")
    evidence: list[dict[str, Any]] = []
    for result in results:
        row = {key: getattr(result, key) for key in ("evidence_id", "text", "source_id", "score")}
        if any(not isinstance(row[key], str) or not row[key].strip() for key in ("evidence_id", "text", "source_id")):
            raise ValueError("invalid counseling evidence metadata")
        if len(row["text"]) > 6000 or not re.fullmatch(r"E[1-3]", row["evidence_id"]):
            raise ValueError("invalid counseling evidence bounds")
        score = row["score"]
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not isfinite(score):
            raise ValueError("invalid counseling retrieval score")
        evidence.append(row)
    if len({row["evidence_id"] for row in evidence}) != len(evidence):
        raise ValueError("duplicate counseling evidence ID")
    # The common filter reads Mapping.text; immutable evidence dataclasses are
    # projected without modifying the retriever's result or weakening its status.
    return filter_rag_results(evidence)


async def _search(topic: CounselingTopic, retriever: Any) -> list[dict[str, Any]]:
    started = monotonic()
    try:
        evidence = await asyncio.to_thread(_retrieve, topic, retriever)
    except (Exception, asyncio.CancelledError) as error:
        record_operation_observation(
            stage=OperationStage.SEARCH,
            status=OperationStatus.FAILED,
            operation_name="counseling.search",
            duration_ms=max(0, int((monotonic() - started) * 1000)),
            agent="aggregator",
            error_code="counseling_retrieval_unavailable",
            result_count=0,
            output={"result_count": 0},
            **exception_identity_fields(error),
        )
        raise
    record_operation_observation(
        stage=OperationStage.SEARCH,
        status=OperationStatus.SUCCEEDED,
        operation_name="counseling.search",
        duration_ms=max(0, int((monotonic() - started) * 1000)),
        agent="aggregator",
        resource_ids=tuple(row["source_id"] for row in evidence),
        scores=tuple(float(row["score"]) for row in evidence),
        result_count=len(evidence),
        output={"result_count": len(evidence)},
    )
    return evidence


async def counseling_evidence(
    *,
    question: str,
    conversation_context: Mapping[str, Any],
    task_types: Sequence[str],
    retriever: Any,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Return the selected context and grounded cards before answer generation."""
    if not _ELIGIBLE_TASKS.intersection(task_types):
        return {}, []
    topic = select_topic(question)
    if topic is CounselingTopic.NONE:
        return {}, []
    context = _context_projection(conversation_context)
    evidence = await _search(topic, retriever)
    return context, [
        {"evidence_id": row["evidence_id"], "text": row["text"]}
        for row in evidence
    ]
