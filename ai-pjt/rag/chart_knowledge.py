"""Safe injection of approved RAG documents for Presentation explanations."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any


DATA_ROOT = Path(__file__).resolve().parent / "data"
CATALOG_PATH = DATA_ROOT / "catalog.json"
PRESENTATION_GUIDE_SOURCE = "retirewise-presentation-report-interpretation"

CHART_KNOWLEDGE_SOURCES: dict[str, tuple[str, ...]] = {
    "balance-sheet": (
        PRESENTATION_GUIDE_SOURCE,
        "retirewise-household-balance-cash-flow",
    ),
    "cash-flow": (
        PRESENTATION_GUIDE_SOURCE,
        "retirewise-household-balance-cash-flow",
    ),
    "financial-indicators": (
        PRESENTATION_GUIDE_SOURCE,
        "retirewise-household-financial-indicator-policy",
        "retirewise-household-balance-cash-flow",
    ),
    "financial-goal-result": (
        PRESENTATION_GUIDE_SOURCE,
        "retirewise-financial-goals-time-value",
        "retirewise-planning-assumptions-2026",
    ),
    "retirement-fund": (
        PRESENTATION_GUIDE_SOURCE,
        "retirewise-retirement-planning-report-interpretation",
        "retirewise-planning-assumptions-2026",
    ),
    "retirement-readiness": (
        PRESENTATION_GUIDE_SOURCE,
        "retirewise-retirement-planning-report-interpretation",
        "retirewise-planning-assumptions-2026",
    ),
    "retirement-simulation": (
        PRESENTATION_GUIDE_SOURCE,
        "retirewise-retirement-planning-report-interpretation",
        "retirewise-planning-assumptions-2026",
    ),
    "pension-schedule": (
        PRESENTATION_GUIDE_SOURCE,
        "retirewise-retirement-planning-report-interpretation",
        "retirewise-planning-assumptions-2026",
    ),
    "lifetime-cashflow": (
        PRESENTATION_GUIDE_SOURCE,
        "retirewise-retirement-planning-report-interpretation",
        "retirewise-planning-assumptions-2026",
        "retirewise-household-balance-cash-flow",
    ),
    "required-savings": (
        PRESENTATION_GUIDE_SOURCE,
        "retirewise-retirement-planning-report-interpretation",
        "retirewise-financial-goals-time-value",
        "retirewise-planning-assumptions-2026",
    ),
}

_NUMBER = re.compile(r"(?<![A-Za-z가-힣])[-+]?\d[\d,.]*(?:%|원|세|년|개월|배)?")
_SECTION_END = re.compile(r"^## (?:공식 출처|갱신|출처)", re.MULTILINE)
_MAX_SECTION_CHARACTERS = 900
_MAX_CONTEXT_CHARACTERS = 2_400
_CHART_TITLES = dict(zip(CHART_KNOWLEDGE_SOURCES, (
    "자산·부채 현황", "현금흐름", "가계재무지표", "재무목표 결과", "은퇴 필요자금",
    "은퇴 준비도", "은퇴 시뮬레이션", "연금 수령 일정", "생애 현금흐름", "필요저축액",
)))


class ChartKnowledgeError(ValueError):
    pass


def load_chart_knowledge(step_id: str, requested_source_ids: Any, *, question: str = "") -> str:
    """Return the current report guide and at most two relevant source sections."""
    approved = CHART_KNOWLEDGE_SOURCES.get(step_id)
    if approved is None:
        raise ChartKnowledgeError("unsupported chart knowledge step")
    if not isinstance(requested_source_ids, list) or tuple(requested_source_ids) != approved:
        raise ChartKnowledgeError("chart knowledge scope mismatch")

    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    if catalog.get("acceptance", {}).get("status") != "domain-approved":
        raise ChartKnowledgeError("chart knowledge catalog is not approved")
    records = {
        record["source_id"]: record
        for record in catalog.get("documents", [])
        if record.get("collection") in {"financial-knowledge", "chart-guides"}
    }

    guide = ""
    candidates: list[tuple[int, str]] = []
    terms = set(re.findall(r"[가-힣A-Za-z]{2,}", question))
    for source_id in approved:
        record = records.get(source_id)
        if record is None:
            raise ChartKnowledgeError(f"approved source is missing: {source_id}")
        path = CATALOG_PATH.parents[3] / record["path"]
        raw = path.read_bytes()
        if sha256(raw).hexdigest() != record.get("sha256"):
            raise ChartKnowledgeError(f"approved source hash mismatch: {source_id}")
        text = raw.decode("utf-8")
        end = _SECTION_END.search(text)
        if end:
            text = text[:end.start()]
        text = _NUMBER.sub("[검증 수치 참조]", text)
        lines = [
            line
            for line in text.splitlines()
            if not line.startswith(("source_id:", "기준일:", "적용 범위:"))
            and "http://" not in line
            and "https://" not in line
        ]
        cleaned = "\n".join(lines).strip()
        if source_id == PRESENTATION_GUIDE_SOURCE:
            for line in lines:
                columns = [column.strip() for column in line.strip("|").split("|")]
                if len(columns) == 4 and columns[0] == _CHART_TITLES[step_id]:
                    guide = "\n".join((
                        f"보고서: {columns[0]}", f"의미: {columns[1]}",
                        f"읽는 순서: {columns[2]}", f"주의사항: {columns[3]}",
                    ))
        if not question:
            continue
        for section in re.split(r"(?m)(?=^## )", cleaned):
            if not section.startswith("## ") or "| 보고서 |" in section:
                continue
            heading = section.splitlines()[0]
            score = sum(3 if term in heading else 1 for term in terms if term in section)
            if score:
                candidates.append((score, section[:_MAX_SECTION_CHARACTERS].strip()))
    if not guide:
        raise ChartKnowledgeError("current report guide is missing")
    selected = [section for _score, section in sorted(candidates, key=lambda item: -item[0])[:2]]
    return "\n\n".join([guide, *selected])[:_MAX_CONTEXT_CHARACTERS]
