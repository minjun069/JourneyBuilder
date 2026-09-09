"""Output-only numeric boundaries and deliberately corrupted model regressions."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from math import nextafter, inf
from pathlib import Path
import sys
import traceback
import unittest
from unittest.mock import patch


AI_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(AI_PROJECT_ROOT))

from retirement_simulation import (  # noqa: E402
    AmountPercentiles, ModelValidationError, RetirementAnalysisError, RetirementAnalysisResult,
    WhatIfResult, WhatIfScenarioResult, analysis_error_to_dict, analysis_to_dict, analyze_retirement,
)
from retirement_simulation.tests.test_analysis import compare, lower_expenses, request  # noqa: E402
from retirement_simulation.tests.test_input_adapter import assumption_data, saved_data  # noqa: E402


LIMIT = 9_007_199_254_740_991


class AnalysisOutputEdgeTests(unittest.TestCase):
    def assert_output_error(self, model, code="invalid_analysis_result", path=None):
        with self.assertRaises(RetirementAnalysisError) as raised:
            analysis_to_dict(model)
        error = raised.exception
        self.assertEqual((error.code, error.stage, error.reason_code), (code, "output", None))
        if path is not None:
            self.assertEqual(error.field_name, path)
        self.assertTrue(error.__suppress_context__)
        self.assertIsNone(error.__cause__)
        self.assertNotIn("PRIVATE", str(error))
        self.assertNotIn("PRIVATE", repr(error))
        self.assertEqual(analysis_error_to_dict(error)["error"]["field_name"], error.field_name)
        return error

    def test_non_analysis_and_subclass_objects_are_rejected_without_inspection(self):
        class Unexpected:
            def __getattr__(self, name):
                raise AssertionError("must not inspect")
        for value in (None, True, {}, [], "PRIVATE", Unexpected()):
            self.assert_output_error(value, path="analysis")
        class DerivedAnalysis(RetirementAnalysisResult):
            pass
        original = compare()
        derived = DerivedAnalysis(mapped_input=original.mapped_input, scenario_definitions=original.scenario_definitions,
                                  scenario_profiles=original.scenario_profiles, calculation=original.calculation)
        self.assert_output_error(derived, path="analysis")

    def test_missing_required_fields_report_known_output_paths(self):
        cases = (
            (lambda m: m, "contract_version", "contract_version"),
            (lambda m: m, "mapped_input", "analysis"),
            (lambda m: m.mapped_input, "mapping_version", "mapping_version"),
            (lambda m: m.mapped_input.notices[0], "code", "notices[0].code"),
            (lambda m: m.mapped_input.profile, "liquid_assets", "baseline_profile.liquid_assets"),
            (lambda m: m.calculation.metadata.assumptions, "seed", "metadata.assumptions.seed"),
            (lambda m: m.calculation.metadata, "python_version", "metadata.python_version"),
            (lambda m: m.calculation.baseline, "asset_percentiles", "baseline.asset_percentiles"),
            (lambda m: m.calculation.baseline.asset_percentiles[0], "balance", "baseline.asset_percentiles[0].balance"),
            (lambda m: m.calculation.scenarios[0], "first_depletion_deltas", "scenarios[0].delta.first_depletion_deltas"),
        )
        for select, name, path in cases:
            model = compare()
            object.__delattr__(select(model), name)
            self.assert_output_error(model, path=path)

    def test_versions_are_validated_without_relabeling(self):
        cases = (
            (lambda m: m, "contract_version", "contract_version"),
            (lambda m: m.mapped_input, "mapping_version", "mapping_version"),
            (lambda m: m.calculation.metadata, "model_version", "metadata.mc_model_version"),
            (lambda m: m.calculation, "model_version", "metadata.what_if_model_version"),
        )
        for select, name, path in cases:
            model = compare()
            object.__setattr__(select(model), name, "PRIVATE_VERSION")
            self.assert_output_error(model, path=path)

    def test_wrong_scalar_types_are_not_stringified_or_coerced(self):
        for value in (True, False, None, "PRIVATE", Decimal(100), [], {}):
            model = compare()
            object.__setattr__(model.calculation.baseline.asset_percentiles[0].balance, "p50", value)
            self.assert_output_error(model, path="baseline.asset_percentiles[0].balance.p50")
        for value in (True, 60.0, "60", Decimal(60)):
            model = compare()
            object.__setattr__(model.mapped_input.profile, "current_age", value)
            self.assert_output_error(model, path="baseline_profile.current_age")

    def test_unsafe_integer_source_values_fail_output_without_changing_engine_limits(self):
        for amount in (LIMIT + 1, 10**16):
            values = request()
            values["saved_data"]["financial_assets"][0]["current_value"] = amount
            model = analyze_retirement(**values)
            self.assertEqual(model.mapped_input.profile.liquid_assets, amount)
            self.assert_output_error(model, "unsafe_json_number", "baseline_profile.liquid_assets")

    def test_exact_integer_bound_is_accepted(self):
        values = request()
        values["saved_data"]["financial_assets"][0]["current_value"] = LIMIT
        model = analyze_retirement(**values)
        self.assertEqual(analysis_to_dict(model)["baseline_profile"]["liquid_assets"], LIMIT)

    def test_large_configured_seed_is_rejected_even_when_unused(self):
        for volatility in (0.0, 0.1):
            values = request() | {"assumption_values": assumption_data(seed=10**100, annual_return_volatility=volatility)}
            model = analyze_retirement(**values)
            self.assertEqual(model.calculation.metadata.assumptions.seed, 10**100)
            self.assert_output_error(model, "unsafe_json_number", "metadata.assumptions.seed")

    def test_unsafe_change_value_is_detected_even_when_post_retirement_savings_are_unused(self):
        model = analyze_retirement(**request(), scenarios=[{"scenario_id": "unused", "changes": {"monthly_savings": LIMIT + 1}}])
        self.assert_output_error(model, "unsafe_json_number", "scenarios[0].changes.monthly_savings")

    def test_huge_integer_does_not_overflow_conversion_or_echo_digits(self):
        model = compare()
        object.__setattr__(model.mapped_input.profile, "monthly_savings", 10**5000)
        self.assert_output_error(model, "unsafe_json_number", "baseline_profile.monthly_savings")

    def test_nonfinite_output_amounts_are_unsafe_not_null(self):
        for value in (float("nan"), inf, -inf):
            model = compare()
            object.__setattr__(model.calculation.baseline.asset_percentiles[0].balance, "p10", value)
            self.assert_output_error(model, "unsafe_json_number", "baseline.asset_percentiles[0].balance.p10")

    def test_finite_float_bounds_are_inclusive_and_next_values_are_rejected(self):
        for amount in (float(LIMIT), -float(LIMIT)):
            model = analyze_retirement(**request())
            for name in ("p10", "p50", "p90"):
                object.__setattr__(model.calculation.asset_percentiles[0].balance, name, amount)
            self.assertEqual(analysis_to_dict(model)["baseline"]["final_balance_percentiles"]["p50"], amount)
        for amount in (nextafter(float(LIMIT), inf), nextafter(-float(LIMIT), -inf)):
            model = analyze_retirement(**request())
            object.__setattr__(model.calculation.asset_percentiles[0].balance, "p50", amount)
            self.assert_output_error(model, "unsafe_json_number", "baseline.asset_percentiles[0].balance.p50")

    def test_float_precision_and_negative_zero_are_not_rounded(self):
        for amount in (0.12345678901234568, -0.0):
            model = analyze_retirement(**request())
            for name in ("p10", "p50", "p90"):
                object.__setattr__(model.calculation.asset_percentiles[0].balance, name, amount)
            output = analysis_to_dict(model)["baseline"]["final_balance_percentiles"]["p50"]
            self.assertEqual(output.hex(), amount.hex())

    def test_nonfinite_assumption_field_reports_its_output_location(self):
        for name in ("annual_return_mean", "annual_return_volatility", "annual_inflation_rate"):
            model = compare()
            object.__setattr__(model.calculation.metadata.assumptions, name, float("nan"))
            self.assert_output_error(model, "unsafe_json_number", f"metadata.assumptions.{name}")

    def test_nested_profile_and_delta_numeric_paths_are_precise(self):
        cases = (
            (lambda m: m.mapped_input.profile.retirement_expenses[0], "monthly_amount", LIMIT + 1,
             "baseline_profile.retirement_expenses[0].monthly_amount"),
            (lambda m: m.scenario_definitions[0].changes.retirement_expenses[0], "monthly_amount", LIMIT + 1,
             "scenarios[0].changes.retirement_expenses[0].monthly_amount"),
            (lambda m: m.calculation.scenarios[0].result.final_shortfall_percentiles, "p90", inf,
             "scenarios[0].result.final_shortfall_percentiles.p90"),
            (lambda m: m.calculation.scenarios[0].asset_deltas[0].balance, "p10", inf,
             "scenarios[0].delta.asset_deltas[0].balance.p10"),
            (lambda m: m.calculation.scenarios[0].first_depletion_deltas[0], "count", LIMIT + 1,
             "scenarios[0].delta.first_depletion_deltas[0].count"),
        )
        for select, name, value, path in cases:
            model = compare()
            object.__setattr__(select(model), name, value)
            self.assert_output_error(model, "unsafe_json_number", path)

    def test_unsafe_delta_is_rejected_even_when_both_individual_balances_fit(self):
        model = compare()
        base = model.calculation.baseline
        changed = model.calculation.scenarios[0].result
        base = replace(base, asset_percentiles=(replace(base.asset_percentiles[0], balance=AmountPercentiles(
            p10=-float(LIMIT), p50=-float(LIMIT), p90=-float(LIMIT),
        )),), final_shortfall_percentiles=AmountPercentiles(p10=LIMIT, p50=LIMIT, p90=LIMIT))
        changed = replace(changed, asset_percentiles=(replace(changed.asset_percentiles[0], balance=AmountPercentiles(
            p10=float(LIMIT), p50=float(LIMIT), p90=float(LIMIT),
        )),))
        pair = WhatIfScenarioResult(scenario_id="lower_expenses", baseline=base, result=changed)
        calculation = WhatIfResult(metadata=model.calculation.metadata, baseline=base, scenarios=(pair,))
        model = replace(model, scenario_definitions=model.scenario_definitions[:1],
                        scenario_profiles=model.scenario_profiles[:1], calculation=calculation)
        self.assert_output_error(model, "unsafe_json_number", "scenarios[0].delta.asset_deltas[0].balance.p10")

    def test_bad_counts_and_percentile_order_are_invalid_models(self):
        model = compare()
        object.__setattr__(model.calculation.baseline.first_depletion_counts[0], "count", 3)
        self.assert_output_error(model, path="baseline")
        model = compare()
        object.__setattr__(model.calculation.baseline.asset_percentiles[0].balance, "p50", -3000.0)
        self.assert_output_error(model, path="baseline.asset_percentiles[0].balance")

    def test_zero_paths_is_rejected_before_probability_division(self):
        model = compare()
        object.__setattr__(model.calculation.metadata.assumptions, "num_paths", 0)
        self.assert_output_error(model, path="metadata.assumptions")

    def test_missing_years_and_wrong_age_order_are_not_filled_or_sorted(self):
        for mutate in (lambda rows: (), lambda rows: rows[::-1], lambda rows: rows[1:]):
            model = analyze_retirement(saved_data(), assumption_data())
            object.__setattr__(model.calculation, "asset_percentiles", mutate(model.calculation.asset_percentiles))
            self.assert_output_error(model, path="baseline")

    def test_wrong_model_types_lists_and_cycles_are_structural_errors(self):
        cases = (
            (lambda m: m, "mapped_input", None, "analysis"),
            (lambda m: m, "scenario_profiles", [], "scenarios"),
            (lambda m: m.mapped_input, "notices", [], "notices"),
            (lambda m: m.mapped_input.profile, "retirement_expenses", [], "baseline_profile.retirement_expenses"),
            (lambda m: m.calculation.baseline.asset_percentiles[0], "balance", {}, "baseline.asset_percentiles[0].balance"),
        )
        for select, name, value, path in cases:
            model = compare()
            object.__setattr__(select(model), name, value)
            self.assert_output_error(model, path=path)
        model = compare()
        object.__setattr__(model.mapped_input.profile, "retirement_expenses", (model.mapped_input.profile,))
        self.assert_output_error(model, path="baseline_profile.retirement_expenses[0]")

    def test_invalid_notice_identity_or_path_is_not_output_as_free_text(self):
        for field in ("code", "field_name"):
            model = compare()
            object.__setattr__(model.mapped_input.notices[0], field, "PRIVATE_NOTICE")
            error = self.assert_output_error(model, path="notices[0]")
            self.assertNotIn("PRIVATE_NOTICE", "".join(traceback.format_exception(error)))

    def test_wrong_mode_and_mismatched_scenario_counts_are_rejected(self):
        model = compare()
        object.__setattr__(model, "calculation", analyze_retirement(**request()).calculation)
        self.assert_output_error(model, path="analysis")
        for field in ("scenario_definitions", "scenario_profiles"):
            model = compare()
            object.__setattr__(model, field, getattr(model, field)[:1])
            self.assert_output_error(model, path="scenarios")
        model = compare()
        object.__setattr__(model.calculation, "scenarios", model.calculation.scenarios[:1])
        self.assert_output_error(model, path="scenarios")

    def test_scenario_identity_order_and_application_must_match(self):
        model = compare()
        object.__setattr__(model.calculation, "scenarios", model.calculation.scenarios[::-1])
        self.assert_output_error(model, path="scenarios[0].scenario_id")
        model = compare()
        object.__setattr__(model, "scenario_profiles", model.scenario_profiles[::-1])
        self.assert_output_error(model, path="analysis")

    def test_batch_and_child_execution_sources_are_checked(self):
        model = compare()
        object.__setattr__(model.calculation.metadata, "path_source", "provided")
        self.assert_output_error(model, path="metadata.path_source")
        model = compare()
        object.__setattr__(model.calculation.scenarios[0].result.metadata, "path_source", "generated")
        self.assert_output_error(model, path="scenarios[0].result.execution.path_source")

    def test_child_assumptions_environment_and_hidden_baseline_are_checked(self):
        for name, value in (("python_version", "3.9.99"), ("python_implementation", "PRIVATE_IMPL")):
            model = compare()
            object.__setattr__(model.calculation.scenarios[0].result.metadata, name, value)
            self.assert_output_error(model, path="scenarios[0].result.execution")
        model = compare()
        changed = model.calculation.scenarios[0].result
        metadata = deepcopy(changed.metadata)
        object.__setattr__(metadata.assumptions, "seed", True)
        object.__setattr__(changed, "metadata", metadata)
        self.assert_output_error(model, path="scenarios[0].result.execution")
        model = compare()
        other = deepcopy(model.calculation.baseline)
        object.__setattr__(other.asset_percentiles[0].balance, "p50", False)
        object.__setattr__(model.calculation.scenarios[0], "baseline", other)
        self.assert_output_error(model, path="baseline.asset_percentiles[0].balance.p50")

    def test_corrupted_stored_deltas_are_not_silently_recomputed(self):
        model = compare()
        amount = model.calculation.scenarios[0].asset_deltas[0].balance
        object.__setattr__(amount, "p50", 2500.0)
        original = deepcopy(model)
        self.assert_output_error(model, path="scenarios[0].delta")
        self.assertEqual(model, original)
        self.assertEqual(amount.p50, 2500.0)

    def test_delta_percentiles_do_not_gain_a_sorting_requirement(self):
        model = compare()
        base = model.calculation.baseline
        changed = model.calculation.scenarios[0].result
        base = replace(base, asset_percentiles=(replace(base.asset_percentiles[0], balance=AmountPercentiles(p10=-3000, p50=-2000, p90=-1000)),))
        changed = replace(changed, asset_percentiles=(replace(changed.asset_percentiles[0], balance=AmountPercentiles(p10=100, p50=200, p90=300)),))
        pair = WhatIfScenarioResult(scenario_id="lower_expenses", baseline=base, result=changed)
        calculation = WhatIfResult(metadata=model.calculation.metadata, baseline=base, scenarios=(pair,))
        model = replace(model, scenario_definitions=model.scenario_definitions[:1],
                        scenario_profiles=model.scenario_profiles[:1], calculation=calculation)
        self.assertEqual(analysis_to_dict(model)["scenarios"][0]["delta"]["final_balance_delta"], {"p10": 3100, "p50": 2200, "p90": 1300})

    def test_unsorted_applied_profile_is_rejected_without_repair(self):
        model = analyze_retirement(saved_data(), assumption_data())
        profile = model.mapped_input.profile
        object.__setattr__(profile, "retirement_expenses", profile.retirement_expenses[::-1])
        original = deepcopy(model)
        self.assert_output_error(model, path="baseline_profile")
        self.assertEqual(model, original)

    def test_late_failure_has_no_partial_return_and_does_not_poison_next_call(self):
        model = compare()
        good = deepcopy(model)
        object.__setattr__(model.calculation.scenarios[1].result.final_shortfall_percentiles, "p50", inf)
        error = self.assert_output_error(model, "unsafe_json_number", "scenarios[1].result.final_shortfall_percentiles.p50")
        self.assertFalse(hasattr(error, "result"))
        self.assertFalse(hasattr(error, "payload"))
        self.assertEqual(analysis_to_dict(good), analysis_to_dict(compare()))

    def test_unexpected_bugs_memory_and_interruptions_are_not_hidden(self):
        model = compare()
        for error in (RuntimeError("bug"), MemoryError(), KeyboardInterrupt(), SystemExit(), OverflowError("unexpected")):
            with patch("retirement_simulation.analysis_output.copy", side_effect=error):
                with self.assertRaises(type(error)) as raised:
                    analysis_to_dict(model)
            self.assertIs(raised.exception, error)


if __name__ == "__main__":
    unittest.main()
