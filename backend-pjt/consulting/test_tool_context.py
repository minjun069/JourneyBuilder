from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from reports.models import Report
from reports.policy import active_policy_version

from .field_registry import get_step_field_registry
from .tool_context import ToolContextOwnershipError, build_tool_context


NOW = datetime(2026, 9, 9, 3, 0, tzinfo=timezone.utc)
REPORT_TIME = datetime(2026, 9, 9, 2, 30, tzinfo=timezone.utc)


class FakeReports(list):
    def filter(self, **criteria):
        return FakeReports([
            report for report in self
            if all(getattr(report, key) == value for key, value in criteria.items())
        ])

    def order_by(self, field):
        return FakeReports(sorted(self, key=lambda report: getattr(report, field)))


FIXTURE = json.loads((
    Path(__file__).resolve().parents[2]
    / "contract-fixtures/ai-lifecycle/v2/tool-contexts.json"
).read_text(encoding="utf-8"))


def report(
    *,
    status=Report.Status.READY,
    content=None,
    is_latest=True,
    version=3,
    report_id=None,
):
    return SimpleNamespace(
        id=report_id or 42 + version,
        report_type=Report.ReportType.REQUIRED_SAVINGS,
        version=version,
        is_latest=is_latest,
        status=status,
        content=content,
        generated_at=REPORT_TIME if status == Report.Status.READY else None,
        created_at=REPORT_TIME,
    )


def session(*reports):
    return SimpleNamespace(
        id=12,
        user_id=3,
        reports=FakeReports(reports),
        get_section_completion=lambda: {
            "basic_info": True,
            "financial_assets": False,
        },
    )


def ready_content(**overrides):
    value = {
        "retirement_gap": 375_000_000,
        "calculation_policy_version": active_policy_version(),
        "input_snapshot_digest": "trusted-digest",
    }
    value.update(overrides)
    return value


class ToolContextProviderTests(SimpleTestCase):
    @patch("consulting.tool_context.get_step_field_registry", return_value=[])
    @patch("consulting.tool_context.calculation_input_digest", return_value="trusted-digest")
    def test_provider_matches_shared_ready_fixture(self, _digest, _registry):
        snapshot = build_tool_context(
            session=session(report(content=ready_content(), report_id=42)),
            user=SimpleNamespace(id=3),
            step_id="basic-info",
            generated_at=NOW,
        )

        self.assertEqual(snapshot, FIXTURE["ready"])

    @patch("consulting.tool_context.calculation_input_digest", return_value="trusted-digest")
    def test_ready_metric_preserves_exact_report_provenance(self, _digest):
        latest = report(content=ready_content())
        snapshot = build_tool_context(
            session=session(latest),
            user=SimpleNamespace(id=3),
            step_id="basic-info",
            generated_at=NOW,
        )

        metric = snapshot["dashboard_metrics"]["retirement_gap"]
        self.assertEqual(metric, {
            "availability": "ready",
            "metric_id": "retirement_gap",
            "value": 375_000_000,
            "unit": "KRW",
            "report_id": latest.id,
            "report_version": 3,
            "calculation_policy_version": active_policy_version(),
            "generated_at": "2026-09-09T02:30:00Z",
        })
        self.assertEqual(
            snapshot["field_specs"],
            get_step_field_registry("basic-info"),
        )
        self.assertNotIn("user_id", snapshot)
        self.assertNotIn("content", repr(snapshot["available_reports"]))

    @patch("consulting.tool_context.calculation_input_digest", return_value="trusted-digest")
    def test_unavailable_reasons_fail_closed(self, _digest):
        cases = {
            "report_not_found": session(),
            "report_processing": session(report(status=Report.Status.PROCESSING)),
            "report_failed": session(report(status=Report.Status.FAILED)),
            "report_stale": session(report(content=ready_content(
                input_snapshot_digest="old-digest",
            ))),
            "policy_version_mismatch": session(report(content=ready_content(
                calculation_policy_version="old-policy",
            ))),
            "report_schema_invalid": session(report(content=ready_content(
                retirement_gap="estimated-value",
            ))),
        }

        for expected_reason, current_session in cases.items():
            with self.subTest(reason=expected_reason):
                snapshot = build_tool_context(
                    session=current_session,
                    user=SimpleNamespace(id=3),
                    step_id="basic-info",
                    generated_at=NOW,
                )
                self.assertEqual(
                    snapshot["dashboard_metrics"]["retirement_gap"],
                    {"availability": "unavailable", "reason": expected_reason},
                )

    @patch("consulting.tool_context.calculation_input_digest", return_value="trusted-digest")
    def test_newer_processing_report_blocks_older_ready_fallback(self, _digest):
        older_ready = report(content=ready_content(), is_latest=False, version=2)
        newer_processing = report(status=Report.Status.PROCESSING, version=3)

        snapshot = build_tool_context(
            session=session(older_ready, newer_processing),
            user=SimpleNamespace(id=3),
            step_id="basic-info",
            generated_at=NOW,
        )

        self.assertEqual(
            snapshot["dashboard_metrics"]["retirement_gap"]["reason"],
            "report_processing",
        )
        self.assertEqual(
            [item["report_id"] for item in snapshot["available_reports"]],
            [newer_processing.id],
        )

    def test_owner_mismatch_is_rejected_before_report_access(self):
        with self.assertRaises(ToolContextOwnershipError):
            build_tool_context(
                session=session(),
                user=SimpleNamespace(id=99),
                step_id="basic-info",
                generated_at=NOW,
            )
