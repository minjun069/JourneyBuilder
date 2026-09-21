"""Deterministic quality gate for Presentation Chart Agent responses."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
from typing import Any, Mapping


AI_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = AI_ROOT.parent
DEFAULT_FIXTURE = (
    REPOSITORY_ROOT
    / "contract-fixtures"
    / "ai-lifecycle"
    / "v2"
    / "chart-evaluations.json"
)

_NUMBER_WITH_COMMAS = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_UNIT_SUFFIXES = {
    "KRW": ("원",),
    "KRW/month": ("원/월",),
    "KRW/year": ("원/년",),
    "percent": ("%",),
    "fraction": ("%",),
    "age": ("세",),
    "month": ("개월",),
    "year": ("년",),
    "count": ("개",),
    "ratio": ("배",),
}


def load_chart_evaluation_fixture(path: Path = DEFAULT_FIXTURE) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("fixture_kind") != "presentation-chart-evaluations":
        raise ValueError("unsupported chart evaluation fixture")
    if payload.get("schema_version") != "1.0":
        raise ValueError("unsupported chart evaluation schema")
    if payload.get("quality_disposition") != "quality_gate":
        raise ValueError("chart evaluation must be a quality gate")
    if len(payload.get("normal_cases", [])) != 10:
        raise ValueError("all ten Presentation stages require a normal case")
    return payload


def evaluation_cases(fixture: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        *deepcopy(fixture.get("normal_cases", [])),
        *deepcopy(fixture.get("edge_cases", [])),
    ]


def evaluation_case_index(fixture: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {case["id"]: case for case in evaluation_cases(fixture)}


def build_evaluation_payload(case: Mapping[str, Any]) -> dict[str, Any]:
    from rag.chart_knowledge import CHART_KNOWLEDGE_SOURCES

    step_id = str(case["step_id"])
    status = case.get("status", "ready")
    title = str(case["title"])
    source_ids = list(CHART_KNOWLEDGE_SOURCES[step_id])
    if status == "guidance_only":
        reason = str(case["reason"])
        return {
            "schema_version": "2.1",
            "chart_id": step_id,
            "title": title,
            "status": status,
            "read_only": True,
            "availability": {
                "reason": reason,
                "numeric_interpretation_allowed": False,
            },
            "generated_at": None,
            "source_report": None,
            "report_version_key": f"guidance-only:{reason}",
            "provenance": {
                "fact_source": "none",
                "meaning_source": "approved_rag",
                "natural_language_source": "llm",
            },
            "period": None,
            "units": {},
            "assumptions": {},
            "assumption_details": [],
            "summary": {},
            "series": [],
            "highlights": [],
            "events": [],
            "warnings": [{
                "code": reason,
                "message": "검증된 수치 없이 보고서의 목적과 읽는 방법만 안내합니다.",
            }],
            "details": {},
            "knowledge_scope": {"source_ids": source_ids},
            "connections": [],
        }

    facts = []
    units: dict[str, str] = {}
    for index, raw in enumerate(case.get("facts", [])):
        fact = {
            "id": f"evaluation-fact-{index + 1}",
            "label": raw["label"],
            "value": raw["value"],
            "unit": raw["unit"],
            "display": raw["display"],
            "provenance": {
                "source": "verified_payload",
                "path": f"evaluation.facts[{index}]",
            },
        }
        facts.append(fact)
        units[f"fact_{index + 1}"] = str(raw["unit"])
    events = []
    for index, raw in enumerate(case.get("events", [])):
        events.append({
            "id": f"evaluation-event-{index + 1}",
            "type": raw["type"],
            "label": raw["label"],
            "age": raw.get("age"),
            "facts": [],
            "reason_code": raw["reason_code"],
            "provenance": {
                "source": "verified_payload",
                "path": f"evaluation.events[{index}]",
            },
        })
    return {
        "schema_version": "2.1",
        "chart_id": step_id,
        "title": title,
        "status": "ready",
        "read_only": True,
        "availability": {
            "reason": "ready",
            "numeric_interpretation_allowed": True,
        },
        "generated_at": "2026-09-17T00:00:00Z",
        "source_report": {
            "report_id": 9001,
            "report_type": step_id,
            "version": 1,
        },
        "report_version_key": f"evaluation:{step_id}:v1",
        "provenance": {
            "fact_source": "verified_payload",
            "meaning_source": "approved_rag",
            "natural_language_source": "llm",
            "input_snapshot_digest": "evaluation-snapshot",
            "calculation_policy_version": "2026-09-12.v1",
        },
        "period": deepcopy(case.get("period", {"dimension": "snapshot", "unit": "current"})),
        "units": units,
        "assumptions": {},
        "assumption_details": [],
        "summary": {},
        "series": [],
        "highlights": facts[:3],
        "events": events,
        "warnings": deepcopy(case.get("warnings", [])),
        "details": deepcopy(case.get("details", {})),
        "knowledge_scope": {"source_ids": source_ids},
        "connections": deepcopy(case.get("connections", [])),
    }


def _normalized_claim_number(value: str | int | float) -> str:
    number = float(str(value).replace(",", ""))
    return str(int(number)) if number.is_integer() else format(number, ".12g")


def _authorized_number_units(payload: Mapping[str, Any]) -> dict[str, set[str]]:
    """Return only Backend-presented values and their required display units.

    Raw IDs, timestamps and schema versions are intentionally excluded. A number is
    an explainable chart fact only when the payload pairs it with a unit/display, or
    declares it as a period/event age.
    """
    authorized: dict[str, set[str]] = {}

    def add(value: Any, suffixes: tuple[str, ...]) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            return
        try:
            normalized = _normalized_claim_number(value)
        except ValueError:
            return
        authorized.setdefault(normalized, set()).update(suffixes)

    def collect(value: Any) -> None:
        if isinstance(value, Mapping):
            unit = value.get("unit")
            if "value" in value and unit in _UNIT_SUFFIXES:
                add(value["value"], _UNIT_SUFFIXES[str(unit)])
                if unit == "fraction" and isinstance(value["value"], (int, float)):
                    add(float(value["value"]) * 100, ("%",))
            if "age" in value and isinstance(value.get("age"), (int, float)):
                add(value["age"], ("세",))
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    for key in (
        "summary", "series", "highlights", "events", "warnings", "details",
        "assumption_details", "connections",
    ):
        collect(payload.get(key))

    period = payload.get("period")
    if isinstance(period, Mapping):
        dimension = period.get("dimension")
        boundary_suffix = "세" if dimension == "age" else _UNIT_SUFFIXES.get(
            str(period.get("unit")), ("년",)
        )[0]
        for key in ("from", "to"):
            if key in period:
                add(period[key], (boundary_suffix,))
        if "interval" in period:
            add(period["interval"], ("년",))
    return authorized


def _numeric_unit_period_mismatches(
    response: str,
    payload: Mapping[str, Any],
) -> list[str]:
    without_list_markers = re.sub(r"(?m)^\s*\d+[.)]\s+", "", response)
    authorized = _authorized_number_units(payload)
    mismatches: list[str] = []
    for match in _NUMBER_WITH_COMMAS.finditer(without_list_markers):
        normalized = _normalized_claim_number(match.group(0))
        suffixes = authorized.get(normalized, set())
        following = without_list_markers[match.end():match.end() + 12].lstrip()
        if not suffixes or not any(following.startswith(suffix) for suffix in suffixes):
            mismatches.append(match.group(0))
    return mismatches


def evaluate_chart_response(
    *,
    case: Mapping[str, Any],
    payload: Mapping[str, Any],
    response: str,
    action: Any,
) -> dict[str, Any]:
    from agents.chart_interpret import (
        recalculation_claims,
        unauthorized_numeric_claims,
        unsupported_causal_claims,
    )
    from rag.chart_knowledge import CHART_KNOWLEDGE_SOURCES

    unsupported_numbers = sorted(unauthorized_numeric_claims(response, payload))
    unit_period_mismatches = _numeric_unit_period_mismatches(response, payload)
    recalculations = recalculation_claims(response)
    unsupported_causes = unsupported_causal_claims(response, payload)
    sensitive_mentions = [
        value
        for value in case.get("forbidden_response_values", [])
        if value in response
    ]
    response_numbers = re.findall(r"-?\d+(?:\.\d+)?", response.replace(",", ""))
    numeric_exact = not unsupported_numbers and not unit_period_mismatches
    if payload.get("status") == "guidance_only":
        numeric_exact = numeric_exact and not response_numbers

    entry_progressive = True
    if case.get("request_mode") == "presentation_entry":
        bullet_count = sum(
            line.lstrip().startswith(("- ", "* "))
            for line in response.splitlines()
        )
        entry_progressive = len(response) <= 1_200 and bullet_count <= 3

    expected_scope = list(CHART_KNOWLEDGE_SOURCES[str(case["step_id"])])
    rag_scope_exact = payload.get("knowledge_scope", {}).get("source_ids") == expected_scope
    passed = all((
        action is None,
        bool(response.strip()),
        numeric_exact,
        not recalculations,
        not unsupported_causes,
        not sensitive_mentions,
        entry_progressive,
        rag_scope_exact,
    ))
    return {
        "passed": passed,
        "action_absent": action is None,
        "response_nonempty": bool(response.strip()),
        "numeric_unit_period_exact_match": numeric_exact,
        "numeric_unit_period_mismatches": unit_period_mismatches,
        "unsupported_numeric_claims": unsupported_numbers,
        "recalculation_claims": recalculations,
        "unsupported_causal_claims": unsupported_causes,
        "sensitive_value_mentions": sensitive_mentions,
        "entry_progressive": entry_progressive,
        "rag_scope_exact_match": rag_scope_exact,
    }


def summarize_chart_quality(
    results: list[Mapping[str, Any]],
    fixture: Mapping[str, Any],
) -> dict[str, Any]:
    thresholds = fixture["acceptance_thresholds"]
    completed = [row for row in results if row.get("status") == "completed"]
    normal_ids = {case["id"] for case in fixture["normal_cases"]}
    normal = [row for row in completed if row.get("id") in normal_ids]
    observations = [row["observations"] for row in completed]

    def rate(rows: list[Mapping[str, Any]], field: str) -> float:
        return (
            sum(bool(row["observations"].get(field)) for row in rows) / len(rows)
            if rows else 0.0
        )

    metrics = {
        "normal_case_pass_rate": rate(normal, "passed"),
        "all_case_pass_rate": rate(completed, "passed") if len(completed) == len(results) else 0.0,
        "numeric_unit_period_exact_match_rate": rate(completed, "numeric_unit_period_exact_match"),
        "rag_scope_exact_match_rate": rate(completed, "rag_scope_exact_match"),
        "unsupported_numeric_claim_count": sum(len(row["unsupported_numeric_claims"]) for row in observations),
        "recalculation_claim_count": sum(len(row["recalculation_claims"]) for row in observations),
        "unsupported_causal_claim_count": sum(len(row["unsupported_causal_claims"]) for row in observations),
        "sensitive_value_mention_count": sum(len(row["sensitive_value_mentions"]) for row in observations),
    }
    passed = all((
        metrics["normal_case_pass_rate"] >= thresholds["normal_case_pass_rate"],
        metrics["all_case_pass_rate"] >= thresholds["all_case_pass_rate"],
        metrics["numeric_unit_period_exact_match_rate"] >= thresholds["numeric_unit_period_exact_match_rate"],
        metrics["rag_scope_exact_match_rate"] >= thresholds["rag_scope_exact_match_rate"],
        metrics["unsupported_numeric_claim_count"] <= thresholds["maximum_unsupported_numeric_claims"],
        metrics["recalculation_claim_count"] <= thresholds["maximum_recalculation_claims"],
        metrics["unsupported_causal_claim_count"] <= thresholds["maximum_unsupported_causal_claims"],
        metrics["sensitive_value_mention_count"] <= thresholds["maximum_sensitive_value_mentions"],
    ))
    return {
        "quality_disposition": "quality_gate",
        "passed": passed,
        "acceptance_thresholds": deepcopy(thresholds),
        "metrics": metrics,
        "cases": results,
    }
