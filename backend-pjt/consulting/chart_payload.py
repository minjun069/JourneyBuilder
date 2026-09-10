"""Build the verified read-only chart input sent from Django to AI."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone
from math import isfinite
from typing import Any

from reports.models import Report
from reports.policy import active_policy_version
from reports.snapshots import calculation_input_digest


CHART_STEP_ID = "lifetime-cashflow"
CHART_TITLE = "생애현금흐름 및 생애순자산"
SERIES_FIELDS = (
    "age",
    "cash_inflow",
    "cash_outflow",
    "net_flow",
    "liquid_assets",
    "pension_assets",
    "real_estate_assets",
    "liabilities",
    "total_net_worth",
)
RATE_FIELDS = (
    "inflation_rate",
    "investment_rate",
    "wage_growth_rate",
    "real_estate_growth_rate",
)
UNITS = {
    "age": "age",
    "cash_inflow": "KRW/year",
    "cash_outflow": "KRW/year",
    "net_flow": "KRW/year",
    "liquid_assets": "KRW",
    "pension_assets": "KRW",
    "real_estate_assets": "KRW",
    "liabilities": "KRW",
    "total_net_worth": "KRW",
    "inflation_rate": "decimal",
    "investment_rate": "decimal",
    "wage_growth_rate": "decimal",
    "real_estate_growth_rate": "decimal",
}
SERVICE_ASSUMPTION_SOURCE = {"label": "서비스 기본 가정", "url": None}


class ChartPayloadOwnershipError(PermissionError):
    pass


class ChartPayloadValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ChartPayloadBuildResult:
    payload: dict[str, Any] | None
    reason: str | None = None


def build_chart_payload(*, session, user, step_id: str) -> ChartPayloadBuildResult:
    """Return only a fully validated chart payload or a sanitized null reason."""

    if step_id != CHART_STEP_ID:
        return ChartPayloadBuildResult(None, "not_applicable")
    if session.user_id != user.id:
        raise ChartPayloadOwnershipError("chart payload session owner mismatch")

    report = (
        session.reports.filter(
            report_type=Report.ReportType.LIFETIME_CASHFLOW,
            is_latest=True,
        )
        .order_by("-version")
        .first()
    )
    if report is None:
        return ChartPayloadBuildResult(None, "report_not_found")
    if report.status == Report.Status.PROCESSING:
        return ChartPayloadBuildResult(None, "report_processing")
    if report.status == Report.Status.FAILED:
        return ChartPayloadBuildResult(None, "report_failed")
    if report.status != Report.Status.READY:
        return ChartPayloadBuildResult(None, "report_schema_invalid")

    try:
        payload = _validated_payload(
            report,
            step_id=step_id,
            current_digest=calculation_input_digest(session),
        )
    except ChartPayloadValidationError as exc:
        return ChartPayloadBuildResult(None, str(exc))
    return ChartPayloadBuildResult(payload)


def _validated_payload(report, *, step_id: str, current_digest: str) -> dict[str, Any]:
    if report.report_type != step_id or not report.is_latest:
        raise ChartPayloadValidationError("report_identity_invalid")
    if not _positive_int(report.id) or not _positive_int(report.version):
        raise ChartPayloadValidationError("report_identity_invalid")
    if report.generated_at is None or report.generated_at.tzinfo is None:
        raise ChartPayloadValidationError("report_generated_at_invalid")

    content = report.content
    if not isinstance(content, dict):
        raise ChartPayloadValidationError("report_schema_invalid")
    required = {
        "assumptions",
        "depletion_point",
        "final_status",
        "chart_data",
        "input_snapshot_digest",
    }
    if not required.issubset(content):
        raise ChartPayloadValidationError("report_schema_invalid")
    if content["input_snapshot_digest"] != current_digest:
        raise ChartPayloadValidationError("report_stale")

    assumptions = _validate_assumptions(content["assumptions"])
    rows = _validate_series(content["chart_data"])
    summary = _validate_summary(
        content["depletion_point"],
        content["final_status"],
        rows,
    )
    return {
        "chart_id": CHART_STEP_ID,
        "title": CHART_TITLE,
        "status": "ready",
        "read_only": True,
        "generated_at": _isoformat(report.generated_at),
        "source_report": {
            "report_id": report.id,
            "report_type": report.report_type,
            "version": report.version,
        },
        "period": {
            "dimension": "age",
            "from": rows[0]["age"],
            "to": rows[-1]["age"],
            "interval": 1,
            "unit": "year",
        },
        "units": dict(UNITS),
        "assumptions": assumptions,
        "summary": summary,
        "series": rows,
    }


def _validate_assumptions(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ChartPayloadValidationError("report_schema_invalid")
    if raw.get("calculation_policy_version") != active_policy_version():
        raise ChartPayloadValidationError("policy_version_mismatch")

    result: dict[str, Any] = {}
    for field in RATE_FIELDS:
        value = raw.get(field)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(float(value))
            or not 0 <= float(value) <= 1
        ):
            raise ChartPayloadValidationError("report_schema_invalid")
        result[field] = value

    raw_sources = raw.get("sources")
    if not isinstance(raw_sources, dict):
        raise ChartPayloadValidationError("report_schema_invalid")
    sources = {}
    for field in RATE_FIELDS:
        source = raw_sources.get(field, SERVICE_ASSUMPTION_SOURCE)
        if not isinstance(source, dict) or set(source) != {"label", "url"}:
            raise ChartPayloadValidationError("report_schema_invalid")
        if not isinstance(source.get("label"), str) or not source["label"].strip():
            raise ChartPayloadValidationError("report_schema_invalid")
        if source.get("url") is not None and not isinstance(source["url"], str):
            raise ChartPayloadValidationError("report_schema_invalid")
        sources[field] = {"label": source["label"], "url": source["url"]}
    result["sources"] = sources
    return result


def _validate_series(raw: Any) -> list[dict[str, int]]:
    if not isinstance(raw, list) or not raw:
        raise ChartPayloadValidationError("report_schema_invalid")
    rows: list[dict[str, int]] = []
    previous_age: int | None = None
    for raw_row in raw:
        if not isinstance(raw_row, dict) or set(raw_row) != set(SERIES_FIELDS):
            raise ChartPayloadValidationError("report_schema_invalid")
        row: dict[str, int] = {}
        for field in SERIES_FIELDS:
            value = raw_row.get(field)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ChartPayloadValidationError("report_schema_invalid")
            row[field] = value
        if row["age"] < 0 or row["age"] > 120:
            raise ChartPayloadValidationError("period_invalid")
        if previous_age is not None and row["age"] != previous_age + 1:
            raise ChartPayloadValidationError("period_invalid")
        previous_age = row["age"]
        if row["cash_outflow"] > 0:
            raise ChartPayloadValidationError("series_formula_invalid")
        if row["net_flow"] != row["cash_inflow"] + row["cash_outflow"]:
            raise ChartPayloadValidationError("series_formula_invalid")
        expected_net_worth = (
            row["liquid_assets"]
            + row["pension_assets"]
            + row["real_estate_assets"]
            - row["liabilities"]
        )
        if row["total_net_worth"] != expected_net_worth:
            raise ChartPayloadValidationError("series_formula_invalid")
        rows.append(row)
    return rows


def _validate_summary(
    raw_depletion: Any,
    raw_final: Any,
    rows: list[dict[str, int]],
) -> dict[str, Any]:
    if not isinstance(raw_depletion, dict) or set(raw_depletion) != {
        "is_depleted",
        "depletion_age",
    }:
        raise ChartPayloadValidationError("report_schema_invalid")
    if not isinstance(raw_depletion.get("is_depleted"), bool):
        raise ChartPayloadValidationError("report_schema_invalid")
    first_depletion = next(
        (row["age"] for row in rows if row["liquid_assets"] < 0),
        None,
    )
    if (
        raw_depletion["is_depleted"] != (first_depletion is not None)
        or raw_depletion.get("depletion_age") != first_depletion
    ):
        raise ChartPayloadValidationError("summary_invalid")

    if not isinstance(raw_final, dict) or set(raw_final) != {
        "end_age",
        "final_deficit",
        "final_net_worth",
    }:
        raise ChartPayloadValidationError("report_schema_invalid")
    last = rows[-1]
    expected_final = {
        "end_age": last["age"],
        "final_deficit": max(0, -last["liquid_assets"]),
        "final_net_worth": last["total_net_worth"],
    }
    if any(raw_final.get(key) != value for key, value in expected_final.items()):
        raise ChartPayloadValidationError("summary_invalid")
    return {
        "depletion_point": {
            "is_depleted": raw_depletion["is_depleted"],
            "depletion_age": raw_depletion["depletion_age"],
        },
        "final_status": expected_final,
    }


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _isoformat(value) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
