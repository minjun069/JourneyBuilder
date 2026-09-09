from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal, localcontext
from math import isclose, nextafter
from pathlib import Path
from platform import python_implementation, python_version
import random
from statistics import quantiles
import subprocess
import sys
import unittest
from unittest.mock import call, patch


AI_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(AI_PROJECT_ROOT))

from retirement_simulation import (  # noqa: E402
    AmountPercentiles,
    AssetPercentiles,
    FirstDepletionCount,
    ModelValidationError,
    MonteCarloCalculationError,
    MonteCarloMetadata,
    MonteCarloResult,
    PensionIncome,
    RetirementExpensePeriod,
    RetirementProfile,
    ReturnPathGenerationError,
    SimulationAssumptions,
    calculate_cashflow,
    calculate_cashflow_path,
    calculate_monte_carlo,
    calculate_monte_carlo_paths,
    generate_return_paths,
)
from retirement_simulation.monte_carlo import _linear_quantile, _percentiles  # noqa: E402


EXAMPLE_PATHS = ((-0.5, 0.0), (0.0, 0.0), (0.5, 0.0), (1.0, 0.0))


def profile(**changes) -> RetirementProfile:
    return RetirementProfile(**({
        "current_age": 60, "retirement_age": 60, "end_age": 61,
        "liquid_assets": 120, "monthly_savings": 0,
        "retirement_expenses": (
            RetirementExpensePeriod(from_age=60, to_age=61, monthly_amount=10),
        ),
        "pension_incomes": (PensionIncome(start_age=61, end_age=61, monthly_amount=20),),
    } | changes))


def assumptions(**changes) -> SimulationAssumptions:
    return SimulationAssumptions(**({
        "annual_return_mean": 0.05, "annual_return_volatility": 0.1,
        "annual_inflation_rate": 0.0, "num_paths": 4, "seed": 42,
    } | changes))


def example_result() -> MonteCarloResult:
    return calculate_monte_carlo_paths(profile(), assumptions(), annual_return_paths=EXAMPLE_PATHS)


def financial_summary(result: MonteCarloResult) -> tuple:
    return (result.asset_percentiles, result.first_depletion_counts,
            result.final_shortfall_percentiles, result.success_count, result.depletion_count)


class MonteCarloBasicTests(unittest.TestCase):
    def assert_amounts(self, actual: AmountPercentiles, expected: tuple[float, ...]) -> None:
        for value, reference in zip((actual.p10, actual.p50, actual.p90), expected):
            self.assertAlmostEqual(value, reference, delta=1e-10)

    def test_documented_example_b_includes_failed_and_recovered_paths(self) -> None:
        result = example_result()
        self.assertEqual(result.num_paths, 4)
        self.assertEqual(result.success_count, 3)
        self.assertEqual(result.depletion_count, 1)
        self.assertEqual(result.never_depleted_count, 3)
        self.assertEqual(result.success_probability, 0.75)
        self.assertEqual(result.depletion_probability, 0.25)
        self.assertEqual(result.first_depletion_counts, (
            FirstDepletionCount(age=60, count=1), FirstDepletionCount(age=61, count=0),
        ))
        self.assertEqual(tuple(item.age for item in result.asset_percentiles), (60, 61))
        self.assert_amounts(result.asset_percentiles[0].balance, (-42, 30, 102))
        self.assert_amounts(result.final_balance_percentiles, (78, 150, 222))
        self.assert_amounts(result.final_shortfall_percentiles, (0, 0, 0))
        self.assertIs(result.final_balance_percentiles, result.asset_percentiles[-1].balance)

    def test_only_first_depletion_is_counted_for_each_path(self) -> None:
        value = profile(end_age=62, pension_incomes=(), retirement_expenses=(
            RetirementExpensePeriod(from_age=60, to_age=62, monthly_amount=10),
        ))
        paths = ((-0.5, 0, 0), (0, 0, 0), (1, 0, 0), (2, 0, 0))
        result = calculate_monte_carlo_paths(value, assumptions(), annual_return_paths=paths)
        self.assertEqual(result.first_depletion_counts,
                         tuple(FirstDepletionCount(age=age, count=1) for age in (60, 61, 62)))
        self.assertEqual(result.depletion_count, 3)
        self.assertEqual(result.success_probability, 0.25)

    def test_final_shortfall_percentiles_use_path_shortfalls_before_interpolation(self) -> None:
        result = calculate_monte_carlo_paths(
            profile(end_age=60), assumptions(num_paths=2), annual_return_paths=((-0.5,), (0.5,)),
        )
        self.assert_amounts(result.final_balance_percentiles, (-48, 0, 48))
        self.assert_amounts(result.final_shortfall_percentiles, (6, 30, 54))
        self.assertEqual(result.success_probability, 0.5)
        self.assertNotEqual(result.final_shortfall_percentiles.p50,
                            max(-result.final_balance_percentiles.p50, 0))

    def test_generated_execution_calls_generator_once_and_cashflow_for_every_path(self) -> None:
        value, config = profile(), assumptions()
        with patch("retirement_simulation.monte_carlo.generate_return_paths", return_value=EXAMPLE_PATHS) as gen:
            with patch("retirement_simulation.monte_carlo.calculate_cashflow_path",
                       wraps=calculate_cashflow_path) as engine:
                result = calculate_monte_carlo(value, config)
        gen.assert_called_once_with(value, config)
        self.assertEqual(engine.call_args_list, [
            call(value, config, annual_returns=rates) for rates in EXAMPLE_PATHS
        ])
        self.assertEqual(result.success_probability, 0.75)
        self.assertEqual(result.metadata.path_source, "generated")

    def test_seeded_and_explicit_execution_have_the_same_financial_summary(self) -> None:
        value, config = profile(), assumptions(num_paths=31, seed=71)
        paths = generate_return_paths(value, config)
        generated = calculate_monte_carlo(value, config)
        provided = calculate_monte_carlo_paths(value, config, annual_return_paths=paths)
        self.assertEqual(financial_summary(generated), financial_summary(provided))
        self.assertNotEqual(generated.metadata.path_source, provided.metadata.path_source)

    def test_zero_volatility_matches_cashflow_examples_a_and_c_exactly(self) -> None:
        example_a = profile(
            current_age=59, retirement_age=60, end_age=61,
            liquid_assets=10_000_000, monthly_savings=100_000,
            retirement_expenses=(
                RetirementExpensePeriod(from_age=60, to_age=60, monthly_amount=100_000),
                RetirementExpensePeriod(from_age=61, to_age=61, monthly_amount=200_000),
            ),
            pension_incomes=(PensionIncome(start_age=60, end_age=60, monthly_amount=50_000),),
        )
        example_c = profile(
            end_age=63, liquid_assets=1_200_000,
            retirement_expenses=(
                RetirementExpensePeriod(from_age=60, to_age=63, monthly_amount=100_000),
            ),
            pension_incomes=(PensionIncome(start_age=62, end_age=63, monthly_amount=300_000),),
        )
        for value, mean, success, first_age in (
            (example_a, 0.1, 1.0, None), (example_c, 0.0, 0.0, 61),
        ):
            with self.subTest(mean=mean):
                config = assumptions(annual_return_mean=mean, annual_return_volatility=0.0)
                with patch("retirement_simulation.return_paths.Random") as rng_factory:
                    result = calculate_monte_carlo(value, config)
                rng_factory.assert_not_called()
                fixed = calculate_cashflow(value, config)
                self.assertEqual(result.success_probability, success)
                for point, year in zip(result.asset_percentiles, fixed.years):
                    self.assertEqual(point.age, year.age)
                    self.assertEqual(point.balance, AmountPercentiles(
                        p10=year.closing_balance, p50=year.closing_balance, p90=year.closing_balance,
                    ))
                self.assertEqual(result.final_shortfall_percentiles, AmountPercentiles(
                    p10=fixed.final_shortfall, p50=fixed.final_shortfall, p90=fixed.final_shortfall,
                ))
                self.assertEqual(tuple(item.count for item in result.first_depletion_counts),
                                 tuple(4 if item.age == first_age else 0
                                       for item in result.first_depletion_counts))

    def test_one_year_one_path_exact_zero_is_success(self) -> None:
        result = calculate_monte_carlo(
            profile(end_age=60), assumptions(annual_return_mean=0.0,
                                            annual_return_volatility=0.0, num_paths=1),
        )
        self.assertEqual(result.success_probability, 1.0)
        self.assertEqual(result.first_depletion_counts, (FirstDepletionCount(age=60, count=0),))
        self.assertEqual(len(result.asset_percentiles), 1)
        self.assert_amounts(result.final_balance_percentiles, (0, 0, 0))

    def test_all_depleted_and_tiny_negative_balances_remain_failures(self) -> None:
        result = calculate_monte_carlo_paths(
            profile(end_age=60), assumptions(num_paths=2),
            annual_return_paths=((-0.5,), (-1e-15,)),
        )
        self.assertEqual(result.depletion_count, 2)
        self.assertEqual(result.success_probability, 0.0)
        self.assertEqual(result.depletion_probability, 1.0)
        self.assertLess(result.final_balance_percentiles.p90, 0)

    def test_provided_paths_ignore_mean_volatility_seed_but_not_inflation(self) -> None:
        value, config = profile(), assumptions()
        with patch("retirement_simulation.monte_carlo.generate_return_paths") as gen:
            reference = calculate_monte_carlo_paths(value, config, annual_return_paths=EXAMPLE_PATHS)
            changed = calculate_monte_carlo_paths(value, replace(
                config, annual_return_mean=-0.9, annual_return_volatility=1e200, seed=999,
            ), annual_return_paths=EXAMPLE_PATHS)
            inflated = calculate_monte_carlo_paths(value, replace(
                config, annual_inflation_rate=0.1,
            ), annual_return_paths=EXAMPLE_PATHS)
        gen.assert_not_called()
        self.assertEqual(financial_summary(reference), financial_summary(changed))
        self.assert_amounts(inflated.final_balance_percentiles, (66, 138, 210))

    def test_repeated_execution_preserves_input_and_global_rng_state(self) -> None:
        value, config = profile(), assumptions(num_paths=17)
        original = deepcopy((value, config))
        state = random.getstate()
        first = calculate_monte_carlo(value, config)
        calculate_monte_carlo(profile(current_age=61), assumptions(seed=77, num_paths=3))
        self.assertEqual(calculate_monte_carlo(value, config), first)
        self.assertEqual(random.getstate(), state)
        self.assertEqual((value, config), original)

    def test_reusing_provided_lists_does_not_mutate_them(self) -> None:
        paths = [list(path) for path in EXAMPLE_PATHS]
        before = deepcopy(paths)
        first = calculate_monte_carlo_paths(profile(), assumptions(), annual_return_paths=paths)
        self.assertEqual(paths, before)
        self.assertEqual(calculate_monte_carlo_paths(
            profile(), assumptions(), annual_return_paths=paths,
        ), first)

    def test_provided_batch_is_copied_before_cashflow_calls(self) -> None:
        paths = [list(path) for path in EXAMPLE_PATHS]
        expected = example_result()

        def mutate_caller_then_calculate(value, config, *, annual_returns):
            paths[-1][0] = 99.0
            return calculate_cashflow_path(value, config, annual_returns=annual_returns)

        with patch("retirement_simulation.monte_carlo.calculate_cashflow_path",
                   side_effect=mutate_caller_then_calculate):
            actual = calculate_monte_carlo_paths(profile(), assumptions(), annual_return_paths=paths)
        self.assertEqual(actual, expected)

    def test_summary_runs_without_site_packages(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-S", "-B", "-c", """
from retirement_simulation import RetirementExpensePeriod, RetirementProfile, SimulationAssumptions
from retirement_simulation import calculate_monte_carlo
p = RetirementProfile(current_age=60, retirement_age=60, end_age=60,
    liquid_assets=0, monthly_savings=0, pension_incomes=(), retirement_expenses=(
        RetirementExpensePeriod(from_age=60, to_age=60, monthly_amount=0),))
a = SimulationAssumptions(annual_return_mean=0.05, annual_return_volatility=0.1,
    annual_inflation_rate=0.0, num_paths=3)
r = calculate_monte_carlo(p, a)
assert r.success_probability == 1.0 and r.final_balance_percentiles.p50 == 0.0
"""], cwd=AI_PROJECT_ROOT, text=True, capture_output=True, check=True,
        )
        self.assertEqual(completed.stdout, "")
        self.assertEqual(completed.stderr, "")


class MonteCarloModelTests(unittest.TestCase):
    def test_metadata_records_actual_generation_mode_and_environment(self) -> None:
        config = assumptions()
        result = calculate_monte_carlo(profile(), config)
        meta = result.metadata
        self.assertEqual((meta.current_age, meta.end_age, meta.num_paths), (60, 61, 4))
        self.assertEqual(meta.assumptions, config)
        self.assertEqual(meta.path_source, "generated")
        self.assertEqual(meta.return_model, "lognormal_gross_return")
        self.assertEqual(meta.rng_algorithm, "python.random.Random.gauss")
        self.assertEqual(meta.seed, 42)
        self.assertEqual(meta.model_version, "retirement-mc-v1")
        self.assertEqual(meta.python_version, python_version())
        self.assertEqual(meta.python_implementation, python_implementation())

    def test_provided_and_zero_volatility_metadata_do_not_claim_rng_usage(self) -> None:
        provided = example_result().metadata
        fixed = calculate_monte_carlo(profile(), assumptions(annual_return_volatility=0.0)).metadata
        self.assertIsNone(provided.return_model)
        self.assertEqual(provided.path_source, "provided")
        self.assertEqual(fixed.return_model, "constant")
        for meta in (provided, fixed):
            self.assertIsNone(meta.rng_algorithm)
            self.assertIsNone(meta.seed)
            self.assertEqual(meta.assumptions.seed, 42)

    def test_amount_percentiles_require_finite_ordered_values(self) -> None:
        for changes in ({"p10": True}, {"p50": float("nan")}, {"p90": float("inf")},
                        {"p10": 10}, {"p90": "3"}):
            with self.subTest(field=next(iter(changes))):
                with self.assertRaises(ModelValidationError):
                    AmountPercentiles(**({"p10": -2, "p50": 0, "p90": 3} | changes))
        self.assertEqual(AmountPercentiles(p10=-2, p50=0, p90=3).p10, -2.0)

    def test_age_and_count_result_models_validate_their_fields(self) -> None:
        for age, count in ((17, 0), (121, 0), (60, -1), (60, True), (60, 1.5), (60, 100_001)):
            with self.subTest(age=age, count=count):
                with self.assertRaises(ModelValidationError):
                    FirstDepletionCount(age=age, count=count)
        with self.assertRaises(ModelValidationError):
            AssetPercentiles(age=60, balance={})
        with self.assertRaises(ModelValidationError):
            AssetPercentiles(age=True, balance=AmountPercentiles(p10=0, p50=0, p90=0))

    def test_metadata_rejects_invalid_horizon_assumptions_and_source(self) -> None:
        defaults = dict(current_age=60, end_age=61, assumptions=assumptions(), path_source="provided")
        for changes in ({"current_age": True}, {"current_age": 101}, {"end_age": 59},
                        {"end_age": 121}, {"assumptions": {}}, {"path_source": "unknown"},
                        {"path_source": []}):
            with self.subTest(field=next(iter(changes))):
                with self.assertRaises(ModelValidationError):
                    MonteCarloMetadata(**(defaults | changes))

    def test_result_requires_exact_ordered_horizon_for_both_collections(self) -> None:
        result = example_result()
        with self.assertRaises(ModelValidationError):
            replace(result, metadata={})
        for field_name in ("asset_percentiles", "first_depletion_counts"):
            values = getattr(result, field_name)
            for invalid in (None, [], values[:1], values[::-1], (values[0], values[0]), ({}, {})):
                with self.subTest(field=field_name):
                    with self.assertRaises(ModelValidationError):
                        replace(result, **{field_name: invalid})

    def test_result_rejects_excess_depletion_counts_and_negative_shortfall(self) -> None:
        result = example_result()
        with self.assertRaises(ModelValidationError):
            replace(result, first_depletion_counts=(
                FirstDepletionCount(age=60, count=3), FirstDepletionCount(age=61, count=2),
            ))
        for invalid in ({}, AmountPercentiles(p10=-1, p50=0, p90=1)):
            with self.assertRaises(ModelValidationError):
                replace(result, final_shortfall_percentiles=invalid)

    def test_result_copies_collections_and_keeps_nested_models_frozen(self) -> None:
        original = example_result()
        assets, counts = list(original.asset_percentiles), list(original.first_depletion_counts)
        copied = replace(original, asset_percentiles=assets, first_depletion_counts=counts)
        assets.clear()
        counts.clear()
        self.assertEqual(copied, original)
        self.assertIsInstance(copied.asset_percentiles, tuple)
        self.assertIsInstance(copied.first_depletion_counts, tuple)
        for value, name, replacement in (
            (copied, "metadata", None), (copied.metadata, "path_source", "generated"),
            (copied.asset_percentiles[0], "age", 61),
            (copied.asset_percentiles[0].balance, "p50", 999),
            (copied.first_depletion_counts[0], "count", 999),
            (copied.metadata.assumptions, "annual_return_mean", 0.5),
        ):
            with self.subTest(model=type(value).__name__, field=name):
                with self.assertRaises(FrozenInstanceError):
                    setattr(value, name, replacement)

    def test_result_repr_does_not_echo_amounts_or_assumptions(self) -> None:
        value = profile(liquid_assets=987_654_321)
        config = assumptions(annual_return_mean=0.123456789, annual_return_volatility=0.0)
        result = calculate_monte_carlo(value, config)
        for item in (result, result.metadata, result.asset_percentiles[0],
                     result.final_balance_percentiles, result.final_shortfall_percentiles):
            shown = repr(item)
            self.assertNotIn("987654321", shown)
            self.assertNotIn("0.123456789", shown)
            self.assertNotIn(str(result.final_balance_percentiles.p50), shown)


class MonteCarloQuantileTests(unittest.TestCase):
    def test_small_samples_match_inclusive_linear_reference_without_mutation(self) -> None:
        for values in ([7.0], [-5.0, 5.0], [9.0, -3.0, 2.0], [4.0, 0.0, -6.0, 4.0]):
            with self.subTest(size=len(values)):
                original = values.copy()
                actual = _percentiles(values, field_name="asset_percentiles", age=60)
                if len(values) == 1:
                    expected = (values[0],) * 3
                else:
                    reference = quantiles(values, n=10, method="inclusive")
                    expected = (reference[0], reference[4], reference[8])
                for value, target in zip((actual.p10, actual.p50, actual.p90), expected):
                    self.assertAlmostEqual(value, target, delta=1e-12)
                self.assertEqual(values, original)
                ordered = sorted(values)
                self.assertEqual(_linear_quantile(ordered, 0), min(values))
                self.assertEqual(_linear_quantile(ordered, 1), max(values))

    def test_identical_extreme_and_tiny_values_are_preserved_exactly(self) -> None:
        for value in (sys.float_info.max, -sys.float_info.max, nextafter(0.0, 1.0),
                      -nextafter(0.0, 1.0), 0.1):
            with self.subTest(value=value):
                actual = _percentiles([value] * 4, field_name="asset_percentiles", age=60)
                self.assertEqual(actual, AmountPercentiles(p10=value, p50=value, p90=value))

    def test_finite_extreme_interpolation_matches_decimal_without_overflow(self) -> None:
        maximum = sys.float_info.max
        for values in ([-maximum, maximum], [maximum / 2, maximum], [-maximum, -maximum / 2]):
            actual = _percentiles(values, field_name="asset_percentiles", age=60)
            for value, q in zip((actual.p10, actual.p50, actual.p90), ("0.1", "0.5", "0.9")):
                with localcontext() as context:
                    context.prec = 80
                    weight = Decimal(q)
                    expected = float((1 - weight) * Decimal.from_float(values[0])
                                     + weight * Decimal.from_float(values[1]))
                self.assertTrue(isclose(value, expected, rel_tol=1e-14))

    def test_empty_and_nonfinite_aggregation_samples_are_errors(self) -> None:
        for values in ([], [0.0, float("nan"), 1.0], [float("inf")], [-float("inf")]):
            with self.assertRaises(MonteCarloCalculationError) as caught:
                _percentiles(values, field_name="asset_percentiles", age=61)
            self.assertEqual(caught.exception.stage, "aggregation")
            self.assertEqual(caught.exception.age, 61)


class MonteCarloValidationTests(unittest.TestCase):
    def test_both_entry_points_reject_non_model_inputs(self) -> None:
        for invalid in (None, True, {}, "invalid"):
            for field_name, value, config in (
                ("profile", invalid, assumptions()), ("assumptions", profile(), invalid),
            ):
                for calculate in (calculate_monte_carlo, calculate_monte_carlo_paths):
                    kwargs = {"annual_return_paths": EXAMPLE_PATHS} if (
                        calculate is calculate_monte_carlo_paths
                    ) else {}
                    with self.subTest(function=calculate.__name__, field=field_name):
                        with self.assertRaises(ModelValidationError) as caught:
                            calculate(value, config, **kwargs)
                        self.assertEqual(caught.exception.field_name, field_name)

    def test_provided_batch_shape_and_rates_are_validated_before_calculation(self) -> None:
        invalid_batches = (
            None, True, {}, iter(EXAMPLE_PATHS), (), EXAMPLE_PATHS[:3],
            EXAMPLE_PATHS + (EXAMPLE_PATHS[0],),
            EXAMPLE_PATHS[:3] + ((0,),), EXAMPLE_PATHS[:3] + ((0, 0, 0),),
            EXAMPLE_PATHS[:3] + ("00",), EXAMPLE_PATHS[:3] + ((0, -1),),
            EXAMPLE_PATHS[:3] + ((0, True),), EXAMPLE_PATHS[:3] + ((0, "0.1"),),
            EXAMPLE_PATHS[:3] + ((0, float("nan")),), EXAMPLE_PATHS[:3] + ((0, float("inf")),),
        )
        with patch("retirement_simulation.monte_carlo.calculate_cashflow_path") as engine:
            with patch("retirement_simulation.monte_carlo.generate_return_paths") as gen:
                for invalid in invalid_batches:
                    with self.assertRaises(ModelValidationError) as caught:
                        calculate_monte_carlo_paths(
                            profile(), assumptions(), annual_return_paths=invalid,
                        )
                    self.assertTrue(caught.exception.field_name.startswith("annual_return_paths"))
        engine.assert_not_called()
        gen.assert_not_called()

    def test_generation_errors_preserve_stage_and_location_without_partial_output(self) -> None:
        result = None
        failure = ReturnPathGenerationError("annual_returns", path_index=2, age=61)
        with patch("retirement_simulation.monte_carlo.generate_return_paths", side_effect=failure):
            with patch("retirement_simulation.monte_carlo.calculate_cashflow_path") as engine:
                with self.assertRaises(MonteCarloCalculationError) as caught:
                    result = calculate_monte_carlo(profile(), assumptions())
        engine.assert_not_called()
        self.assertIsNone(result)
        error = caught.exception
        self.assertEqual((error.stage, error.field_name, error.path_index, error.age),
                         ("return_generation", "annual_returns", 2, 61))
        self.assertTrue(error.__suppress_context__)

    def test_actual_generation_numeric_failure_is_not_a_depleted_path(self) -> None:
        with self.assertRaises(MonteCarloCalculationError) as caught:
            calculate_monte_carlo(profile(), assumptions(
                annual_return_mean=nextafter(-1.0, 0.0), annual_return_volatility=1e308,
            ))
        self.assertEqual(caught.exception.stage, "return_generation")

    def test_late_cashflow_overflow_aborts_with_correct_path_and_age(self) -> None:
        value = profile(end_age=60, liquid_assets=int(1e308), pension_incomes=(), retirement_expenses=(
            RetirementExpensePeriod(from_age=60, to_age=60, monthly_amount=0),
        ))
        result = None
        with patch("retirement_simulation.monte_carlo.calculate_cashflow_path",
                   wraps=calculate_cashflow_path) as engine:
            with self.assertRaises(MonteCarloCalculationError) as caught:
                result = calculate_monte_carlo_paths(
                    value, assumptions(num_paths=3), annual_return_paths=((0,), (1,), (0,)),
                )
        self.assertIsNone(result)
        self.assertEqual(engine.call_count, 2)
        error = caught.exception
        self.assertEqual((error.stage, error.field_name, error.path_index, error.age),
                         ("cashflow", "balance_after_return", 1, 60))
        self.assertNotIn(str(value.liquid_assets), str(error))

    def test_initial_amount_conversion_failure_keeps_unknown_age(self) -> None:
        value = profile(liquid_assets=10 ** 400)
        with self.assertRaises(MonteCarloCalculationError) as caught:
            calculate_monte_carlo_paths(value, assumptions(), annual_return_paths=EXAMPLE_PATHS)
        error = caught.exception
        self.assertEqual((error.stage, error.field_name, error.path_index, error.age),
                         ("cashflow", "liquid_assets", 0, None))
        self.assertNotIn(str(value.liquid_assets), str(error))

    def test_nonfinite_interpolated_result_aborts_aggregation(self) -> None:
        result = None
        with patch("retirement_simulation.monte_carlo._linear_quantile", return_value=float("nan")):
            with self.assertRaises(MonteCarloCalculationError) as caught:
                result = example_result()
        self.assertIsNone(result)
        error = caught.exception
        self.assertEqual((error.stage, error.field_name, error.path_index, error.age),
                         ("aggregation", "asset_percentiles", None, 60))

    def test_wrong_generated_count_cannot_change_probability_denominator(self) -> None:
        with patch("retirement_simulation.monte_carlo.generate_return_paths", return_value=EXAMPLE_PATHS[:3]):
            with patch("retirement_simulation.monte_carlo.calculate_cashflow_path") as engine:
                with self.assertRaises(MonteCarloCalculationError) as caught:
                    calculate_monte_carlo(profile(), assumptions())
        engine.assert_not_called()
        self.assertEqual(caught.exception.field_name, "num_paths")


if __name__ == "__main__":
    unittest.main()
