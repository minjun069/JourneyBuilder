"""Verified task-result aggregation and response composition."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from copy import deepcopy
import logging
from math import isfinite
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from core.model_boundary import ModelPurpose, prompt_messages, secure_ainvoke
from core.observability_masking import masked_exception_fields
from graph.counseling import EXECUTION_CLAIM, INTERNAL_OR_LINK, WRITTEN_QUANTITY, counseling_evidence
from prompts import get_prompt
from tools.output_policy import validate_form_update_contract


logger = logging.getLogger(__name__)
_NUMBER_TOKEN = re.compile(r"(?<![A-Za-z0-9])[-+]?\d[\d,.]*(?:%|원|개월|년)?")
_MEANING_CONDITION = re.compile(
    r"저장|승인|제안|확인|적용|완료|변경안|반영|수정|삭제|추가|않|없|아니|경우|하면|부터|까지|이상|이하|초과|미만"
)


def _protected_sentences(response: str) -> list[str]:
    """Financial values and material conditions are not a copy editor's decision."""
    return [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?。])\s+|\n+", response)
        if sentence.strip() and (_NUMBER_TOKEN.search(sentence) or _MEANING_CONDITION.search(sentence))
    ]

AggregateComposer = Callable[..., Awaitable[dict[str, Any]]]


class AggregatorModelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    response: str = Field(min_length=1)


def failure_response(
    reason_code: str,
    *,
    detail_codes: Sequence[str] = (),
) -> str:
    """Render the deterministic recovery policy owned by the Aggregator."""

    if reason_code == "ambiguous_intent":
        return (
            "요청의 뜻을 하나로 확정하기 어렵습니다. 궁금한 내용이나 바꾸려는 "
            "입력 항목을 한 가지씩 구체적으로 말씀해 주세요."
        )
    if reason_code == "unsupported_intent":
        return (
            "현재 상담은 재무정보 입력, 재무진단 결과 설명과 은퇴설계 질문을 "
            "지원합니다. 이 범위 안의 요청으로 바꾸어 말씀해 주세요."
        )
    if reason_code == "all_tasks_failed":
        return (
            "실행한 작업에서 전달할 수 있는 검증된 결과를 만들지 못했습니다. "
            "위 실패 원인을 확인한 뒤 다시 시도해 주세요."
        )
    if reason_code == "plan_invalid":
        categories = _plan_failure_categories(detail_codes)
        detail = " ".join(categories)
        if detail:
            detail = f" {detail}"
        return (
            "요청을 안전한 실행 계획으로 확정하지 못했습니다."
            f"{detail} 요청을 한 가지씩 나누거나 필요한 대상을 더 구체적으로 "
            "적어 다시 시도해 주세요."
        )
    return "요청을 안전하게 처리하지 못했습니다. 내용을 확인해 다시 시도해 주세요."


def aggregate_task_results(
    *,
    tasks: list[Any],
    task_results: Mapping[str, Mapping[str, Any]],
    step_id: str,
    field_registry: list[dict[str, Any]],
    outcome_reason: str | None = None,
) -> dict[str, Any]:
    """Collapse internal results into one public response and at most one action."""

    responses: list[str] = []
    form_changes: list[Mapping[str, Any]] = []
    reviews: list[dict[str, Any]] = []
    elicitations: list[dict[str, Any]] = []
    completed_count = 0

    for task in tasks:
        if not isinstance(task, Mapping):
            continue
        result = task_results.get(str(task.get("id")))
        if not isinstance(result, Mapping):
            continue
        status = result.get("status")
        if status == "failed":
            responses.append(_failure_message(str(result.get("reason_code", ""))))
            continue
        if status == "blocked":
            responses.append("선행 작업이 완료되지 않아 연결된 요청은 실행하지 않았습니다.")
            continue
        if status != "completed":
            continue
        completed_count += 1

        if isinstance(result.get("review"), Mapping):
            reviews.append(deepcopy(dict(result["review"])))
        if isinstance(result.get("elicitation"), Mapping):
            elicitations.append(deepcopy(dict(result["elicitation"])))

        response = result.get("response")
        artifact = result.get("artifact")
        if isinstance(response, str) and response.strip():
            responses.append(response.strip())
        elif isinstance(artifact, Mapping) and artifact.get("type") == "text_response":
            text = artifact.get("text")
            if isinstance(text, str) and text.strip():
                responses.append(text.strip())

        if isinstance(artifact, Mapping) and artifact.get("type") == "reference_value":
            responses.append(_reference_value_message(artifact))
        if isinstance(artifact, Mapping) and artifact.get("type") == "form_change":
            form_changes.append(artifact)

    action, has_alternatives = _merge_form_changes(form_changes)
    if has_alternatives:
        responses.append(
            "같은 입력 항목에 서로 다른 변경안이 있어 선택지로 각각 제시했습니다."
        )

    if outcome_reason is not None:
        responses.append(failure_response(outcome_reason))
    elif tasks and completed_count == 0:
        responses.append(failure_response("all_tasks_failed"))

    output = {
        "response": "\n\n".join(_deduplicate(responses))
        or "요청을 처리할 수 있는 결과를 만들지 못했습니다. 내용을 확인해 다시 요청해 주세요.",
        "step_id": step_id,
        "action": action,
    }
    if len(reviews) == 1:
        output["review"] = reviews[0]
    if elicitations:
        if len(elicitations) != 1 or len(tasks) != 1 or completed_count != 1:
            raise ValueError("mindset collection requires one successful task")
        output["elicitation"] = elicitations[0]
    return validate_form_update_contract({
        "step_id": step_id,
        "output": output,
    }).output


async def compose_aggregate_response(
    *,
    output: Mapping[str, Any],
    step_id: str,
    field_registry: list[dict[str, Any]],
    question: str = "",
    conversation_context: Mapping[str, Any] | None = None,
    task_types: Sequence[str] = (),
    counseling_retriever: Any = None,
    timeout_seconds: float = 12.0,
) -> dict[str, Any]:
    """Rewrite once after optional counseling retrieval, preserving verified facts."""

    response = output.get("response")
    if not isinstance(response, str) or not response.strip():
        return deepcopy(dict(output))
    from core.llm import llm

    try:
        if counseling_retriever is not None and (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 30
        ):
            raise ValueError("invalid counseling composition timeout")
        async with asyncio.timeout(timeout_seconds if counseling_retriever is not None else None):
            context, evidence = ({}, [])
            if counseling_retriever is not None:
                context, evidence = await counseling_evidence(
                    question=question,
                    conversation_context=conversation_context or {},
                    task_types=task_types,
                    retriever=counseling_retriever,
                )
            prompt = get_prompt("aggregator.counseling") if evidence else get_prompt("aggregator.compose")
            payload = (
                {"answer": response, "question": question, "conversation_context": context,
                 "evidence": evidence}
                if evidence else {"answer": response}
            )
            raw = await secure_ainvoke(
                llm.with_structured_output(AggregatorModelOutput),
                prompt_messages(prompt, payload),
                purpose=ModelPurpose.AGGREGATOR,
                operation_name=prompt.prompt_id,
                sensitive_payload=(
                    {"answer": response, "question": question, "conversation_context": context}
                    if evidence else {"answer": response}
                ),
                prompt=prompt,
            )
        composed = (
            raw
            if isinstance(raw, AggregatorModelOutput)
            else AggregatorModelOutput.model_validate(raw)
        )
        if _NUMBER_TOKEN.findall(composed.response) != _NUMBER_TOKEN.findall(response):
            raise ValueError("aggregator changed verified numeric tokens or order")
        if evidence:
            if composed.response.strip().startswith(response.strip()) and composed.response.strip() != response.strip():
                raise ValueError("aggregator appended counseling instead of revising the answer")
            protected = _protected_sentences(response)
            actual = _protected_sentences(composed.response)
            remaining = iter(actual)
            if any(not any(item == expected for item in remaining) for expected in protected):
                raise ValueError("aggregator changed a verified condition or approval statement")
            for pattern in (WRITTEN_QUANTITY, EXECUTION_CLAIM, INTERNAL_OR_LINK):
                if pattern.findall(composed.response) != pattern.findall(response):
                    raise ValueError("aggregator added a quantity, execution claim or internal reference")
        elif _protected_sentences(composed.response) != _protected_sentences(response):
            raise ValueError("aggregator changed a verified value, condition or approval statement")
        validation = validate_form_update_contract({
            "step_id": step_id,
            "output": {
                **deepcopy(dict(output)),
                "response": composed.response.strip(),
            },
        })
        if not validation.valid:
            raise ValueError("aggregator response failed output validation")
        return validation.output
    except Exception as error:  # noqa: BLE001 - deterministic join is authoritative
        logger.warning(
            "aggregate response composer fell back to deterministic join",
            extra={
                "event": "aggregation_composer_fallback",
                "detail": masked_exception_fields(error, step_id=step_id),
            },
        )
        return deepcopy(dict(output))


def _plan_failure_categories(detail_codes: Sequence[str]) -> list[str]:
    categories: list[str] = []
    for raw_code in detail_codes:
        code = str(raw_code).upper()
        if code in {"PLANNER_ERROR", "STRUCTURED_MODEL_OUTPUT_ERROR"}:
            message = "계획 생성 모델이 유효한 구조를 반환하지 못했습니다."
        elif "BINDING" in code or "DEPENDENCY" in code or "CYCLIC" in code:
            message = "작업 간 순서와 결과 연결 관계를 안전하게 구성하지 못했습니다."
        elif "TASK_TYPE" in code or code == "UNSUPPORTED_TASK_TYPE":
            message = "현재 단계에서 지원되는 작업 유형으로 요청을 변환하지 못했습니다."
        elif "ROUTE" in code or "AGENT" in code or "TOOL" in code:
            message = "현재 단계에서 허용된 실행 경로를 확정하지 못했습니다."
        elif "CONTEXT" in code:
            message = "안전한 대화 문맥을 복원하지 못했습니다."
        else:
            message = "계획의 필수 구조가 실행 계약과 일치하지 않았습니다."
        if message not in categories:
            categories.append(message)
        if len(categories) == 2:
            break
    return categories


def _change_key(
    change: Mapping[str, Any],
    task_index: int,
    change_index: int,
) -> tuple[Any, ...]:
    if change.get("operation") == "add":
        return ("add", task_index, change_index)
    return (
        change.get("section"),
        change.get("path"),
        change.get("item_id"),
        change.get("client_item_id"),
    )


def _merge_form_changes(
    form_changes: list[Mapping[str, Any]],
) -> tuple[dict[str, Any] | None, bool]:
    if not form_changes:
        return None, False

    merged_changes: list[dict[str, Any]] = []
    changes_by_key: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    user_requested_values: list[bool] = []
    has_alternatives = False

    for form_index, form_change in enumerate(form_changes):
        user_requested_values.append(form_change.get("user_requested") is True)

        for change_index, raw_change in enumerate(form_change.get("changes", [])):
            if not isinstance(raw_change, Mapping):
                continue
            change = deepcopy(dict(raw_change))
            key = _change_key(change, form_index, change_index)
            existing_options = changes_by_key.setdefault(key, [])
            if any(
                _changes_equivalent(existing, change)
                for existing in existing_options
            ):
                continue
            if existing_options:
                has_alternatives = True
            existing_options.append(change)
            merged_changes.append(change)

    return {
        "type": "form_change",
        "target": "draft_form",
        "user_requested": all(user_requested_values),
        "changes": merged_changes,
    }, has_alternatives


def _failure_message(reason_code: str) -> str:
    messages = {
        "capability_unavailable": "승인된 기준값 조회 도구가 아직 연결되지 않아 해당 조회를 완료하지 못했습니다.",
        "binding_not_consumed": "조회 결과와 입력 변경안의 값이 일치하지 않아 변경 제안을 제외했습니다.",
        "reference_target_not_allowed": "조회 기준값을 현재 폼의 승인된 기존 항목에 안전하게 연결할 수 없어 변경 제안을 제외했습니다.",
        "reference_contract_mismatch": "요청한 기준값과 조회 결과의 식별 조건이 일치하지 않아 결과를 폐기했습니다.",
        "output_schema_invalid": "작업 결과가 안전성 검증을 통과하지 못해 응답에서 제외했습니다.",
        "artifact_invalid": "작업 결과가 안전성 검증을 통과하지 못해 응답에서 제외했습니다.",
        "dependency_failed": "선행 작업이 실패해 연결된 요청은 실행하지 않았습니다.",
        "executor_unavailable": "요청을 처리할 실행 기능이 연결되지 않았습니다.",
        "executor_error": "요청 처리 중 오류가 발생해 해당 결과를 제외했습니다.",
        "route_missing": "안전한 실행 경로를 확인하지 못해 해당 요청을 처리하지 않았습니다.",
        "binding_resolution_failed": "선행 결과를 후속 입력으로 안전하게 연결하지 못했습니다.",
        "task_budget_exhausted": "요청당 실행 가능한 작업 수를 초과했습니다.",
        "tool_resource_not_found": "검증된 조회 결과를 찾지 못했습니다.",
        "tool_dependency_unavailable": "검증된 조회 결과를 현재 사용할 수 없습니다.",
        "invalid_tool_arguments": "도구 조회 조건이 안전한 계약과 일치하지 않습니다.",
        "invalid_tool_result": "도구 조회 결과가 artifact 계약을 통과하지 못했습니다.",
        "invalid_tool_task": "도구 task가 허용된 실행 계약과 일치하지 않습니다.",
    }
    return messages.get(reason_code, "요청의 일부를 완료하지 못했습니다.")


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _changes_equivalent(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> bool:
    return {
        key: value for key, value in left.items() if key != "reason"
    } == {
        key: value for key, value in right.items() if key != "reason"
    }


def _reference_value_message(artifact: Mapping[str, Any]) -> str:
    value = artifact.get("value")
    rendered_value = f"{value:,}" if isinstance(value, (int, float)) else str(value)
    return (
        f"{artifact.get('population')} 기준 {artifact.get('period')} 값은 "
        f"{rendered_value} {artifact.get('unit')}입니다. "
        f"출처는 {artifact.get('source_id')}, 기준일은 {artifact.get('as_of')}입니다."
    )
