"""Explanation-only consumer for Backend-verified chart payloads."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from langgraph.graph import END, START, StateGraph

from core.llm import llm
from core.model_boundary import (
    ModelPurpose,
    prompt_messages,
    secure_ainvoke,
)
from graph.state import ChatState
from prompts import get_prompt
from rag.chart_knowledge import ChartKnowledgeError, load_chart_knowledge


CHART_PROMPT = get_prompt("chart_interpret.explain")
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
_CAUSE_MARKER = re.compile(r"원인|때문|영향|따라|로 인해|에서 비롯|기여|증가해|감소해")
_RECALC_MARKER = re.compile(r"계산하면|빼면|더하면|나누면|곱하면|차액은|합계는")


def _normalized_number(value: str | int | float) -> str:
    number = float(value)
    return str(int(number)) if number.is_integer() else format(number, ".12g")


def _walk_numbers(value: Any):
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        yield value
        return
    if isinstance(value, Mapping):
        for child in value.values():
            yield from _walk_numbers(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_numbers(child)


def _authorized_numbers(payload: Mapping[str, Any]) -> set[str]:
    allowed = {_normalized_number(value) for value in _walk_numbers(payload)}
    # Percent display is a deterministic Backend display convention for
    # fraction/decimal values, not a value the model is asked to calculate.
    for value in _walk_numbers(payload):
        if isinstance(value, float) and 0 < abs(value) <= 1:
            allowed.add(_normalized_number(value * 100))
    for token in _NUMBER.findall(json.dumps(payload, ensure_ascii=False).replace(",", "")):
        allowed.add(_normalized_number(token))
    return allowed


def unauthorized_numeric_claims(response: str, payload: Mapping[str, Any]) -> set[str]:
    without_list_markers = re.sub(r"(?m)^\s*\d+[.)]\s+", "", response)
    claimed = {
        _normalized_number(token)
        for token in _NUMBER.findall(without_list_markers.replace(",", ""))
    }
    return claimed - _authorized_numbers(payload)


def _evidence_terms(payload: Mapping[str, Any]) -> set[str]:
    evidence_roots = (
        payload.get("highlights", []),
        payload.get("events", []),
        payload.get("warnings", []),
        payload.get("details", {}),
        payload.get("connections", []),
        payload.get("assumption_details", []),
    )
    terms: set[str] = set()

    def collect(value: Any) -> None:
        if isinstance(value, str):
            normalized = value.replace("_", " ").strip()
            if len(normalized) >= 2:
                terms.add(normalized)
        elif isinstance(value, Mapping):
            for key, child in value.items():
                if key not in {"id", "path", "source", "provenance", "display"}:
                    collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    for root in evidence_roots:
        collect(root)
    return terms


def unsupported_causal_claims(
    response: str,
    payload: Mapping[str, Any],
) -> list[str]:
    """Reject causal prose that cannot be tied to structured payload evidence."""
    evidence = _evidence_terms(payload)
    sentences = re.split(r"(?:[.!?。]\s+|\n+)", response)
    return [
        sentence.strip()
        for sentence in sentences
        if _CAUSE_MARKER.search(sentence)
        and not any(term in sentence for term in evidence)
    ]


def recalculation_claims(response: str) -> list[str]:
    return [match.group(0) for match in _RECALC_MARKER.finditer(response)]


def _fallback_response(payload: Mapping[str, Any], *, entry: bool) -> str:
    title = str(payload.get("title") or "현재 보고서")
    if payload.get("status") != "ready":
        return (
            f"{title} 화면에서는 검증된 수치가 준비되기 전까지 숫자를 추정하지 않습니다. "
            "현재는 보고서의 목적, 축과 범례를 읽는 순서, 다른 보고서와 함께 볼 때의 "
            "주의사항만 안내해 드릴 수 있습니다."
        )
    highlights = [
        item for item in payload.get("highlights", [])
        if isinstance(item, Mapping) and item.get("label") and item.get("display")
    ][:3]
    if highlights:
        conclusion = f"{title}의 우선 확인 항목은 {highlights[0]['label']} {highlights[0]['display']}입니다."
        bullets = "\n".join(
            f"- {item['label']}: {item['display']}"
            for item in highlights
        )
    else:
        conclusion = f"{title}의 검증 결과를 확인했습니다."
        bullets = "- 검증된 기간과 단위를 기준으로 차트 흐름을 확인해 주세요."
    warnings = [
        str(item.get("message"))
        for item in payload.get("warnings", [])
        if isinstance(item, Mapping) and item.get("message")
    ]
    caution = warnings[0] if warnings else "적용 가정이나 입력 snapshot이 바뀌면 보고서를 다시 계산해야 합니다."
    if entry:
        return f"{conclusion}\n\n{bullets}\n\n주의: {caution}"
    return f"{conclusion}\n\n{bullets}\n\n해석 한계: {caution}"


def _is_progressive_entry(response: str) -> bool:
    bullet_count = sum(
        line.lstrip().startswith(("- ", "* "))
        for line in response.splitlines()
    )
    return len(response) <= 1_200 and bullet_count <= 3


async def _run(state: ChatState) -> dict:
    payload = state.get("chart_payload")
    if payload is None:
        return {
            "output": {
                "response": (
                    "현재 단계는 Chart Agent 설명 대상이 아닙니다. "
                    "해당 화면의 일반 질문으로 다시 요청해 주세요."
                ),
                "step_id": state["step_id"],
                "action": None,
            }
        }
    if (
        not isinstance(payload, Mapping)
        or payload.get("chart_id") != state.get("step_id")
        or payload.get("status") not in {"ready", "guidance_only"}
        or payload.get("read_only") is not True
    ):
        raise ValueError("invalid chart payload contract")
    try:
        knowledge = load_chart_knowledge(
            state["step_id"],
            payload.get("knowledge_scope", {}).get("source_ids"),
        )
    except ChartKnowledgeError:
        knowledge = "승인 문서 주입을 확인할 수 없어 payload 사실만 설명합니다."

    entry = state.get("request_mode") == "presentation_entry"
    result = await secure_ainvoke(
        llm,
        prompt_messages(CHART_PROMPT, {
            "user_request": state["message"],
            "chart_payload": payload,
            "approved_chart_knowledge": knowledge,
            "response_mode": "initial_entry" if entry else "detail",
        }),
        purpose=ModelPurpose.CHART_INTERPRET,
        operation_name="chart_interpret.generate",
        sensitive_payload={
            "message": state["message"],
            "chart_payload": payload,
        },
        prompt=CHART_PROMPT,
    )
    response = str(result.content)
    if (
        unauthorized_numeric_claims(response, payload)
        or unsupported_causal_claims(response, payload)
        or recalculation_claims(response)
        or (
        entry and not _is_progressive_entry(response)
        )
    ):
        response = _fallback_response(payload, entry=entry)
    return {
        "output": {
            "response": response,
            "step_id": state["step_id"],
            "action": None,
        }
    }


def _build():
    graph_builder = StateGraph(ChatState)
    graph_builder.add_node("run", _run)
    graph_builder.add_edge(START, "run")
    graph_builder.add_edge("run", END)
    return graph_builder.compile()


graph = _build()
