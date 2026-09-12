from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from adminapi.grading import (
    DSR_GOOD_UNDER,
    EMERGENCY_GOOD_FROM,
    SAVING_GOOD_FROM,
    build_graded_indicators,
)
from .calculators import (
    DEFAULT_ASSUMPTIONS,
    PENSION_ASSUMPTIONS,
    calculate_financial_indicators,
    calculate_pension_schedule,
)
from .models import Report
from .policy import active_policy_version, load_financial_policy
from .services import _build_report_content
from .snapshots import calculation_input_digest


class FakeManager(list):
    def all(self):
        return self

    def filter(self, **criteria):
        return FakeManager([
            item for item in self
            if all(getattr(item, key) == value for key, value in criteria.items())
        ])

    def prefetch_related(self, *_args):
        return self

    def order_by(self, field):
        return FakeManager(sorted(self, key=lambda item: getattr(item, field)))

    def values(self, *fields):
        return [
            {field: getattr(item, field) for field in fields}
            for item in self
        ]


def record(**values):
    return SimpleNamespace(**values)


def empty_session():
    return SimpleNamespace(
        basic_info=record(
            age=40,
            monthly_income=10_000_000,
            retirement_age=60,
            end_age=90,
            dependents={},
        ),
        financial_assets=FakeManager(),
        pension_assets=FakeManager(),
        real_estate_assets=FakeManager(),
        liabilities=FakeManager(),
        incomes=FakeManager(),
        expenses=FakeManager(),
        goal_periods=FakeManager(),
        goal_milestones=FakeManager(),
    )


class FinancialPolicyContractTests(SimpleTestCase):
    def test_approved_policy_is_the_calculator_source(self):
        policy = load_financial_policy()

        self.assertEqual(active_policy_version(), "2026-09-12.v1")
        self.assertEqual(DEFAULT_ASSUMPTIONS, policy["assumptions"])
        self.assertEqual(DEFAULT_ASSUMPTIONS["conservative_inflation_rate"], 0.03)
        self.assertEqual(DEFAULT_ASSUMPTIONS["real_estate_appreciation_rate"], 0.02)
        self.assertEqual(PENSION_ASSUMPTIONS["min_income"], 410_000)
        self.assertEqual(PENSION_ASSUMPTIONS["max_income"], 6_590_000)
        self.assertEqual(DSR_GOOD_UNDER["50s"], 20)
        self.assertEqual(DSR_GOOD_UNDER["60-64"], 10)
        self.assertEqual(DSR_GOOD_UNDER["65+"], 0)
        self.assertEqual(EMERGENCY_GOOD_FROM["60-64"], 6)
        self.assertEqual(SAVING_GOOD_FROM["65+"], 5)

    def test_undefined_indicator_is_not_graded_as_zero(self):
        graded = build_graded_indicators({"household_balance": None}, age=40)

        self.assertEqual(graded[0]["grade"], "판정 불가")
        self.assertIsNone(graded[0]["value"])

    def test_indicator_denominators_match_approved_policy(self):
        session = empty_session()
        session.expenses.append(record(name="소득세", amount=1_000_000))
        session.pension_assets.append(record(
            type="개인연금",
            expected_amount=0,
            monthly_savings=1_000_000,
        ))
        result = calculate_financial_indicators(
            session,
            {
                "total_assets": 12_000_000,
                "asset_categories": {
                    "cash_savings": {"total": 6_000_000},
                    "investment_assets": {"total": 3_000_000},
                },
            },
            {
                "total_cash_inflow": 10_000_000,
                "total_cash_outflow": 3_000_000,
                "outflow_categories": {
                    "savings": {"total": 1_000_000},
                    "investments": {"total": 1_000_000},
                    "debt_repayment": {"total": 500_000},
                },
            },
        )

        self.assertEqual(result["total_savings_propensity"], 20.0)
        self.assertEqual(result["emergency_reserve"], 2.0)
        self.assertEqual(result["retirement_savings"], 50.0)
        self.assertEqual(
            result["assumptions"]["calculation_policy_version"],
            active_policy_version(),
        )

    def test_zero_denominator_is_undefined_instead_of_zero_score(self):
        session = empty_session()
        result = calculate_financial_indicators(
            session,
            {
                "total_assets": 0,
                "asset_categories": {
                    "cash_savings": {"total": 0},
                    "investment_assets": {"total": 0},
                },
            },
            {
                "total_cash_inflow": 0,
                "total_cash_outflow": 0,
                "outflow_categories": {
                    "savings": {"total": 0},
                    "investments": {"total": 0},
                    "debt_repayment": {"total": 0},
                },
            },
        )

        self.assertIsNone(result["household_balance"])
        self.assertIsNone(result["total_savings_propensity"])
        self.assertIsNone(result["emergency_reserve"])
        self.assertIsNone(result["retirement_savings"])

    def test_pension_estimate_does_not_invent_a_contribution_start_age(self):
        result = calculate_pension_schedule(empty_session())

        self.assertEqual(result["results"]["estimate_method"], "unavailable")
        self.assertEqual(result["results"]["total_months"], 0)
        self.assertEqual(result["results"]["pension_start_age"], 65)

    def test_snapshot_digest_is_deterministic_and_input_sensitive(self):
        session = empty_session()
        first = calculation_input_digest(session)
        second = calculation_input_digest(session)
        session.incomes.append(record(
            id=1,
            type="급여",
            name="월급",
            amount=1,
            is_fixed=True,
        ))

        self.assertEqual(first, second)
        self.assertNotEqual(first, calculation_input_digest(session))

    def test_required_savings_persists_gap_policy_and_input_digest(self):
        session = empty_session()
        with (
            patch("reports.services.calculate_retirement_fund", return_value={
                "total_required_fund": 500_000_000,
            }),
            patch("reports.services.calculate_retirement_readiness", return_value={
                "total_retirement_assets": 125_000_000,
            }),
            patch("reports.services.calculate_required_savings", return_value={
                "target_monthly_savings": 1_000_000,
            }),
        ):
            content = _build_report_content(
                Report.ReportType.REQUIRED_SAVINGS,
                session,
            )

        self.assertEqual(content["retirement_gap"], 375_000_000)
        self.assertEqual(content["calculation_policy_version"], active_policy_version())
        self.assertEqual(content["input_snapshot_digest"], calculation_input_digest(session))
