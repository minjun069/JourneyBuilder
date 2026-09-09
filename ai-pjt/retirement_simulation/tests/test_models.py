from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import sys
import unittest


AI_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(AI_PROJECT_ROOT))

from retirement_simulation import (  # noqa: E402
    AssetPoint,
    ModelValidationError,
    PensionIncome,
    RetirementExpensePeriod,
    RetirementProfile,
    SimulationAssumptions,
)
from retirement_simulation.models import MAX_PATHS  # noqa: E402


def profile(**changes) -> RetirementProfile:
    values = {
        "current_age": 40,
        "retirement_age": 60,
        "end_age": 90,
        "liquid_assets": 100_000_000,
        "monthly_savings": 500_000,
        "retirement_expenses": (
            RetirementExpensePeriod(from_age=60, to_age=90, monthly_amount=2_000_000),
        ),
        "pension_incomes": (),
    }
    return RetirementProfile(**(values | changes))


def assumptions(**changes) -> SimulationAssumptions:
    return SimulationAssumptions(**({
        "annual_return_mean": 0.0,
        "annual_return_volatility": 0.0,
        "annual_inflation_rate": 0.0,
    } | changes))


class PeriodTests(unittest.TestCase):
    def test_zero_amount_and_single_year_are_valid(self) -> None:
        expense = RetirementExpensePeriod(from_age=60, to_age=60, monthly_amount=0)
        pension = PensionIncome(start_age=65, end_age=65, monthly_amount=0)
        self.assertEqual(expense.monthly_amount, 0)
        self.assertEqual(pension.monthly_amount, 0)

    def test_invalid_age_ranges_are_rejected(self) -> None:
        for start, end in ((65, 60), (0, 90), (60, 121), (True, 90), (60, None)):
            for model, start_name, end_name in (
                (RetirementExpensePeriod, "from_age", "to_age"),
                (PensionIncome, "start_age", "end_age"),
            ):
                with self.subTest(model=model.__name__, start=start, end=end):
                    with self.assertRaises(ModelValidationError):
                        model(**{start_name: start, end_name: end, "monthly_amount": 0})

    def test_money_must_be_a_non_negative_integer(self) -> None:
        for amount in (-1, None, True, 1.0, "10000", float("nan"), float("inf")):
            for model, ages in (
                (RetirementExpensePeriod, {"from_age": 60, "to_age": 90}),
                (PensionIncome, {"start_age": 65, "end_age": 90}),
            ):
                with self.subTest(model=model.__name__, amount=amount):
                    with self.assertRaises(ModelValidationError) as error:
                        model(**ages, monthly_amount=amount)
                    self.assertEqual(error.exception.field_name, "monthly_amount")

    def test_periods_are_immutable_and_money_is_not_in_repr(self) -> None:
        expense = RetirementExpensePeriod(
            from_age=60, to_age=90, monthly_amount=1_234_567
        )
        pension = PensionIncome(start_age=65, end_age=90, monthly_amount=2_345_678)
        for value in (expense, pension):
            with self.subTest(model=type(value).__name__):
                with self.assertRaises(FrozenInstanceError):
                    value.monthly_amount = 0
                self.assertNotIn(str(value.monthly_amount), repr(value))


class ProfileTests(unittest.TestCase):
    def test_zero_assets_and_savings_are_valid(self) -> None:
        value = profile(liquid_assets=0, monthly_savings=0)
        self.assertEqual(value.liquid_assets, 0)
        self.assertEqual(value.monthly_savings, 0)

    def test_missing_required_inputs_are_not_defaulted(self) -> None:
        with self.assertRaises(TypeError):
            RetirementProfile(current_age=40)

    def test_invalid_profile_ages_are_rejected(self) -> None:
        for name, value in (
            ("current_age", 17), ("current_age", 101), ("current_age", None),
            ("retirement_age", 17), ("retirement_age", 101),
            ("retirement_age", "60"), ("retirement_age", True),
            ("end_age", 121), ("end_age", 90.0),
        ):
            with self.subTest(field=name, value=value):
                with self.assertRaises(ModelValidationError) as error:
                    profile(**{name: value})
                self.assertEqual(error.exception.field_name, name)

    def test_end_age_cannot_precede_current_or_retirement_age(self) -> None:
        for changes in (
            {"end_age": 59},
            {"current_age": 95, "end_age": 90},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(ModelValidationError) as error:
                    profile(**changes)
                self.assertEqual(error.exception.field_name, "end_age")

    def test_already_retired_profile_does_not_require_past_expenses(self) -> None:
        value = profile(
            current_age=70,
            retirement_age=60,
            monthly_savings=0,
            retirement_expenses=(
                RetirementExpensePeriod(from_age=70, to_age=90, monthly_amount=1_000_000),
            ),
        )
        self.assertEqual(value.retirement_expenses[0].from_age, 70)

    def test_retirement_today_and_one_year_horizon_are_supported(self) -> None:
        value = profile(
            current_age=60,
            retirement_age=60,
            end_age=60,
            retirement_expenses=(
                RetirementExpensePeriod(from_age=60, to_age=60, monthly_amount=0),
            ),
        )
        self.assertEqual(value.current_age, value.end_age)

    def test_missing_or_non_integer_money_is_not_coerced(self) -> None:
        for name in ("liquid_assets", "monthly_savings"):
            for value in (None, True, "5000", 5000.0, -1):
                with self.subTest(field=name, value=value):
                    with self.assertRaises(ModelValidationError) as error:
                        profile(**{name: value})
                    self.assertEqual(error.exception.field_name, name)

    def test_period_collections_require_typed_models(self) -> None:
        for name in ("retirement_expenses", "pension_incomes"):
            for value in (None, {}, "", ({"monthly_amount": 0},)):
                with self.subTest(field=name, value=value):
                    with self.assertRaises(ModelValidationError) as error:
                        profile(**{name: value})
                    self.assertEqual(error.exception.field_name, name)

    def test_missing_expense_coverage_is_rejected(self) -> None:
        for periods in (
            (),
            (RetirementExpensePeriod(from_age=61, to_age=90, monthly_amount=0),),
            (RetirementExpensePeriod(from_age=60, to_age=89, monthly_amount=0),),
            (
                RetirementExpensePeriod(from_age=60, to_age=74, monthly_amount=0),
                RetirementExpensePeriod(from_age=76, to_age=90, monthly_amount=0),
            ),
        ):
            with self.subTest(periods=periods):
                with self.assertRaises(ModelValidationError) as error:
                    profile(retirement_expenses=periods)
                self.assertEqual(error.exception.field_name, "retirement_expenses")

    def test_inclusive_overlap_is_rejected(self) -> None:
        with self.assertRaises(ModelValidationError):
            profile(retirement_expenses=(
                RetirementExpensePeriod(from_age=60, to_age=75, monthly_amount=1_000_000),
                RetirementExpensePeriod(from_age=75, to_age=90, monthly_amount=2_000_000),
            ))

    def test_unsorted_adjacent_periods_preserve_distinct_amounts(self) -> None:
        later = RetirementExpensePeriod(from_age=75, to_age=90, monthly_amount=1_500_000)
        earlier = RetirementExpensePeriod(from_age=60, to_age=74, monthly_amount=2_000_000)
        original = [later, earlier]
        value = profile(retirement_expenses=original)
        self.assertEqual(value.retirement_expenses, (earlier, later))
        self.assertEqual(original, [later, earlier])

    def test_explicit_zero_expenses_are_not_treated_as_missing(self) -> None:
        value = profile(retirement_expenses=(
            RetirementExpensePeriod(from_age=60, to_age=90, monthly_amount=0),
        ))
        self.assertEqual(value.retirement_expenses[0].monthly_amount, 0)

    def test_overlapping_and_time_limited_pensions_are_allowed(self) -> None:
        pensions = (
            PensionIncome(start_age=65, end_age=79, monthly_amount=1_000_000),
            PensionIncome(start_age=70, end_age=90, monthly_amount=500_000),
        )
        value = profile(pension_incomes=pensions)
        self.assertEqual(value.pension_incomes, pensions)

    def test_caller_lists_are_copied_to_immutable_tuples(self) -> None:
        expenses = list(profile().retirement_expenses)
        pensions = [PensionIncome(start_age=65, end_age=90, monthly_amount=1_000_000)]
        value = profile(retirement_expenses=expenses, pension_incomes=pensions)
        expenses.clear()
        pensions.clear()
        self.assertEqual(len(value.retirement_expenses), 1)
        self.assertEqual(len(value.pension_incomes), 1)
        self.assertIsInstance(value.retirement_expenses, tuple)
        self.assertIsInstance(value.pension_incomes, tuple)

    def test_profile_is_immutable_and_replace_does_not_change_original(self) -> None:
        original = profile()
        with self.assertRaises(FrozenInstanceError):
            original.monthly_savings = 0
        changed = replace(original, monthly_savings=700_000)
        self.assertEqual(original.monthly_savings, 500_000)
        self.assertEqual(changed.monthly_savings, 700_000)

    def test_replace_cannot_bypass_validation(self) -> None:
        with self.assertRaises(ModelValidationError):
            replace(profile(), monthly_savings=-1)

    def test_validation_error_does_not_echo_rejected_financial_value(self) -> None:
        rejected = "999999999999 confidential"
        with self.assertRaises(ModelValidationError) as error:
            profile(liquid_assets=rejected)
        self.assertNotIn(rejected, str(error.exception))
        self.assertEqual(error.exception.field_name, "liquid_assets")


class AssumptionsTests(unittest.TestCase):
    def test_market_assumptions_are_required(self) -> None:
        with self.assertRaises(TypeError):
            SimulationAssumptions()

    def test_zero_and_negative_mean_or_inflation_are_supported(self) -> None:
        for changes in ({}, {"annual_return_mean": -0.1, "annual_inflation_rate": -0.01}):
            with self.subTest(changes=changes):
                value = assumptions(**changes)
                self.assertEqual(value.seed, 0)
                self.assertEqual(value.num_paths, 1_000)

    def test_rates_use_decimal_fractions_without_percent_guessing(self) -> None:
        value = assumptions(annual_return_mean=0.05, annual_return_volatility=0.1)
        self.assertEqual(value.annual_return_mean, 0.05)
        self.assertEqual(value.annual_return_volatility, 0.1)
        self.assertEqual(assumptions(annual_return_mean=5).annual_return_mean, 5.0)

    def test_all_rates_must_be_finite_numbers(self) -> None:
        for name in ("annual_return_mean", "annual_return_volatility", "annual_inflation_rate"):
            for value in (None, True, "0.05", float("nan"), float("inf"), -float("inf"), 10**400):
                with self.subTest(field=name, value=value):
                    with self.assertRaises(ModelValidationError) as error:
                        assumptions(**{name: value})
                    self.assertEqual(error.exception.field_name, name)

    def test_rate_boundaries_are_enforced(self) -> None:
        for name, value in (
            ("annual_return_mean", -1),
            ("annual_return_mean", -1.1),
            ("annual_inflation_rate", -1),
            ("annual_return_volatility", -0.001),
        ):
            with self.subTest(field=name, value=value):
                with self.assertRaises(ModelValidationError):
                    assumptions(**{name: value})

    def test_path_count_limits(self) -> None:
        for value in (1, MAX_PATHS):
            self.assertEqual(assumptions(num_paths=value).num_paths, value)
        for value in (0, -1, MAX_PATHS + 1, True, 100.0, None):
            with self.subTest(value=value):
                with self.assertRaises(ModelValidationError):
                    assumptions(num_paths=value)

    def test_seed_is_an_explicit_non_negative_integer(self) -> None:
        self.assertEqual(assumptions(seed=42).seed, 42)
        for value in (-1, True, None, 1.0):
            with self.subTest(value=value):
                with self.assertRaises(ModelValidationError):
                    assumptions(seed=value)


class AssetPointTests(unittest.TestCase):
    def test_negative_balance_is_retained_and_zero_is_not_depletion(self) -> None:
        for amount, depleted in ((100, False), (0, False), (-0.1, True)):
            with self.subTest(amount=amount):
                point = AssetPoint(age=70, liquid_assets=amount)
                self.assertEqual(point.liquid_assets, amount)
                self.assertEqual(point.is_depleted, depleted)

    def test_non_finite_or_non_numeric_balances_are_rejected(self) -> None:
        for amount in (None, True, "100", float("nan"), float("inf"), 10**400):
            with self.subTest(amount=amount):
                with self.assertRaises(ModelValidationError):
                    AssetPoint(age=70, liquid_assets=amount)

    def test_invalid_ages_are_rejected(self) -> None:
        for age in (0, 121, True, None, 70.0):
            with self.subTest(age=age):
                with self.assertRaises(ModelValidationError):
                    AssetPoint(age=age, liquid_assets=0)

    def test_point_is_immutable_and_money_is_not_in_repr(self) -> None:
        point = AssetPoint(age=70, liquid_assets=1_234_567.5)
        with self.assertRaises(FrozenInstanceError):
            point.liquid_assets = 0
        self.assertNotIn("1234567", repr(point))


if __name__ == "__main__":
    unittest.main()
