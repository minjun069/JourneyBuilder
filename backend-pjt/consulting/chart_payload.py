"""Build Backend-verified, explanation-friendly Presentation payloads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from math import isfinite
from typing import Any

from reports.models import Report
from reports.policy import active_policy_version, load_financial_policy
from reports.snapshots import calculation_input_digest

from .chart_payload_adapters import PROJECTORS, ProjectionError
from .simulation_notices import simulation_notice_warning


CHART_SCHEMA_VERSION = "2.1"


@dataclass(frozen=True)
class ChartDefinition:
    step_id: str
    title: str
    report_type: str | None
    assumption_fields: tuple[str, ...]
    assumption_overrides: tuple[tuple[str, str], ...]
    knowledge_source_ids: tuple[str, ...]
    linked_steps: tuple[str, ...]


BALANCE_SOURCE = "retirewise-household-balance-cash-flow"
INDICATOR_SOURCE = "retirewise-household-financial-indicator-policy"
GOAL_SOURCE = "retirewise-financial-goals-time-value"
RETIREMENT_SOURCE = "retirewise-retirement-planning-report-interpretation"
ASSUMPTION_SOURCE = "retirewise-planning-assumptions-2026"
PRESENTATION_GUIDE_SOURCE = "retirewise-presentation-report-interpretation"


CHART_DEFINITIONS: dict[str, ChartDefinition] = {
    "balance-sheet": ChartDefinition("balance-sheet", "자산·부채 현황", Report.ReportType.ASSET_STATEMENT, (), (), (PRESENTATION_GUIDE_SOURCE, BALANCE_SOURCE), ("cash-flow", "retirement-readiness")),
    "cash-flow": ChartDefinition("cash-flow", "현금흐름", Report.ReportType.CASH_FLOW, (), (), (PRESENTATION_GUIDE_SOURCE, BALANCE_SOURCE), ("balance-sheet", "lifetime-cashflow", "required-savings")),
    "financial-indicators": ChartDefinition("financial-indicators", "가계재무지표", Report.ReportType.FINANCIAL_INDICATORS, (), (), (PRESENTATION_GUIDE_SOURCE, INDICATOR_SOURCE, BALANCE_SOURCE), ("balance-sheet", "cash-flow")),
    "financial-goal-result": ChartDefinition("financial-goal-result", "재무목표 결과", Report.ReportType.FINANCIAL_GOAL_RESULT, ("inflation_rate",), (), (PRESENTATION_GUIDE_SOURCE, GOAL_SOURCE, ASSUMPTION_SOURCE), ("retirement-fund", "required-savings")),
    "retirement-fund": ChartDefinition("retirement-fund", "은퇴 필요자금", Report.ReportType.RETIREMENT_FUND, ("inflation_rate",), (), (PRESENTATION_GUIDE_SOURCE, RETIREMENT_SOURCE, ASSUMPTION_SOURCE), ("financial-goal-result", "retirement-readiness", "required-savings")),
    "retirement-readiness": ChartDefinition(
        "retirement-readiness", "은퇴 준비도", Report.ReportType.RETIREMENT_READINESS,
        ("financial_return_rate", "pension_default_return_rate", "real_estate_appreciation_rate", "wage_growth_rate", "rent_growth_rate", "vacancy_rate"),
        (
            ("financial_return_rate", "aggressive_financial_return_rate"),
            ("pension_default_return_rate", "financial_return_rate"),
        ),
        (PRESENTATION_GUIDE_SOURCE, RETIREMENT_SOURCE, ASSUMPTION_SOURCE), ("retirement-fund", "retirement-simulation", "lifetime-cashflow"),
    ),
    "retirement-simulation": ChartDefinition("retirement-simulation", "은퇴 시뮬레이션", None, (), (), (PRESENTATION_GUIDE_SOURCE, RETIREMENT_SOURCE, ASSUMPTION_SOURCE), ("retirement-readiness", "lifetime-cashflow")),
    "pension-schedule": ChartDefinition("pension-schedule", "연금 수령 일정", Report.ReportType.PENSION_SCHEDULE, ("inflation_rate",), (), (PRESENTATION_GUIDE_SOURCE, RETIREMENT_SOURCE, ASSUMPTION_SOURCE), ("retirement-readiness", "lifetime-cashflow")),
    "lifetime-cashflow": ChartDefinition(
        "lifetime-cashflow", "생애 현금흐름", Report.ReportType.LIFETIME_CASHFLOW,
        ("inflation_rate", "investment_rate", "wage_growth_rate", "real_estate_growth_rate"),
        (("inflation_rate", "conservative_inflation_rate"), ("investment_rate", "financial_return_rate")),
        (PRESENTATION_GUIDE_SOURCE, RETIREMENT_SOURCE, ASSUMPTION_SOURCE, BALANCE_SOURCE),
        ("cash-flow", "retirement-readiness", "pension-schedule", "required-savings"),
    ),
    "required-savings": ChartDefinition(
        "required-savings", "필요저축액", Report.ReportType.REQUIRED_SAVINGS,
        ("expected_return_rate",), (("expected_return_rate", "financial_return_rate"),),
        (PRESENTATION_GUIDE_SOURCE, RETIREMENT_SOURCE, GOAL_SOURCE, ASSUMPTION_SOURCE), ("cash-flow", "retirement-fund", "lifetime-cashflow"),
    ),
}

REPORT_TYPE_BY_STEP = {step_id: item.report_type for step_id, item in CHART_DEFINITIONS.items() if item.report_type is not None}
PRESENTATION_CHART_STEP_IDS = tuple(CHART_DEFINITIONS)


class ChartPayloadOwnershipError(PermissionError):
    pass


class ChartPayloadValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ChartPayloadBuildResult:
    payload: dict[str, Any] | None
    reason: str | None = None


def build_chart_payload(*, session, user, step_id: str) -> ChartPayloadBuildResult:
    """Return a verified payload or a non-numeric guidance envelope."""
    definition = CHART_DEFINITIONS.get(step_id)
    if definition is None:
        return ChartPayloadBuildResult(None, "not_applicable")
    if session.user_id != user.id:
        raise ChartPayloadOwnershipError("chart payload session owner mismatch")

    current_digest = calculation_input_digest(session)
    if definition.report_type is None:
        try:
            return ChartPayloadBuildResult(_validated_simulation_payload(session=session, definition=definition, current_digest=current_digest))
        except (ChartPayloadValidationError, ProjectionError) as exc:
            reason = _reason_code(exc)
            return ChartPayloadBuildResult(_guidance_only_payload(definition, reason), reason)

    report = session.reports.filter(report_type=definition.report_type, is_latest=True).order_by("-version").first()
    reason = _report_availability_reason(report)
    if reason is not None:
        return ChartPayloadBuildResult(_guidance_only_payload(definition, reason), reason)
    try:
        payload = _validated_report_payload(report, definition=definition, current_digest=current_digest)
        payload["connections"] = _compatible_connections(session=session, definition=definition, current_digest=current_digest)
    except (ChartPayloadValidationError, ProjectionError) as exc:
        reason = _reason_code(exc)
        return ChartPayloadBuildResult(_guidance_only_payload(definition, reason), reason)
    return ChartPayloadBuildResult(payload)


def _report_availability_reason(report: Any) -> str | None:
    if report is None:
        return "report_not_found"
    if report.status == Report.Status.PROCESSING:
        return "report_processing"
    if report.status == Report.Status.FAILED:
        return "report_failed"
    if report.status != Report.Status.READY:
        return "report_schema_invalid"
    return None


def _guidance_only_payload(definition: ChartDefinition, reason: str) -> dict[str, Any]:
    return {
        "schema_version": CHART_SCHEMA_VERSION,
        "chart_id": definition.step_id,
        "title": definition.title,
        "status": "guidance_only",
        "read_only": True,
        "availability": {"reason": reason, "numeric_interpretation_allowed": False},
        "generated_at": None,
        "source_report": None,
        "report_version_key": f"guidance-only:{reason}",
        "provenance": {"fact_source": "none", "meaning_source": "approved_rag", "natural_language_source": "llm"},
        "period": None,
        "units": {},
        "assumptions": {},
        "assumption_details": [],
        "summary": {},
        "series": [],
        "highlights": [],
        "events": [],
        "warnings": [{"code": reason, "message": "검증된 수치 없이 보고서의 목적과 읽는 방법만 안내합니다."}],
        "details": {},
        "knowledge_scope": {"source_ids": list(definition.knowledge_source_ids)},
        "connections": [],
    }


def _validated_report_payload(report: Any, *, definition: ChartDefinition, current_digest: str) -> dict[str, Any]:
    if report.report_type != definition.report_type or not report.is_latest:
        raise ChartPayloadValidationError("report_identity_invalid")
    if not _positive_int(report.id) or not _positive_int(report.version):
        raise ChartPayloadValidationError("report_identity_invalid")
    if report.generated_at is None or report.generated_at.tzinfo is None:
        raise ChartPayloadValidationError("report_generated_at_invalid")
    content = report.content
    if not isinstance(content, dict):
        raise ChartPayloadValidationError("report_schema_invalid")
    if content.get("input_snapshot_digest") != current_digest:
        raise ChartPayloadValidationError("report_stale")
    if content.get("calculation_policy_version") != active_policy_version():
        raise ChartPayloadValidationError("policy_version_mismatch")

    assumptions = _validate_policy_assumptions(content.get("assumptions"), definition)
    projection = PROJECTORS[definition.step_id](content)
    return {
        "schema_version": CHART_SCHEMA_VERSION,
        "chart_id": definition.step_id,
        "title": definition.title,
        "status": "ready",
        "read_only": True,
        "availability": {"reason": "ready", "numeric_interpretation_allowed": True},
        "generated_at": _isoformat(report.generated_at),
        "source_report": {"report_id": report.id, "report_type": report.report_type, "version": report.version},
        "report_version_key": f"report:{report.id}:v{report.version}",
        "provenance": {
            "fact_source": "verified_payload",
            "meaning_source": "approved_rag",
            "natural_language_source": "llm",
            "input_snapshot_digest": current_digest,
            "calculation_policy_version": active_policy_version(),
        },
        "assumptions": assumptions,
        "assumption_details": _assumption_details(assumptions, definition.assumption_fields),
        "knowledge_scope": {"source_ids": list(definition.knowledge_source_ids)},
        "connections": [],
        **projection,
    }


def _validate_policy_assumptions(raw: Any, definition: ChartDefinition) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ChartPayloadValidationError("report_schema_invalid")
    policy = load_financial_policy()
    if raw.get("calculation_policy_version") != policy["policy_version"]:
        raise ChartPayloadValidationError("policy_version_mismatch")
    expected = dict(policy["assumptions"])
    for target, policy_key in definition.assumption_overrides:
        expected[target] = policy["assumptions"][policy_key]
    for field, value in expected.items():
        if not _same_number(raw.get(field), value):
            raise ChartPayloadValidationError("policy_value_mismatch")

    sources = raw.get("sources")
    if not isinstance(sources, dict):
        raise ChartPayloadValidationError("report_schema_invalid")
    selected: dict[str, Any] = {"calculation_policy_version": policy["policy_version"]}
    for field in definition.assumption_fields:
        value = raw.get(field)
        if not _valid_number(value):
            raise ChartPayloadValidationError("report_schema_invalid")
        selected[field] = value
    selected["sources"] = _validated_sources(sources, definition.assumption_fields)
    return selected


def _validated_sources(raw: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    sources = {}
    for field in fields:
        source = raw.get(field, {"label": "서비스 기본 가정", "url": None})
        if not isinstance(source, dict) or set(source) != {"label", "url"}:
            raise ChartPayloadValidationError("report_schema_invalid")
        if not isinstance(source.get("label"), str) or not source["label"].strip():
            raise ChartPayloadValidationError("report_schema_invalid")
        if source.get("url") is not None and not isinstance(source["url"], str):
            raise ChartPayloadValidationError("report_schema_invalid")
        sources[field] = {"label": source["label"], "url": source["url"]}
    return sources


def _assumption_details(
    assumptions: dict[str, Any],
    fields: tuple[str, ...],
) -> list[dict[str, Any]]:
    """Pre-format policy values so the Agent never converts decimal rates."""
    labels = {
        "inflation_rate": "물가상승률",
        "investment_rate": "투자수익률",
        "wage_growth_rate": "임금상승률",
        "real_estate_growth_rate": "부동산 상승률",
        "financial_return_rate": "금융자산 수익률",
        "pension_default_return_rate": "연금자산 기본 수익률",
        "real_estate_appreciation_rate": "부동산 상승률",
        "rent_growth_rate": "임대료 상승률",
        "vacancy_rate": "공실률",
        "expected_return_rate": "기대수익률",
    }
    sources = assumptions.get("sources", {})
    return [
        {
            "id": field,
            "label": labels.get(field, field),
            "value": assumptions[field],
            "unit": "fraction",
            "display": f"{assumptions[field] * 100:g}%",
            "source": sources[field],
            "provenance": {
                "source": "verified_payload",
                "path": f"assumptions.{field}",
            },
        }
        for field in fields
    ]


def _compatible_connections(*, session: Any, definition: ChartDefinition, current_digest: str) -> list[dict[str, Any]]:
    connections = []
    for linked_step in definition.linked_steps:
        linked_definition = CHART_DEFINITIONS[linked_step]
        if linked_definition.report_type is None:
            continue
        report = session.reports.filter(report_type=linked_definition.report_type, is_latest=True).order_by("-version").first()
        if _report_availability_reason(report) is not None:
            continue
        try:
            linked = _validated_report_payload(report, definition=linked_definition, current_digest=current_digest)
        except (ChartPayloadValidationError, ProjectionError):
            continue
        connections.append({
            "step_id": linked_step,
            "title": linked_definition.title,
            "source_report": linked["source_report"],
            "report_version_key": linked["report_version_key"],
            "highlights": linked["highlights"],
            "events": linked["events"],
            "compatibility": {"latest": True, "status": "ready", "input_snapshot_digest": current_digest, "calculation_policy_version": active_policy_version()},
        })
    return connections


def _validated_simulation_payload(*, session: Any, definition: ChartDefinition, current_digest: str) -> dict[str, Any]:
    try:
        from simulations.services import confirmed_empty_sections, load_consistent_snapshot, load_policy, package_version, prepare_input, run_analysis
        assumptions, policy_version = load_policy()
        snapshot, snapshot_digest = load_consistent_snapshot(session)
        if snapshot_digest != current_digest:
            raise ChartPayloadValidationError("report_stale")
        # "금융자산 없음" 확인을 넘기지 않으면 해당 세션은 항상 INPUT_NOT_READY 가 된다.
        prepared = prepare_input(snapshot, confirmed_empty_sections=confirmed_empty_sections(session, snapshot))
        analysis, _elapsed_ms = run_analysis(prepared, assumptions, scenarios=())
    except ChartPayloadValidationError:
        raise
    except Exception as exc:
        raise ChartPayloadValidationError("simulation_unavailable") from exc

    if analysis.get("contract_version") != "retirement-analysis-v1" or analysis.get("mapping_version") != "retirement-input-v1" or analysis.get("mode") != "baseline":
        raise ChartPayloadValidationError("simulation_schema_invalid")
    units, profile, baseline, metadata = (analysis.get("units"), analysis.get("baseline_profile"), analysis.get("baseline"), analysis.get("metadata"))
    if not all(isinstance(item, dict) for item in (units, profile, baseline, metadata)):
        raise ChartPayloadValidationError("simulation_schema_invalid")
    if units.get("amount") != "KRW" or units.get("probability") != "fraction":
        raise ChartPayloadValidationError("simulation_unit_invalid")
    if metadata.get("assumptions") != assumptions:
        raise ChartPayloadValidationError("policy_value_mismatch")

    current_age = _strict_int(profile.get("current_age"), "baseline_profile.current_age")
    end_age = _strict_int(profile.get("end_age"), "baseline_profile.end_age")
    success = _strict_number(baseline.get("success_probability"), "baseline.success_probability")
    balances = _simulation_percentiles(baseline.get("final_balance_percentiles"), "baseline.final_balance_percentiles")
    shortfalls = _simulation_percentiles(baseline.get("final_shortfall_percentiles"), "baseline.final_shortfall_percentiles")
    series = []
    prior_age = None
    for index, raw in enumerate(baseline.get("asset_percentiles", [])):
        if not isinstance(raw, dict):
            raise ChartPayloadValidationError("simulation_schema_invalid")
        age = _strict_int(raw.get("age"), f"baseline.asset_percentiles[{index}].age")
        if prior_age is not None and age != prior_age + 1:
            raise ChartPayloadValidationError("simulation_period_invalid")
        prior_age = age
        series.append({"age": age, "balance": _simulation_percentiles(raw.get("balance"), f"baseline.asset_percentiles[{index}].balance")})
    if not series or series[0]["age"] != current_age or series[-1]["age"] != end_age:
        raise ChartPayloadValidationError("simulation_period_invalid")

    first_depletion = next((_strict_int(row.get("age"), "baseline.first_depletion_counts.age") for row in baseline.get("first_depletion_counts", []) if isinstance(row, dict) and _strict_int(row.get("count"), "baseline.first_depletion_counts.count") > 0), None)
    events = []
    if first_depletion is not None:
        events.append({
            "id": "simulated-depletion-risk", "type": "asset_depletion_risk", "label": "일부 경로의 첫 자산 고갈", "age": first_depletion,
            "facts": [_simulation_fact("depletion-age", "첫 고갈 관측 연령", first_depletion, "age", "baseline.first_depletion_counts")],
            "reason_code": "verified_monte_carlo_count", "provenance": {"source": "verified_payload", "path": "baseline.first_depletion_counts"},
        })
    warnings = [simulation_notice_warning(item) for item in analysis.get("notices", []) if isinstance(item, dict)]
    warnings.append({"code": "simulation_not_guarantee", "message": "확률 시뮬레이션 결과는 미래를 보장하지 않습니다."})
    package = package_version()
    material = json.dumps({"digest": current_digest, "policy": policy_version, "package": package}, sort_keys=True, separators=(",", ":")).encode("utf-8")
    version_key = f"simulation:{sha256(material).hexdigest()}"
    return {
        "schema_version": CHART_SCHEMA_VERSION, "chart_id": definition.step_id, "title": definition.title,
        "status": "ready", "read_only": True, "availability": {"reason": "ready", "numeric_interpretation_allowed": True},
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_report": {"report_id": None, "report_type": "retirement-simulation", "version": version_key},
        "report_version_key": version_key,
        "provenance": {
            "fact_source": "verified_payload", "meaning_source": "approved_rag", "natural_language_source": "llm",
            "input_snapshot_digest": current_digest, "simulation_policy_version": policy_version,
            "simulation_package_version": package, "contract_version": analysis["contract_version"], "mapping_version": analysis["mapping_version"],
        },
        "period": {"dimension": "age", "from": current_age, "to": end_age, "interval": 1, "unit": "year"},
        "units": units, "assumptions": {"simulation_policy_version": policy_version, **assumptions},
        "assumption_details": _simulation_assumption_details(assumptions),
        "summary": {"success_probability": success, "final_balance_percentiles": balances, "final_shortfall_percentiles": shortfalls},
        "series": series,
        "highlights": [
            _simulation_fact("success-probability", "계획 성공확률", success, "fraction", "baseline.success_probability"),
            _simulation_fact("final-balance-p50", "마지막 연령 자산 중앙값", balances["p50"], "KRW", "baseline.final_balance_percentiles.p50"),
            _simulation_fact("final-shortfall-p50", "마지막 연령 부족액 중앙값", shortfalls["p50"], "KRW", "baseline.final_shortfall_percentiles.p50"),
        ],
        "events": events, "warnings": warnings,
        "details": {"retirement_expenses": profile.get("retirement_expenses", []), "pension_incomes": profile.get("pension_incomes", []), "num_paths": baseline.get("num_paths")},
        "knowledge_scope": {"source_ids": list(definition.knowledge_source_ids)},
        "connections": _compatible_connections(session=session, definition=definition, current_digest=current_digest),
    }


def _simulation_percentiles(value: Any, path: str) -> dict[str, int | float]:
    if not isinstance(value, dict) or set(value) != {"p10", "p50", "p90"}:
        raise ChartPayloadValidationError("simulation_schema_invalid")
    return {key: _strict_number(value[key], f"{path}.{key}") for key in ("p10", "p50", "p90")}


def _simulation_assumption_details(assumptions: dict[str, Any]) -> list[dict[str, Any]]:
    labels = {
        "annual_return_mean": "연평균 수익률",
        "annual_return_volatility": "연 수익률 변동성",
        "annual_inflation_rate": "연 물가상승률",
        "num_paths": "시뮬레이션 경로 수",
        "seed": "난수 시드",
    }
    details = []
    for field in ("annual_return_mean", "annual_return_volatility", "annual_inflation_rate", "num_paths", "seed"):
        value = assumptions.get(field)
        if not _valid_number(value):
            raise ChartPayloadValidationError("simulation_schema_invalid")
        is_rate = field.startswith("annual_")
        details.append({
            "id": field,
            "label": labels[field],
            "value": value,
            "unit": "fraction" if is_rate else "count",
            "display": f"{value * 100:g}%" if is_rate else f"{int(value):,}",
            "provenance": {
                "source": "verified_payload",
                "path": f"metadata.assumptions.{field}",
            },
        })
    return details


def _simulation_fact(fact_id: str, label: str, value: int | float, unit: str, path: str) -> dict[str, Any]:
    display = f"{value * 100:.1f}%" if unit == "fraction" else f"{int(value)}세" if unit == "age" else f"{int(value):,}원"
    return {"id": fact_id, "label": label, "value": value, "unit": unit, "display": display, "provenance": {"source": "verified_payload", "path": path}}


def _strict_number(value: Any, path: str) -> int | float:
    if not _valid_number(value):
        raise ChartPayloadValidationError(f"simulation_schema_invalid:{path}")
    return value


def _strict_int(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ChartPayloadValidationError(f"simulation_schema_invalid:{path}")
    return value


def _reason_code(exc: Exception) -> str:
    value = str(exc)
    return value.split(":", 1)[0] if value else "report_schema_invalid"


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _valid_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and isfinite(float(value))


def _same_number(actual: Any, expected: Any) -> bool:
    return _valid_number(actual) and _valid_number(expected) and float(actual) == float(expected)


def _isoformat(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
