"""Verified asynchronous summarization and conversation-memory retrieval."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
import logging
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from core.context_domain import (
    ContextScope,
    ContextSummary,
    ConversationContext,
    RetrievedMemory,
    StructuredConversationState,
    SummaryJob,
)
from core.context_service import ContextStateAPI
from core.context_window import ApproximateUnitCounter
from core.model_boundary import (
    ModelPurpose,
    bind_model_call_recorder,
    prompt_messages,
    secure_ainvoke,
)
from core.prompt_registry import bind_prompt_trace_sink
from core.runtime_instrumentation_adapter import RuntimeInstrumentationAdapter
from prompts import get_prompt


logger = logging.getLogger(__name__)
SUMMARY_PROMPT = get_prompt("summary.extract")

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
        "context",
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

    def __init__(
        self,
        model: Any,
        *,
        instrumentation: RuntimeInstrumentationAdapter | None = None,
    ) -> None:
        self._model = model
        self._instrumentation = instrumentation

    async def generate(self, job: SummaryJob) -> SummaryCandidate:
        source = _summary_source(job)
        runnable = self._model.with_structured_output(SummaryCandidate)
        messages = prompt_messages(SUMMARY_PROMPT, {"source": source})
        if self._instrumentation is None:
            result = await secure_ainvoke(
                runnable,
                messages,
                purpose=ModelPurpose.SUMMARY,
                operation_name="summary.extract",
                prompt=SUMMARY_PROMPT,
            )
        else:
            observed = self._instrumentation.bind(
                request_id=(
                    f"summary-{job.scope.session_id}-{job.target_cursor}-"
                    f"{job.attempts}"
                ),
                thread_id=job.scope.thread_id,
                step_id=f"summary-{job.target_cursor}",
            )
            with bind_model_call_recorder(observed.record_model_call):
                with bind_prompt_trace_sink(observed.record_prompt_invocation):
                    result = await secure_ainvoke(
                        runnable,
                        messages,
                        purpose=ModelPurpose.SUMMARY,
                        operation_name="summary.extract",
                        prompt=SUMMARY_PROMPT,
                    )
        return result if isinstance(result, SummaryCandidate) else SummaryCandidate.model_validate(result)


def verify_summary_candidate(
    job: SummaryJob,
    candidate: SummaryCandidate,
    *,
    max_summary_units: int = 600,
) -> tuple[ContextSummary, StructuredConversationState]:
    """Check source quotations and retain who supplied each remembered claim."""

    sources = _summary_sources(job)
    state = job.existing_structured_state
    goal = state.current_goal
    preferences = list(state.response_preferences)
    decisions = list(state.decisions)
    questions = list(state.open_questions)
    entities = list(state.entities)

    for claim in candidate.claims:
        value = " ".join(claim.value.split())
        quote = " ".join(claim.evidence_quote.split())
        origins = {origin for origin, text in sources if quote in text}
        if not value or not origins:
            raise SummaryValidationError("summary claim is not grounded in its source")
        # A source quote validates where the claim came from, not that an AI
        # suggestion is a user decision or a currently saved financial value.
        origin = next(iter(origins)) if len(origins) == 1 else "대화"
        value = f"{origin}: {value}"
        if claim.kind == "goal":
            goal = value
        elif claim.kind == "response_preference":
            _append_unique(preferences, value, limit=8)
        elif claim.kind == "decision":
            _append_unique(decisions, value, limit=12)
        elif claim.kind == "open_question":
            _append_unique(questions, value, limit=12)
        elif claim.kind in {"entity", "context"}:
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


def _summary_sources(job: SummaryJob) -> list[tuple[str, str]]:
    sources: list[tuple[str, str]] = []
    if job.existing_summary is not None and job.existing_summary.text:
        sources.append(("이전 요약", " ".join(job.existing_summary.text.split())))
    for turn in job.source_turns:
        sources.append(("사용자", " ".join(turn.user_message.split())))
        sources.append(("AI", " ".join(turn.assistant_message.split())))
    return sources


def _summary_source(job: SummaryJob) -> str:
    return "\n\n".join(f"{origin}: {text}" for origin, text in _summary_sources(job))


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
        sentences.append("대화 맥락: " + "; ".join(state.entities) + ".")
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
