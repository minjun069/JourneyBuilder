from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import random
import sys
import unittest
from unittest.mock import patch


AI_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(AI_PROJECT_ROOT))

from retirement_simulation import (  # noqa: E402
    ModelValidationError,
    PensionIncome,
    RetirementExpensePeriod,
    RetirementProfile,
    SimulationAssumptions,
    WhatIfChanges,
    calculate_cashflow,
    calculate_cashflow_path,
    create_what_if_profile,
)


def expense(start: int, end: int, amount: int = 100) -> RetirementExpensePeriod:
    return RetirementExpensePeriod(from_age=start, to_age=end, monthly_amount=amount)


def profile(**changes) -> RetirementProfile:
    return RetirementProfile(**({
        "current_age": 58, "retirement_age": 60, "end_age": 61,
        "liquid_assets": 1_200, "monthly_savings": 100,
        "retirement_expenses": (expense(60, 61),),
        "pension_incomes": (PensionIncome(start_age=61, end_age=61, monthly_amount=50),),
    } | changes))


def assumptions(**changes) -> SimulationAssumptions:
    return SimulationAssumptions(**({
        "annual_return_mean": 0.0, "annual_return_volatility": 0.0,
        "annual_inflation_rate": 0.0, "num_paths": 3, "seed": 7,
    } | changes))


class WhatIfChangesTests(unittest.TestCase):
    def test_omitted_and_none_fields_mean_unchanged(self) -> None:
        changes = WhatIfChanges()
        self.assertEqual(changes, WhatIfChanges(
            monthly_savings=None, retirement_age=None, retirement_expenses=None,
        ))
        self.assertIsNone(changes.monthly_savings)
        self.assertIsNone(changes.retirement_age)
        self.assertIsNone(changes.retirement_expenses)

    def test_zero_and_large_integer_savings_are_preserved(self) -> None:
        for amount in (0, 200, 10 ** 400):
            with self.subTest(amount=amount):
                self.assertEqual(WhatIfChanges(monthly_savings=amount).monthly_savings, amount)

    def test_invalid_savings_are_not_coerced(self) -> None:
        for amount in (-1, True, False, 1.0, "200", float("nan"), float("inf")):
            with self.subTest(amount=amount):
                with self.assertRaises(ModelValidationError) as error:
                    WhatIfChanges(monthly_savings=amount)
                self.assertEqual(error.exception.field_name, "monthly_savings")

    def test_retirement_age_type_and_global_bounds(self) -> None:
        for age in (18, 100):
            self.assertEqual(WhatIfChanges(retirement_age=age).retirement_age, age)
        for age in (0, 17, 101, True, False, 60.0, "60", float("nan"), float("inf")):
            with self.subTest(age=age):
                with self.assertRaises(ModelValidationError) as error:
                    WhatIfChanges(retirement_age=age)
                self.assertEqual(error.exception.field_name, "retirement_age")

    def test_expenses_require_list_or_tuple_of_typed_periods(self) -> None:
        for periods in (
            {}, "secret raw payload", True, {expense(60, 61)},
            iter((expense(60, 61),)), ({"monthly_amount": 100},), (None,),
            (PensionIncome(start_age=60, end_age=61, monthly_amount=100),),
        ):
            with self.subTest(kind=type(periods).__name__):
                with self.assertRaises(ModelValidationError) as error:
                    WhatIfChanges(retirement_expenses=periods)
                self.assertEqual(error.exception.field_name, "retirement_expenses")

    def test_expense_list_is_snapshotted_before_profile_creation(self) -> None:
        earlier, later = expense(60, 60, 123), expense(61, 61, 456)
        supplied = [later, earlier]
        changes = WhatIfChanges(retirement_expenses=supplied)
        self.assertEqual(supplied, [later, earlier])
        self.assertEqual(changes.retirement_expenses, (later, earlier))
        supplied.clear()
        result = create_what_if_profile(profile(), changes)
        self.assertEqual(result.retirement_expenses, (earlier, later))
        self.assertEqual(changes.retirement_expenses, (later, earlier))

    def test_empty_expense_list_remains_explicit_not_none(self) -> None:
        for supplied in ([], ()):
            changes = WhatIfChanges(retirement_expenses=supplied)
            self.assertEqual(changes.retirement_expenses, ())
            self.assertNotEqual(changes, WhatIfChanges())

    def test_changes_and_nested_periods_are_frozen_and_hidden_in_repr(self) -> None:
        changes = WhatIfChanges(
            monthly_savings=1_234_567, retirement_age=59,
            retirement_expenses=(expense(59, 61, 7_654_321),),
        )
        for name in ("monthly_savings", "retirement_age", "retirement_expenses"):
            with self.subTest(field=name):
                with self.assertRaises(FrozenInstanceError):
                    setattr(changes, name, None)
        with self.assertRaises(FrozenInstanceError):
            changes.retirement_expenses[0].monthly_amount = 0
        self.assertEqual(repr(changes), "WhatIfChanges()")

    def test_only_named_supported_overrides_are_accepted(self) -> None:
        for name in (
            "current_age", "end_age", "liquid_assets", "pension_incomes",
            "annual_return_mean", "annual_return_volatility", "annual_inflation_rate",
            "num_paths", "seed", "label", "monthly_savings_delta",
        ):
            with self.subTest(field=name):
                with self.assertRaises(TypeError):
                    WhatIfChanges(**{name: 0})
        with self.assertRaises(TypeError):
            WhatIfChanges(200)


class WhatIfProfileTests(unittest.TestCase):
    def test_no_op_returns_new_equal_validated_profile(self) -> None:
        baseline = profile()
        for changes in (
            WhatIfChanges(),
            WhatIfChanges(monthly_savings=None, retirement_age=None, retirement_expenses=None),
            WhatIfChanges(monthly_savings=100, retirement_age=60,
                          retirement_expenses=baseline.retirement_expenses),
        ):
            with self.subTest(changes=changes):
                result = create_what_if_profile(baseline, changes)
                self.assertEqual(result, baseline)
                self.assertIsNot(result, baseline)
                self.assertIsInstance(result, RetirementProfile)

    def test_savings_is_absolute_and_zero_not_a_fallback(self) -> None:
        baseline = profile()
        for amount in (0, 50, 100, 200):
            with self.subTest(amount=amount):
                result = create_what_if_profile(baseline, WhatIfChanges(monthly_savings=amount))
                self.assertEqual(result, replace(baseline, monthly_savings=amount))
                self.assertEqual(baseline.monthly_savings, 100)

    def test_all_overrides_applied_before_coverage_validation(self) -> None:
        baseline = profile()
        changes = WhatIfChanges(
            retirement_age=59, monthly_savings=200,
            retirement_expenses=(expense(59, 61, 80),),
        )
        self.assertEqual(create_what_if_profile(baseline, changes), profile(
            retirement_age=59, monthly_savings=200,
            retirement_expenses=(expense(59, 61, 80),),
        ))

    def test_early_retirement_missing_expenses_is_rejected_without_filling(self) -> None:
        baseline = profile()
        with self.assertRaises(ModelValidationError) as error:
            create_what_if_profile(baseline, WhatIfChanges(retirement_age=59))
        self.assertEqual(error.exception.field_name, "retirement_expenses")
        self.assertEqual(baseline, profile())

    def test_early_retirement_can_reuse_already_covering_expenses(self) -> None:
        baseline = profile(retirement_expenses=(expense(58, 61),))
        result = create_what_if_profile(baseline, WhatIfChanges(retirement_age=59))
        self.assertEqual(result.retirement_expenses, baseline.retirement_expenses)
        self.assertEqual(result.retirement_age, 59)

    def test_later_retirement_preserves_inactive_expenses_and_pensions(self) -> None:
        baseline = profile(retirement_expenses=(expense(60, 60, 80), expense(61, 61, 100)))
        result = create_what_if_profile(baseline, WhatIfChanges(retirement_age=61))
        self.assertEqual(result, replace(baseline, retirement_age=61))

    def test_expenses_replace_whole_schedule_and_preserve_amounts(self) -> None:
        baseline = profile(retirement_expenses=(expense(60, 60, 80), expense(61, 61, 100)))
        supplied = [expense(61, 61, 70), expense(60, 60, 50)]
        result = create_what_if_profile(baseline, WhatIfChanges(retirement_expenses=supplied))
        self.assertEqual(result.retirement_expenses, (supplied[1], supplied[0]))
        self.assertEqual(baseline.retirement_expenses, (expense(60, 60, 80), expense(61, 61, 100)))

    def test_partial_schedule_is_not_merged_with_baseline(self) -> None:
        for supplied in ((expense(60, 60, 0),), (expense(61, 61, 0),)):
            with self.subTest(supplied=supplied):
                with self.assertRaises(ModelValidationError) as error:
                    create_what_if_profile(profile(), WhatIfChanges(retirement_expenses=supplied))
                self.assertEqual(error.exception.field_name, "retirement_expenses")

    def test_empty_schedule_is_rejected_on_application(self) -> None:
        for supplied in ([], ()):
            with self.assertRaises(ModelValidationError) as error:
                create_what_if_profile(profile(), WhatIfChanges(retirement_expenses=supplied))
            self.assertEqual(error.exception.field_name, "retirement_expenses")

    def test_explicit_zero_expenses_are_valid(self) -> None:
        result = create_what_if_profile(profile(), WhatIfChanges(
            retirement_expenses=(expense(60, 61, 0),),
        ))
        self.assertEqual(result.retirement_expenses, (expense(60, 61, 0),))

    def test_overlap_including_inactive_periods_is_rejected(self) -> None:
        for supplied in (
            (expense(60, 60), expense(60, 61)),
            (expense(18, 30), expense(30, 35), expense(60, 61)),
            (expense(60, 61), expense(80, 90), expense(85, 95)),
        ):
            with self.subTest(supplied=supplied):
                with self.assertRaises(ModelValidationError) as error:
                    create_what_if_profile(profile(), WhatIfChanges(retirement_expenses=supplied))
                self.assertEqual(error.exception.field_name, "retirement_expenses")

    def test_nonoverlapping_outside_horizon_periods_are_kept(self) -> None:
        supplied = (expense(90, 120, 70), expense(60, 61), expense(1, 30, 0))
        result = create_what_if_profile(profile(), WhatIfChanges(retirement_expenses=supplied))
        self.assertEqual(result.retirement_expenses, (supplied[2], supplied[1], supplied[0]))

    def test_current_age_and_end_age_retirement_are_allowed(self) -> None:
        for age in (58, 61):
            with self.subTest(age=age):
                result = create_what_if_profile(profile(), WhatIfChanges(
                    retirement_age=age, retirement_expenses=(expense(age, 61),),
                ))
                self.assertEqual(result.retirement_age, age)
                self.assertEqual((result.current_age, result.end_age), (58, 61))

    def test_retirement_after_end_age_reuses_profile_validation(self) -> None:
        with self.assertRaises(ModelValidationError) as error:
            create_what_if_profile(profile(), WhatIfChanges(retirement_age=62))
        self.assertEqual(error.exception.field_name, "end_age")

    def test_working_profile_cannot_move_retirement_into_the_past(self) -> None:
        with self.assertRaises(ModelValidationError) as error:
            create_what_if_profile(profile(), WhatIfChanges(
                retirement_age=57, retirement_expenses=(expense(57, 61),),
            ))
        self.assertEqual(error.exception.field_name, "retirement_age")

    def test_already_retired_age_cannot_be_changed_even_with_coverage(self) -> None:
        baseline = profile(current_age=65, end_age=68, retirement_expenses=(expense(60, 68),))
        for age in (59, 61, 65, 67):
            with self.subTest(age=age):
                with self.assertRaises(ModelValidationError) as error:
                    create_what_if_profile(baseline, WhatIfChanges(retirement_age=age))
                self.assertEqual(error.exception.field_name, "retirement_age")

    def test_already_retired_allows_unchanged_age_savings_and_expenses(self) -> None:
        baseline = profile(current_age=65, end_age=68, retirement_expenses=(expense(65, 68),))
        for age in (None, 60):
            with self.subTest(age=age):
                result = create_what_if_profile(baseline, WhatIfChanges(
                    retirement_age=age, monthly_savings=200,
                    retirement_expenses=(expense(65, 68, 50),),
                ))
                self.assertEqual(result.retirement_age, 60)
                self.assertEqual(result.monthly_savings, 200)
                self.assertEqual(result.retirement_expenses, (expense(65, 68, 50),))

    def test_retirement_today_may_be_delayed_but_not_backdated(self) -> None:
        baseline = profile(current_age=60)
        result = create_what_if_profile(baseline, WhatIfChanges(retirement_age=61))
        self.assertEqual(result.retirement_age, 61)
        with self.assertRaises(ModelValidationError) as error:
            create_what_if_profile(baseline, WhatIfChanges(
                retirement_age=59, retirement_expenses=(expense(59, 61),),
            ))
        self.assertEqual(error.exception.field_name, "retirement_age")

    def test_frozen_inputs_are_preserved_after_success_and_failure(self) -> None:
        baseline = profile()
        changes = WhatIfChanges(monthly_savings=200)
        baseline_before, changes_before = deepcopy(baseline), deepcopy(changes)
        result = create_what_if_profile(baseline, changes)
        with self.assertRaises(ModelValidationError):
            create_what_if_profile(baseline, WhatIfChanges(retirement_age=59))
        self.assertEqual(baseline, baseline_before)
        self.assertEqual(changes, changes_before)
        with self.assertRaises(FrozenInstanceError):
            result.monthly_savings = 0
        with self.assertRaises(FrozenInstanceError):
            result.retirement_expenses[0].monthly_amount = 0
        with self.assertRaises(FrozenInstanceError):
            result.pension_incomes[0].monthly_amount = 0

    def test_repeated_calls_do_not_accumulate_or_depend_on_order(self) -> None:
        baseline = profile()
        savings = WhatIfChanges(monthly_savings=200)
        spending = WhatIfChanges(retirement_expenses=(expense(60, 61, 50),))
        first = create_what_if_profile(baseline, savings)
        second = create_what_if_profile(baseline, spending)
        self.assertEqual(first, create_what_if_profile(baseline, savings))
        self.assertEqual(second, create_what_if_profile(baseline, spending))
        self.assertEqual(first.retirement_expenses, baseline.retirement_expenses)
        self.assertEqual(second.monthly_savings, baseline.monthly_savings)
        self.assertEqual(baseline, profile())

    def test_wrong_profile_or_changes_types_are_rejected(self) -> None:
        for value in (None, {}, [], "secret customer input", True):
            for name in ("profile", "changes"):
                with self.subTest(field=name, kind=type(value).__name__):
                    arguments = {"profile": profile(), "changes": WhatIfChanges()}
                    arguments[name] = value
                    with self.assertRaises(ModelValidationError) as error:
                        create_what_if_profile(**arguments)
                    self.assertEqual(error.exception.field_name, name)
                    self.assertNotIn("secret customer input", str(error.exception))

    def test_changes_argument_is_required_and_no_extra_fields_are_accepted(self) -> None:
        with self.assertRaises(TypeError):
            create_what_if_profile(profile())
        with self.assertRaises(TypeError):
            create_what_if_profile(profile(), WhatIfChanges(), seed=1)

    def test_validation_errors_do_not_echo_financial_values(self) -> None:
        secret_amount = -987654321
        with self.assertRaises(ModelValidationError) as error:
            WhatIfChanges(monthly_savings=secret_amount)
        self.assertNotIn(str(secret_amount), str(error.exception))
        changes = WhatIfChanges(retirement_expenses=(expense(61, 61, 87654321),))
        with self.assertRaises(ModelValidationError) as error:
            create_what_if_profile(profile(liquid_assets=76543210), changes)
        for secret in ("87654321", "76543210"):
            self.assertNotIn(secret, str(error.exception))
            self.assertNotIn(secret, repr(changes))

    def test_creation_does_not_generate_returns_or_calculate_results(self) -> None:
        state = random.getstate()
        with (
            patch("retirement_simulation.return_paths.generate_return_paths") as generate,
            patch("retirement_simulation.cashflow.calculate_cashflow_path") as cashflow,
            patch("retirement_simulation.monte_carlo.calculate_monte_carlo_paths") as monte_carlo,
            patch("random.Random") as rng,
        ):
            create_what_if_profile(profile(), WhatIfChanges(monthly_savings=200))
            for mocked in (generate, cashflow, monte_carlo, rng):
                mocked.assert_not_called()
        self.assertEqual(random.getstate(), state)


class WhatIfCashflowCompatibilityTests(unittest.TestCase):
    def test_example_a_savings_and_no_op_use_existing_engine(self) -> None:
        baseline = profile()
        for changes, expected in (
            (WhatIfChanges(), (2400, 3600, 2400, 1800)),
            (WhatIfChanges(monthly_savings=100), (2400, 3600, 2400, 1800)),
            (WhatIfChanges(monthly_savings=200), (3600, 6000, 4800, 4200)),
            (WhatIfChanges(monthly_savings=0), (1200, 1200, 0, -600)),
        ):
            with self.subTest(expected=expected):
                changed = create_what_if_profile(baseline, changes)
                result = calculate_cashflow(changed, assumptions())
                self.assertEqual(tuple(y.closing_balance for y in result.years), expected)

    def test_example_b_early_retirement_and_delay_preserve_pension_timing(self) -> None:
        for changes, expected, first_depletion in (
            (WhatIfChanges(retirement_age=59, retirement_expenses=(expense(59, 61),)),
             (2400, 1200, 0, -600), 61),
            (WhatIfChanges(retirement_age=61), (2400, 3600, 4800, 4200), None),
        ):
            with self.subTest(expected=expected):
                result = calculate_cashflow(create_what_if_profile(profile(), changes), assumptions())
                self.assertEqual(tuple(y.closing_balance for y in result.years), expected)
                self.assertEqual(tuple(y.pension_income for y in result.years), (0, 0, 0, 600))
                self.assertEqual(result.first_depletion_age, first_depletion)

    def test_example_c_expense_replacement_preserves_depletion_history(self) -> None:
        baseline = profile(
            current_age=60, liquid_assets=120, monthly_savings=0,
            retirement_expenses=(expense(60, 61, 10),),
            pension_incomes=(PensionIncome(start_age=61, end_age=61, monthly_amount=20),),
        )
        changed = create_what_if_profile(baseline, WhatIfChanges(
            retirement_expenses=(expense(60, 61, 5),),
        ))
        for path, before, after in (
            ((-0.5, 0), (-60, 60), (0, 180)),
            ((0, 0), (0, 120), (60, 240)),
            ((0.5, 0), (60, 180), (120, 300)),
            ((1.0, 0), (120, 240), (180, 360)),
        ):
            with self.subTest(path=path):
                base_result = calculate_cashflow_path(baseline, assumptions(), annual_returns=path)
                changed_result = calculate_cashflow_path(changed, assumptions(), annual_returns=path)
                self.assertEqual(tuple(y.closing_balance for y in base_result.years), before)
                self.assertEqual(tuple(y.closing_balance for y in changed_result.years), after)
                self.assertEqual(base_result.first_depletion_age, 60 if before[0] < 0 else None)
                self.assertIsNone(changed_result.first_depletion_age)

    def test_inflation_still_accumulates_from_current_age(self) -> None:
        changed = create_what_if_profile(profile(), WhatIfChanges(
            retirement_age=59, retirement_expenses=(expense(59, 61),),
        ))
        result = calculate_cashflow(changed, assumptions(annual_inflation_rate=0.1))
        for year, expected in zip(result.years, (0, 1320, 1452, 1597.2)):
            with self.subTest(age=year.age):
                self.assertAlmostEqual(year.expenses, expected)

    def test_already_retired_savings_override_does_not_reactivate_contributions(self) -> None:
        baseline = profile(current_age=65, end_age=68, retirement_expenses=(expense(65, 68),))
        changed = create_what_if_profile(baseline, WhatIfChanges(monthly_savings=200))
        self.assertEqual(calculate_cashflow(changed, assumptions()),
                         calculate_cashflow(baseline, assumptions()))


if __name__ == "__main__":
    unittest.main()
