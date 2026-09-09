"""Unified execution contracts using real engines and synthetic saved inputs."""

from __future__ import annotations

from contextlib import ExitStack, redirect_stderr, redirect_stdout
from copy import deepcopy
from dataclasses import replace
from io import StringIO
from pathlib import Path
import random
import sys
import traceback
from types import MappingProxyType
import unittest
from unittest.mock import Mock, patch


AI_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(AI_PROJECT_ROOT))

from retirement_simulation import (  # noqa: E402
    InputMappingError, ModelValidationError, MonteCarloCalculationError, MonteCarloResult,
    RetirementAnalysisError, RetirementAnalysisResult, ReturnPathGenerationError,
    WhatIfCalculationError, WhatIfResult, analyze_retirement, calculate_monte_carlo,
    calculate_what_if, create_what_if_profile, generate_return_paths,
    map_retirement_profile, map_simulation_assumptions, map_what_if_scenarios,
)
from retirement_simulation.monte_carlo import calculate_monte_carlo_paths  # noqa: E402
from retirement_simulation.tests.test_input_adapter import assumption_data, saved_data  # noqa: E402


ANALYSIS = "retirement_simulation.analysis."
ENGINE = "retirement_simulation.what_if_engine."


def request():
    """The one-year 10,000 - 12 * 1,000 example in the analysis contract."""
    return {
        "saved_data": {
            "basic_info": {"age": 60, "retirement_age": 60, "end_age": 60},
            "financial_assets": [{"asset_type": "예금/적금", "current_value": 10_000, "monthly_savings": 0}],
            "pension_assets": [],
            "goal_periods": [{"phase": "after", "from_age": 60, "to_age": 60,
                              "items": [{"amount": 1000}]}],
            "real_estate_assets": [], "liabilities": [], "goal_milestones": [],
        },
        "assumption_values": assumption_data(),
        "confirmed_empty_sections": ["pension_assets"],
    }


def lower_expenses():
    return {"scenario_id": "lower_expenses", "changes": {"retirement_expenses": [
        {"from_age": 60, "to_age": 60, "monthly_amount": 800},
    ]}}


def compare():
    return analyze_retirement(**request(), scenarios=[lower_expenses(), {"scenario_id": "keep", "changes": {}}])


class AnalysisExecutionTests(unittest.TestCase):
    def assert_analysis_error(self, values, code, stage, path, reason=None):
        with self.assertRaises(RetirementAnalysisError) as raised:
            analyze_retirement(**values)
        error = raised.exception
        self.assertEqual((error.code, error.stage, error.field_name, error.reason_code),
                         (code, stage, path, reason))
        self.assertIsNone(error.__cause__)
        self.assertTrue(error.__suppress_context__)
        return error

    def test_default_empty_scenarios_use_baseline_mode(self):
        for overrides in ({}, {"scenarios": []}, {"scenarios": ()}):
            result = analyze_retirement(**(request() | overrides))
            self.assertIsInstance(result, RetirementAnalysisResult)
            self.assertIsInstance(result.calculation, MonteCarloResult)
            self.assertEqual(result.mode, "baseline")
            self.assertEqual(result.contract_version, "retirement-analysis-v1")
            self.assertEqual((result.scenario_definitions, result.scenario_profiles), ((), ()))
            self.assertEqual(result.calculation.final_balance_percentiles.p50, -2000)
            self.assertEqual(result.calculation.success_probability, 0)

    def test_what_if_contract_example_uses_actual_summaries(self):
        result = analyze_retirement(**request(), scenarios=[lower_expenses()])
        self.assertIsInstance(result.calculation, WhatIfResult)
        self.assertEqual(result.mode, "what_if")
        changed = result.calculation.scenarios[0]
        self.assertEqual(result.calculation.baseline.final_balance_percentiles.p50, -2000)
        self.assertEqual(changed.result.final_balance_percentiles.p50, 400)
        self.assertEqual(changed.final_balance_delta.p50, 2400)
        self.assertEqual(changed.final_shortfall_delta.p50, -2000)
        self.assertEqual(changed.success_probability_delta_pp, 100)
        self.assertEqual(changed.result.first_depletion_counts[0].count, 0)

    def test_all_mapping_notices_and_versions_are_preserved_in_order(self):
        raw = saved_data()
        raw["real_estate_assets"] = [{"id": 1}]
        raw["liabilities"] = [{"id": 1}]
        raw["goal_milestones"] = [{"id": 1}]
        expected = map_retirement_profile(raw)
        result = analyze_retirement(raw, assumption_data())
        self.assertEqual(result.mapped_input, expected)
        self.assertEqual(result.mapped_input.notices, expected.notices)
        self.assertEqual(result.mapped_input.mapping_version, "retirement-input-v1")
        self.assertEqual(len(result.mapped_input.notices), 8)

    def test_baseline_calls_mapping_and_engine_exactly_once(self):
        values = request()
        with (
            patch(ANALYSIS + "map_retirement_profile", wraps=map_retirement_profile) as mapper,
            patch(ANALYSIS + "map_simulation_assumptions", wraps=map_simulation_assumptions) as config,
            patch(ANALYSIS + "map_what_if_scenarios", wraps=map_what_if_scenarios) as changes,
            patch(ANALYSIS + "calculate_monte_carlo", wraps=calculate_monte_carlo) as mc,
            patch(ANALYSIS + "calculate_what_if", side_effect=AssertionError("unexpected What-if")) as what_if,
        ):
            result = analyze_retirement(**values)
        mapper.assert_called_once_with(values["saved_data"], confirmed_empty_sections=["pension_assets"])
        config.assert_called_once_with(values["assumption_values"])
        changes.assert_called_once_with(())
        mc.assert_called_once_with(result.mapped_input.profile, result.calculation.metadata.assumptions)
        what_if.assert_not_called()

    def test_what_if_generates_one_common_batch_and_calculates_baseline_once(self):
        with (
            patch(ANALYSIS + "calculate_monte_carlo", side_effect=AssertionError("extra MC")) as mc,
            patch(ANALYSIS + "calculate_what_if", wraps=calculate_what_if) as what_if,
            patch(ENGINE + "generate_return_paths", wraps=generate_return_paths) as generate,
            patch(ENGINE + "calculate_monte_carlo_paths", wraps=calculate_monte_carlo_paths) as calc,
        ):
            result = compare()
        mc.assert_not_called()
        what_if.assert_called_once()
        generate.assert_called_once()
        self.assertEqual(calc.call_count, 3)
        self.assertIs(calc.call_args_list[0].args[0], result.mapped_input.profile)
        paths = calc.call_args_list[0].kwargs["annual_return_paths"]
        for call in calc.call_args_list:
            self.assertIs(call.kwargs["annual_return_paths"], paths)
        for row in result.calculation.scenarios:
            self.assertIs(row.baseline, result.calculation.baseline)

    def test_all_mapping_and_preapplication_happen_before_engine(self):
        manager = Mock()
        targets = (
            ("map_retirement_profile", map_retirement_profile),
            ("map_simulation_assumptions", map_simulation_assumptions),
            ("map_what_if_scenarios", map_what_if_scenarios),
            ("create_what_if_profile", create_what_if_profile),
            ("calculate_what_if", calculate_what_if),
        )
        with ExitStack() as stack:
            for name, function in targets:
                manager.attach_mock(stack.enter_context(patch(ANALYSIS + name, wraps=function)), name)
            compare()
        self.assertEqual([call[0] for call in manager.mock_calls], [
            "map_retirement_profile", "map_simulation_assumptions", "map_what_if_scenarios",
            "create_what_if_profile", "create_what_if_profile", "calculate_what_if",
            "create_what_if_profile", "create_what_if_profile",  # Result consistency only.
        ])

    def test_ten_scenarios_keep_request_order_and_common_baseline(self):
        definitions = [{"scenario_id": f"case_{i}", "changes": {}} for i in reversed(range(10))]
        result = analyze_retirement(**request(), scenarios=definitions)
        ids = [row["scenario_id"] for row in definitions]
        self.assertEqual([row.scenario_id for row in result.scenario_definitions], ids)
        self.assertEqual([row.scenario_id for row in result.calculation.scenarios], ids)
        self.assertEqual(len(result.scenario_profiles), 10)
        self.assertTrue(all(row.result == result.calculation.baseline for row in result.calculation.scenarios))

    def test_explicit_noop_is_a_comparison_and_zero_savings_is_not_a_noop(self):
        result = analyze_retirement(saved_data(), assumption_data(), scenarios=[
            {"scenario_id": "zero", "changes": {"monthly_savings": 0}},
            {"scenario_id": "keep", "changes": {}},
            {"scenario_id": "nulls", "changes": {"monthly_savings": None, "retirement_age": None,
                                                  "retirement_expenses": None}},
        ])
        self.assertEqual(result.mode, "what_if")
        self.assertEqual([p.monthly_savings for p in result.scenario_profiles], [0, 750_000, 750_000])
        self.assertNotEqual(result.calculation.scenarios[0].result, result.calculation.baseline)
        for row in result.calculation.scenarios[1:]:
            self.assertEqual(row.result, result.calculation.baseline)

    def test_applied_profiles_are_independent_and_do_not_shift_pensions(self):
        result = analyze_retirement(saved_data(), assumption_data(), scenarios=[
            {"scenario_id": "savings", "changes": {"monthly_savings": 1_000_000}},
            {"scenario_id": "late", "changes": {"retirement_age": 61}},
        ])
        self.assertEqual([p.monthly_savings for p in result.scenario_profiles], [1_000_000, 750_000])
        self.assertEqual([p.retirement_age for p in result.scenario_profiles], [60, 61])
        for profile in result.scenario_profiles:
            self.assertEqual(profile.pension_incomes, result.mapped_input.profile.pension_incomes)
        self.assertEqual(result.calculation.scenarios[0].result.final_balance_percentiles.p50, 24_000_000)

    def test_early_retirement_and_full_schedule_are_applied_together(self):
        result = analyze_retirement(saved_data(), assumption_data(), scenarios=[{
            "scenario_id": "early", "changes": {"retirement_age": 59, "retirement_expenses": [
                {"from_age": 75, "to_age": 90, "monthly_amount": 1_000_000},
                {"from_age": 59, "to_age": 74, "monthly_amount": 1_500_000},
            ]},
        }])
        self.assertEqual(result.scenario_profiles[0].retirement_age, 59)
        self.assertEqual([p.from_age for p in result.scenario_profiles[0].retirement_expenses], [59, 75])
        self.assertEqual([p.from_age for p in result.scenario_definitions[0].changes.retirement_expenses], [75, 59])

    def test_past_retirement_can_be_repeated_but_not_changed(self):
        raw = saved_data()
        raw["basic_info"]["age"] = 62
        original = deepcopy(raw)
        result = analyze_retirement(raw, assumption_data(), scenarios=[{
            "scenario_id": "same_age", "changes": {"retirement_age": 60},
        }])
        self.assertEqual(result.calculation.baseline, result.calculation.scenarios[0].result)
        self.assert_analysis_error({"saved_data": raw, "assumption_values": assumption_data(), "scenarios": [{
            "scenario_id": "change_past", "changes": {"retirement_age": 61},
        }]}, "scenario_application_failed", "scenarios", "scenarios[0].changes.retirement_age", "invalid_profile")
        self.assertEqual(raw, original)

    def test_json_number_guard_is_not_prematurely_applied_to_internal_results(self):
        values = request()
        values["saved_data"]["financial_assets"][0]["current_value"] = 10**16
        result = analyze_retirement(**values)
        self.assertEqual(result.mapped_input.profile.liquid_assets, 10**16)
        self.assertGreater(result.calculation.final_balance_percentiles.p50, 9007199254740991)

    def test_zero_volatility_records_generated_batch_and_provided_children(self):
        baseline, comparison = analyze_retirement(**request()), compare()
        for result in (baseline, comparison):
            metadata = result.calculation.metadata
            self.assertEqual(metadata.path_source, "generated")
            self.assertEqual(metadata.assumptions.seed, 7)
            self.assertEqual(metadata.return_model, "constant")
            self.assertIsNone(metadata.seed)
            self.assertIsNone(metadata.rng_algorithm)
        self.assertEqual(comparison.calculation.baseline.metadata.path_source, "provided")
        self.assertIsNone(comparison.calculation.baseline.metadata.seed)
        for row in comparison.calculation.scenarios:
            self.assertEqual(row.result.metadata.path_source, "provided")
            self.assertIsNone(row.result.metadata.seed)
        self.assertEqual(baseline.calculation.asset_percentiles, comparison.calculation.baseline.asset_percentiles)

    def test_seeded_runs_match_direct_engine_and_are_reproducible(self):
        values = request() | {"assumption_values": assumption_data(
            annual_return_mean=0.05, annual_return_volatility=0.10, num_paths=20, seed=10**100,
        ), "scenarios": [lower_expenses()]}
        first = analyze_retirement(**values)
        expected = calculate_what_if(map_retirement_profile(values["saved_data"],
                                     confirmed_empty_sections=values["confirmed_empty_sections"]).profile,
                                    map_simulation_assumptions(values["assumption_values"]),
                                    map_what_if_scenarios(values["scenarios"]))
        self.assertEqual(first.calculation, expected)
        self.assertEqual(first, analyze_retirement(**values))
        self.assertEqual(first.calculation.metadata.seed, 10**100)
        self.assertEqual(first.calculation.metadata.return_model, "lognormal_gross_return")
        baseline = analyze_retirement(**(values | {"scenarios": []}))
        self.assertEqual(baseline.calculation.asset_percentiles, first.calculation.baseline.asset_percentiles)

    def test_source_and_output_container_isolation(self):
        values = request() | {"scenarios": [lower_expenses()]}
        original = deepcopy(values)
        result = analyze_retirement(**values)
        self.assertEqual(values, original)
        values["saved_data"]["financial_assets"][0]["current_value"] = 0
        values["saved_data"]["goal_periods"][0]["items"].clear()
        values["assumption_values"]["seed"] = 999
        values["scenarios"][0]["changes"]["retirement_expenses"][0]["monthly_amount"] = 0
        values["scenarios"].clear()
        values["confirmed_empty_sections"].clear()
        self.assertEqual(result, analyze_retirement(**original))

    def test_mapping_proxy_inputs_and_tuple_changes_work(self):
        values = request()
        values["saved_data"] = MappingProxyType(values["saved_data"])
        values["assumption_values"] = MappingProxyType(values["assumption_values"])
        values["confirmed_empty_sections"] = ("pension_assets",)
        result = analyze_retirement(**values, scenarios=(MappingProxyType(lower_expenses()),))
        self.assertEqual(result, analyze_retirement(**request(), scenarios=[lower_expenses()]))

    def test_missing_confirmation_preserves_safe_mapping_error(self):
        self.assert_analysis_error(request() | {"confirmed_empty_sections": ()},
                                   "input_mapping_failed", "input", "pension_assets", "empty_confirmation_required")

    def test_input_failure_prevents_all_later_work(self):
        with (
            patch(ANALYSIS + "map_simulation_assumptions") as config,
            patch(ANALYSIS + "map_what_if_scenarios") as definitions,
            patch(ANALYSIS + "calculate_monte_carlo") as mc,
            patch(ANALYSIS + "calculate_what_if") as what_if,
        ):
            self.assert_analysis_error(request() | {"saved_data": None, "assumption_values": None, "scenarios": None},
                                       "input_mapping_failed", "input", "saved_data", "invalid_type")
        for mock in (config, definitions, mc, what_if):
            mock.assert_not_called()

    def test_assumption_failure_prevents_change_mapping_and_engines(self):
        with (
            patch(ANALYSIS + "map_what_if_scenarios") as definitions,
            patch(ANALYSIS + "calculate_monte_carlo") as mc,
            patch(ANALYSIS + "calculate_what_if") as what_if,
        ):
            self.assert_analysis_error(request() | {"assumption_values": {}, "scenarios": None},
                                       "assumption_mapping_failed", "assumptions",
                                       "assumptions.annual_return_mean", "missing_field")
        for mock in (definitions, mc, what_if):
            mock.assert_not_called()

    def test_scenario_mapping_failure_prevents_application_and_engines(self):
        cases = (
            (None, "scenarios", "invalid_type"),
            ([None] * 11, "scenarios", "invalid_value"),
            ([{"scenario_id": "baseline", "changes": {}}], "scenarios[0].scenario_id", "invalid_value"),
            ([lower_expenses(), lower_expenses()], "scenarios[1].scenario_id", "duplicate_id"),
            ([{"scenario_id": "empty", "changes": {"retirement_expenses": []}}],
             "scenarios[0].changes.retirement_expenses", "missing_value"),
        )
        for scenarios, path, reason in cases:
            with (
                patch(ANALYSIS + "create_what_if_profile") as apply,
                patch(ANALYSIS + "calculate_monte_carlo") as mc,
                patch(ANALYSIS + "calculate_what_if") as what_if,
            ):
                self.assert_analysis_error(request() | {"scenarios": scenarios},
                                           "scenario_mapping_failed", "scenarios", path, reason)
            for mock in (apply, mc, what_if):
                mock.assert_not_called()

    def test_later_application_failure_prevents_any_generation_or_calculation(self):
        values = request() | {"scenarios": [lower_expenses(), {"scenario_id": "early", "changes": {"retirement_age": 59}}]}
        original = deepcopy(values)
        with (
            patch(ANALYSIS + "calculate_monte_carlo") as mc,
            patch(ANALYSIS + "calculate_what_if") as what_if,
            patch(ENGINE + "generate_return_paths") as generate,
        ):
            self.assert_analysis_error(values, "scenario_application_failed", "scenarios",
                                       "scenarios[1].changes.retirement_age", "invalid_profile")
        for mock in (mc, what_if, generate):
            mock.assert_not_called()
        self.assertEqual(values, original)

    def test_end_age_failure_maps_to_editable_retirement_age(self):
        self.assert_analysis_error(request() | {"scenarios": [{"scenario_id": "late", "changes": {"retirement_age": 61}}]},
                                   "scenario_application_failed", "scenarios",
                                   "scenarios[0].changes.retirement_age", "invalid_profile")

    def test_expense_gaps_and_overlap_map_to_expenses(self):
        for periods in ([{"from_age": 61, "to_age": 61, "monthly_amount": 0}],
                        [{"from_age": 60, "to_age": 60, "monthly_amount": 0}] * 2):
            self.assert_analysis_error(request() | {"scenarios": [{"scenario_id": "invalid", "changes": {
                "retirement_expenses": periods,
            }}]}, "scenario_application_failed", "scenarios",
                "scenarios[0].changes.retirement_expenses", "invalid_profile")

    def test_unknown_application_field_is_shortened_without_echoing_message(self):
        with patch(ANALYSIS + "create_what_if_profile",
                   side_effect=ModelValidationError("PRIVATE_FIELD", "PRIVATE_MESSAGE")):
            error = self.assert_analysis_error(request() | {"scenarios": [lower_expenses()]},
                                               "scenario_application_failed", "scenarios",
                                               "scenarios[0].changes", "invalid_profile")
        self.assertNotIn("PRIVATE", str(error))
        self.assertNotIn("PRIVATE", "".join(traceback.format_exception(error)))

    def test_real_baseline_numeric_overflow_fails_whole_analysis(self):
        values = request()
        values["saved_data"]["financial_assets"][0]["current_value"] = 10**400
        self.assert_analysis_error(values, "calculation_failed", "calculation",
                                   "calculation.baseline.cashflow.liquid_assets")

    def test_real_later_scenario_numeric_overflow_returns_no_partial_bundle(self):
        bad = lower_expenses()
        bad["scenario_id"] = "overflow"
        bad["changes"]["retirement_expenses"][0]["monthly_amount"] = 10**400
        values = request() | {"scenarios": [lower_expenses(), bad]}
        original = deepcopy(values)
        with patch(ANALYSIS + "RetirementAnalysisResult", side_effect=AssertionError("partial result")) as bundle:
            self.assert_analysis_error(values, "calculation_failed", "calculation",
                                       "calculation.scenarios[1].cashflow.annual_expenses")
        bundle.assert_not_called()
        self.assertEqual(values, original)
        self.assertEqual(compare().calculation.scenarios[0].result.success_probability, 1)

    def test_generation_failures_are_wrapped_for_both_modes_without_detail_indices(self):
        for scenarios, module in (([], "retirement_simulation.monte_carlo."),
                                  ([lower_expenses()], ENGINE)):
            with patch(module + "generate_return_paths", side_effect=ReturnPathGenerationError(
                "annual_returns", path_index=1, age=60,
            )):
                error = self.assert_analysis_error(request() | {"scenarios": scenarios},
                                                   "calculation_failed", "calculation",
                                                   "calculation.market.return_generation.annual_returns")
            self.assertFalse(hasattr(error, "path_index"))
            self.assertFalse(hasattr(error, "age"))

    def test_known_calculation_locations_and_unknown_metadata_fallback(self):
        cases = (
            (MonteCarloCalculationError("aggregation", "num_paths"), "calculation.baseline.aggregation.num_paths"),
            (WhatIfCalculationError("comparison", "asset_deltas.p90", scenario_index=0),
             "calculation.scenarios[0].comparison.asset_deltas.p90"),
            (WhatIfCalculationError("cashflow", "closing_balance", scope="baseline"),
             "calculation.baseline.cashflow.closing_balance"),
            (MonteCarloCalculationError("PRIVATE_STAGE", "PRIVATE_FIELD"), "calculation"),
            (WhatIfCalculationError("cashflow", "savings", scope="PRIVATE_SCOPE"), "calculation"),
            (WhatIfCalculationError("comparison", "asset_deltas.p50", scenario_index=True), "calculation"),
            (WhatIfCalculationError("cashflow", "savings", scenario_index=10), "calculation"),
        )
        for failure, path in cases:
            with patch(ANALYSIS + "calculate_what_if", side_effect=failure):
                error = self.assert_analysis_error(request() | {"scenarios": [lower_expenses()]},
                                                   "calculation_failed", "calculation", path)
            self.assertNotIn("PRIVATE", str(error))

    def test_unexpected_bugs_memory_failure_and_interruptions_propagate(self):
        for target in ("map_retirement_profile", "calculate_monte_carlo"):
            for error in (RuntimeError("bug"), MemoryError(), KeyboardInterrupt(), SystemExit(),
                          ModelValidationError("internal", "bug"), OverflowError("unclassified")):
                with patch(ANALYSIS + target, side_effect=error):
                    with self.assertRaises(type(error)) as raised:
                        analyze_retirement(**request())
                self.assertIs(raised.exception, error)

    def test_wrong_engine_assumptions_are_rejected_as_programming_error(self):
        original = analyze_retirement(**request()).calculation
        wrong = replace(original, metadata=replace(original.metadata, assumptions=replace(
            original.metadata.assumptions, seed=8,
        )))
        with patch(ANALYSIS + "calculate_monte_carlo", return_value=wrong):
            with self.assertRaises(ModelValidationError) as raised:
                analyze_retirement(**request())
        self.assertEqual(raised.exception.field_name, "calculation.metadata.assumptions")

    def test_success_failure_and_retry_preserve_global_rng_and_do_not_print_or_use_io(self):
        values = request() | {"assumption_values": assumption_data(annual_return_volatility=0.1)}
        state, stdout, stderr = random.getstate(), StringIO(), StringIO()
        with ExitStack() as stack, redirect_stdout(stdout), redirect_stderr(stderr):
            guards = [stack.enter_context(patch(name, side_effect=AssertionError(name))) for name in (
                "builtins.open", "os.getenv", "socket.socket", "subprocess.run", "time.time",
            )]
            first = analyze_retirement(**values)
            self.assert_analysis_error(values | {"scenarios": None}, "scenario_mapping_failed",
                                       "scenarios", "scenarios", "invalid_type")
            self.assertEqual(first, analyze_retirement(**values))
        self.assertEqual(random.getstate(), state)
        self.assertEqual((stdout.getvalue(), stderr.getvalue()), ("", ""))
        for guard in guards:
            guard.assert_not_called()

    def test_private_values_are_not_retained_in_diagnostic_attributes_or_repr(self):
        values = request() | {"scenarios": [{"scenario_id": "private_identifier", "changes": {"PRIVATE_KEY": "PRIVATE_VALUE"}}]}
        error = self.assert_analysis_error(values, "scenario_mapping_failed", "scenarios",
                                           "scenarios[0].changes", "unknown_field")
        for text in (str(error), repr(error), repr(error.args), repr(error.__dict__)):
            self.assertNotIn("PRIVATE", text)
            self.assertNotIn("private_identifier", text)
        self.assertEqual(error.message, "What-if scenario mapping failed.")

    def test_existing_standalone_error_contract_is_unchanged(self):
        with self.assertRaises(InputMappingError):
            map_what_if_scenarios(None)
        with self.assertRaises(InputMappingError):
            map_retirement_profile(None)
        with self.assertRaises(InputMappingError):
            map_simulation_assumptions(None)

    def test_python_signature_rejects_unsupported_arguments(self):
        with self.assertRaises(TypeError):
            analyze_retirement()
        with self.assertRaises(TypeError):
            analyze_retirement({}, {}, [])
        for key in ("mode", "annual_return_paths", "current_form", "baseline_key"):
            with self.assertRaises(TypeError):
                analyze_retirement(**request(), **{key: None})


if __name__ == "__main__":
    unittest.main()
