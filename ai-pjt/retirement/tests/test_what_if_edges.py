"""Synthetic What-if boundary regressions, not market or service certification."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal, localcontext
from fractions import Fraction
from itertools import product
from math import isclose, isfinite, nextafter
from pathlib import Path
import random
import sys
import unittest
from unittest.mock import patch


AI_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(AI_PROJECT_ROOT))

from retirement import (  # noqa: E402
    AmountPercentiles, AssetPercentiles, FirstDepletionCount, ModelValidationError,
    MonteCarloMetadata, MonteCarloResult, PensionIncome, RetirementExpensePeriod,
    RetirementProfile, SimulationAssumptions, WhatIfCalculationError, WhatIfChanges,
    WhatIfScenario, WhatIfScenarioResult, calculate_cashflow, calculate_monte_carlo_paths,
    calculate_what_if, calculate_what_if_paths, generate_return_paths,
)
from retirement.tests.test_monte_carlo_edges import (  # noqa: E402
    financial_profiles, reference_batch, reference_percentiles,
)


ENGINE = "retirement.what_if_engine."


def expense(amount=10, start=60, end=64):
    return RetirementExpensePeriod(from_age=start, to_age=end, monthly_amount=amount)


def profile(**changes):
    return RetirementProfile(**({
        "current_age": 60, "retirement_age": 60, "end_age": 64,
        "liquid_assets": 120, "monthly_savings": 0,
        "retirement_expenses": (expense(),), "pension_incomes": (),
    } | changes))


def assumptions(**changes):
    return SimulationAssumptions(**({
        "annual_return_mean": 0.05, "annual_return_volatility": 0.1,
        "annual_inflation_rate": 0.02, "num_paths": 7, "seed": 42,
    } | changes))


def scenario(name="changed", **changes):
    return WhatIfScenario(scenario_id=name, changes=WhatIfChanges(**changes))


def amounts(value):
    return value.p10, value.p50, value.p90


def comparison_cases():
    """Expected inputs are built directly, never with the production What-if builder."""
    cases = []
    for baseline in financial_profiles():
        start, end = max(baseline.current_age, baseline.retirement_age), baseline.end_age
        overrides = [
            {}, {"monthly_savings": 0},
            {"retirement_expenses": (expense(7_003, start, end),)},
        ]
        if baseline.retirement_age < baseline.current_age:
            overrides += [
                {"retirement_expenses": (expense(0, start, end),)},
                {"retirement_age": baseline.retirement_age, "monthly_savings": 15_003},
            ]
        else:
            overrides += [
                {"retirement_age": baseline.current_age,
                 "retirement_expenses": (expense(12_003, baseline.current_age, end),)},
                {"retirement_age": end, "monthly_savings": 20_003,
                 "retirement_expenses": (expense(5_003, end, end),)},
            ]
        items = tuple(scenario(f"case_{index}", **change) for index, change in enumerate(overrides))
        expected = tuple(replace(baseline, **change) for change in overrides)
        cases.append((baseline, items, expected))
    return tuple(cases)


class _Checks(unittest.TestCase):
    def assert_amounts_close(self, actual, expected):
        for value, reference in zip(amounts(actual), expected):
            self.assertTrue(isfinite(value))
            self.assertTrue(isclose(value, float(reference), rel_tol=1e-10, abs_tol=1e-7),
                            f"{value!r} differs from Decimal reference {reference!r}")

    def assert_summary_reference(self, actual, expected_input, config, paths):
        samples, counts, gaps = reference_batch(expected_input, config, paths)
        ages = tuple(range(expected_input.current_age, expected_input.end_age + 1))
        self.assertEqual(tuple(row.age for row in actual.asset_percentiles), ages)
        self.assertEqual(tuple(row.age for row in actual.first_depletion_counts), ages)
        self.assertEqual(tuple(row.count for row in actual.first_depletion_counts), tuple(counts))
        self.assertEqual(actual.num_paths, len(paths))
        self.assertEqual(actual.depletion_count, sum(counts))
        self.assertEqual(actual.success_count, len(paths) - sum(counts))
        for row, values in zip(actual.asset_percentiles, samples):
            self.assert_amounts_close(row.balance, reference_percentiles(values))
        self.assert_amounts_close(actual.final_shortfall_percentiles, reference_percentiles(gaps))
        return samples, counts, gaps

    def assert_comparison_reference(self, actual, baseline, expected_inputs, config, paths):
        base_samples, base_counts, base_gaps = self.assert_summary_reference(
            actual.baseline, baseline, config, paths,
        )
        self.assertEqual(len(actual.scenarios), len(expected_inputs))
        for item, expected_input in zip(actual.scenarios, expected_inputs):
            samples, counts, gaps = self.assert_summary_reference(item.result, expected_input, config, paths)
            self.assertIs(item.baseline, actual.baseline)
            self.assertEqual(tuple(row.age for row in item.asset_deltas),
                             tuple(row.age for row in actual.baseline.asset_percentiles))
            self.assertEqual(item.depletion_count_delta, sum(counts) - sum(base_counts))
            self.assertEqual(item.success_count_delta, sum(base_counts) - sum(counts))
            self.assertEqual(item.never_depleted_count_delta, item.success_count_delta)
            self.assertEqual(tuple(row.age for row in item.first_depletion_deltas),
                             tuple(row.age for row in actual.baseline.first_depletion_counts))
            self.assertEqual(tuple(row.count for row in item.first_depletion_deltas),
                             tuple(c - b for c, b in zip(counts, base_counts)))
            self.assertEqual(sum(row.count for row in item.first_depletion_deltas), item.depletion_count_delta)
            expected_pp = float(Fraction(sum(base_counts) - sum(counts), len(paths)) * 100)
            self.assertAlmostEqual(item.success_probability_delta_pp, expected_pp, delta=1e-12)
            self.assertAlmostEqual(item.depletion_probability_delta_pp, -expected_pp, delta=1e-12)
            with localcontext() as context:
                context.prec = 80
                for row, base, changed in zip(item.asset_deltas, base_samples, samples):
                    self.assert_amounts_close(row.balance, tuple(
                        c - b for c, b in zip(reference_percentiles(changed), reference_percentiles(base))
                    ))
                self.assert_amounts_close(item.final_shortfall_delta, tuple(
                    c - b for c, b in zip(reference_percentiles(gaps), reference_percentiles(base_gaps))
                ))
            self.assertIs(item.final_balance_delta, item.asset_deltas[-1].balance)


class WhatIfReferenceEdgeTests(_Checks):
    def test_explicit_grid_matches_independent_decimal_for_all_scenarios(self):
        successes = failures = checked = 0
        # 3 schedules x 3 inflations x 3 counts x 2 seeds = 54 five-scenario batches.
        for (baseline, items, expected), inflation, count, seed in product(
            comparison_cases(), (-0.02, 0.0, 0.03), (1, 2, 17), (0, 97),
        ):
            config = assumptions(annual_inflation_rate=inflation, num_paths=count, seed=seed)
            rng = random.Random(seed)
            paths = tuple(tuple(rng.choice((-0.375, -0.125, 0, 0.0625, 0.25, 0.5))
                                for _ in range(baseline.end_age - baseline.current_age + 1))
                          for _ in range(count))
            with self.subTest(age=baseline.current_age, inflation=inflation, count=count, seed=seed):
                result = calculate_what_if_paths(baseline, config, items, annual_return_paths=paths)
                self.assert_comparison_reference(result, baseline, expected, config, paths)
                successes += sum(item.result.success_count for item in result.scenarios)
                failures += sum(item.result.depletion_count for item in result.scenarios)
                checked += 1
        self.assertEqual(checked, 54)
        self.assertGreater(successes, 0)
        self.assertGreater(failures, 0)

    def test_generated_grid_matches_independent_cashflow_and_delta_oracle(self):
        for (baseline, items, expected), (mean, volatility), seed in product(
            comparison_cases(), ((-0.2, 0.1), (0.05, 0.2)), (0, 97),
        ):
            config = assumptions(annual_return_mean=mean, annual_return_volatility=volatility,
                                 num_paths=19, seed=seed)
            with self.subTest(age=baseline.current_age, mean=mean, seed=seed):
                paths = generate_return_paths(baseline, config)
                result = calculate_what_if(baseline, config, items)
                self.assert_comparison_reference(result, baseline, expected, config, paths)

    def test_zero_volatility_grid_matches_fixed_engine_bit_for_bit(self):
        with patch("retirement.return_paths.Random") as rng:
            for (baseline, items, expected), mean, inflation, count, seed in product(
                comparison_cases(), (-0.25, 0.0, 0.05), (0.0, 0.02), (1, 13), (0, 2**256 + 1),
            ):
                config = assumptions(annual_return_mean=mean, annual_return_volatility=0,
                                     annual_inflation_rate=inflation, num_paths=count, seed=seed)
                with self.subTest(age=baseline.current_age, mean=mean, count=count, seed=seed):
                    output = calculate_what_if(baseline, config, items)
                    fixed_base = calculate_cashflow(baseline, config)
                    for item, changed in zip(output.scenarios, expected):
                        fixed = calculate_cashflow(changed, config)
                        self.assertEqual(item.result.success_count,
                                         count if fixed.first_depletion_age is None else 0)
                        for row, before, after in zip(item.asset_deltas, fixed_base.years, fixed.years):
                            self.assertEqual(amounts(row.balance),
                                             (after.closing_balance - before.closing_balance,) * 3)
                        self.assertEqual(amounts(item.final_shortfall_delta),
                                         (fixed.final_shortfall - fixed_base.final_shortfall,) * 3)
                    self.assertIsNone(output.metadata.seed)
        rng.assert_not_called()

    def test_maximum_horizon_and_scenario_count_with_small_path_batch(self):
        baseline = profile(
            current_age=18, retirement_age=100, end_age=120, liquid_assets=1001, monthly_savings=17,
            retirement_expenses=(expense(37, 100, 119), expense(91, 120, 120)),
            pension_incomes=(PensionIncome(start_age=100, end_age=120, monthly_amount=17),),
        )
        overrides = [{"monthly_savings": amount} for amount in range(9)] + [
            {"retirement_age": 18, "retirement_expenses": (expense(37, 18, 120),)},
        ]
        items = tuple(scenario(f"case_{index}", **change) for index, change in enumerate(overrides))
        expected = tuple(replace(baseline, **change) for change in overrides)
        config = assumptions(num_paths=3, annual_return_mean=0.025, annual_return_volatility=0.05)
        paths = generate_return_paths(baseline, config)
        output = calculate_what_if_paths(baseline, config, items, annual_return_paths=paths)
        self.assertEqual(len(output.scenarios), 10)
        self.assertEqual(len(output.baseline.asset_percentiles), 103)
        self.assertEqual(output.scenarios[-1].asset_deltas[-1].age, 120)
        self.assert_comparison_reference(output, baseline, expected, config, paths)


class WhatIfScheduleEdgeTests(_Checks):
    def test_single_year_at_both_current_age_limits_keeps_zero_successful(self):
        for age in (18, 100):
            baseline = profile(current_age=age, retirement_age=age, end_age=age,
                               liquid_assets=12, retirement_expenses=(expense(1, age, age),))
            changes = {"retirement_expenses": (expense(0, age, age),)}
            config = assumptions(num_paths=3, annual_inflation_rate=0)
            paths = ((-0.5,), (0,), (0.5,))
            with self.subTest(age=age):
                output = calculate_what_if_paths(baseline, config, (scenario(**changes),),
                                                 annual_return_paths=paths)
                self.assertEqual(output.baseline.success_count, 2)
                self.assertEqual(output.scenarios[0].result.success_count, 3)
                self.assertEqual(amounts(output.scenarios[0].final_balance_delta), (12,) * 3)
                self.assert_comparison_reference(output, baseline, (replace(baseline, **changes),),
                                                 config, paths)

    def test_last_invalid_schedule_aborts_both_entrypoints_before_any_market_work(self):
        baseline = profile(current_age=59, retirement_age=61, end_age=65,
                           retirement_expenses=(expense(10, 61, 65),))
        invalid = (
            {"retirement_expenses": ()},
            {"retirement_expenses": (expense(10, 62, 65),)},
            {"retirement_expenses": (expense(10, 61, 62), expense(10, 64, 65))},
            {"retirement_expenses": (expense(10, 61, 63), expense(10, 63, 65))},
            {"retirement_expenses": (expense(10, 18, 30), expense(10, 30, 35), expense(10, 61, 65))},
            {"retirement_age": 59},
        )
        for changes, entry in product(invalid, (calculate_what_if, calculate_what_if_paths)):
            items = [scenario(f"valid_{index}") for index in range(9)] + [scenario("invalid", **changes)]
            snapshot = deepcopy((baseline, items))
            kwargs = {"annual_return_paths": None} if entry is calculate_what_if_paths else {}
            with (
                self.subTest(changes=changes, entry=entry.__name__),
                patch(ENGINE + "generate_return_paths") as generate,
                patch(ENGINE + "_validated_paths") as validate_paths,
                patch(ENGINE + "calculate_monte_carlo_paths") as calculate,
                self.assertRaises(ModelValidationError) as caught,
            ):
                entry(baseline, assumptions(), items, **kwargs)
            self.assertEqual(caught.exception.field_name, "scenarios[9].changes.retirement_expenses")
            generate.assert_not_called()
            validate_paths.assert_not_called()
            calculate.assert_not_called()
            self.assertEqual((baseline, items), snapshot)

    def test_already_retired_cannot_rewrite_retirement_through_comparison(self):
        baseline = profile(current_age=75, retirement_age=60, end_age=79,
                           retirement_expenses=(expense(1, 75, 79),))
        config = assumptions(annual_return_mean=0, annual_return_volatility=0, num_paths=1)
        output = calculate_what_if(baseline, config, (
            scenario("omitted", monthly_savings=999), scenario("restated", retirement_age=60),
        ))
        self.assertTrue(all(item.result == output.baseline for item in output.scenarios))
        for age in (59, 61, 75, 76):
            with (
                self.subTest(age=age), patch(ENGINE + "generate_return_paths") as generate,
                self.assertRaises(ModelValidationError) as caught,
            ):
                calculate_what_if(baseline, config, (scenario("valid"), scenario(retirement_age=age)))
            self.assertEqual(caught.exception.field_name, "scenarios[1].changes.retirement_age")
            generate.assert_not_called()

    def test_inactive_huge_amounts_and_zero_expense_skip_unused_numeric_work(self):
        baseline = profile(
            current_age=65, retirement_age=60, end_age=68, monthly_savings=10**400,
            retirement_expenses=(expense(10**400, 18, 64), expense(0, 65, 68)),
            pension_incomes=(PensionIncome(start_age=18, end_age=64, monthly_amount=10**400),
                            PensionIncome(start_age=69, end_age=120, monthly_amount=10**400)),
        )
        config = assumptions(annual_return_mean=0, annual_return_volatility=0,
                             annual_inflation_rate=1e308, num_paths=1)
        output = calculate_what_if(baseline, config, (
            scenario("unused_savings", monthly_savings=10**401),
            scenario("active_only", retirement_expenses=(expense(0, 65, 68),)),
        ))
        self.assertEqual(amounts(output.baseline.final_balance_percentiles), (120,) * 3)
        for item in output.scenarios:
            self.assertEqual(item.result, output.baseline)
            self.assertTrue(all(amounts(row.balance) == (0,) * 3 for row in item.asset_deltas))

    def test_first_and_final_age_depletion_histograms_remain_aligned(self):
        baseline = profile(current_age=100, retirement_age=100, end_age=120,
                           liquid_assets=0,
                           retirement_expenses=(expense(0, 100, 119), expense(1, 120, 120)))
        config = assumptions(annual_return_mean=0, annual_return_volatility=0,
                             annual_inflation_rate=0, num_paths=2)
        output = calculate_what_if(baseline, config, (
            scenario(retirement_expenses=(expense(1, 100, 120),)),
        ))
        item = output.scenarios[0]
        self.assertEqual(tuple(row.count for row in output.baseline.first_depletion_counts), (0,) * 20 + (2,))
        self.assertEqual(tuple(row.count for row in item.result.first_depletion_counts), (2,) + (0,) * 20)
        self.assertEqual(tuple(row.count for row in item.first_depletion_deltas), (2,) + (0,) * 19 + (-2,))
        self.assertEqual(item.success_probability_delta_pp, 0)


class WhatIfIndependenceEdgeTests(_Checks):
    def test_path_permutation_and_extra_scenarios_preserve_existing_comparisons(self):
        baseline, items, _ = comparison_cases()[0]
        config = assumptions(num_paths=7)
        paths = generate_return_paths(baseline, config)
        snapshot = deepcopy((baseline, items, paths))
        output = calculate_what_if_paths(baseline, config, items[:2], annual_return_paths=paths)
        extended = calculate_what_if_paths(baseline, config, items, annual_return_paths=tuple(reversed(paths)))
        self.assertEqual(output.baseline, extended.baseline)
        self.assertEqual(output.metadata, extended.metadata)
        self.assertEqual(output.scenarios, extended.scenarios[:2])
        self.assertEqual((baseline, items, paths), snapshot)

    def test_duplicate_paths_scale_counts_not_interpolated_quantile_differences(self):
        baseline = profile(current_age=59, end_age=60, monthly_savings=10,
                           retirement_expenses=(expense(10, 60, 60),))
        paths = ((0, -0.5), (0, 0), (0, 0.5))
        items = (scenario(monthly_savings=0),)
        outputs = tuple(calculate_what_if_paths(
            baseline, assumptions(num_paths=3 * copies, annual_inflation_rate=0), items,
            annual_return_paths=paths * copies,
        ) for copies in (1, 2))
        original, duplicate = (output.scenarios[0] for output in outputs)
        # These are marginal quantile differences, not quantiles of paired path changes.
        self.assert_amounts_close(original.final_balance_delta, (-72, -120, -168))
        paired_deltas = reference_percentiles([Decimal(-60), Decimal(-120), Decimal(-180)])
        self.assertNotEqual(amounts(original.final_balance_delta), tuple(map(float, paired_deltas)))
        self.assertEqual(amounts(duplicate.final_balance_delta), (-60, -120, -180))
        self.assertEqual(duplicate.result.success_count, original.result.success_count * 2)
        self.assertEqual(duplicate.success_count_delta, original.success_count_delta * 2)
        self.assertEqual(duplicate.success_probability_delta_pp, original.success_probability_delta_pp)
        self.assertEqual(tuple(row.count for row in duplicate.first_depletion_deltas),
                         tuple(row.count * 2 for row in original.first_depletion_deltas))

    def test_power_of_two_money_scaling_preserves_counts_and_scales_all_deltas(self):
        baseline, items, _ = comparison_cases()[0]
        config = assumptions(num_paths=7, annual_inflation_rate=0)
        paths = tuple((rate,) * 7 for rate in (-0.5, -0.25, 0, 0.0625, 0.125, 0.25, 0.5))
        scaled = replace(
            baseline, liquid_assets=baseline.liquid_assets * 8, monthly_savings=baseline.monthly_savings * 8,
            retirement_expenses=tuple(replace(row, monthly_amount=row.monthly_amount * 8)
                                      for row in baseline.retirement_expenses),
            pension_incomes=tuple(replace(row, monthly_amount=row.monthly_amount * 8)
                                 for row in baseline.pension_incomes),
        )
        scaled_items = tuple(replace(item, changes=replace(
            item.changes,
            monthly_savings=None if item.changes.monthly_savings is None else item.changes.monthly_savings * 8,
            retirement_expenses=None if item.changes.retirement_expenses is None else tuple(
                replace(row, monthly_amount=row.monthly_amount * 8) for row in item.changes.retirement_expenses
            ),
        )) for item in items)
        original = calculate_what_if_paths(baseline, config, items, annual_return_paths=paths)
        multiplied = calculate_what_if_paths(scaled, config, scaled_items, annual_return_paths=paths)
        for before, after in zip(original.scenarios, multiplied.scenarios):
            self.assertEqual(after.success_count_delta, before.success_count_delta)
            self.assertEqual(after.success_probability_delta_pp, before.success_probability_delta_pp)
            self.assertEqual(after.first_depletion_deltas, before.first_depletion_deltas)
            for old, new in zip(before.asset_deltas, after.asset_deltas):
                self.assertEqual(amounts(new.balance), tuple(value * 8 for value in amounts(old.balance)))
            self.assertEqual(amounts(after.final_shortfall_delta),
                             tuple(value * 8 for value in amounts(before.final_shortfall_delta)))

    def test_concurrent_independent_calls_match_serial_without_global_random_changes(self):
        baseline, items, _ = comparison_cases()[0]
        configs = tuple(assumptions(num_paths=31, seed=seed) for seed in (0, 97, 2**256 + 1))
        state = random.getstate()
        expected = tuple(calculate_what_if(baseline, config, items) for config in configs)
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(calculate_what_if, baseline, config, items) for config in configs * 3]
            self.assertEqual(tuple(future.result() for future in futures), expected * 3)
        self.assertEqual(random.getstate(), state)

    def test_failed_call_and_unrelated_random_draws_do_not_change_repeated_result(self):
        baseline, config = profile(), assumptions(num_paths=13)
        items = (scenario(retirement_expenses=(expense(5),)),)
        snapshot = deepcopy((baseline, config, items))
        state = random.getstate()
        try:
            expected = calculate_what_if(baseline, config, items)
            with self.assertRaises(WhatIfCalculationError):
                calculate_what_if(baseline, config, (scenario(retirement_expenses=(expense(10**400),)),))
            for _ in range(50):
                random.random()
            after_draws = random.getstate()
            self.assertEqual(calculate_what_if(baseline, config, items), expected)
            self.assertEqual(random.getstate(), after_draws)
            self.assertEqual((baseline, config, items), snapshot)
        finally:
            random.setstate(state)


class WhatIfNumericEdgeTests(_Checks):
    def test_tiny_negative_is_depleted_without_rounding_or_epsilon(self):
        baseline = profile(end_age=60, liquid_assets=12, retirement_expenses=(expense(1, 60, 60),))
        config = assumptions(num_paths=3, annual_inflation_rate=0)
        paths = ((0,), (-2**-52,), (0.5,))
        changes = {"retirement_expenses": (expense(0, 60, 60),)}
        output = calculate_what_if_paths(baseline, config, (scenario(**changes),), annual_return_paths=paths)
        self.assertEqual(output.baseline.depletion_count, 1)
        self.assertEqual(output.scenarios[0].result.depletion_count, 0)
        self.assertGreater(output.baseline.final_shortfall_percentiles.p90, 0)
        self.assertLess(output.baseline.final_shortfall_percentiles.p90, 1e-12)
        self.assert_comparison_reference(output, baseline, (replace(baseline, **changes),), config, paths)

    def test_negative_balance_and_recovery_do_not_earn_returns_or_erase_first_depletion(self):
        baseline = profile(liquid_assets=0, pension_incomes=(
            PensionIncome(start_age=61, end_age=61, monthly_amount=20),
            PensionIncome(start_age=63, end_age=63, monthly_amount=30),
        ))
        config = assumptions(num_paths=3, annual_inflation_rate=0)
        paths = tuple((1e308, 1e308, 1e308, 1e308, rate) for rate in (-0.5, 0, 0.5))
        output = calculate_what_if_paths(baseline, config, (
            scenario(retirement_expenses=(expense(20),)),
        ), annual_return_paths=paths)
        item = output.scenarios[0]
        for result in (output.baseline, item.result):
            self.assertEqual(result.success_count, 0)
            self.assertEqual(tuple(row.count for row in result.first_depletion_counts), (3, 0, 0, 0, 0))
        for row, value in zip(output.baseline.asset_percentiles[:4], (-120, 0, -120, 120)):
            self.assertEqual(amounts(row.balance), (value,) * 3)
        self.assert_amounts_close(output.baseline.final_balance_percentiles, (-48, 0, 48))
        self.assertEqual(amounts(item.result.final_balance_percentiles), (-600,) * 3)
        self.assert_amounts_close(output.baseline.final_shortfall_percentiles, (0, 0, 48))
        self.assert_amounts_close(item.final_shortfall_delta, (600, 600, 552))
        self.assertEqual(item.success_probability_delta_pp, 0)
        self.assertTrue(all(row.count == 0 for row in item.first_depletion_deltas))

    def test_return_immediately_above_minus_one_is_accepted_without_clipping(self):
        baseline = profile(end_age=60, liquid_assets=1, retirement_expenses=(expense(0, 60, 60),))
        config = assumptions(num_paths=1)
        rate = nextafter(-1.0, 0.0)
        output = calculate_what_if_paths(baseline, config, (scenario(),), annual_return_paths=((rate,),))
        self.assertEqual(amounts(output.baseline.final_balance_percentiles), (1.0 + rate,) * 3)
        self.assertEqual(output.baseline.success_count, 1)
        for invalid in (-1.0, nextafter(-1.0, -2.0)):
            with (
                self.subTest(rate=invalid), patch(ENGINE + "calculate_monte_carlo_paths") as calculate,
                self.assertRaises(ModelValidationError) as caught,
            ):
                calculate_what_if_paths(baseline, config, (scenario(),), annual_return_paths=((invalid,),))
            # Existing MC contract reports the path for r <= -1, the element for nonfinite/type errors.
            self.assertEqual(caught.exception.field_name, "annual_return_paths[0]")
            calculate.assert_not_called()

    def test_finite_extreme_and_subnormal_summary_deltas_match_exact_fraction(self):
        # Public output-model contract only: these summaries are not simulated market paths.
        def summary(value):
            gap = max(-value, 0.0)
            return MonteCarloResult(
                metadata=MonteCarloMetadata(current_age=60, end_age=60,
                                           assumptions=assumptions(num_paths=1), path_source="provided"),
                asset_percentiles=(AssetPercentiles(age=60, balance=AmountPercentiles(
                    p10=value, p50=value, p90=value,
                )),),
                first_depletion_counts=(FirstDepletionCount(age=60, count=int(value < 0)),),
                final_shortfall_percentiles=AmountPercentiles(p10=gap, p50=gap, p90=gap),
            )

        maximum, tiny = sys.float_info.max, nextafter(0.0, 1.0)
        pairs = ((nextafter(maximum, 0), maximum), (1.0, nextafter(1.0, 2.0)),
                 (0.0, tiny), (-maximum / 2, maximum / 2), (-maximum / 3, maximum / 3))
        for before, after in pairs:
            with self.subTest(before=before, after=after):
                item = WhatIfScenarioResult(scenario_id="changed", baseline=summary(before), result=summary(after))
                expected = float(Fraction(after) - Fraction(before))
                expected_gap = float(Fraction(max(-after, 0.0)) - Fraction(max(-before, 0.0)))
                self.assertEqual(amounts(item.final_balance_delta), (expected,) * 3)
                self.assertEqual(amounts(item.final_shortfall_delta), (expected_gap,) * 3)

    def test_real_finite_simulations_can_overflow_only_when_comparing(self):
        baseline = profile(end_age=61, liquid_assets=10**308,
                           retirement_expenses=(expense(10**307, 60, 61),))
        changed = replace(baseline, retirement_expenses=(expense(0, 60, 61),))
        config = assumptions(num_paths=1, annual_inflation_rate=0)
        paths = ((0, 0),)
        for source in (baseline, changed):
            separate = calculate_monte_carlo_paths(source, config, annual_return_paths=paths)
            self.assertTrue(all(isfinite(value) for row in separate.asset_percentiles for value in amounts(row.balance)))
        for before, after in ((baseline, changed), (changed, baseline)):
            with (
                self.subTest(direction=before is baseline),
                patch(ENGINE + "calculate_monte_carlo_paths", wraps=calculate_monte_carlo_paths) as calculate,
                self.assertRaises(WhatIfCalculationError) as caught,
            ):
                calculate_what_if_paths(before, config, (
                    scenario("valid"), scenario(retirement_expenses=after.retirement_expenses), scenario("not_run"),
                ), annual_return_paths=paths)
            error = caught.exception
            self.assertEqual((error.scope, error.scenario_index, error.stage, error.field_name, error.age, error.path_index),
                             ("scenario", 1, "comparison", "asset_deltas.p10", 61, None))
            self.assertEqual(calculate.call_count, 3)  # Baseline + first two changes; no partial return.
            self.assertNotIn(str(10**307), str(error))
            self.assertNotIn("changed", str(error))

    def test_actual_changed_only_cashflow_errors_report_stage_field_age_and_abort(self):
        retired = profile(liquid_assets=0, end_age=62, retirement_expenses=(expense(0, 60, 62),))
        working = profile(current_age=59, end_age=60, liquid_assets=0,
                          retirement_expenses=(expense(0, 60, 60),))
        cases = (
            (retired, 0, 1e200, {"retirement_expenses": (expense(1, 60, 62),)}, "inflation_factor", 62),
            (retired, 0, 1e220, {"retirement_expenses": (expense(10**100, 60, 62),)}, "expenses", 61),
            (retired, 0, 0, {"retirement_expenses": (expense(10**400, 60, 62),)}, "annual_expenses", 60),
            (working, 1e308, 0, {"monthly_savings": 1}, "investment_return", 60),
            (replace(working, liquid_assets=10**308), 0, 0, {"monthly_savings": 10**307}, "balance_after_savings", 59),
        )
        for baseline, mean, inflation, changes, field, age in cases:
            config = assumptions(annual_return_mean=mean, annual_return_volatility=0,
                                 annual_inflation_rate=inflation, num_paths=2)
            with (
                self.subTest(field=field),
                patch(ENGINE + "calculate_monte_carlo_paths", wraps=calculate_monte_carlo_paths) as calculate,
                self.assertRaises(WhatIfCalculationError) as caught,
            ):
                calculate_what_if(baseline, config, (
                    scenario("valid"), scenario("invalid", **changes), scenario("not_run"),
                ))
            error = caught.exception
            self.assertEqual((error.scope, error.scenario_index, error.stage, error.field_name, error.age, error.path_index),
                             ("scenario", 1, "cashflow", field, age, 0))
            self.assertEqual(calculate.call_count, 3)
            self.assertIsNone(error.__cause__)
            self.assertTrue(error.__suppress_context__)

    def test_failure_in_last_path_of_middle_scenario_retains_exact_location(self):
        baseline = profile(current_age=59, end_age=60, liquid_assets=0,
                           retirement_expenses=(expense(0, 60, 60),))
        paths = ((0, 0), (0, 0), (0, 1e308))
        with (
            patch(ENGINE + "calculate_monte_carlo_paths", wraps=calculate_monte_carlo_paths) as calculate,
            self.assertRaises(WhatIfCalculationError) as caught,
        ):
            calculate_what_if_paths(baseline, assumptions(num_paths=3), (
                scenario("valid"), scenario(monthly_savings=1), scenario("not_run"),
            ), annual_return_paths=paths)
        error = caught.exception
        self.assertEqual((error.scope, error.scenario_index, error.stage, error.field_name, error.age, error.path_index),
                         ("scenario", 1, "cashflow", "investment_return", 60, 2))
        self.assertEqual(calculate.call_count, 3)

    def test_unrepresentable_nonzero_volatility_fails_before_sampling_or_calculation(self):
        config = assumptions(annual_return_mean=1e308, annual_return_volatility=nextafter(0.0, 1.0))
        with (
            patch("retirement.return_paths.Random") as rng,
            patch(ENGINE + "calculate_monte_carlo_paths") as calculate,
            self.assertRaises(WhatIfCalculationError) as caught,
        ):
            calculate_what_if(profile(), config, (scenario(),))
        error = caught.exception
        self.assertEqual((error.scope, error.stage, error.field_name),
                         ("market", "return_generation", "log_return_volatility"))
        self.assertEqual((error.scenario_index, error.path_index, error.age), (None, None, None))
        rng.assert_not_called()
        calculate.assert_not_called()

    def test_invalid_last_random_draw_aborts_market_without_resampling(self):
        baseline = profile(end_age=61, retirement_expenses=(expense(0, 60, 61),))
        for shock, field in ((float("nan"), "standard_normal"), (1e5, "annual_returns"), (-1e5, "annual_returns")):
            with (
                self.subTest(shock=shock), patch("retirement.return_paths.Random") as rng,
                patch(ENGINE + "calculate_monte_carlo_paths") as calculate,
                self.assertRaises(WhatIfCalculationError) as caught,
            ):
                rng.return_value.gauss.side_effect = [0] * 5 + [shock]
                calculate_what_if(baseline, assumptions(num_paths=3), (scenario(),))
            error = caught.exception
            self.assertEqual((error.scope, error.stage, error.field_name, error.path_index, error.age),
                             ("market", "return_generation", field, 2, 61))
            self.assertIsNone(error.scenario_index)
            self.assertEqual(rng.return_value.gauss.call_count, 6)
            self.assertNotIn(str(shock), str(error))
            calculate.assert_not_called()

    def test_huge_last_supplied_rate_is_rejected_before_baseline_without_value_leak(self):
        baseline = profile(end_age=61, retirement_expenses=(expense(0, 60, 61),))
        huge = 10**400 + 123456789
        paths = [[0, 0], [0, 0], [0, huge]]
        snapshot = deepcopy(paths)
        with (
            patch(ENGINE + "generate_return_paths") as generate,
            patch(ENGINE + "calculate_monte_carlo_paths") as calculate,
            self.assertRaises(ModelValidationError) as caught,
        ):
            calculate_what_if_paths(baseline, assumptions(num_paths=3), (scenario(),), annual_return_paths=paths)
        self.assertEqual(caught.exception.field_name, "annual_return_paths[2][1]")
        self.assertNotIn(str(huge), str(caught.exception))
        self.assertEqual(paths, snapshot)
        generate.assert_not_called()
        calculate.assert_not_called()


class WhatIfCapacityEdgeTests(_Checks):
    def test_maximum_path_count_with_one_year_and_two_changes(self):
        # Contract boundary only; deliberately not maximum paths x years x scenarios.
        baseline = profile(end_age=60, retirement_expenses=(expense(10, 60, 60),))
        config = assumptions(num_paths=100_000, annual_inflation_rate=0)
        paths = ((-0.5,), (0,), (0.5,), (1,)) * 25_000
        output = calculate_what_if_paths(baseline, config, (
            scenario("half", retirement_expenses=(expense(5, 60, 60),)),
            scenario("zero", retirement_expenses=(expense(0, 60, 60),)),
        ), annual_return_paths=paths)
        self.assertEqual(output.baseline.success_count, 75_000)
        self.assertEqual(amounts(output.baseline.final_balance_percentiles), (-60, 30, 120))
        self.assertEqual(amounts(output.baseline.final_shortfall_percentiles), (0, 0, 60))
        for item, delta in zip(output.scenarios, (60, 120)):
            self.assertEqual(item.result.success_count, 100_000)
            self.assertEqual(item.success_count_delta, 25_000)
            self.assertEqual(item.success_probability_delta_pp, 25)
            self.assertEqual(item.first_depletion_deltas[0].count, -25_000)
            self.assertEqual(amounts(item.final_balance_delta), (delta,) * 3)
            self.assertEqual(amounts(item.final_shortfall_delta), (0, 0, -60))


if __name__ == "__main__":
    unittest.main()
