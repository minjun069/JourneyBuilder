"""Safe injection of approved RAG documents for Presentation explanations."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any


DATA_ROOT = Path(__file__).resolve().parent / "data"
CATALOG_PATH = DATA_ROOT / "catalog.json"

CHART_KNOWLEDGE_SOURCES: dict[str, tuple[str, ...]] = {
    "balance-sheet": ("retirewise-household-balance-cash-flow",),
    "cash-flow": ("retirewise-household-balance-cash-flow",),
    "financial-indicators": (
        "retirewise-household-financial-indicator-policy",
        "retirewise-household-balance-cash-flow",
    ),
    "financial-goal-result": (
        "retirewise-financial-goals-time-value",
        "retirewise-planning-assumptions-2026",
    ),
    "retirement-fund": (
        "retirewise-retirement-planning-report-interpretation",
        "retirewise-planning-assumptions-2026",
    ),
    "retirement-readiness": (
        "retirewise-retirement-planning-report-interpretation",
        "retirewise-planning-assumptions-2026",
    ),
    "retirement-simulation": (
        "retirewise-retirement-planning-report-interpretation",
        "retirewise-planning-assumptions-2026",
    ),
    "pension-schedule": (
        "retirewise-retirement-planning-report-interpretation",
        "retirewise-planning-assumptions-2026",
    ),
    "lifetime-cashflow": (
        "retirewise-retirement-planning-report-interpretation",
        "retirewise-planning-assumptions-2026",
        "retirewise-household-balance-cash-flow",
    ),
    "required-savings": (
        "retirewise-retirement-planning-report-interpretation",
        "retirewise-financial-goals-time-value",
        "retirewise-planning-assumptions-2026",
    ),
}

_NUMBER = re.compile(r"(?<![A-Za-z가-힣])[-+]?\d[\d,.]*(?:%|원|세|년|개월|배)?")
_SECTION_END = re.compile(r"^## (?:공식 출처|갱신|출처)", re.MULTILINE)
_MAX_DOCUMENT_CHARACTERS = 4_000
_MAX_CONTEXT_CHARACTERS = 9_000


class ChartKnowledgeError(ValueError):
    pass


def load_chart_knowledge(step_id: str, requested_source_ids: Any) -> str:
    """Load approved, hash-matched sources and remove every numeric literal."""
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
        if record.get("collection") == "financial-knowledge"
    }

    blocks = []
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
        blocks.append("\n".join(lines).strip()[:_MAX_DOCUMENT_CHARACTERS])
    return "\n\n--- 승인 문서 구분 ---\n\n".join(blocks)[:_MAX_CONTEXT_CHARACTERS]
