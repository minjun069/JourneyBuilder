"""Verified asynchronous summarization and conversation-memory retrieval."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
import json
import logging
import re
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from core.context import (
    ApproximateUnitCounter,
    ContextScope,
    ContextStateAPI,
    ContextSummary,
    ConversationContext,
    RetrievedMemory,
    StructuredConversationState,
    SummaryJob,
)
from core.model_boundary import ModelPurpose, secure_ainvoke, secure_system_prompt


logger = logging.getLogger(__name__)

_DIRECT_IDENTIFIER = re.compile(
    r"(?:[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)"
    r"|(?:(?:\+?82[- .]?)?0?1[016789][- .]?\d{3,4}[- .]?\d{4})"
    r"|(?:\d{6}[- ]?[1-4]\d{6})"
)
_SECRET = re.compile(
    r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+|"
    r"[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
)
_REFERENCE_TOKEN = re.compile(r"\[\[[^\[\]\r\n]{1,256}\]\]")
_FINANCIAL_MEMORY = re.compile(
    r"(?i)(?:소득|연봉|월급|자산|부채|대출|잔액|저축|연금|보험|지출|금액|수익률|"
    r"income|salary|asset|debt|loan|balance|saving|pension|expense|amount|return)"
)


class SummaryValidationError(ValueError):
    """Raised when a model summary cannot be proven from its source archive."""


class SummaryClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal[
        "goal",
        "response_preference",
        "decision",
        "open_question",
        "entity",
    ]
    value: str = Field(min_length=1, max_length=280)
    evidence_quote: str = Field(min_length=3, max_length=500)


class SummaryCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    claims: list[SummaryClaim] = Field(default_factory=list, max_length=40)


class SummaryGenerator(Protocol):
    async def generate(self, job: SummaryJob) -> SummaryCandidate:
        """Return typed, evidence-bearing candidate claims."""


class ModelSummaryGenerator:
    """Provider adapter; deterministic verification remains a separate step."""

    def __init__(self, model: Any) -> None:
        self._model = model

    async def generate(self, job: SummaryJob) -> SummaryCandidate:
        from langchain_core.messages import HumanMessage, SystemMessage

        source = _summary_source(job)
        system = SystemMessage(content=secure_system_prompt("""
당신은 대화 메모리 추출기입니다. 아래 source에 문자 그대로 근거가 있는 비금융 대화
정보만 claims로 반환하세요. goal, 응답 형식 선호, 확정된 의사결정, 미해결 질문,
프로젝트·기술 같은 개체만 허용됩니다. 금액·소득·자산·부채·연금·개인 식별정보·secret·
참조 token은 절대 추출하지 마세요. value는 evidence_quote 안에 그대로 존재해야 하고,
evidence_quote도 source에서 그대로 복사해야 합니다. 근거가 없으면 claims를 비워 두세요.
"""))
        human = HumanMessage(content=json.dumps(
            {"source": source},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ))
        runnable = self._model.with_structured_output(SummaryCandidate)
        result = await secure_ainvoke(
            runnable,
            [system, human],
            purpose=ModelPurpose.SUMMARY,
            operation_name="summary.extract",
        )
        return result if isinstance(result, SummaryCandidate) else SummaryCandidate.model_validate(result)


def verify_summary_candidate(
    job: SummaryJob,
    candidate: SummaryCandidate,
    *,
    max_summary_units: int = 600,
) -> tuple[ContextSummary, StructuredConversationState]:
    """Build persisted state only from exact, safe source quotations."""

    source = _summary_source(job)
    state = job.existing_structured_state
    goal = state.current_goal
    preferences = list(state.response_preferences)
    decisions = list(state.decisions)
    questions = list(state.open_questions)
    entities = list(state.entities)

    for claim in candidate.claims:
        value = " ".join(claim.value.split())
        quote = " ".join(claim.evidence_quote.split())
        if not value or quote not in source or value not in quote:
            raise SummaryValidationError("summary claim is not grounded in its source")
        if _unsafe_memory_text(value):
            raise SummaryValidationError("summary claim contains forbidden memory data")
        if claim.kind == "goal":
            goal = value
        elif claim.kind == "response_preference":
            _append_unique(preferences, value, limit=8)
        elif claim.kind == "decision":
            _append_unique(decisions, value, limit=12)
        elif claim.kind == "open_question":
            _append_unique(questions, value, limit=12)
        elif claim.kind == "entity":
            _append_unique(entities, value, limit=12)

    verified_state = StructuredConversationState(
        current_goal=goal,
        response_preferences=tuple(preferences),
        decisions=tuple(decisions),
        open_questions=tuple(questions),
        entities=tuple(entities),
    )
    verified_state, text = _fit_summary(verified_state, max_summary_units)
    units = ApproximateUnitCounter().estimate_text(text) if text else 0
    return (
        ContextSummary(
            text=text,
            estimated_units=units,
            summarized_through_sequence=job.target_cursor,
        ),
        verified_state,
    )


class ConversationMemoryManager:
    """Isolate provider-backed memory work from the terminal response path."""

    def __init__(
        self,
        context_api: ContextStateAPI,
        *,
        embedding_model: Any,
        summary_generator: SummaryGenerator,
        retrieval_limit: int = 5,
        embedding_batch_size: int = 8,
    ) -> None:
        self._context_api = context_api
        self._embedding_model = embedding_model
        self._summary_generator = summary_generator
        self._retrieval_limit = retrieval_limit
        self._embedding_batch_size = embedding_batch_size

    async def retrieve(
        self,
        scope: ContextScope,
        query: str,
        context: ConversationContext,
    ) -> tuple[RetrievedMemory, ...]:
        embedding: tuple[float, ...] | None = None
        try:
            embedding = tuple(float(value) for value in await self._embed_query(query))
        except Exception:  # noqa: BLE001 - retrieval enrichment is optional
            logger.warning("conversation_retrieval_embedding_failed")
        try:
            return await self._context_api.retrieve_memories(
                scope,
                embedding,
                exclude_turn_ids=tuple(turn.turn_id for turn in context.turns),
                limit=self._retrieval_limit,
            )
        except Exception:  # noqa: BLE001 - a request can continue with recent context
            logger.exception("conversation_memory_retrieval_failed")
            return ()

    async def run_once(self) -> bool:
        did_work = await self._embed_pending_archive()
        job = await self._context_api.claim_summary_job()
        if job is None:
            return did_work
        try:
            candidate = await self._summary_generator.generate(job)
            summary, state = verify_summary_candidate(job, candidate)
            await self._context_api.complete_summary_job(
                job,
                summary=summary,
                structured_state=state,
            )
        except Exception as error:  # noqa: BLE001 - persisted retry owns recovery
            await self._context_api.fail_summary_job(
                job,
                type(error).__name__.lower(),
            )
            logger.warning("conversation_summary_attempt_failed")
        return True

    async def _embed_pending_archive(self) -> bool:
        rows = await self._context_api.pending_archive_embeddings(
            limit=self._embedding_batch_size,
        )
        if not rows:
            return False
        for row in rows:
            text = f"사용자: {row.turn.user_message}\nAI: {row.turn.assistant_message}"
            try:
                vector = tuple(
                    float(value) for value in await self._embed_text(text)
                )
                await self._context_api.store_archive_embedding(row.archive_id, vector)
            except Exception:  # noqa: BLE001 - NULL embedding remains retryable
                logger.warning("conversation_archive_embedding_failed")
                break
        return True

    async def _embed_query(self, text: str) -> Sequence[float]:
        method = getattr(self._embedding_model, "aget_query_embedding", None)
        if callable(method):
            return await method(text)
        return await asyncio.to_thread(self._embedding_model.get_query_embedding, text)

    async def _embed_text(self, text: str) -> Sequence[float]:
        method = getattr(self._embedding_model, "aget_text_embedding", None)
        if callable(method):
            return await method(text)
        return await asyncio.to_thread(self._embedding_model.get_text_embedding, text)


def _summary_source(job: SummaryJob) -> str:
    sections: list[str] = []
    if job.existing_summary is not None and job.existing_summary.text:
        sections.append("기존 검증 요약:\n" + " ".join(job.existing_summary.text.split()))
    for turn in job.source_turns:
        sections.append(
            "사용자: "
            + " ".join(turn.user_message.split())
            + "\nAI: "
            + " ".join(turn.assistant_message.split())
        )
    return "\n\n".join(sections)


def _unsafe_memory_text(value: str) -> bool:
    return bool(
        any(character.isdigit() for character in value)
        or _DIRECT_IDENTIFIER.search(value)
        or _SECRET.search(value)
        or _REFERENCE_TOKEN.search(value)
        or _FINANCIAL_MEMORY.search(value)
    )


def _append_unique(items: list[str], value: str, *, limit: int) -> None:
    if value in items:
        return
    items.append(value)
    if len(items) > limit:
        del items[0 : len(items) - limit]


def _render_summary(state: StructuredConversationState) -> str:
    sentences: list[str] = []
    if state.current_goal:
        sentences.append(f"현재 목표: {state.current_goal}.")
    if state.response_preferences:
        sentences.append("응답 선호: " + "; ".join(state.response_preferences) + ".")
    if state.decisions:
        sentences.append("결정 사항: " + "; ".join(state.decisions) + ".")
    if state.open_questions:
        sentences.append("미해결 질문: " + "; ".join(state.open_questions) + ".")
    if state.entities:
        sentences.append("주요 개체: " + "; ".join(state.entities) + ".")
    return " ".join(sentences)


def _fit_summary(
    state: StructuredConversationState,
    max_units: int,
) -> tuple[StructuredConversationState, str]:
    counter = ApproximateUnitCounter()
    working = {
        "current_goal": state.current_goal,
        "response_preferences": list(state.response_preferences),
        "decisions": list(state.decisions),
        "open_questions": list(state.open_questions),
        "entities": list(state.entities),
    }
    while True:
        projected = StructuredConversationState(
            current_goal=working["current_goal"],
            response_preferences=tuple(working["response_preferences"]),
            decisions=tuple(working["decisions"]),
            open_questions=tuple(working["open_questions"]),
            entities=tuple(working["entities"]),
        )
        text = _render_summary(projected)
        if counter.estimate_text(text) <= max_units:
            return projected, text
        for key in ("entities", "open_questions", "decisions", "response_preferences"):
            values = working[key]
            if values:
                values.pop(0)
                break
        else:
            working["current_goal"] = None
