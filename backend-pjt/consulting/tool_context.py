"""Backend-owned provider for the minimal read-only AI tool snapshot."""

from __future__ import annotations

from datetime import timezone
from typing import Any

from reports.models import Report
from reports.policy import active_policy_version
from reports.snapshots import calculation_input_digest

from .field_registry import get_step_field_registry


TOOL_CONTEXT_SCHEMA_VERSION = "1.0"
METRIC_ALLOWLIST = {
    "retirement_gap": {
        "report_type": Report.ReportType.REQUIRED_SAVINGS,
        "content_path": "retirement_gap",
        "unit": "KRW",
    },
}


class ToolContextOwnershipError(PermissionError):
    pass


def build_tool_context(*, session, user, step_id: str, generated_at) -> dict[str, Any]:
    """Build one immutable request snapshot after authenticating the owner."""

    if session.user_id != user.id:
        raise ToolContextOwnershipError("tool context session owner mismatch")
    latest_reports = list(
        session.reports.filter(is_latest=True).order_by("report_type")
    )
    reports_by_type = {report.report_type: report for report in latest_reports}
    current_digest = calculation_input_digest(session)
    metrics = {
        metric_id: _build_dashboard_metric(
            metric_id,
            spec,
            reports_by_type.get(spec["report_type"]),
            current_digest=current_digest,
        )
        for metric_id, spec in METRIC_ALLOWLIST.items()
    }
    return {
        "schema_version": TOOL_CONTEXT_SCHEMA_VERSION,
        "session_id": session.id,
        "step_id": step_id,
        "generated_at": _isoformat(generated_at),
        "field_specs": get_step_field_registry(step_id),
        "dashboard_metrics": metrics,
        "retirement_scenarios": {},
        "available_reports": [
            {
                "report_id": report.id,
                "report_type": report.report_type,
                "version": report.version,
                "status": report.status,
                "generated_at": _isoformat(
                    report.generated_at or report.created_at
                ),
            }
            for report in latest_reports
        ],
        "section_completion": session.get_section_completion(),
    }


def _build_dashboard_metric(
    metric_id: str,
    spec: dict[str, Any],
    report,
    *,
    current_digest: str,
) -> dict[str, Any]:
    if report is None:
        return _unavailable("report_not_found")
    if report.status == Report.Status.PROCESSING:
        return _unavailable("report_processing")
    if report.status == Report.Status.FAILED:
        return _unavailable("report_failed")
    if report.status != Report.Status.READY:
        return _unavailable("report_schema_invalid")
    content = report.content
    if not isinstance(content, dict):
        return _unavailable("report_schema_invalid")
    if content.get("input_snapshot_digest") != current_digest:
        return _unavailable("report_stale")
    policy_version = content.get("calculation_policy_version")
    if policy_version != active_policy_version():
        return _unavailable("policy_version_mismatch")
    value = content.get(spec["content_path"])
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return _unavailable("report_schema_invalid")
    if report.generated_at is None:
        return _unavailable("report_schema_invalid")
    return {
        "availability": "ready",
        "metric_id": metric_id,
        "value": value,
        "unit": spec["unit"],
        "report_id": report.id,
        "report_version": report.version,
        "calculation_policy_version": policy_version,
        "generated_at": _isoformat(report.generated_at),
    }


def _unavailable(reason: str) -> dict[str, str]:
    return {"availability": "unavailable", "reason": reason}


def _isoformat(value) -> str:
    if value is None:
        return ""
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
