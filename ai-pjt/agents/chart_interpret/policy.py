"""Deterministic Chart Interpret response policy."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any
from .context import project_chart_facts


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
    if payload.get("status") != "ready":
        return set()
    facts = project_chart_facts(payload)
    allowed = {_normalized_number(value) for value in _walk_numbers(facts)}
    for token in _NUMBER.findall(json.dumps(facts, ensure_ascii=False).replace(",", "")):
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
    payload = project_chart_facts(payload)
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


def fallback_response(payload: Mapping[str, Any], *, entry: bool) -> str:
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
            f"- {item['label']}: {item['display']}" for item in highlights
        )
    else:
        conclusion = f"{title}의 검증 결과를 확인했습니다."
        bullets = "- 검증된 기간과 단위를 기준으로 차트 흐름을 확인해 주세요."
    warnings = [
        str(item.get("message"))
        for item in payload.get("warnings", [])
        if isinstance(item, Mapping) and item.get("message")
    ]
    caution = warnings[0] if warnings else "적용 가정이나 입력값이 바뀌면 보고서를 다시 계산해야 합니다."
    if entry:
        return f"{conclusion}\n\n{bullets}\n\n주의: {caution}"
    return f"{conclusion}\n\n{bullets}\n\n해석 한계: {caution}"


def response_is_allowed(
    response: str,
    payload: Mapping[str, Any],
    *,
    entry: bool,
) -> bool:
    bullet_count = sum(
        line.lstrip().startswith(("- ", "* ")) for line in response.splitlines()
    )
    progressive = not entry or (len(response) <= 1_200 and bullet_count <= 3)
    return not (
        unauthorized_numeric_claims(response, payload)
        or unsupported_causal_claims(response, payload)
        or recalculation_claims(response)
    ) and progressive
