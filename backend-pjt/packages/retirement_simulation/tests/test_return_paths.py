from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal, localcontext
from itertools import product
from math import exp, expm1, isclose, isfinite, nextafter, sqrt
from pathlib import Path
import random
from statistics import fmean, pstdev
import subprocess
import sys
import unittest
from unittest.mock import call, patch


AI_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(AI_PROJECT_ROOT))

from retirement_simulation import (  # noqa: E402
    ModelValidationError,
    PensionIncome,
    RetirementExpensePeriod,
    RetirementProfile,
    ReturnPathGenerationError,
    SimulationAssumptions,
    calculate_cashflow,
    calculate_cashflow_path,
    generate_return_paths,
)
from retirement_simulation.return_paths import _lognormal_parameters  # noqa: E402


def profile(**changes) -> RetirementProfile:
    return RetirementProfile(**({
        "current_age": 60, "retirement_age": 60, "end_age": 63,
        "liquid_assets": 1_200_000, "monthly_savings": 100_000,
        "retirement_expenses": (
            RetirementExpensePeriod(from_age=60, to_age=63, monthly_amount=100_000),
        ),
        "pension_incomes": (),
    } | changes))


def assumptions(**changes) -> SimulationAssumptions:
    return SimulationAssumptions(**({
        "annual_return_mean": 0.05, "annual_return_volatility": 0.1,
        "annual_inflation_rate": 0.0, "num_paths": 4, "seed": 42,
    } | changes))


def reference_parameters(mean: float, volatility: float) -> tuple[float, float]:
    # Independent high-precision direct formula, not the production branches.
    with localcontext() as context:
        context.prec = 80
        gross_mean = Decimal(1) + Decimal.from_float(mean)
        ratio = Decimal.from_float(volatility) / gross_mean
        variance = (Decimal(1) + ratio * ratio).ln()
        return float(gross_mean.ln() - variance / 2), float(variance.sqrt())


class ReturnParameterTests(unittest.TestCase):
    def test_documented_parameters_preserve_simple_return_moments(self) -> None:
        mu, sigma = _lognormal_parameters(0.05, 0.10)
        self.assertAlmostEqual(mu, 0.044275460810, delta=5e-13)
        self.assertAlmostEqual(sigma, 0.095023190429, delta=5e-13)
        self.assertAlmostEqual(expm1(mu + sigma * sigma / 2), 0.05, delta=1e-14)
        self.assertAlmostEqual(
            sqrt(exp(2 * mu + sigma * sigma) * expm1(sigma * sigma)),
            0.10, delta=1e-14,
        )

    def test_parameter_grid_matches_independent_decimal_reference(self) -> None:
        for mean, volatility in product((-0.9, -0.2, 0.0, 0.05, 0.5, 3.0),
                                        (0.001, 0.1, 0.5, 1.0, 5.0)):
            with self.subTest(mean=mean, volatility=volatility):
                actual = _lognormal_parameters(mean, volatility)
                expected = reference_parameters(mean, volatility)
                for value, reference in zip(actual, expected):
                    self.assertTrue(isclose(value, reference, rel_tol=5e-13, abs_tol=1e-16))

    def test_large_ratios_do_not_overflow_during_parameter_conversion(self) -> None:
        for mean, volatility in ((nextafter(-1.0, 0.0), 1e308), (0.0, 1e200),
                                 (1e300, 1e300)):
            with self.subTest(mean=mean, volatility=volatility):
                actual = _lognormal_parameters(mean, volatility)
                expected = reference_parameters(mean, volatility)
                for value, reference in zip(actual, expected):
                    self.assertTrue(isfinite(value))
                    self.assertTrue(isclose(value, reference, rel_tol=5e-13))

    def test_small_positive_volatility_survives_squared_ratio_underflow(self) -> None:
        for volatility in (1e-8, 1e-160, 1e-200, nextafter(0.0, 1.0)):
            with self.subTest(volatility=volatility):
                mu, sigma = _lognormal_parameters(0.0, volatility)
                self.assertTrue(isfinite(mu))
                self.assertEqual(sigma, volatility)
                self.assertGreater(sigma, 0.0)

    def test_unrepresentable_positive_stddev_fails_before_rng_creation(self) -> None:
        config = assumptions(annual_return_mean=sys.float_info.max,
                             annual_return_volatility=nextafter(0.0, 1.0))
        with patch("retirement_simulation.return_paths.Random") as rng_factory:
            with self.assertRaises(ReturnPathGenerationError) as caught:
                generate_return_paths(profile(), config)
        rng_factory.assert_not_called()
        self.assertEqual(caught.exception.field_name, "log_return_volatility")
        self.assertIsNone(caught.exception.path_index)
        self.assertIsNone(caught.exception.age)


class ReturnPathBasicTests(unittest.TestCase):
    def test_shape_and_values_match_count_and_inclusive_horizon(self) -> None:
        paths = generate_return_paths(profile(end_age=62), assumptions(num_paths=5))
        self.assertIsInstance(paths, tuple)
        self.assertEqual(len(paths), 5)
        for path in paths:
            self.assertIsInstance(path, tuple)
            self.assertEqual(len(path), 3)
            for rate in path:
                self.assertIsInstance(rate, float)
                self.assertTrue(isfinite(rate))
                self.assertGreater(rate, -1.0)

    def test_controlled_shocks_follow_path_then_age_order_without_adding_mean(self) -> None:
        shocks = (0.0, 1.0, -1.0, 0.5, -0.5, 2.0)
        config = assumptions(num_paths=2)
        with patch("retirement_simulation.return_paths.Random", autospec=True) as rng_factory:
            rng = rng_factory.return_value
            rng.gauss.side_effect = shocks
            paths = generate_return_paths(profile(end_age=62), config)
        rng_factory.assert_called_once_with(config.seed)
        self.assertEqual(rng.gauss.call_args_list, [call(0.0, 1.0)] * 6)
        mu, sigma = reference_parameters(0.05, 0.1)
        for index, shock in enumerate(shocks):
            self.assertAlmostEqual(paths[index // 3][index % 3],
                                   expm1(mu + sigma * shock), delta=1e-14)

    def test_real_rng_advances_across_odd_length_paths(self) -> None:
        config = assumptions(seed=123)
        paths = generate_return_paths(profile(end_age=62), config)
        reference_rng = random.Random(config.seed)
        mu, sigma = reference_parameters(0.05, 0.1)
        for path in paths:
            for rate in path:
                expected = expm1(mu + sigma * reference_rng.gauss(0.0, 1.0))
                self.assertAlmostEqual(rate, expected, delta=1e-14)
        self.assertNotEqual(paths[0], paths[1])

    def test_repeat_calls_and_intervening_customer_calls_are_reproducible(self) -> None:
        value, config = profile(), assumptions()
        expected = generate_return_paths(value, config)
        generate_return_paths(profile(current_age=61), assumptions(seed=777, num_paths=7))
        self.assertEqual(generate_return_paths(value, config), expected)

    def test_local_rng_does_not_consume_or_depend_on_global_state(self) -> None:
        original_state = random.getstate()
        try:
            random.seed(12345)
            before = random.getstate()
            first = generate_return_paths(profile(), assumptions())
            self.assertEqual(random.getstate(), before)
            random.seed(98765)
            random.gauss(0.0, 1.0)
            before = random.getstate()
            self.assertEqual(generate_return_paths(profile(), assumptions()), first)
            self.assertEqual(random.getstate(), before)
        finally:
            random.setstate(original_state)

    def test_different_fixed_seeds_change_nonzero_volatility_paths(self) -> None:
        first = generate_return_paths(profile(), assumptions(seed=0))
        second = generate_return_paths(profile(), assumptions(seed=1))
        self.assertNotEqual(first, second)

    def test_zero_volatility_repeats_exact_mean_without_rng_or_conversion(self) -> None:
        for mean, seed in product((-0.5, 0.0, 0.1, nextafter(-1.0, 0.0),
                                   sys.float_info.max), (0, 42)):
            config = assumptions(annual_return_mean=mean, annual_return_volatility=0.0,
                                 num_paths=3, seed=seed)
            with self.subTest(mean=mean, seed=seed):
                with patch("retirement_simulation.return_paths.Random") as rng_factory, patch(
                    "retirement_simulation.return_paths._lognormal_parameters",
                ) as conversion:
                    paths = generate_return_paths(profile(), config)
                self.assertEqual(paths, ((mean,) * 4,) * 3)
                rng_factory.assert_not_called()
                conversion.assert_not_called()

    def test_zero_volatility_paths_match_cashflow_examples_a_and_c(self) -> None:
        example_a = profile(
            current_age=59, retirement_age=60, end_age=61,
            liquid_assets=10_000_000, monthly_savings=100_000,
            retirement_expenses=(
                RetirementExpensePeriod(from_age=60, to_age=60, monthly_amount=100_000),
                RetirementExpensePeriod(from_age=61, to_age=61, monthly_amount=200_000),
            ),
            pension_incomes=(PensionIncome(start_age=60, end_age=60, monthly_amount=50_000),),
        )
        example_c = profile(monthly_savings=0, pension_incomes=(
            PensionIncome(start_age=62, end_age=63, monthly_amount=300_000),
        ))
        for value, mean, balances, depletion_age in (
            (example_a, 0.1, (12_200_000, 12_820_000, 11_702_000), None),
            (example_c, 0.0, (0, -1_200_000, 1_200_000, 3_600_000), 61),
        ):
            with self.subTest(mean=mean):
                config = assumptions(annual_return_mean=mean, annual_return_volatility=0.0)
                fixed = calculate_cashflow(value, config)
                for rates in generate_return_paths(value, config):
                    result = calculate_cashflow_path(value, config, annual_returns=rates)
                    self.assertEqual(result, fixed)
                    self.assertEqual(tuple(year.closing_balance for year in result.years),
                                     balances)
                    self.assertEqual(result.first_depletion_age, depletion_age)

    def test_customer_finances_retirement_and_inflation_do_not_change_paths(self) -> None:
        value, config = profile(), assumptions()
        expected = generate_return_paths(value, config)
        for changed in (
            replace(value, liquid_assets=0),
            replace(value, liquid_assets=10 ** 400),
            replace(value, monthly_savings=10 ** 400),
            replace(value, retirement_age=62),
            replace(value, retirement_age=58),
            replace(value, retirement_expenses=(
                RetirementExpensePeriod(from_age=60, to_age=63, monthly_amount=0),
            )),
            replace(value, pension_incomes=(
                PensionIncome(start_age=60, end_age=63, monthly_amount=10 ** 400),
            )),
        ):
            self.assertEqual(generate_return_paths(changed, config), expected)
        self.assertEqual(generate_return_paths(
            value, replace(config, annual_inflation_rate=1e308),
        ), expected)

    def test_increasing_path_count_preserves_existing_prefix(self) -> None:
        value = profile(end_age=62)
        smaller = generate_return_paths(value, assumptions(num_paths=3))
        larger = generate_return_paths(value, assumptions(num_paths=7))
        self.assertEqual(smaller, larger[:3])

    def test_one_year_and_maximum_horizon_have_exact_path_lengths(self) -> None:
        for value, length in (
            (profile(end_age=60), 1),
            (profile(current_age=18, retirement_age=100, end_age=120,
                     retirement_expenses=(
                         RetirementExpensePeriod(from_age=100, to_age=120, monthly_amount=0),
                     )), 103),
        ):
            paths = generate_return_paths(value, assumptions(num_paths=1))
            self.assertEqual(len(paths), 1)
            self.assertEqual(len(paths[0]), length)

    def test_maximum_path_count_is_not_silently_reduced(self) -> None:
        paths = generate_return_paths(profile(end_age=60), assumptions(
            annual_return_volatility=0.0, num_paths=100_000,
        ))
        self.assertEqual(len(paths), 100_000)
        self.assertEqual(paths[0], (0.05,))
        self.assertEqual(paths[-1], (0.05,))

    def test_default_count_and_seed_are_used(self) -> None:
        config = SimulationAssumptions(
            annual_return_mean=0.05, annual_return_volatility=0.1, annual_inflation_rate=0.0,
        )
        paths = generate_return_paths(profile(), config)
        self.assertEqual(len(paths), 1_000)
        self.assertEqual(paths[:4], generate_return_paths(profile(), assumptions(seed=0)))

    def test_input_and_nested_output_are_immutable_and_reusable(self) -> None:
        value, config = profile(), assumptions()
        original = deepcopy((value, config))
        paths = generate_return_paths(value, config)
        before = tuple(tuple(path) for path in paths)
        with self.assertRaises(TypeError):
            paths[0] = ()
        with self.assertRaises(TypeError):
            paths[0][0] = 0.5
        result = calculate_cashflow_path(value, config, annual_returns=paths[0])
        self.assertEqual(tuple(year.age for year in result.years), (60, 61, 62, 63))
        self.assertEqual(tuple(year.annual_return_rate for year in result.years), paths[0])
        self.assertEqual(paths, before)
        self.assertEqual((value, config), original)

    def test_invalid_model_types_are_rejected_before_rng_creation(self) -> None:
        with patch("retirement_simulation.return_paths.Random") as rng_factory:
            for invalid in (None, True, {}, [], "not-a-model"):
                for field_name, value, config in (
                    ("profile", invalid, assumptions()),
                    ("assumptions", profile(), invalid),
                ):
                    with self.subTest(field=field_name, invalid_type=type(invalid).__name__):
                        with self.assertRaises(ModelValidationError) as caught:
                            generate_return_paths(value, config)
                        self.assertEqual(caught.exception.field_name, field_name)
            rng_factory.assert_not_called()

    def test_fixed_seed_sample_moments_are_reasonable_not_forced_exact(self) -> None:
        config = assumptions(num_paths=5_000, seed=20260908)
        values = [rate for path in generate_return_paths(profile(), config) for rate in path]
        self.assertEqual(len(values), 20_000)
        self.assertAlmostEqual(fmean(values), 0.05, delta=0.004)
        self.assertAlmostEqual(pstdev(values), 0.1, delta=0.004)
        self.assertNotEqual(fmean(values), 0.05)

    def test_package_generator_runs_without_site_packages(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-S", "-B", "-c", """
from retirement_simulation import RetirementExpensePeriod, RetirementProfile
from retirement_simulation import SimulationAssumptions, generate_return_paths
p = RetirementProfile(current_age=60, retirement_age=60, end_age=60,
    liquid_assets=0, monthly_savings=0, pension_incomes=(), retirement_expenses=(
        RetirementExpensePeriod(from_age=60, to_age=60, monthly_amount=0),))
a = SimulationAssumptions(annual_return_mean=0.05, annual_return_volatility=0.1,
    annual_inflation_rate=0.0, num_paths=2)
paths = generate_return_paths(p, a)
assert len(paths) == 2 and all(len(path) == 1 for path in paths)
assert all(path[0] > -1 for path in paths)
"""], cwd=AI_PROJECT_ROOT, capture_output=True, text=True, check=True,
        )
        self.assertEqual(completed.stdout, "")
        self.assertEqual(completed.stderr, "")


class ReturnPathNumericTests(unittest.TestCase):
    def test_late_total_loss_rounding_aborts_without_resampling_or_partial_batch(self) -> None:
        result = None
        with patch("retirement_simulation.return_paths.Random", autospec=True) as rng_factory:
            rng = rng_factory.return_value
            rng.gauss.side_effect = (0.0, 0.0, 0.0, 0.0, 0.0, -1000.0, 0.0)
            with self.assertRaises(ReturnPathGenerationError) as caught:
                result = generate_return_paths(profile(liquid_assets=987_654_321), assumptions())
        self.assertIsNone(result)
        self.assertEqual(rng.gauss.call_count, 6)
        self.assertEqual(caught.exception.field_name, "annual_returns")
        self.assertEqual(caught.exception.path_index, 1)
        self.assertEqual(caught.exception.age, 61)
        self.assertNotIn("987654321", str(caught.exception))
        self.assertNotIn("-1000", str(caught.exception))

    def test_exponential_overflow_is_a_safe_generation_error(self) -> None:
        with patch("retirement_simulation.return_paths.Random", autospec=True) as rng_factory:
            rng_factory.return_value.gauss.return_value = 100_000.0
            with self.assertRaises(ReturnPathGenerationError) as caught:
                generate_return_paths(profile(), assumptions())
        self.assertEqual(caught.exception.field_name, "annual_returns")
        self.assertEqual((caught.exception.path_index, caught.exception.age), (0, 60))
        self.assertTrue(caught.exception.__suppress_context__)
        self.assertNotIn("100000", str(caught.exception))

    def test_nonfinite_shocks_are_rejected(self) -> None:
        for shock in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(shock=shock):
                with patch("retirement_simulation.return_paths.Random", autospec=True) as rng_factory:
                    rng_factory.return_value.gauss.return_value = shock
                    with self.assertRaises(ReturnPathGenerationError) as caught:
                        generate_return_paths(profile(), assumptions())
                self.assertEqual(caught.exception.field_name, "standard_normal")

    def test_nonfinite_log_return_is_rejected(self) -> None:
        with patch("retirement_simulation.return_paths.Random", autospec=True) as rng_factory:
            rng_factory.return_value.gauss.return_value = sys.float_info.max
            with self.assertRaises(ReturnPathGenerationError) as caught:
                generate_return_paths(profile(), assumptions(annual_return_volatility=10.0))
        self.assertEqual(caught.exception.field_name, "log_return")

    def test_nonfinite_transformed_returns_are_rejected(self) -> None:
        for rate in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(rate=rate):
                with patch("retirement_simulation.return_paths.expm1", return_value=rate):
                    with self.assertRaises(ReturnPathGenerationError) as caught:
                        generate_return_paths(profile(), assumptions())
                self.assertEqual(caught.exception.field_name, "annual_returns")

    def test_actual_extreme_input_fails_instead_of_clipping_returns(self) -> None:
        config = assumptions(annual_return_mean=nextafter(-1.0, 0.0),
                             annual_return_volatility=1e308)
        with self.assertRaises(ReturnPathGenerationError) as caught:
            generate_return_paths(profile(), config)
        self.assertEqual(caught.exception.field_name, "annual_returns")
        self.assertEqual((caught.exception.path_index, caught.exception.age), (0, 60))

    def test_representable_tiny_volatility_still_draws_a_shock(self) -> None:
        tiny = nextafter(0.0, 1.0)
        config = assumptions(annual_return_mean=0.0, annual_return_volatility=tiny, num_paths=1)
        with patch("retirement_simulation.return_paths.Random", autospec=True) as rng_factory:
            rng_factory.return_value.gauss.return_value = 1.0
            paths = generate_return_paths(profile(end_age=60), config)
        rng_factory.return_value.gauss.assert_called_once_with(0.0, 1.0)
        self.assertEqual(paths, ((tiny,),))


if __name__ == "__main__":
    unittest.main()
