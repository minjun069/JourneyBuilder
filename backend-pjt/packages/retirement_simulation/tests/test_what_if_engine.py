from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import random
from statistics import quantiles
import sys
import unittest
from unittest.mock import patch


AI_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(AI_PROJECT_ROOT))

from retirement_simulation import (  # noqa: E402
    MAX_WHAT_IF_SCENARIOS,
    AmountPercentileDelta, AmountPercentiles, AssetPercentileDelta, AssetPercentiles,
    FirstDepletionCount, FirstDepletionDelta, ModelValidationError,
    MonteCarloCalculationError, MonteCarloMetadata, MonteCarloResult,
    PensionIncome, RetirementExpensePeriod, RetirementProfile, ReturnPathGenerationError,
    SimulationAssumptions, WhatIfCalculationError, WhatIfChanges, WhatIfResult,
    WhatIfScenario, WhatIfScenarioResult, calculate_monte_carlo_paths,
    calculate_what_if, calculate_what_if_paths, create_what_if_profile, generate_return_paths,
)


PATHS = ((-0.5, 0.0), (0.0, 0.0), (0.5, 0.0), (1.0, 0.0))
ENGINE = "retirement_simulation.what_if_engine."


def expense(start=60, end=61, amount=10):
    return RetirementExpensePeriod(from_age=start, to_age=end, monthly_amount=amount)


def profile(**changes):
    return RetirementProfile(**({
        "current_age": 60, "retirement_age": 60, "end_age": 61,
        "liquid_assets": 120, "monthly_savings": 0,
        "retirement_expenses": (expense(),),
        "pension_incomes": (PensionIncome(start_age=61, end_age=61, monthly_amount=20),),
    } | changes))


def assumptions(**changes):
    return SimulationAssumptions(**({
        "annual_return_mean": 0.05, "annual_return_volatility": 0.10,
        "annual_inflation_rate": 0.0, "num_paths": 4, "seed": 42,
    } | changes))


def scenario(name="lower_expenses", **changes):
    return WhatIfScenario(scenario_id=name, changes=WhatIfChanges(**changes))


def comparison():
    return calculate_what_if_paths(profile(), assumptions(), (
        scenario(retirement_expenses=(expense(amount=5),)),
    ), annual_return_paths=PATHS)


def synthetic_summary(values, *, age=60):
    """Independent marginal summary fixture, not an engine-produced market claim."""
    deciles = quantiles(values, n=10, method="inclusive")
    gaps = quantiles([max(-value, 0) for value in values], n=10, method="inclusive")
    return MonteCarloResult(
        metadata=MonteCarloMetadata(
            current_age=age, end_age=age, assumptions=assumptions(num_paths=len(values)),
            path_source="provided",
        ),
        asset_percentiles=(AssetPercentiles(age=age, balance=AmountPercentiles(
            p10=deciles[0], p50=deciles[4], p90=deciles[8],
        )),),
        first_depletion_counts=(FirstDepletionCount(
            age=age, count=sum(value < 0 for value in values),
        ),),
        final_shortfall_percentiles=AmountPercentiles(p10=gaps[0], p50=gaps[4], p90=gaps[8]),
    )


class WhatIfEngineBasicTests(unittest.TestCase):
    def assert_amounts(self, actual, expected):
        for name, value in zip(("p10", "p50", "p90"), expected):
            self.assertAlmostEqual(getattr(actual, name), value, delta=1e-10)

    def test_example_c_success_histogram_and_all_amount_deltas(self):
        output = comparison()
        item = output.scenarios[0]
        self.assertEqual(output.baseline.success_count, 3)
        self.assertEqual(item.result.success_count, 4)
        self.assertEqual(item.success_count_delta, 1)
        self.assertEqual(item.depletion_count_delta, -1)
        self.assertEqual(item.never_depleted_count_delta, 1)
        self.assertEqual(item.success_probability_delta_pp, 25)
        self.assertEqual(item.depletion_probability_delta_pp, -25)
        self.assertEqual(item.first_depletion_deltas, (
            FirstDepletionDelta(age=60, count=-1), FirstDepletionDelta(age=61, count=0),
        ))
        self.assertEqual(sum(row.count for row in item.first_depletion_deltas),
                         item.depletion_count_delta)
        self.assert_amounts(output.baseline.final_balance_percentiles, (78, 150, 222))
        self.assert_amounts(item.result.final_balance_percentiles, (198, 270, 342))
        self.assert_amounts(item.asset_deltas[0].balance, (60, 60, 60))
        self.assert_amounts(item.final_balance_delta, (120, 120, 120))
        self.assert_amounts(item.final_shortfall_delta, (0, 0, 0))
        self.assertIs(item.final_balance_delta, item.asset_deltas[-1].balance)
        self.assertIs(item.baseline, output.baseline)
        self.assertEqual(output.num_paths, 4)

    def test_examples_a_b_use_absolute_savings_and_independent_changes(self):
        baseline = profile(
            current_age=58, liquid_assets=1200, monthly_savings=100,
            retirement_expenses=(expense(amount=100),),
            pension_incomes=(PensionIncome(start_age=61, end_age=61, monthly_amount=50),),
        )
        items = (
            scenario("saving_more", monthly_savings=200),
            scenario("early", retirement_age=59, retirement_expenses=(expense(59, 61, 100),)),
            scenario("late", retirement_age=61),
            scenario("unchanged"),
        )
        output = calculate_what_if(baseline, assumptions(
            annual_return_mean=0, annual_return_volatility=0, num_paths=3, seed=7,
        ), items)
        expected = (
            ((1200, 2400, 2400, 2400), 0, 0),
            ((0, -2400, -2400, -2400), -100, 600),
            ((0, 0, 2400, 2400), 0, 0),
            ((0, 0, 0, 0), 0, 0),
        )
        self.assertEqual(output.baseline.success_count, 3)
        for item, (amounts, pp, shortfall) in zip(output.scenarios, expected):
            with self.subTest(scenario=item.scenario_id):
                for row, amount in zip(item.asset_deltas, amounts):
                    self.assert_amounts(row.balance, (amount,) * 3)
                self.assertEqual(item.success_probability_delta_pp, pp)
                self.assert_amounts(item.final_shortfall_delta, (shortfall,) * 3)
        self.assertEqual(output.scenarios[1].first_depletion_deltas[-1].count, 3)

    def test_generated_batch_calls_generator_once_and_baseline_once(self):
        baseline, config = profile(), assumptions()
        items = (scenario("first"), scenario("second", retirement_expenses=(expense(amount=5),)))
        with (
            patch(ENGINE + "generate_return_paths", return_value=PATHS) as generate,
            patch(ENGINE + "calculate_monte_carlo_paths", wraps=calculate_monte_carlo_paths) as calc,
        ):
            output = calculate_what_if(baseline, config, items)
        generate.assert_called_once_with(baseline, config)
        self.assertEqual(calc.call_count, 3)
        self.assertIs(calc.call_args_list[0].args[0], baseline)
        for call in calc.call_args_list:
            self.assertIs(call.args[1], config)
            self.assertIs(call.kwargs["annual_return_paths"], PATHS)
        self.assertEqual(calc.call_args_list[1].args[0], baseline)
        self.assertEqual(calc.call_args_list[2].args[0], create_what_if_profile(baseline, items[1].changes))
        self.assertTrue(all(item.baseline is output.baseline for item in output.scenarios))

    def test_provided_batch_never_generates_or_consumes_randomness(self):
        state = random.getstate()
        with patch(ENGINE + "generate_return_paths") as generate:
            output = comparison()
        generate.assert_not_called()
        self.assertEqual(state, random.getstate())
        self.assertEqual(output.metadata.path_source, "provided")
        self.assertIsNone(output.metadata.return_model)
        self.assertIsNone(output.metadata.rng_algorithm)
        self.assertIsNone(output.metadata.seed)

    def test_generated_provenance_is_separate_from_provided_children(self):
        config = assumptions()
        output = calculate_what_if(profile(), config, (scenario(),))
        self.assertEqual(output.model_version, "retirement-what-if-v1")
        self.assertEqual(output.metadata.model_version, "retirement-mc-v1")
        self.assertEqual(output.metadata.path_source, "generated")
        self.assertEqual(output.metadata.return_model, "lognormal_gross_return")
        self.assertEqual(output.metadata.rng_algorithm, "python.random.Random.gauss")
        self.assertEqual(output.metadata.seed, config.seed)
        for child in (output.baseline, output.scenarios[0].result):
            self.assertEqual(child.metadata.path_source, "provided")
            self.assertIsNone(child.metadata.seed)
            self.assertIsNone(child.metadata.rng_algorithm)

    def test_zero_volatility_keeps_configured_seed_but_no_effective_rng(self):
        with patch("retirement_simulation.return_paths.Random") as rng:
            output = calculate_what_if(profile(), assumptions(annual_return_volatility=0), (scenario(),))
        rng.assert_not_called()
        self.assertEqual(output.metadata.return_model, "constant")
        self.assertIsNone(output.metadata.seed)
        self.assertIsNone(output.metadata.rng_algorithm)
        self.assertEqual(output.metadata.assumptions.seed, 42)

    def test_no_op_has_identical_summary_and_all_zero_deltas(self):
        output = calculate_what_if(profile(), assumptions(num_paths=31), (scenario("noop"),))
        item = output.scenarios[0]
        self.assertEqual(item.result, output.baseline)
        self.assertEqual(item.success_probability_delta_pp, 0)
        self.assertEqual(item.depletion_probability_delta_pp, 0)
        for row in item.asset_deltas:
            self.assertEqual(row.balance, AmountPercentileDelta(p10=0, p50=0, p90=0))
        self.assertEqual(item.final_shortfall_delta, AmountPercentileDelta(p10=0, p50=0, p90=0))
        self.assertTrue(all(row.count == 0 for row in item.first_depletion_deltas))

    def test_seeded_and_provided_paths_have_identical_financial_results(self):
        baseline, config = profile(), assumptions(num_paths=37, seed=13)
        items = (scenario(retirement_expenses=(expense(amount=5),)),)
        paths = generate_return_paths(baseline, config)
        generated = calculate_what_if(baseline, config, items)
        supplied = calculate_what_if_paths(baseline, config, items, annual_return_paths=paths)
        self.assertEqual(generated.baseline, supplied.baseline)
        self.assertEqual(generated.scenarios, supplied.scenarios)
        self.assertNotEqual(generated.metadata.path_source, supplied.metadata.path_source)

    def test_each_changed_profile_matches_direct_engine_with_nonconstant_market_and_inflation(self):
        baseline = profile(
            current_age=58, monthly_savings=100,
            retirement_expenses=(expense(58, 61),),
        )
        config = assumptions(num_paths=7, annual_inflation_rate=0.02)
        items = (scenario("saving", monthly_savings=200),
                 scenario("early", retirement_age=59),
                 scenario("less", retirement_expenses=(expense(60, 61, 5),)))
        paths = generate_return_paths(baseline, config)
        output = calculate_what_if_paths(baseline, config, items, annual_return_paths=paths)
        self.assertEqual(output.baseline, calculate_monte_carlo_paths(
            baseline, config, annual_return_paths=paths,
        ))
        for definition, item in zip(items, output.scenarios):
            direct = calculate_monte_carlo_paths(
                create_what_if_profile(baseline, definition.changes), config,
                annual_return_paths=paths,
            )
            self.assertEqual(item.result, direct)
            for row, base, changed in zip(item.asset_deltas, output.baseline.asset_percentiles,
                                          direct.asset_percentiles):
                self.assertEqual(row.age, base.age)
                for name in ("p10", "p50", "p90"):
                    self.assertEqual(getattr(row.balance, name),
                                     getattr(changed.balance, name) - getattr(base.balance, name))

    def test_repeat_and_scenario_reordering_preserve_results_without_global_rng_changes(self):
        baseline, config = profile(), assumptions(num_paths=23)
        items = (scenario("noop"), scenario("less", retirement_expenses=(expense(amount=5),)))
        state = random.getstate()
        output = calculate_what_if(baseline, config, items)
        self.assertEqual(output, calculate_what_if(baseline, config, items))
        reordered = calculate_what_if(baseline, config, tuple(reversed(items)))
        self.assertEqual(output.baseline, reordered.baseline)
        self.assertEqual(output.scenarios, tuple(reversed(reordered.scenarios)))
        self.assertEqual(random.getstate(), state)

    def test_inputs_and_paths_are_not_modified(self):
        baseline, config = profile(), assumptions()
        items = [scenario(retirement_expenses=[expense(amount=5)])]
        paths = [list(row) for row in PATHS]
        before = deepcopy((baseline, config, items, paths))
        calculate_what_if_paths(baseline, config, items, annual_return_paths=paths)
        self.assertEqual((baseline, config, items, paths), before)

    def test_provided_paths_and_scenario_list_are_snapshotted_before_calculation(self):
        items = [scenario(retirement_expenses=(expense(amount=5),))]
        paths = [list(row) for row in PATHS]
        def mutate_caller_lists(*args, **kwargs):
            items.clear()
            paths[-1][0] = float("nan")
            return calculate_monte_carlo_paths(*args, **kwargs)
        with patch(ENGINE + "calculate_monte_carlo_paths", side_effect=mutate_caller_lists):
            result = calculate_what_if_paths(profile(), assumptions(), items, annual_return_paths=paths)
        self.assertEqual(result, comparison())

    def test_one_year_one_path_and_zero_baseline_probability(self):
        baseline = profile(end_age=60, liquid_assets=0, pension_incomes=())
        output = calculate_what_if_paths(baseline, assumptions(num_paths=1), (
            scenario(retirement_expenses=(expense(60, 60, 0),)),
        ), annual_return_paths=((0,),))
        self.assertEqual(output.baseline.success_probability, 0)
        self.assertEqual(output.scenarios[0].result.success_probability, 1)
        self.assertEqual(output.scenarios[0].success_probability_delta_pp, 100)
        self.assert_amounts(output.scenarios[0].final_shortfall_delta, (-120,) * 3)

    def test_maximum_scenario_batch_is_supported_without_deduping_equal_changes(self):
        items = tuple(scenario(f"case_{index}") for index in range(MAX_WHAT_IF_SCENARIOS))
        output = calculate_what_if(profile(), assumptions(num_paths=1), items)
        self.assertEqual(len(output.scenarios), 10)
        self.assertEqual(tuple(item.scenario_id for item in output.scenarios),
                         tuple(item.scenario_id for item in items))


class WhatIfEngineValidationTests(unittest.TestCase):
    def test_identifier_rules_and_immutable_input(self):
        for name in ("a", "saving_more", "early-2", "a" * 64):
            item = scenario(name)
            self.assertEqual(item.scenario_id, name)
            with self.assertRaises(FrozenInstanceError):
                item.scenario_id = "changed"
            self.assertEqual(repr(item), "WhatIfScenario()")
        for name in (None, True, "", "baseline", "A", "1a", "한글", "a b", "a\n", "a" * 65):
            with self.subTest(name=name):
                with self.assertRaises(ModelValidationError) as error:
                    scenario(name)
                self.assertEqual(error.exception.field_name, "scenario_id")

    def test_scenario_requires_typed_changes(self):
        for value in (None, {}, [], "private input"):
            with self.assertRaises(ModelValidationError) as error:
                WhatIfScenario(scenario_id="test", changes=value)
            self.assertEqual(error.exception.field_name, "changes")
        with self.assertRaises(TypeError):
            WhatIfScenario(scenario_id="test")

    def test_invalid_batch_types_sizes_and_duplicates_precede_generation(self):
        for items in (
            None, {}, (), [], scenario(), WhatIfChanges(), iter((scenario(),)),
            [scenario()] * 2, [scenario()] * 11, [scenario(), {}],
        ):
            with self.subTest(kind=type(items).__name__):
                with (
                    patch(ENGINE + "generate_return_paths") as generate,
                    patch(ENGINE + "calculate_monte_carlo_paths") as calc,
                ):
                    with self.assertRaises(ModelValidationError):
                        calculate_what_if(profile(), assumptions(), items)
                generate.assert_not_called()
                calc.assert_not_called()

    def test_profile_and_assumptions_types(self):
        for bad in (None, {}, "private input", True):
            for field in ("profile", "assumptions"):
                values = {"profile": profile(), "assumptions": assumptions(), "scenarios": [scenario()]}
                values[field] = bad
                with self.assertRaises(ModelValidationError) as error:
                    calculate_what_if(**values)
                self.assertEqual(error.exception.field_name, field)
                self.assertNotIn("private input", str(error.exception))

    def test_late_invalid_change_is_rejected_before_any_rng_or_calculation(self):
        baseline = profile(current_age=58)
        items = (scenario("valid"), scenario("missing_expenses", retirement_age=59))
        for function in (calculate_what_if, calculate_what_if_paths):
            kwargs = {} if function is calculate_what_if else {"annual_return_paths": ((0,) * 4,) * 4}
            with (
                patch(ENGINE + "generate_return_paths") as generate,
                patch(ENGINE + "calculate_monte_carlo_paths") as calc,
            ):
                with self.assertRaises(ModelValidationError) as error:
                    function(baseline, assumptions(), items, **kwargs)
            self.assertEqual(error.exception.field_name, "scenarios[1].changes.retirement_expenses")
            self.assertTrue(error.exception.__suppress_context__)
            generate.assert_not_called()
            calc.assert_not_called()

    def test_entire_provided_batch_is_validated_before_first_calculation(self):
        for bad in (True, float("nan"), float("inf"), -1.0, -2.0, "secret rate"):
            paths = [list(row) for row in PATHS]
            paths[-1][-1] = bad
            with (
                patch(ENGINE + "generate_return_paths") as generate,
                patch(ENGINE + "calculate_monte_carlo_paths") as calc,
            ):
                with self.assertRaises(ModelValidationError) as error:
                    calculate_what_if_paths(profile(), assumptions(), [scenario()], annual_return_paths=paths)
            self.assertTrue(error.exception.field_name.startswith("annual_return_paths[3]"))
            self.assertNotIn("secret rate", str(error.exception))
            generate.assert_not_called()
            calc.assert_not_called()

    def test_provided_shape_and_container_validation(self):
        for paths in (None, {}, iter(PATHS), PATHS[:-1], PATHS + PATHS[:1],
                      PATHS[:-1] + ((0,),), PATHS[:-1] + (iter((0, 0)),)):
            with patch(ENGINE + "calculate_monte_carlo_paths") as calc:
                with self.assertRaises(ModelValidationError):
                    calculate_what_if_paths(profile(), assumptions(), [scenario()], annual_return_paths=paths)
            calc.assert_not_called()


class WhatIfEngineResultTests(unittest.TestCase):
    def test_amount_delta_has_no_order_requirement_and_is_not_paired_delta_quantiles(self):
        baseline, changed = synthetic_summary([0, 100]), synthetic_summary([40, 60])
        item = WhatIfScenarioResult(scenario_id="synthetic", baseline=baseline, result=changed)
        self.assertEqual(item.final_balance_delta, AmountPercentileDelta(p10=32, p50=0, p90=-32))
        paired = synthetic_summary([40, -40]).final_balance_percentiles
        self.assertEqual((paired.p10, paired.p50, paired.p90), (-32, 0, 32))

    def test_delta_models_validate_finite_numbers_types_and_signed_counts(self):
        for bad in (True, "1", float("nan"), float("inf"), 10 ** 400):
            with self.assertRaises(ModelValidationError):
                AmountPercentileDelta(p10=bad, p50=0, p90=0)
        for value in (-MAX_WHAT_IF_SCENARIOS, 0, 100_000, -100_000):
            self.assertEqual(FirstDepletionDelta(age=60, count=value).count, value)
        for bad in (True, 1.0, -100_001, 100_001):
            with self.assertRaises(ModelValidationError):
                FirstDepletionDelta(age=60, count=bad)
        with self.assertRaises(ModelValidationError):
            AssetPercentileDelta(age=60, balance=AmountPercentiles(p10=0, p50=0, p90=0))
        for age in (True, 17, 121):
            with self.assertRaises(ModelValidationError):
                AssetPercentileDelta(age=age, balance=AmountPercentileDelta(p10=0, p50=0, p90=0))

    def test_result_and_nested_deltas_are_frozen_with_derived_fields(self):
        output = comparison()
        item = output.scenarios[0]
        for value, attribute in ((output, "baseline"), (item, "result"),
                                 (item.final_balance_delta, "p10"),
                                 (item.first_depletion_deltas[0], "count")):
            with self.assertRaises(FrozenInstanceError):
                setattr(value, attribute, None)
        with self.assertRaises(TypeError):
            WhatIfScenarioResult(scenario_id="test", baseline=output.baseline, result=item.result,
                                 asset_deltas=())
        for value in (output, item, item.final_balance_delta):
            self.assertNotIn("lower_expenses", repr(value))
            self.assertNotIn("198.0", repr(value))

    def test_results_copy_scenario_list_and_validate_batch_consistency(self):
        output = comparison()
        supplied = list(output.scenarios)
        copied = replace(output, scenarios=supplied)
        supplied.clear()
        self.assertEqual(copied, output)
        for changes in ({"metadata": None}, {"baseline": None}, {"scenarios": ()},
                        {"scenarios": [output.scenarios[0]] * 2}, {"scenarios": [scenario()]}):
            with self.assertRaises(ModelValidationError):
                replace(output, **changes)
        with self.assertRaises(ModelValidationError):
            replace(output, baseline=output.scenarios[0].result)

    def test_pair_rejects_incompatible_horizon_assumptions_and_provenance(self):
        output = comparison()
        item = output.scenarios[0]
        wrong_horizon = synthetic_summary([0, 100])
        bad_config = replace(item.result, metadata=replace(
            item.result.metadata, assumptions=assumptions(seed=43),
        ))
        generated = replace(item.result, metadata=replace(item.result.metadata, path_source="generated"))
        for bad in (None, wrong_horizon, bad_config, generated):
            with self.assertRaises(ModelValidationError):
                replace(item, result=bad)
        with self.assertRaises(ModelValidationError):
            replace(item, baseline=generated)
        with self.assertRaises(ModelValidationError):
            replace(output, metadata=replace(output.metadata, assumptions=assumptions(seed=43)))

    def test_deltas_are_recomputed_not_carried_when_replacing_a_pair(self):
        output = comparison()
        same = replace(output.scenarios[0], result=output.baseline)
        self.assertEqual(same.success_count_delta, 0)
        self.assertEqual(same.final_balance_delta, AmountPercentileDelta(p10=0, p50=0, p90=0))


class WhatIfEngineErrorTests(unittest.TestCase):
    def test_generation_error_preserves_market_location_without_partial_calculation(self):
        failure = ReturnPathGenerationError("annual_returns", path_index=2, age=61)
        with (
            patch(ENGINE + "generate_return_paths", side_effect=failure),
            patch(ENGINE + "calculate_monte_carlo_paths") as calc,
        ):
            with self.assertRaises(WhatIfCalculationError) as error:
                calculate_what_if(profile(), assumptions(), [scenario()])
        self.assertEqual((error.exception.scope, error.exception.stage, error.exception.field_name),
                         ("market", "return_generation", "annual_returns"))
        self.assertEqual((error.exception.path_index, error.exception.age), (2, 61))
        self.assertIsNone(error.exception.scenario_index)
        self.assertTrue(error.exception.__suppress_context__)
        calc.assert_not_called()

    def test_actual_baseline_cashflow_failure_aborts_before_any_scenario(self):
        baseline = profile(liquid_assets=10 ** 400)
        with patch(ENGINE + "calculate_monte_carlo_paths", wraps=calculate_monte_carlo_paths) as calc:
            with self.assertRaises(WhatIfCalculationError) as error:
                calculate_what_if(baseline, assumptions(annual_return_volatility=0), [scenario()])
        self.assertEqual(calc.call_count, 1)
        self.assertEqual((error.exception.scope, error.exception.stage, error.exception.field_name),
                         ("baseline", "cashflow", "liquid_assets"))
        self.assertEqual(error.exception.path_index, 0)
        self.assertIsNone(error.exception.scenario_index)
        self.assertNotIn(str(10 ** 400), str(error.exception))

    def test_actual_scenario_cashflow_failure_aborts_remaining_scenarios(self):
        baseline = profile(current_age=58)
        items = (scenario("valid_first"), scenario("secret_identifier", monthly_savings=10 ** 400),
                 scenario("never_run"))
        with patch(ENGINE + "calculate_monte_carlo_paths", wraps=calculate_monte_carlo_paths) as calc:
            with self.assertRaises(WhatIfCalculationError) as error:
                calculate_what_if(baseline, assumptions(annual_return_volatility=0), items)
        self.assertEqual(calc.call_count, 3)
        self.assertEqual((error.exception.scope, error.exception.stage, error.exception.field_name),
                         ("scenario", "cashflow", "savings"))
        self.assertEqual((error.exception.scenario_index, error.exception.path_index, error.exception.age),
                         (1, 0, 58))
        self.assertNotIn("secret_identifier", str(error.exception))
        self.assertTrue(error.exception.__suppress_context__)

    def test_aggregation_failure_retains_stage_and_scope(self):
        failure = MonteCarloCalculationError("aggregation", "asset_percentiles", age=61)
        with patch(ENGINE + "calculate_monte_carlo_paths", side_effect=failure):
            with self.assertRaises(WhatIfCalculationError) as error:
                calculate_what_if_paths(profile(), assumptions(), [scenario()], annual_return_paths=PATHS)
        self.assertEqual((error.exception.scope, error.exception.stage, error.exception.age),
                         ("baseline", "aggregation", 61))

    def test_finite_summaries_with_overflowing_difference_abort_whole_comparison(self):
        # Avoid overflow in a fixture's quantile arithmetic: replace constant
        # summaries directly with finite endpoints, then exercise real subtraction.
        base = synthetic_summary([0, 0])
        def constant(amount):
            return replace(base, asset_percentiles=(AssetPercentiles(
                age=60, balance=AmountPercentiles(p10=amount, p50=amount, p90=amount),
            ),))
        with patch(ENGINE + "calculate_monte_carlo_paths",
                   side_effect=(constant(-1e308), constant(1e308))) as calc:
            with self.assertRaises(WhatIfCalculationError) as error:
                calculate_what_if_paths(profile(end_age=60), assumptions(num_paths=2),
                                        [scenario("first"), scenario("never_run")],
                                        annual_return_paths=((0,), (0,)))
        self.assertEqual(calc.call_count, 2)
        self.assertEqual((error.exception.scope, error.exception.stage, error.exception.field_name),
                         ("scenario", "comparison", "asset_deltas.p10"))
        self.assertEqual((error.exception.scenario_index, error.exception.age), (0, 60))
        self.assertIsNone(error.exception.path_index)
        self.assertTrue(error.exception.__suppress_context__)


if __name__ == "__main__":
    unittest.main()
