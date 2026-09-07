from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from decimal import Decimal, localcontext
from itertools import product
from math import isclose, nextafter
from pathlib import Path
import sys
import unittest


AI_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(AI_PROJECT_ROOT))

from retirement import (  # noqa: E402
    CashflowCalculationError,
    CashflowResult,
    ModelValidationError,
    PensionIncome,
    RetirementExpensePeriod,
    RetirementProfile,
    SimulationAssumptions,
    calculate_cashflow,
    calculate_cashflow_path,
)


def profile(**changes) -> RetirementProfile:
    return RetirementProfile(**({
        "current_age": 60, "retirement_age": 60, "end_age": 63,
        "liquid_assets": 1_200_000, "monthly_savings": 0,
        "retirement_expenses": (
            RetirementExpensePeriod(from_age=60, to_age=63, monthly_amount=100_000),
        ),
        "pension_incomes": (),
    } | changes))


def assumptions(**changes) -> SimulationAssumptions:
    return SimulationAssumptions(**({
        "annual_return_mean": 0.0,
        "annual_return_volatility": 0.0,
        "annual_inflation_rate": 0.0,
    } | changes))


def expense(amount: int, start: int = 60, end: int = 63) -> RetirementExpensePeriod:
    return RetirementExpensePeriod(from_age=start, to_age=end, monthly_amount=amount)


class CashflowBoundaryTests(unittest.TestCase):
    def test_example_c_zero_depletion_recovery_and_last_shortfall(self) -> None:
        value = profile(pension_incomes=(
            PensionIncome(start_age=62, end_age=63, monthly_amount=300_000),
        ))
        result = calculate_cashflow(value, assumptions())
        self.assertEqual([year.closing_balance for year in result.years],
                         [0, -1_200_000, 1_200_000, 3_600_000])
        self.assertEqual([year.shortfall for year in result.years], [0, 1_200_000, 0, 0])
        self.assertEqual([point.is_depleted for point in result.asset_path],
                         [False, True, False, False])
        self.assertEqual(result.first_depletion_age, 61)
        self.assertEqual(result.final_balance, 3_600_000)
        self.assertEqual(result.final_shortfall, 0)
        for size, first in ((1, None), (2, 61), (3, 61), (4, 61)):
            self.assertEqual(CashflowResult(years=result.years[:size]).first_depletion_age,
                             first)

    def test_negative_opening_balance_never_earns_return_or_interest(self) -> None:
        value = profile(liquid_assets=0)
        for rate in (-0.5, 0.0, 0.5):
            with self.subTest(rate=rate):
                result = calculate_cashflow(value, assumptions(annual_return_mean=rate))
                self.assertEqual([year.investment_return for year in result.years], [0] * 4)
                self.assertEqual([year.closing_balance for year in result.years],
                                 [-1_200_000, -2_400_000, -3_600_000, -4_800_000])
                self.assertEqual(result.first_depletion_age, 60)
                self.assertEqual(result.final_shortfall, 4_800_000)

    def test_returns_resume_only_after_shortfall_is_offset(self) -> None:
        value = profile(liquid_assets=0, pension_incomes=(
            PensionIncome(start_age=61, end_age=63, monthly_amount=300_000),
        ))
        result = calculate_cashflow(value, assumptions(annual_return_mean=0.1))
        self.assertEqual([year.investment_return for year in result.years],
                         [0, 0, 120_000, 372_000])
        self.assertEqual([year.closing_balance for year in result.years],
                         [-1_200_000, 1_200_000, 3_720_000, 6_492_000])
        self.assertEqual(result.first_depletion_age, 60)

    def test_already_retired_has_no_savings_or_backdated_inflation(self) -> None:
        value = profile(current_age=68, retirement_age=65, end_age=70,
                        monthly_savings=999_999,
                        retirement_expenses=(expense(100_000, 65, 70),))
        result = calculate_cashflow(value, assumptions(annual_inflation_rate=0.1))
        self.assertEqual([year.age for year in result.years], [68, 69, 70])
        self.assertEqual([year.savings for year in result.years], [0, 0, 0])
        for year, expected in zip(result.years, (1_200_000, 1_320_000, 1_452_000)):
            self.assertAlmostEqual(year.expenses, expected)

    def test_one_year_horizon_and_exact_zero_are_valid(self) -> None:
        result = calculate_cashflow(profile(end_age=60, monthly_savings=999), assumptions())
        self.assertEqual(len(result.years), 1)
        self.assertEqual(len(result.asset_path), 1)
        self.assertEqual(result.years[0].savings, 0)
        self.assertEqual(result.final_balance, 0)
        self.assertIsNone(result.first_depletion_age)

    def test_maximum_supported_horizon_has_all_years(self) -> None:
        value = profile(current_age=18, retirement_age=100, end_age=120,
                        retirement_expenses=(expense(0, 100, 120),))
        result = calculate_cashflow(value, assumptions())
        self.assertEqual([year.age for year in result.years], list(range(18, 121)))
        self.assertEqual(result.final_balance, value.liquid_assets)

    def test_overlapping_pensions_are_nominal_inclusive_and_can_precede_retirement(self) -> None:
        value = profile(current_age=59, end_age=62, monthly_savings=100,
                        retirement_expenses=(expense(0),), pension_incomes=(
                            PensionIncome(start_age=59, end_age=60, monthly_amount=100),
                            PensionIncome(start_age=60, end_age=61, monthly_amount=200),
                            PensionIncome(start_age=61, end_age=61, monthly_amount=0),
                            PensionIncome(start_age=50, end_age=58, monthly_amount=999),
                            PensionIncome(start_age=63, end_age=65, monthly_amount=999),
                        ))
        result = calculate_cashflow(value, assumptions(annual_inflation_rate=0.1))
        self.assertEqual([year.pension_income for year in result.years], [1200, 3600, 2400, 0])
        self.assertEqual([year.savings for year in result.years], [1200, 0, 0, 0])

    def test_zero_expenses_and_no_pension_stay_zero(self) -> None:
        result = calculate_cashflow(
            profile(liquid_assets=0, retirement_expenses=(expense(0),)), assumptions(),
        )
        for year in result.years:
            self.assertEqual((year.expenses, year.pension_income, year.closing_balance),
                             (0, 0, 0))
        self.assertIsNone(result.first_depletion_age)

    def test_negative_inflation_reduces_expenses(self) -> None:
        result = calculate_cashflow(profile(), assumptions(annual_inflation_rate=-0.5))
        self.assertEqual([year.expenses for year in result.years],
                         [1_200_000, 600_000, 300_000, 150_000])

    def test_negative_return_applies_to_positive_opening_balance(self) -> None:
        result = calculate_cashflow(
            profile(retirement_expenses=(expense(0),)), assumptions(annual_return_mean=-0.5),
        )
        self.assertEqual([year.closing_balance for year in result.years],
                         [600_000, 300_000, 150_000, 75_000])

    def test_nearest_float_above_total_loss_is_accepted(self) -> None:
        rate = nextafter(-1.0, 0.0)
        result = calculate_cashflow_path(
            profile(end_age=60, retirement_expenses=(expense(0),)), assumptions(),
            annual_returns=[rate],
        )
        self.assertGreaterEqual(result.final_balance, 0)
        self.assertLess(result.final_balance, 1)
        self.assertEqual(result.years[0].annual_return_rate, rate)

    def test_tiny_negative_balance_is_not_rounded_to_zero(self) -> None:
        result = calculate_cashflow(
            profile(end_age=60, liquid_assets=12, retirement_expenses=(expense(1),)),
            assumptions(annual_return_mean=-1e-15),
        )
        self.assertLess(result.final_balance, 0)
        self.assertGreater(result.final_balance, -0.01)
        self.assertTrue(result.asset_path[0].is_depleted)
        self.assertEqual(result.first_depletion_age, 60)

    def test_unused_stochastic_settings_do_not_change_fixed_result(self) -> None:
        value = profile()
        baseline = calculate_cashflow(value, assumptions())
        changed = assumptions(annual_return_volatility=3.5, num_paths=100_000, seed=999)
        self.assertEqual(calculate_cashflow(value, changed), baseline)
        # Explicit paths also ignore the mean; inflation still belongs to both modes.
        explicit = calculate_cashflow_path(
            value, replace(changed, annual_return_mean=0.8), annual_returns=[0] * 4,
        )
        self.assertEqual(explicit, baseline)

    def test_inputs_paths_and_results_remain_immutable_and_repeatable(self) -> None:
        value, config = profile(), assumptions()
        original_value, original_config = replace(value), replace(config)
        rates = [0.0, 0.1, -0.2, 0.0]
        original_rates = rates.copy()
        first = calculate_cashflow_path(value, config, annual_returns=rates)
        second = calculate_cashflow_path(value, config, annual_returns=rates)
        self.assertEqual(first, second)
        self.assertIsNot(first, second)
        self.assertEqual((value, config, rates), (original_value, original_config, original_rates))
        rates[0] = 0.9
        self.assertEqual(first.years[0].annual_return_rate, 0)
        copied_years = list(first.years)
        copied_result = CashflowResult(years=copied_years)
        copied_years.clear()
        self.assertEqual(copied_result, first)
        for obj, name, new_value in (
            (first, "years", ()), (first.years[0], "closing_balance", 0),
            (first.asset_path[0], "liquid_assets", 0),
        ):
            with self.assertRaises(FrozenInstanceError):
                setattr(obj, name, new_value)

    def test_results_repr_does_not_echo_financial_values(self) -> None:
        result = calculate_cashflow(profile(liquid_assets=9_876_543), assumptions())
        text = repr((result, result.years, result.asset_path))
        for number in ("9876543", "8676543", "1200000"):
            self.assertNotIn(number, text)


class CashflowValidationTests(unittest.TestCase):
    def test_non_model_inputs_are_rejected_by_both_entry_points(self) -> None:
        for bad in (None, True, {}, "private-customer-input", object()):
            for name in ("profile", "assumptions"):
                for explicit in (False, True):
                    args = {"profile": profile(), "assumptions": assumptions(), name: bad}
                    with self.subTest(name=name, explicit=explicit):
                        with self.assertRaises(ModelValidationError) as error:
                            if explicit:
                                calculate_cashflow_path(**args, annual_returns=[0] * 4)
                            else:
                                calculate_cashflow(**args)
                        self.assertEqual(error.exception.field_name, name)
                        self.assertNotIn("private-customer-input", str(error.exception))

    def test_path_length_must_match_inclusive_horizon(self) -> None:
        for path in ([], [0], [0] * 3, [0] * 5):
            with self.subTest(length=len(path)), self.assertRaises(ModelValidationError):
                calculate_cashflow_path(profile(), assumptions(), annual_returns=path)

    def test_path_container_must_be_list_or_tuple(self) -> None:
        for path in (None, "0000", {0, 1}, {0: 0}, iter([0] * 4), 4):
            with self.subTest(container=type(path).__name__):
                with self.assertRaises(ModelValidationError):
                    calculate_cashflow_path(profile(), assumptions(), annual_returns=path)

    def test_invalid_rate_values_are_rejected_without_coercion_or_echo(self) -> None:
        for rate in (-1, -2, True, None, "private-rate", float("nan"), float("inf"),
                     float("-inf"), 10**400, Decimal("0.1"), 0j):
            with self.subTest(rate_type=type(rate).__name__):
                with self.assertRaises(ModelValidationError) as error:
                    calculate_cashflow_path(profile(), assumptions(), annual_returns=[0, 0, 0, rate])
                self.assertEqual(error.exception.field_name, "annual_returns")
                self.assertNotIn("private-rate", str(error.exception))

    def test_full_path_validation_precedes_calculation(self) -> None:
        # A malformed last rate wins over an opening-amount arithmetic overflow.
        with self.assertRaises(ModelValidationError) as error:
            calculate_cashflow_path(profile(liquid_assets=10**400), assumptions(),
                                    annual_returns=[0, 0, 0, None])
        self.assertEqual(error.exception.field_name, "annual_returns")

    def test_result_rejects_empty_invalid_or_discontinuous_years(self) -> None:
        years = calculate_cashflow(profile(), assumptions()).years
        for values in ((), None, "private", (object(),), years[::-1],
                       (years[0], years[2]), (years[0], years[0]),
                       (years[0], replace(years[1], opening_balance=99))):
            with self.subTest(kind=type(values).__name__):
                with self.assertRaises(ModelValidationError):
                    CashflowResult(years=values)

    def test_year_fields_validate_finite_numbers_and_flow_signs(self) -> None:
        year = calculate_cashflow(profile(), assumptions()).years[0]
        for name in ("opening_balance", "annual_return_rate", "investment_return",
                     "savings", "pension_income", "expenses", "closing_balance"):
            for value in (True, "private-value", None, float("inf"), float("nan")):
                with self.subTest(field=name), self.assertRaises(ModelValidationError):
                    replace(year, **{name: value})
        for name in ("savings", "pension_income", "expenses"):
            with self.assertRaises(ModelValidationError):
                replace(year, **{name: -1})
        for rate in (-1, -2):
            with self.assertRaises(ModelValidationError):
                replace(year, annual_return_rate=rate)
        for age in (17, 121, True):
            with self.assertRaises(ModelValidationError):
                replace(year, age=age)


class CashflowArithmeticTests(unittest.TestCase):
    def test_overflow_at_each_calculation_stage_is_explicit_and_safe(self) -> None:
        cases = (
            (profile(liquid_assets=10**400), assumptions(), "liquid_assets", None),
            (profile(current_age=59, monthly_savings=10**400), assumptions(), "savings", 59),
            (profile(pension_incomes=(PensionIncome(start_age=60, end_age=60,
                                                  monthly_amount=10**400),)),
             assumptions(), "pension_income", 60),
            (profile(retirement_expenses=(expense(10**400),)), assumptions(), "annual_expenses", 60),
            (profile(), assumptions(annual_inflation_rate=1e200), "inflation_factor", 62),
            (profile(retirement_expenses=(expense(10**100),)),
             assumptions(annual_inflation_rate=1e220), "expenses", 61),
            (profile(liquid_assets=10**308), assumptions(annual_return_mean=10), "investment_return", 60),
            (profile(liquid_assets=10**308), assumptions(annual_return_mean=1), "balance_after_return", 60),
            (profile(current_age=59, liquid_assets=10**308, monthly_savings=10**307),
             assumptions(), "balance_after_savings", 59),
            (profile(liquid_assets=10**308, pension_incomes=(
                PensionIncome(start_age=60, end_age=60, monthly_amount=10**307),)),
             assumptions(), "balance_after_pension", 60),
            (profile(liquid_assets=0, retirement_expenses=(expense(10**307),)),
             assumptions(), "closing_balance", 61),
        )
        for value, config, field, age in cases:
            with self.subTest(field=field):
                with self.assertRaises(CashflowCalculationError) as error:
                    calculate_cashflow(value, config)
                self.assertEqual(error.exception.field_name, field)
                self.assertEqual(error.exception.age, age)
                self.assertIsInstance(error.exception, ArithmeticError)
                self.assertNotIn(str(10**100), str(error.exception))
                self.assertNotIn(str(10**100), repr(error.exception))

    def test_zero_expenses_skip_unused_inflation_overflow(self) -> None:
        result = calculate_cashflow(profile(retirement_expenses=(expense(0),)),
                                    assumptions(annual_inflation_rate=1e308))
        self.assertEqual(result.final_balance, 1_200_000)
        self.assertEqual([year.expenses for year in result.years], [0] * 4)

    def test_unused_retired_savings_and_inactive_pensions_do_not_overflow(self) -> None:
        value = profile(monthly_savings=10**400, pension_incomes=(
            PensionIncome(start_age=65, end_age=65, monthly_amount=10**400),
        ))
        self.assertEqual(calculate_cashflow(value, assumptions()),
                         calculate_cashflow(profile(), assumptions()))

    def test_non_binary_rates_match_independent_decimal_reference_grid(self) -> None:
        value = profile(current_age=59, retirement_age=61, end_age=62,
                        liquid_assets=1_000_003, monthly_savings=100_001,
                        retirement_expenses=(expense(200_001, 61, 61), expense(100_003, 62, 62)),
                        pension_incomes=(
                            PensionIncome(start_age=59, end_age=60, monthly_amount=20_001),
                            PensionIncome(start_age=60, end_age=62, monthly_amount=50_003),
                        ))
        # 3 inflation settings x 81 return paths, no RNG or third-party oracle.
        with localcontext() as context:
            context.prec = 50
            for inflation in (-0.1, 0.0, 0.1):
                for rates in product((-0.25, 0.0, 0.25), repeat=4):
                    result = calculate_cashflow_path(value, assumptions(annual_inflation_rate=inflation),
                                                     annual_returns=rates)
                    balance = Decimal(value.liquid_assets)
                    expected_first = None
                    for offset, year in enumerate(result.years):
                        age = 59 + offset
                        rate = Decimal(str(rates[offset]))
                        savings = Decimal(1_200_012 if age < 61 else 0)
                        pension = Decimal({59: 240_012, 60: 840_048, 61: 600_036, 62: 600_036}[age])
                        annual_expense = Decimal({59: 0, 60: 0, 61: 2_400_012, 62: 1_200_036}[age])
                        spending = annual_expense * (1 + Decimal(str(inflation))) ** offset
                        balance = balance + max(balance, Decimal(0)) * rate + savings + pension - spending
                        if balance < 0 and expected_first is None:
                            expected_first = age
                        with self.subTest(inflation=inflation, rates=rates, age=age):
                            self.assertTrue(isclose(year.closing_balance, float(balance),
                                                    rel_tol=1e-11, abs_tol=1e-6))
                    self.assertEqual(result.first_depletion_age, expected_first)


if __name__ == "__main__":
    unittest.main()
