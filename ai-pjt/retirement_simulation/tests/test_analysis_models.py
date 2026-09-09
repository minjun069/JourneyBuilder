"""Analysis bundle invariants and safe execution diagnostic construction."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


AI_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(AI_PROJECT_ROOT))

from retirement_simulation import (  # noqa: E402
    InputMappingError, ModelValidationError, RetirementAnalysisError,
    RetirementAnalysisResult, analyze_retirement,
    map_retirement_profile, map_simulation_assumptions, map_what_if_scenarios,
)
from retirement_simulation.tests.test_analysis import ANALYSIS, compare, lower_expenses, request  # noqa: E402
from retirement_simulation.tests.test_input_adapter import assumption_data  # noqa: E402


class AnalysisResultTests(unittest.TestCase):
    def assert_model_error(self, original, field, **changes):
        with self.assertRaises(ModelValidationError) as raised:
            replace(original, **changes)
        self.assertEqual(raised.exception.field_name, field)
        return raised.exception

    def test_baseline_and_what_if_bundles_can_be_reconstructed_without_calculation(self):
        for original in (analyze_retirement(**request()), compare()):
            with (
                patch(ANALYSIS + "calculate_monte_carlo", side_effect=AssertionError("recalculation")) as mc,
                patch(ANALYSIS + "calculate_what_if", side_effect=AssertionError("recalculation")) as what_if,
            ):
                self.assertEqual(replace(original), original)
            mc.assert_not_called()
            what_if.assert_not_called()

    def test_frozen_slots_and_repr_hide_nested_financial_data(self):
        original = compare()
        self.assertFalse(hasattr(original, "__dict__"))
        for field in ("mapped_input", "scenario_definitions", "scenario_profiles", "calculation", "contract_version"):
            with self.assertRaises(FrozenInstanceError):
                setattr(original, field, None)
        for private in ("lower_expenses", "10000", "2000", "800", "financial_assets", "notices"):
            self.assertNotIn(private, repr(original))
        self.assertEqual(repr(original), "RetirementAnalysisResult(contract_version='retirement-analysis-v1')")

    def test_version_is_fixed_and_constructor_requires_keyword_arguments(self):
        original = compare()
        with self.assertRaises(TypeError):
            RetirementAnalysisResult(original.mapped_input, (), (), original.calculation)
        # dataclasses.replace uses TypeError on 3.13, ValueError on older Python.
        with self.assertRaises((TypeError, ValueError)):
            replace(original, contract_version="PRIVATE_VERSION")
        with self.assertRaises(TypeError):
            RetirementAnalysisResult(mapped_input=original.mapped_input, scenario_definitions=(),
                                     scenario_profiles=(), calculation=original.calculation,
                                     contract_version="PRIVATE_VERSION")
        with self.assertRaises(TypeError):
            RetirementAnalysisResult()

    def test_mapping_result_type_is_required(self):
        original = compare()
        for value in (None, {}, original.mapped_input.profile, True):
            self.assert_model_error(original, "mapped_input", mapped_input=value)

    def test_scenario_collections_require_models_and_enforce_count_limit(self):
        original = compare()
        for field in ("scenario_definitions", "scenario_profiles"):
            for value in (None, {}, True, "PRIVATE", iter(()), [None], [lower_expenses()],
                          list(getattr(original, field)[:1]) * 11):
                self.assert_model_error(original, field, **{field: value})

    def test_list_containers_are_detached_as_tuples(self):
        original = compare()
        definitions, profiles = list(original.scenario_definitions), list(original.scenario_profiles)
        copied = replace(original, scenario_definitions=definitions, scenario_profiles=profiles)
        definitions.clear()
        profiles.clear()
        self.assertEqual(copied, original)
        self.assertIsInstance(copied.scenario_definitions, tuple)
        self.assertIsInstance(copied.scenario_profiles, tuple)

    def test_profile_count_must_equal_definition_count(self):
        original = compare()
        for values in ((), original.scenario_profiles[:1]):
            self.assert_model_error(original, "scenario_profiles", scenario_profiles=values)

    def test_duplicate_definition_ids_are_rejected_without_values(self):
        original = compare()
        error = self.assert_model_error(original, "scenario_definitions",
                                        scenario_definitions=(original.scenario_definitions[0],) * 2)
        self.assertNotIn("lower_expenses", str(error))

    def test_calculation_type_must_match_inferred_mode(self):
        baseline, what_if = analyze_retirement(**request()), compare()
        self.assert_model_error(baseline, "calculation", calculation=what_if.calculation)
        self.assert_model_error(what_if, "calculation", calculation=baseline.calculation)
        for invalid in (None, {}, True):
            self.assert_model_error(baseline, "calculation", calculation=invalid)
        self.assert_model_error(what_if, "calculation", scenario_definitions=(), scenario_profiles=())

    def test_calculation_ids_order_and_count_must_match_definitions(self):
        original = compare()
        changed_id = replace(original.calculation.scenarios[0], scenario_id="different")
        for items in (original.calculation.scenarios[::-1], original.calculation.scenarios[:1],
                      (changed_id, original.calculation.scenarios[1])):
            self.assert_model_error(original, "calculation.scenarios",
                                    calculation=replace(original.calculation, scenarios=items))

    def test_profiles_must_equal_each_independent_application(self):
        original = compare()
        for profiles in (original.scenario_profiles[::-1], (original.mapped_input.profile,) * 2,
                         (replace(original.scenario_profiles[0], monthly_savings=1), original.scenario_profiles[1])):
            self.assert_model_error(original, "scenario_profiles[0]", scenario_profiles=profiles)

    def test_invalid_definition_application_is_a_safe_model_error(self):
        original = compare()
        definition = replace(original.scenario_definitions[0],
                             changes=replace(original.scenario_definitions[0].changes, retirement_age=61))
        error = self.assert_model_error(original, "scenario_profiles[0]",
                                        scenario_definitions=(definition, original.scenario_definitions[1]))
        self.assertTrue(error.__suppress_context__)
        self.assertNotIn("lower_expenses", str(error))

    def test_calculation_horizon_must_match_baseline_profile(self):
        original = analyze_retirement(**request())
        profile = original.mapped_input.profile
        for different in (replace(profile, current_age=59), replace(
            profile, end_age=61, retirement_expenses=(replace(profile.retirement_expenses[0], to_age=61),),
        )):
            self.assert_model_error(original, "calculation.metadata",
                                    mapped_input=replace(original.mapped_input, profile=different))

    def test_provided_path_results_cannot_claim_unified_execution_provenance(self):
        for original in (analyze_retirement(**request()), compare()):
            supplied = replace(original.calculation, metadata=replace(original.calculation.metadata, path_source="provided"))
            self.assert_model_error(original, "calculation.metadata", calculation=supplied)

    def test_current_inner_models_already_reject_inconsistent_assumptions_and_environment(self):
        original = compare().calculation
        child = original.scenarios[0]
        bad_metadata = replace(child.result.metadata, assumptions=replace(child.result.metadata.assumptions, seed=8))
        with self.assertRaises(ModelValidationError):
            replace(child, result=replace(child.result, metadata=bad_metadata))
        bad_metadata = deepcopy(child.result.metadata)
        object.__setattr__(bad_metadata, "python_version", "different-test-environment")
        with self.assertRaises(ModelValidationError):
            replace(child, result=replace(child.result, metadata=bad_metadata))


class AnalysisErrorTests(unittest.TestCase):
    def assert_invalid_error(self, code, path, reason, field):
        with self.assertRaises(ModelValidationError) as raised:
            RetirementAnalysisError(code, path, reason_code=reason)
        self.assertEqual(raised.exception.field_name, field)
        self.assertNotIn("PRIVATE", str(raised.exception))

    def test_codes_determine_stage_and_fixed_message(self):
        cases = (
            ("input_mapping_failed", "input", "saved_data", "invalid_type", "Retirement input mapping failed."),
            ("assumption_mapping_failed", "assumptions", "assumptions", "invalid_type", "Simulation assumption mapping failed."),
            ("scenario_mapping_failed", "scenarios", "scenarios", "invalid_type", "What-if scenario mapping failed."),
            ("scenario_application_failed", "scenarios", "scenarios[0].changes", "invalid_profile", "What-if scenario application failed."),
            ("calculation_failed", "calculation", "calculation", None, "Retirement calculation failed."),
        )
        for code, stage, path, reason, message in cases:
            error = RetirementAnalysisError(code, path, reason_code=reason)
            self.assertIsInstance(error, Exception)
            self.assertEqual((error.code, error.stage, error.field_name, error.reason_code, error.message),
                             (code, stage, path, reason, message))
            for name in ("code", "stage", "field_name", "reason_code", "message"):
                with self.assertRaises(AttributeError):
                    setattr(error, name, "PRIVATE_OVERRIDE")

    def test_unknown_and_non_string_codes_are_rejected(self):
        for code in (None, 1, True, [], {}, "PRIVATE_CODE", "calculation_failed\n"):
            self.assert_invalid_error(code, "calculation", None, "error.code")

    def test_reasons_are_required_for_mapping_and_application_and_absent_for_calculation(self):
        for code, path in (("input_mapping_failed", "saved_data"),
                           ("assumption_mapping_failed", "assumptions"),
                           ("scenario_mapping_failed", "scenarios"),
                           ("scenario_application_failed", "scenarios[0].changes")):
            for reason in (None, "PRIVATE_REASON", True, [], {}):
                self.assert_invalid_error(code, path, reason, "error.reason_code")
        self.assert_invalid_error("calculation_failed", "calculation", "invalid_value", "error.reason_code")
        self.assert_invalid_error("assumption_mapping_failed", "assumptions", "invalid_profile", "error.reason_code")
        self.assert_invalid_error("scenario_application_failed", "scenarios[0].changes", "invalid_type", "error.reason_code")

    def test_all_input_path_families_are_accepted(self):
        paths = (
            "saved_data", "basic_info", "basic_info.age", "basic_info.retirement_age", "basic_info.end_age",
            "confirmed_empty_sections", "confirmed_empty_sections[12]", "financial_assets", "financial_assets[0]",
            "financial_assets[10].id", "financial_assets[10].asset_type", "financial_assets[0].current_value",
            "financial_assets[0].monthly_savings", "pension_assets", "pension_assets[0]", "pension_assets[0].id",
            "pension_assets[0].receive_method", "pension_assets[0].pension_start_age",
            "pension_assets[0].expected_amount", "pension_assets[0].pension_period",
            "goal_periods", "goal_periods[0]", "goal_periods[0].phase", "goal_periods[0].from_age",
            "goal_periods[0].to_age", "goal_periods[0].items", "goal_periods[0].items[10]",
            "goal_periods[0].items[10].id", "goal_periods[0].items[10].amount",
            "real_estate_assets", "real_estate_assets[0].id", "liabilities[0]", "goal_milestones[1].id",
        )
        for path in paths:
            self.assertEqual(RetirementAnalysisError("input_mapping_failed", path, reason_code="invalid_value").field_name, path)

    def test_assumption_scenario_and_application_paths_are_allowed(self):
        for key in assumption_data():
            RetirementAnalysisError("assumption_mapping_failed", f"assumptions.{key}", reason_code="invalid_value")
        for path in ("scenarios", "scenarios[9]", "scenarios[0].scenario_id", "scenarios[0].changes",
                     "scenarios[0].changes.monthly_savings", "scenarios[0].changes.retirement_age",
                     "scenarios[0].changes.retirement_expenses", "scenarios[0].changes.retirement_expenses[19]",
                     "scenarios[0].changes.retirement_expenses[19].from_age",
                     "scenarios[0].changes.retirement_expenses[19].to_age",
                     "scenarios[0].changes.retirement_expenses[19].monthly_amount"):
            RetirementAnalysisError("scenario_mapping_failed", path, reason_code="invalid_value")
        for suffix in ("", ".monthly_savings", ".retirement_age", ".retirement_expenses"):
            RetirementAnalysisError("scenario_application_failed", "scenarios[0].changes" + suffix, reason_code="invalid_profile")

    def test_calculation_locations_only_allow_known_scope_stage_field_combinations(self):
        for path in ("calculation", "calculation.market.return_generation.annual_returns",
                     "calculation.baseline.cashflow.savings", "calculation.scenarios[9].aggregation.num_paths",
                     "calculation.scenarios[0].comparison.final_shortfall_delta.p10"):
            RetirementAnalysisError("calculation_failed", path)
        for path in ("calculation.PRIVATE", "calculation.market.cashflow.savings",
                     "calculation.baseline.comparison.asset_deltas.p50", "calculation.scenarios[10].cashflow.savings",
                     "calculation.baseline.cashflow.PRIVATE", "calculation.baseline.cashflow.savings.age",
                     "calculation.baseline.return_generation.annual_returns"):
            self.assert_invalid_error("calculation_failed", path, None, "error.field_name")

    def test_unknown_cross_stage_and_noncanonical_paths_are_rejected(self):
        for code, reason in (("input_mapping_failed", "invalid_value"),
                             ("assumption_mapping_failed", "invalid_value"),
                             ("scenario_mapping_failed", "invalid_value"),
                             ("scenario_application_failed", "invalid_profile"), ("calculation_failed", None)):
            for path in (None, True, [], {}, "PRIVATE_PATH", "", "scenarios[private_identifier].changes"):
                self.assert_invalid_error(code, path, reason, "error.field_name")
        for path in ("scenarios[01]", "scenarios[-1]", "scenarios[10]", "scenarios[０]", "scenarios[0].changes.end_age",
                     "scenarios[0].changes.retirement_expenses[0].monthly_amount\n", "assumptions.seed"):
            self.assert_invalid_error("scenario_mapping_failed", path, "invalid_value", "error.field_name")
        self.assert_invalid_error("input_mapping_failed", "financial_assets[0].PRIVATE", "invalid_value", "error.field_name")
        self.assert_invalid_error("assumption_mapping_failed", "basic_info.age", "invalid_value", "error.field_name")

    def test_manual_diagnostic_constructor_does_not_accept_stage_message_or_positional_reason(self):
        with self.assertRaises(TypeError):
            RetirementAnalysisError("calculation_failed", "calculation", None)
        for key in ("stage", "message", "payload", "path_index", "age"):
            with self.assertRaises(TypeError):
                RetirementAnalysisError("calculation_failed", "calculation", **{key: "PRIVATE"})

    def test_actual_saved_input_mapping_errors_keep_original_code_and_path(self):
        mutations = (
            lambda data: data.update(basic_info=None),
            lambda data: data["basic_info"].update(age=True),
            lambda data: data["financial_assets"][0].update(current_value="PRIVATE_VALUE"),
            lambda data: data["financial_assets"].append(dict(data["financial_assets"][0], id=1)),
            lambda data: data["goal_periods"][0].update(phase="PRIVATE_PHASE"),
            lambda data: data["goal_periods"][0]["items"][0].update(amount=None),
            lambda data: data["goal_periods"][0].update(items=[]),
            lambda data: data["goal_periods"][0].update(from_age=61, to_age=61),
            lambda data: data.update(real_estate_assets=[None]),
            lambda data: data.update(pension_assets=[{"receive_method": "PRIVATE", "pension_start_age": 60}]),
        )
        for mutate in mutations:
            values = request()
            values["saved_data"]["financial_assets"][0]["id"] = 1
            mutate(values["saved_data"])
            if values["saved_data"]["pension_assets"]:
                values["confirmed_empty_sections"] = ()
            original = deepcopy(values)
            with self.assertRaises(InputMappingError) as mapped:
                map_retirement_profile(values["saved_data"], confirmed_empty_sections=values["confirmed_empty_sections"])
            with self.assertRaises(RetirementAnalysisError) as analysis:
                analyze_retirement(**values)
            self.assertEqual((analysis.exception.reason_code, analysis.exception.field_name),
                             (mapped.exception.code, mapped.exception.field_name))
            self.assertEqual(values, original)
            self.assertNotIn("PRIVATE", str(analysis.exception))

    def test_actual_assumption_errors_keep_original_code_and_path(self):
        for config in (None, {}, assumption_data(seed=True), assumption_data(annual_return_mean=-1),
                       assumption_data(annual_return_volatility=float("nan")), assumption_data(PRIVATE_KEY=1)):
            with self.assertRaises(InputMappingError) as mapped:
                map_simulation_assumptions(config)
            with self.assertRaises(RetirementAnalysisError) as analysis:
                analyze_retirement(**(request() | {"assumption_values": config}))
            self.assertEqual((analysis.exception.reason_code, analysis.exception.field_name),
                             (mapped.exception.code, mapped.exception.field_name))

    def test_actual_scenario_errors_keep_nested_indices_and_codes(self):
        for periods in ([{}], [{"from_age": 60}], [{"from_age": 60, "to_age": 60}],
                        [{"from_age": 60, "to_age": 60, "monthly_amount": -1}], [{"PRIVATE_KEY": 0}]):
            values = [{"scenario_id": "keep", "changes": {}}, {"scenario_id": "invalid", "changes": {
                "retirement_expenses": [{"from_age": 59, "to_age": 59, "monthly_amount": 0}] + periods,
            }}]
            with self.assertRaises(InputMappingError) as mapped:
                map_what_if_scenarios(values)
            with self.assertRaises(RetirementAnalysisError) as analysis:
                analyze_retirement(**request(), scenarios=values)
            self.assertEqual((analysis.exception.reason_code, analysis.exception.field_name),
                             (mapped.exception.code, mapped.exception.field_name))


if __name__ == "__main__":
    unittest.main()
