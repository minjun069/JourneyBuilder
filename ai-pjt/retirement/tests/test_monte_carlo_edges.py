"""Deterministic synthetic boundary checks, not market or service validation."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
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
    AmountPercentiles,
    ModelValidationError,
    MonteCarloCalculationError,
    PensionIncome,
    RetirementExpensePeriod,
    RetirementProfile,
    SimulationAssumptions,
    calculate_cashflow,
    calculate_cashflow_path,
    calculate_monte_carlo,
    calculate_monte_carlo_paths,
    generate_return_paths,
)
from retirement.monte_carlo import _linear_quantile  # noqa: E402


def expense(amount: int, start: int = 60, end: int = 62) -> RetirementExpensePeriod:
    return RetirementExpensePeriod(from_age=start, to_age=end, monthly_amount=amount)


def profile(**changes) -> RetirementProfile:
    return RetirementProfile(**({
        "current_age": 60, "retirement_age": 60, "end_age": 62,
        "liquid_assets": 120, "monthly_savings": 0,
        "retirement_expenses": (expense(10),), "pension_incomes": (),
    } | changes))


def assumptions(**changes) -> SimulationAssumptions:
    return SimulationAssumptions(**({
        "annual_return_mean": 0.05, "annual_return_volatility": 0.1,
        "annual_inflation_rate": 0.02, "num_paths": 17, "seed": 42,
    } | changes))


def financial_profiles() -> tuple[RetirementProfile, ...]:
    """Retirement transition, already retired, and retirement in the last year."""
    return (
        profile(current_age=59, retirement_age=61, end_age=65,
                liquid_assets=3_000_017, monthly_savings=140_003,
                retirement_expenses=(expense(170_001, 61, 62), expense(240_007, 63, 65)),
                pension_incomes=(
                    PensionIncome(start_age=60, end_age=63, monthly_amount=50_003),
                    PensionIncome(start_age=63, end_age=65, monthly_amount=100_003),
                )),
        profile(current_age=75, retirement_age=60, end_age=79,
                liquid_assets=0, monthly_savings=999_999,
                retirement_expenses=(expense(20_001, 60, 77), expense(10_007, 78, 79)),
                pension_incomes=(
                    PensionIncome(start_age=78, end_age=79, monthly_amount=100_003),
                )),
        profile(current_age=59, retirement_age=65, end_age=65,
                liquid_assets=90_000_019, monthly_savings=140_003,
                retirement_expenses=(expense(999_999, 59, 64), expense(240_007, 65, 65)),
                pension_incomes=(
                    PensionIncome(start_age=57, end_age=60, monthly_amount=50_003),
                    PensionIncome(start_age=66, end_age=70, monthly_amount=100_003),
                )),
    )


def reference_percentiles(values) -> tuple[Decimal, ...]:
    """Independent type 7 reference: exact decimal weights and 80-digit math."""
    with localcontext() as context:
        context.prec = 80
        ordered = sorted(Decimal(value) for value in values)
        result = []
        for q in (Decimal("0.1"), Decimal("0.5"), Decimal("0.9")):
            position = (len(ordered) - 1) * q
            index = int(position)
            weight = position - index
            result.append((1 - weight) * ordered[index]
                          + weight * ordered[min(index + 1, len(ordered) - 1)])
        return tuple(result)


def reference_batch(value: RetirementProfile, config: SimulationAssumptions, paths):
    """No production cashflow/result/quantile helpers participate in this oracle."""
    with localcontext() as context:
        context.prec = 80
        ages = range(value.current_age, value.end_age + 1)
        samples = [[] for _ in ages]
        counts = [0] * len(ages)
        shortfalls = []
        inflation = Decimal(1) + Decimal.from_float(config.annual_inflation_rate)
        for path in paths:
            balance = Decimal(value.liquid_assets)
            first = None
            for offset, age in enumerate(ages):
                saving = Decimal(value.monthly_savings * 12 if age < value.retirement_age else 0)
                pension = Decimal(sum(item.monthly_amount * 12 for item in value.pension_incomes
                                      if item.start_age <= age <= item.end_age))
                spending = Decimal(0)
                if age >= value.retirement_age:
                    monthly = next(item.monthly_amount for item in value.retirement_expenses
                                   if item.from_age <= age <= item.to_age)
                    spending = Decimal(monthly * 12) * inflation ** offset
                balance += (max(balance, Decimal(0)) * Decimal(path[offset])
                            + saving + pension - spending)
                samples[offset].append(balance)
                if balance < 0 and first is None:
                    first = offset
            if first is not None:
                counts[first] += 1
            shortfalls.append(max(-balance, Decimal(0)))
        return samples, counts, shortfalls


def amounts(value: AmountPercentiles) -> tuple[float, ...]:
    return value.p10, value.p50, value.p90


class _Assertions(unittest.TestCase):
    def assert_percentiles(self, actual: AmountPercentiles, expected) -> None:
        self.assertLessEqual(actual.p10, actual.p50)
        self.assertLessEqual(actual.p50, actual.p90)
        for amount, reference in zip(amounts(actual), expected):
            self.assertTrue(isfinite(amount))
            self.assertTrue(isclose(amount, float(reference), rel_tol=1e-10, abs_tol=1e-7),
                            f"{amount!r} differs from independent reference {reference!r}")

    def assert_reference(self, actual, value, config, paths) -> None:
        samples, counts, shortfalls = reference_batch(value, config, paths)
        self.assertEqual(tuple(point.age for point in actual.asset_percentiles),
                         tuple(range(value.current_age, value.end_age + 1)))
        self.assertEqual(tuple(item.age for item in actual.first_depletion_counts),
                         tuple(point.age for point in actual.asset_percentiles))
        self.assertEqual(tuple(item.count for item in actual.first_depletion_counts), tuple(counts))
        self.assertEqual(actual.num_paths, len(paths))
        self.assertEqual(actual.depletion_count, sum(counts))
        self.assertEqual(actual.success_count, len(paths) - sum(counts))
        self.assertEqual(actual.never_depleted_count, actual.success_count)
        self.assertEqual(actual.success_probability, actual.success_count / len(paths))
        self.assertEqual(actual.depletion_probability, actual.depletion_count / len(paths))
        for point, values in zip(actual.asset_percentiles, samples):
            self.assert_percentiles(point.balance, reference_percentiles(values))
        self.assertIs(actual.final_balance_percentiles, actual.asset_percentiles[-1].balance)
        self.assert_percentiles(actual.final_shortfall_percentiles, reference_percentiles(shortfalls))


class MonteCarloReferenceTests(_Assertions):
    def test_reference_itself_matches_hand_calculated_transition(self) -> None:
        value = profile(current_age=59, end_age=61, liquid_assets=120, monthly_savings=10,
                        retirement_expenses=(expense(10, 60, 60), expense(20, 61, 61)),
                        pension_incomes=(
                            PensionIncome(start_age=59, end_age=60, monthly_amount=5),
                            PensionIncome(start_age=60, end_age=61, monthly_amount=10),
                        ))
        config = assumptions(num_paths=2, annual_inflation_rate=0.25)
        paths = ((0, 0, 0), (-0.5, 0.5, -0.5))
        samples, counts, gaps = reference_batch(value, config, paths)
        # Each column uses a full year; contributions stop at 60, inflation does not reset.
        self.assertEqual(samples, [[300, 240], [330, 390], [75, -60]])
        self.assertEqual(counts, [0, 0, 1])
        self.assertEqual(gaps, [0, 60])
        self.assertEqual(reference_percentiles(samples[-1]),
                         (Decimal("-46.5"), Decimal("7.5"), Decimal("61.5")))
        self.assert_reference(calculate_monte_carlo_paths(value, config, annual_return_paths=paths),
                              value, config, paths)

    def test_explicit_financial_schedule_grid_matches_independent_decimal(self) -> None:
        successes, failures = 0, 0
        # 3 schedules x 3 inflation rates x 3 path counts x 3 local seeds = 81 batches.
        for value, inflation, count, seed in product(
            financial_profiles(), (-0.02, 0.0, 0.03), (1, 2, 17), (0, 13, 97),
        ):
            config = assumptions(annual_inflation_rate=inflation, num_paths=count, seed=seed)
            rng = random.Random(seed)
            paths = tuple(tuple(rng.choice((-0.375, -0.125, 0, 0.0625, 0.25, 0.5))
                                for _ in range(value.end_age - value.current_age + 1))
                          for _ in range(count))
            with self.subTest(age=value.current_age, inflation=inflation, count=count, seed=seed):
                result = calculate_monte_carlo_paths(value, config, annual_return_paths=paths)
                self.assert_reference(result, value, config, paths)
                successes += result.success_count
                failures += result.depletion_count
        self.assertGreater(successes, 0)
        self.assertGreater(failures, 0)

    def test_generated_batches_match_independent_cashflow_and_aggregation(self) -> None:
        # Distribution generation is covered separately; these 27 cases verify its consumers.
        for value, (mean, volatility), seed in product(
            financial_profiles(), ((-0.2, 0.1), (0.05, 0.2), (0.2, 0.1)), (0, 13, 97),
        ):
            config = assumptions(annual_return_mean=mean, annual_return_volatility=volatility,
                                 num_paths=19, seed=seed)
            with self.subTest(age=value.current_age, mean=mean, seed=seed):
                paths = generate_return_paths(value, config)
                self.assert_reference(calculate_monte_carlo(value, config), value, config, paths)

    def test_maximum_horizon_matches_reference_through_age_120(self) -> None:
        value = profile(current_age=18, retirement_age=100, end_age=120,
                        liquid_assets=1_001, monthly_savings=17,
                        retirement_expenses=(expense(37, 100, 109), expense(91, 110, 120)),
                        pension_incomes=(
                            PensionIncome(start_age=100, end_age=119, monthly_amount=17),
                            PensionIncome(start_age=120, end_age=120, monthly_amount=11),
                        ))
        config = assumptions(annual_return_mean=0.025, annual_return_volatility=0.05, num_paths=7)
        paths = generate_return_paths(value, config)
        result = calculate_monte_carlo(value, config)
        self.assertEqual(len(result.asset_percentiles), 103)
        self.assert_reference(result, value, config, paths)

    def test_zero_volatility_grid_is_bitwise_equal_to_fixed_cashflow(self) -> None:
        with patch("retirement.return_paths.Random") as rng_factory:
            for value, mean, inflation, count, seed in product(
                financial_profiles(), (-0.25, 0.0, 0.05), (-0.02, 0.0, 0.02),
                (1, 2, 13), (0, 2**256 + 1),
            ):
                config = assumptions(annual_return_mean=mean, annual_return_volatility=0.0,
                                     annual_inflation_rate=inflation, num_paths=count, seed=seed)
                with self.subTest(age=value.current_age, mean=mean, inflation=inflation,
                                  count=count, seed=seed):
                    fixed = calculate_cashflow(value, config)
                    result = calculate_monte_carlo(value, config)
                    self.assertEqual(result.success_count,
                                     count if fixed.first_depletion_age is None else 0)
                    for year, point in zip(fixed.years, result.asset_percentiles):
                        self.assertEqual(amounts(point.balance), (year.closing_balance,) * 3)
                    self.assertEqual(amounts(result.final_shortfall_percentiles),
                                     (fixed.final_shortfall,) * 3)
                    self.assertIsNone(result.metadata.seed)
        rng_factory.assert_not_called()

    def test_zero_negative_recovery_and_return_resumption_keep_first_failure(self) -> None:
        value = profile(end_age=64, liquid_assets=0, retirement_expenses=(expense(10, 60, 64),),
                        pension_incomes=(
                            PensionIncome(start_age=61, end_age=61, monthly_amount=20),
                            PensionIncome(start_age=63, end_age=63, monthly_amount=30),
                        ))
        config = assumptions(num_paths=3, annual_inflation_rate=0)
        # Huge rates must have no effect on nonpositive opening balances.
        paths = tuple((1e308,) * 4 + (last,) for last in (-0.5, 0, 0.5))
        result = calculate_monte_carlo_paths(value, config, annual_return_paths=paths)
        self.assertEqual(tuple(point.balance.p50 for point in result.asset_percentiles),
                         (-120, 0, -120, 120, 0))
        self.assertEqual(tuple(item.count for item in result.first_depletion_counts), (3, 0, 0, 0, 0))
        self.assertEqual(result.success_probability, 0)
        self.assertEqual(amounts(result.final_balance_percentiles), (-48, 0, 48))
        self.assert_percentiles(result.final_shortfall_percentiles, (0, 0, 48))


class MonteCarloInvariantTests(_Assertions):
    def test_permuting_whole_paths_does_not_change_summaries(self) -> None:
        value, config = financial_profiles()[0], assumptions()
        paths = generate_return_paths(value, config)
        shuffled = list(paths)
        random.Random(87).shuffle(shuffled)
        before = tuple(shuffled)
        original = calculate_monte_carlo_paths(value, config, annual_return_paths=paths)
        self.assertEqual(calculate_monte_carlo_paths(value, config, annual_return_paths=shuffled),
                         original)
        self.assertEqual(tuple(shuffled), before)

    def test_duplicate_paths_scale_counts_not_type7_interpolation_positions(self) -> None:
        value = profile(end_age=60)
        config = assumptions(num_paths=2, annual_inflation_rate=0)
        paths = ((-0.5,), (0.5,))
        original = calculate_monte_carlo_paths(value, config, annual_return_paths=paths)
        duplicated = calculate_monte_carlo_paths(value, replace(config, num_paths=4),
                                                annual_return_paths=paths * 2)
        self.assertEqual(duplicated.success_probability, original.success_probability)
        self.assertEqual(duplicated.depletion_count, original.depletion_count * 2)
        self.assertEqual(amounts(original.final_balance_percentiles), (-48, 0, 48))
        self.assertEqual(amounts(duplicated.final_balance_percentiles), (-60, 0, 60))

    def test_power_of_two_money_scaling_preserves_counts_and_scales_percentiles(self) -> None:
        value, config = financial_profiles()[0], assumptions()
        paths = generate_return_paths(value, config)
        original = calculate_monte_carlo_paths(value, config, annual_return_paths=paths)
        for scale in (2, 8):
            scaled = replace(
                value, liquid_assets=value.liquid_assets * scale,
                monthly_savings=value.monthly_savings * scale,
                retirement_expenses=tuple(
                    replace(item, monthly_amount=item.monthly_amount * scale)
                    for item in value.retirement_expenses
                ),
                pension_incomes=tuple(
                    replace(item, monthly_amount=item.monthly_amount * scale)
                    for item in value.pension_incomes
                ),
            )
            result = calculate_monte_carlo_paths(scaled, config, annual_return_paths=paths)
            self.assertEqual(result.first_depletion_counts, original.first_depletion_counts)
            for point, reference in zip(result.asset_percentiles, original.asset_percentiles):
                self.assertEqual(amounts(point.balance),
                                 tuple(x * scale for x in amounts(reference.balance)))
            self.assertEqual(amounts(result.final_shortfall_percentiles),
                             tuple(x * scale for x in amounts(original.final_shortfall_percentiles)))

    def test_explicit_horizon_prefix_preserves_earlier_balances_and_first_failures(self) -> None:
        value, config = financial_profiles()[0], assumptions()
        paths = generate_return_paths(value, config)
        original = calculate_monte_carlo_paths(value, config, annual_return_paths=paths)
        shortened = replace(value, end_age=62)
        prefix = tuple(path[:4] for path in paths)
        result = calculate_monte_carlo_paths(shortened, config, annual_return_paths=prefix)
        self.assertEqual(result.asset_percentiles, original.asset_percentiles[:4])
        self.assertEqual(result.first_depletion_counts, original.first_depletion_counts[:4])
        self.assertGreaterEqual(result.success_probability, original.success_probability)
        self.assert_reference(result, shortened, config, prefix)

    def test_partitioned_counts_add_up_but_quantiles_are_not_averaged(self) -> None:
        value, config = financial_profiles()[0], assumptions()
        paths = generate_return_paths(value, config)
        original = calculate_monte_carlo_paths(value, config, annual_return_paths=paths)
        left = calculate_monte_carlo_paths(value, replace(config, num_paths=5),
                                          annual_return_paths=paths[:5])
        right = calculate_monte_carlo_paths(value, replace(config, num_paths=12),
                                           annual_return_paths=paths[5:])
        self.assertEqual(original.success_count, left.success_count + right.success_count)
        self.assertEqual(original.success_probability, (left.success_count + right.success_count) / 17)
        self.assertEqual(tuple(item.count for item in original.first_depletion_counts),
                         tuple(a.count + b.count for a, b in zip(left.first_depletion_counts,
                                                               right.first_depletion_counts)))
        self.assert_reference(original, value, config, paths)

    def test_independent_concurrent_calls_match_serial_execution(self) -> None:
        value = financial_profiles()[0]
        configs = [assumptions(seed=seed, num_paths=23) for seed in (0, 42, 2**256 + 1)] * 3
        original_state = random.getstate()
        expected = [calculate_monte_carlo(value, config) for config in configs]
        with ThreadPoolExecutor(max_workers=3) as pool:
            actual = list(pool.map(lambda config: calculate_monte_carlo(value, config), configs))
        self.assertEqual(actual, expected)
        self.assertEqual(random.getstate(), original_state)

    def test_failure_and_unrelated_global_draws_do_not_change_a_repeated_run(self) -> None:
        value, config = financial_profiles()[0], assumptions()
        expected = calculate_monte_carlo(value, config)
        original_state = random.getstate()
        try:
            for _ in range(17):
                random.random()
            with self.assertRaises(MonteCarloCalculationError):
                calculate_monte_carlo(profile(liquid_assets=10**400), assumptions())
            self.assertEqual(calculate_monte_carlo(value, config), expected)
        finally:
            random.setstate(original_state)


class MonteCarloLimitTests(_Assertions):
    def test_maximum_path_count_uses_all_100000_generated_one_year_paths(self) -> None:
        value = profile(end_age=60, liquid_assets=12, retirement_expenses=(expense(1),))
        config = assumptions(num_paths=100_000, annual_inflation_rate=0)
        paths = generate_return_paths(value, config)
        result = calculate_monte_carlo(value, config)
        # Independent one-year identity: 12 + 12*r - 12 = 12*r, not an engine ledger.
        with localcontext() as context:
            context.prec = 80
            balances = [Decimal(12) * Decimal.from_float(path[0]) for path in paths]
            gaps = [max(-balance, Decimal(0)) for balance in balances]
        failures = sum(balance < 0 for balance in balances)
        self.assertEqual(result.num_paths, 100_000)
        self.assertGreater(failures, 0)
        self.assertLess(failures, 100_000)
        self.assertEqual(result.depletion_count, failures)
        self.assertEqual(result.success_probability, (100_000 - failures) / 100_000)
        self.assertEqual(tuple(item.count for item in result.first_depletion_counts), (failures,))
        self.assert_percentiles(result.final_balance_percentiles, reference_percentiles(balances))
        self.assert_percentiles(result.final_shortfall_percentiles, reference_percentiles(gaps))

    def test_invalid_path_counts_are_rejected_before_any_generation(self) -> None:
        with patch("retirement.monte_carlo.generate_return_paths") as generator:
            for count in (0, -1, 100_001, True, 1.0):
                with self.subTest(count=count):
                    with self.assertRaises(ModelValidationError) as caught:
                        calculate_monte_carlo(profile(), assumptions(num_paths=count))
                    self.assertEqual(caught.exception.field_name, "num_paths")
        generator.assert_not_called()

    def test_first_and_last_possible_depletion_ages_are_not_shifted(self) -> None:
        config = assumptions(num_paths=3, annual_inflation_rate=0)
        # Both the smallest and the largest accepted current age reach end_age=120.
        for start in (18, 100):
            length = 121 - start
            value = profile(current_age=start, retirement_age=start, end_age=120,
                            liquid_assets=12, retirement_expenses=(
                                expense(0, start, 119), expense(1, 120, 120),
                            ))
            paths = tuple((0,) * (length - 1) + (last,) for last in (-0.5, 0, 0.5))
            late = calculate_monte_carlo_paths(value, config, annual_return_paths=paths)
            self.assertEqual(tuple(item.count for item in late.first_depletion_counts),
                             (0,) * (length - 1) + (1,))
            self.assertEqual(late.first_depletion_counts[-1].age, 120)
            self.assertEqual(late.success_probability, 2 / 3)
            early_profile = replace(value, liquid_assets=0,
                                    retirement_expenses=(expense(1, start, 120),))
            early = calculate_monte_carlo_paths(early_profile, config, annual_return_paths=paths)
            self.assertEqual(tuple(item.count for item in early.first_depletion_counts),
                             (3,) + (0,) * (length - 1))
            self.assertEqual(early.first_depletion_counts[0].age, start)
            self.assertEqual(early.success_probability, 0)

    def test_return_just_above_total_loss_stays_positive_without_clipping(self) -> None:
        rate = nextafter(-1.0, 0.0)
        value = profile(end_age=60, liquid_assets=1, retirement_expenses=(expense(0),))
        config = assumptions(annual_return_mean=rate, annual_return_volatility=0, num_paths=3)
        generated = calculate_monte_carlo(value, config)
        supplied = calculate_monte_carlo_paths(value, config, annual_return_paths=((rate,),) * 3)
        for result in (generated, supplied):
            self.assertEqual(amounts(result.final_balance_percentiles), (1.0 + rate,) * 3)
            self.assertGreater(result.final_balance_percentiles.p10, 0)
            self.assertEqual(result.success_probability, 1)
        for invalid in (-1.0, nextafter(-1.0, -float("inf"))):
            with self.assertRaises(ModelValidationError):
                calculate_monte_carlo_paths(value, config, annual_return_paths=((invalid,),) * 3)

    def test_large_odd_even_shuffled_samples_match_closed_form_ranks(self) -> None:
        value = profile(end_age=60, liquid_assets=4096, retirement_expenses=(expense(256),))
        for count in (4096, 4097):
            paths = [((index - 2048) / 4096,) for index in range(count)]
            random.Random(51).shuffle(paths)
            before = tuple(paths)
            result = calculate_monte_carlo_paths(value, assumptions(num_paths=count),
                                                annual_return_paths=paths)
            # Binary-exact rates give integer balances 0-1024 through (N-1)-1024.
            expected = tuple(Decimal(-1024) + (count - 1) * Decimal(q)
                             for q in ("0.1", "0.5", "0.9"))
            self.assert_percentiles(result.final_balance_percentiles, expected)
            self.assert_percentiles(result.final_shortfall_percentiles,
                                    reference_percentiles([max(1024 - k, 0) for k in range(count)]))
            self.assertEqual(result.depletion_count, 1024)
            self.assertEqual(result.success_probability, (count - 1024) / count)
            self.assertEqual(tuple(paths), before)

    def test_extreme_and_subnormal_interpolation_matches_exact_fraction(self) -> None:
        maximum, tiny = sys.float_info.max, nextafter(0.0, 1.0)
        for values in (
            [-maximum, -maximum / 2, 0, maximum / 2, maximum],
            [-3 * tiny, -tiny, -0.0, 0.0, tiny, 3 * tiny],
            [nextafter(1.0, 0.0), 1.0, nextafter(1.0, 2.0)],
        ):
            observed = []
            # Dyadic q keeps the rank exact; Fraction does not round intermediate terms.
            for step in range(33):
                q = Fraction(step, 32)
                rank = (len(values) - 1) * q
                index = rank.numerator // rank.denominator
                weight = rank - index
                reference = float((1 - weight) * Fraction(values[index])
                                  + weight * Fraction(values[min(index + 1, len(values) - 1)]))
                actual = _linear_quantile(values, float(q))
                self.assertTrue(isfinite(actual))
                self.assertGreaterEqual(actual, values[0])
                self.assertLessEqual(actual, values[-1])
                self.assertTrue(isclose(actual, reference, rel_tol=1e-14, abs_tol=tiny))
                observed.append(actual)
            self.assertEqual(observed, sorted(observed))

    def test_late_huge_integer_and_sensitive_invalid_rates_are_validated_upfront(self) -> None:
        with patch("retirement.monte_carlo.calculate_cashflow_path") as engine:
            for invalid in (10**400, "private-customer-value", -float("inf"), {"private": 321}):
                paths = ((0, 0, 0), (0, 0, invalid))
                with self.assertRaises(ModelValidationError) as caught:
                    calculate_monte_carlo_paths(profile(), assumptions(num_paths=2),
                                                annual_return_paths=paths)
                self.assertEqual(caught.exception.field_name, "annual_return_paths[1][2]")
                self.assertNotIn(str(invalid), str(caught.exception))
                self.assertNotIn("private", repr(caught.exception))
        engine.assert_not_called()


class MonteCarloFailureTests(_Assertions):
    def test_unrepresentable_positive_volatility_fails_before_rng_or_cashflow(self) -> None:
        config = assumptions(annual_return_mean=1e308,
                             annual_return_volatility=nextafter(0.0, 1.0))
        with patch("retirement.return_paths.Random") as rng_factory:
            with patch("retirement.monte_carlo.calculate_cashflow_path") as engine:
                with self.assertRaises(MonteCarloCalculationError) as caught:
                    calculate_monte_carlo(profile(), config)
        error = caught.exception
        self.assertEqual((error.stage, error.field_name, error.path_index, error.age),
                         ("return_generation", "log_return_volatility", None, None))
        rng_factory.assert_not_called()
        engine.assert_not_called()

    def test_late_invalid_generated_shock_aborts_without_resampling_or_cashflow(self) -> None:
        for shock, field in ((float("nan"), "standard_normal"),
                             (1e5, "annual_returns"), (-1e5, "annual_returns")):
            with self.subTest(field=field, shock=shock):
                with patch("retirement.return_paths.Random") as rng_factory:
                    rng = rng_factory.return_value
                    rng.gauss.side_effect = [0, 0, 0, shock, 0, 0]
                    with patch("retirement.monte_carlo.calculate_cashflow_path") as engine:
                        with self.assertRaises(MonteCarloCalculationError) as caught:
                            calculate_monte_carlo(profile(end_age=61), assumptions(num_paths=3))
                error = caught.exception
                self.assertEqual((error.stage, error.field_name, error.path_index, error.age),
                                 ("return_generation", field, 1, 61))
                self.assertEqual(rng.gauss.call_count, 4)
                self.assertTrue(error.__suppress_context__)
                engine.assert_not_called()

    def test_all_cashflow_overflow_stages_propagate_through_monte_carlo(self) -> None:
        fixed = assumptions(annual_return_mean=0, annual_return_volatility=0, num_paths=3,
                            annual_inflation_rate=0)
        cases = (
            (profile(liquid_assets=10**400), fixed, "liquid_assets", None),
            (profile(current_age=59, monthly_savings=10**400), fixed, "savings", 59),
            (profile(pension_incomes=(PensionIncome(start_age=60, end_age=60,
                                                  monthly_amount=10**400),)),
             fixed, "pension_income", 60),
            (profile(retirement_expenses=(expense(10**400),)), fixed, "annual_expenses", 60),
            (profile(), replace(fixed, annual_inflation_rate=1e200), "inflation_factor", 62),
            (profile(retirement_expenses=(expense(10**100),)),
             replace(fixed, annual_inflation_rate=1e220), "expenses", 61),
            (profile(liquid_assets=10**308), replace(fixed, annual_return_mean=10),
             "investment_return", 60),
            (profile(liquid_assets=10**308), replace(fixed, annual_return_mean=1),
             "balance_after_return", 60),
            (profile(current_age=59, liquid_assets=10**308, monthly_savings=10**307),
             fixed, "balance_after_savings", 59),
            (profile(liquid_assets=10**308, pension_incomes=(
                PensionIncome(start_age=60, end_age=60, monthly_amount=10**307),)),
             fixed, "balance_after_pension", 60),
            (profile(liquid_assets=0, retirement_expenses=(expense(10**307),)),
             fixed, "closing_balance", 61),
        )
        for value, config, field, age in cases:
            with self.subTest(field=field):
                result = None
                with self.assertRaises(MonteCarloCalculationError) as caught:
                    result = calculate_monte_carlo(value, config)
                error = caught.exception
                self.assertIsNone(result)
                self.assertEqual((error.stage, error.field_name, error.path_index, error.age),
                                 ("cashflow", field, 0, age))
                self.assertTrue(error.__suppress_context__)
                self.assertNotIn(str(10**100), str(error))
                self.assertNotIn(str(10**100), repr(error))

    def test_late_path_and_year_overflow_returns_no_partial_aggregation(self) -> None:
        value = profile(liquid_assets=10**308, retirement_expenses=(expense(0),))
        paths = ((0, 0, 0), (0, 0, 0), (0, 1, 0), (0, 0, 0))
        with patch("retirement.monte_carlo.calculate_cashflow_path",
                   wraps=calculate_cashflow_path) as engine:
            with patch("retirement.monte_carlo._linear_quantile") as quantile:
                with self.assertRaises(MonteCarloCalculationError) as caught:
                    calculate_monte_carlo_paths(value, assumptions(num_paths=4),
                                                annual_return_paths=paths)
        error = caught.exception
        self.assertEqual((error.stage, error.field_name, error.path_index, error.age),
                         ("cashflow", "balance_after_return", 2, 61))
        self.assertEqual(engine.call_count, 3)
        quantile.assert_not_called()

    def test_unused_extreme_flows_and_inflation_do_not_cause_false_failures(self) -> None:
        value = profile(liquid_assets=0, monthly_savings=10**400,
                        retirement_expenses=(expense(0),), pension_incomes=(
                            PensionIncome(start_age=1, end_age=59, monthly_amount=10**400),
                            PensionIncome(start_age=63, end_age=65, monthly_amount=10**400),
                        ))
        config = assumptions(annual_return_mean=1e308, annual_return_volatility=0,
                             annual_inflation_rate=1e308, num_paths=3)
        results = (
            calculate_monte_carlo(value, config),
            calculate_monte_carlo_paths(value, config, annual_return_paths=((1e308,) * 3,) * 3),
        )
        for result in results:
            self.assertEqual(result.success_probability, 1)
            self.assertEqual(tuple(point.balance.p50 for point in result.asset_percentiles), (0, 0, 0))
            self.assertEqual(amounts(result.final_shortfall_percentiles), (0, 0, 0))

    def test_later_age_and_shortfall_aggregation_failures_remain_whole_run_errors(self) -> None:
        for failure_call, field, age in ((4, "asset_percentiles", 61),
                                         (10, "final_shortfall_percentiles", 62)):
            with self.subTest(field=field):
                calls = 0

                def fail_one_interpolation(values, q):
                    nonlocal calls
                    calls += 1
                    return float("nan") if calls == failure_call else _linear_quantile(values, q)

                result = None
                with patch("retirement.monte_carlo._linear_quantile",
                           side_effect=fail_one_interpolation):
                    with patch("retirement.monte_carlo.calculate_cashflow_path",
                               wraps=calculate_cashflow_path) as engine:
                        with self.assertRaises(MonteCarloCalculationError) as caught:
                            result = calculate_monte_carlo(profile(), assumptions(num_paths=3))
                error = caught.exception
                self.assertIsNone(result)
                self.assertEqual((error.stage, error.field_name, error.path_index, error.age),
                                 ("aggregation", field, None, age))
                self.assertEqual(engine.call_count, 3)


if __name__ == "__main__":
    unittest.main()
