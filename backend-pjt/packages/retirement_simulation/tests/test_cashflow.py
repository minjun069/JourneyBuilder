from __future__ import annotations

from pathlib import Path
import sys
import unittest


AI_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(AI_PROJECT_ROOT))

from retirement_simulation import (  # noqa: E402
    AssetPoint,
    CashflowResult,
    PensionIncome,
    RetirementExpensePeriod,
    RetirementProfile,
    SimulationAssumptions,
    calculate_cashflow,
    calculate_cashflow_path,
)


def example_a() -> RetirementProfile:
    return RetirementProfile(
        current_age=59, retirement_age=60, end_age=61,
        liquid_assets=10_000_000, monthly_savings=100_000,
        retirement_expenses=(
            RetirementExpensePeriod(from_age=60, to_age=60, monthly_amount=100_000),
            RetirementExpensePeriod(from_age=61, to_age=61, monthly_amount=200_000),
        ),
        pension_incomes=(PensionIncome(start_age=60, end_age=60, monthly_amount=50_000),),
    )


def assumptions(**changes) -> SimulationAssumptions:
    return SimulationAssumptions(**({
        "annual_return_mean": 0.0,
        "annual_return_volatility": 0.0,
        "annual_inflation_rate": 0.0,
    } | changes))


class CashflowBasicTests(unittest.TestCase):
    def test_example_a_timing_retirement_pension_and_expense_changes(self) -> None:
        result = calculate_cashflow(example_a(), assumptions(annual_return_mean=0.1))
        expected = (
            (59, 10_000_000, 1_000_000, 1_200_000, 0, 0, 12_200_000),
            (60, 12_200_000, 1_220_000, 0, 600_000, 1_200_000, 12_820_000),
            (61, 12_820_000, 1_282_000, 0, 0, 2_400_000, 11_702_000),
        )
        self.assertIsInstance(result, CashflowResult)
        self.assertEqual(len(result.years), len(expected))
        for year, row in zip(result.years, expected):
            self.assertEqual(year.age, row[0])
            self.assertEqual(year.annual_return_rate, 0.1)
            for name, amount in zip(
                ("opening_balance", "investment_return", "savings", "pension_income",
                 "expenses", "closing_balance"), row[1:],
            ):
                with self.subTest(age=year.age, field=name):
                    self.assertAlmostEqual(getattr(year, name), amount)
            self.assertEqual(year.shortfall, 0)
        self.assertEqual(result.final_balance, 11_702_000)
        self.assertEqual(result.final_shortfall, 0)
        self.assertIsNone(result.first_depletion_age)

    def test_example_b_inflation_starts_now_not_at_retirement_or_period_change(self) -> None:
        profile = RetirementProfile(
            current_age=60, retirement_age=62, end_age=63,
            liquid_assets=5_000_000, monthly_savings=0,
            retirement_expenses=(
                RetirementExpensePeriod(from_age=62, to_age=62, monthly_amount=100_000),
                RetirementExpensePeriod(from_age=63, to_age=63, monthly_amount=200_000),
            ),
            pension_incomes=(),
        )
        result = calculate_cashflow(profile, assumptions(annual_inflation_rate=0.1))
        for year, expense, balance in zip(
            result.years,
            (0, 0, 1_452_000, 3_194_400),
            (5_000_000, 5_000_000, 3_548_000, 353_600),
        ):
            with self.subTest(age=year.age):
                self.assertAlmostEqual(year.expenses, expense)
                self.assertAlmostEqual(year.closing_balance, balance)

    def test_repeated_explicit_rates_match_fixed_execution(self) -> None:
        profile = example_a()
        config = assumptions(annual_return_mean=0.1)
        fixed = calculate_cashflow(profile, config)
        explicit = calculate_cashflow_path(profile, config, annual_returns=[0.1] * 3)
        self.assertEqual(fixed, explicit)

    def test_explicit_path_replaces_mean_and_preserves_year_order(self) -> None:
        result = calculate_cashflow_path(
            example_a(), assumptions(annual_return_mean=0.8), annual_returns=(0, 0.1, -0.1),
        )
        self.assertEqual([year.annual_return_rate for year in result.years], [0, 0.1, -0.1])
        self.assertEqual([year.closing_balance for year in result.years],
                         [11_200_000, 11_720_000, 8_148_000])

    def test_asset_path_has_one_closing_point_per_year_without_opening_duplicate(self) -> None:
        result = calculate_cashflow(example_a(), assumptions())
        self.assertEqual(result.asset_path, (
            AssetPoint(age=59, liquid_assets=11_200_000),
            AssetPoint(age=60, liquid_assets=10_600_000),
            AssetPoint(age=61, liquid_assets=8_200_000),
        ))
        self.assertIsInstance(result.asset_path, tuple)
        for year, point in zip(result.years, result.asset_path):
            self.assertEqual(year.asset_point, point)
            self.assertEqual(point.liquid_assets, year.closing_balance)


if __name__ == "__main__":
    unittest.main()
