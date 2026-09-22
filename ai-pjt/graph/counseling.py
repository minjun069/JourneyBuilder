"""Optional, bounded counseling additions to an immutable verified answer."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from copy import deepcopy
import json
import logging
from math import isfinite
import re
from time import monotonic
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr

from core.model_boundary import ModelPurpose, prompt_messages, secure_ainvoke
from core.monitoring import OperationStage, OperationStatus
from core.observability_masking import exception_identity_fields
from core.operation_observation import record_operation_observation
from core.prompt_inputs import conversation_for_model
from guardrails.boundary_policy import filter_rag_results
from prompts import get_prompt
from rag.counseling_retriever import CounselingTopic, get_counseling_retriever
from tools.output_policy import validate_form_update_contract


logger = logging.getLogger(__name__)
PLAN_PROMPT = get_prompt("counseling.plan")
COMPOSE_PROMPT = get_prompt("counseling.compose")
JUDGE_PROMPT = get_prompt("counseling.judge")
# The upstream window owns relevance/recency selection (3,200 estimated units,
# up to twelve recent turns). ASCII can use four characters per unit; this cap
# allows serialization overhead without introducing another history window.
MAX_CONTEXT_CHARACTERS = 16_000
_ELIGIBLE_TASKS = {"answer_question", "interpret_chart", "collect_qualitative_info"}
_INTERNAL_OR_LINK = re.compile(
    r"\b[a-z][a-z0-9+.-]*://|www\.|\]\(|\[\[|(?:source|evidence|chunk)_id|retirewise|"
    r"financial-knowledge|interface-guides|\[(?:자료|출처|근거)|출처\s*:|자료\s*:|"
    r"\b[\w.-]+\.(?:md|json|py)(?![a-z0-9_])|(?:/home/|/tmp/|/etc/|/mnt/|[a-z]:[\\/])",
    re.I,
)
_WRITTEN_QUANTITY = re.compile(
    r"(?:[영일이삼사오육칠팔구십백천만억조]+|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열)"
    r"\s*(?:퍼센트|개월|만원|억원|원|년|달|세|배|회)(?=$|[\s.,!?。]|[은는이가을를의에로부까])"
)
_EXECUTION_CLAIM = re.compile(
    r"(?:저장|승인|반영|수정|삭제|적용|처리).{0,8}(?:완료|했습니다|했어요|됐습니다|되었|해\s*드렸)|"
    r"(?:매수|매도|매입|매각|가입|해지|대환|상환)(?:를|을)?\s*(?:하세요|하십시오|권합니다|추천합니다|진행하)|"
    r"(?:무조건|반드시|분명|확실히).{0,16}(?:충분|괜찮|안전|보장)|"
    r"(?:충분합니다|안전합니다|괜찮습니다|걱정할\s*필요.{0,4}없|보장됩니다)"
)


class CounselingPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topic: CounselingTopic


class CounselingAddon(BaseModel):
    model_config = ConfigDict(extra="forbid")
    addon: StrictStr = Field(max_length=450)
    evidence_ids: list[StrictStr] = Field(max_length=3)


class CounselingJudgment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    grounded: StrictBool
    relevant: StrictBool
    noncontradiction: StrictBool
    no_new_personal_claims: StrictBool
    diagnosis_only: StrictBool


def _context_projection(context: Mapping[str, Any]) -> dict[str, Any]:
    """Remove metadata only; reject an oversized window instead of losing corrections."""
    projection = conversation_for_model(context)
    if len(json.dumps(projection, ensure_ascii=False)) > MAX_CONTEXT_CHARACTERS:
        raise ValueError("counseling context exceeds bounded input")
    return projection


async def _plan(payload: dict[str, Any], sensitive: dict[str, Any]) -> CounselingPlan:
    from core.llm import llm

    raw = await secure_ainvoke(
        llm.with_structured_output(CounselingPlan),
        prompt_messages(PLAN_PROMPT, payload),
        purpose=ModelPurpose.AGGREGATOR,
        operation_name="counseling.plan",
        sensitive_payload=sensitive,
        prompt=PLAN_PROMPT,
    )
    return raw if isinstance(raw, CounselingPlan) else CounselingPlan.model_validate(raw)


async def _compose(payload: dict[str, Any], sensitive: dict[str, Any]) -> CounselingAddon:
    from core.llm import llm

    raw = await secure_ainvoke(
        llm.with_structured_output(CounselingAddon),
        prompt_messages(COMPOSE_PROMPT, payload),
        purpose=ModelPurpose.AGGREGATOR,
        operation_name="counseling.compose",
        sensitive_payload=sensitive,
        prompt=COMPOSE_PROMPT,
    )
    return raw if isinstance(raw, CounselingAddon) else CounselingAddon.model_validate(raw)


async def _judge(payload: dict[str, Any], sensitive: dict[str, Any]) -> CounselingJudgment:
    from core.llm import llm

    raw = await secure_ainvoke(
        llm.with_structured_output(CounselingJudgment),
        prompt_messages(JUDGE_PROMPT, payload),
        purpose=ModelPurpose.AGGREGATOR,
        operation_name="counseling.judge",
        sensitive_payload=sensitive,
        prompt=JUDGE_PROMPT,
    )
    return raw if isinstance(raw, CounselingJudgment) else CounselingJudgment.model_validate(raw)


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


def _validate_addon(candidate: CounselingAddon, evidence: Sequence[Mapping[str, Any]], answer: str) -> str:
    addon = candidate.addon.strip()
    available = {row["evidence_id"] for row in evidence}
    used = candidate.evidence_ids
    if not addon or not used or len(used) != len(set(used)) or not set(used) <= available:
        raise ValueError("counseling addition lacks valid grounding references")
    if "\n" in addon or len([part for part in re.split(r"[.!?。！？]+", addon) if part.strip()]) > 3:
        raise ValueError("counseling addition exceeds sentence budget")
    if addon.count("?") + addon.count("？") > 1:
        raise ValueError("counseling addition asks too many questions")
    if re.search(r"\d", addon) or _WRITTEN_QUANTITY.search(addon):
        raise ValueError("counseling addition includes a new numerical expression")
    if _INTERNAL_OR_LINK.search(addon) or any(row["source_id"] in addon for row in evidence):
        raise ValueError("counseling addition discloses internal references")
    if _EXECUTION_CLAIM.search(addon) or addon in answer:
        raise ValueError("counseling addition exceeds explanation scope")
    return addon


async def enrich_counseling_response(
    *,
    output: Mapping[str, Any],
    step_id: str,
    question: str,
    conversation_context: Mapping[str, Any],
    task_types: Sequence[str],
    retriever: Any = None,
    timeout_seconds: float = 12.0,
) -> dict[str, Any]:
    """Three secure-invocation stages at most, no service retries or memory writes."""
    original = deepcopy(dict(output))
    answer = original.get("response")
    if not isinstance(answer, str) or not answer.strip() or not isinstance(question, str) or not question.strip():
        return original
    if not _ELIGIBLE_TASKS.intersection(task_types):
        return original
    try:
        if isinstance(timeout_seconds, bool) or not isfinite(timeout_seconds) or timeout_seconds <= 0:
            return original
        async with asyncio.timeout(timeout_seconds):
            context = _context_projection(conversation_context)
            payload = {"question": question, "conversation_context": context, "answer": answer,
                       "task_types": list(task_types), "step_id": step_id}
            # AGGREGATOR authorizes business numbers only in answer. The raw
            # question and conversation still pass through the ordinary masker.
            sensitive = {"answer": answer, "question": question, "conversation_context": context}
            plan = await _plan(payload, sensitive)
            if plan.topic == CounselingTopic.NONE:
                return original
            evidence = await _search(plan.topic, retriever)
            if not evidence:
                return original
            # Source IDs/scores belong to internal search observation, not prose.
            grounding = [{"evidence_id": row["evidence_id"], "text": row["text"]} for row in evidence]
            grounded_payload = {**payload, "evidence": grounding}
            candidate = await _compose(grounded_payload, sensitive)
            addon = _validate_addon(candidate, evidence, answer)
            judgment = await _judge(
                {**grounded_payload, "addon": {"text": addon, "evidence_ids": candidate.evidence_ids}},
                sensitive,
            )
            if not all(judgment.model_dump().values()):
                return original
            result = {**deepcopy(original), "response": answer + "\n\n" + addon}
            validation = validate_form_update_contract({"step_id": step_id, "output": result})
            return validation.output if validation.valid else original
    except asyncio.CancelledError:
        raise
    except Exception as error:  # optional prose must never discard a verified result
        logger.warning(
            "optional counseling returned the original response",
            extra={"event": "counseling_fallback", "detail": exception_identity_fields(error)},
        )
        return original
