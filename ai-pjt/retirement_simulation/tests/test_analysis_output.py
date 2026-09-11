"""Public output contract, real-engine values, JSON safety and isolation."""

from __future__ import annotations

from contextlib import ExitStack, redirect_stderr, redirect_stdout
from copy import deepcopy
from dataclasses import replace
from io import StringIO
from itertools import product
import json
from pathlib import Path
from platform import python_implementation, python_version
import random
import re
import sys
import unittest
from unittest.mock import patch


AI_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(AI_PROJECT_ROOT))

from retirement_simulation import (  # noqa: E402
    ModelValidationError, RetirementAnalysisError, analysis_error_to_dict, analysis_to_dict, analyze_retirement,
)
from retirement_simulation.tests.test_analysis import compare, lower_expenses, request  # noqa: E402
from retirement_simulation.tests.test_input_adapter import assumption_data, saved_data  # noqa: E402


def contract_examples():
    document = AI_PROJECT_ROOT / 'retirement_simulation/docs/analysis-contract.md'
    return [json.loads(block) for block in re.findall(r'```json\n(.*?)\n```', document.read_text(), re.S)]


def primitive_paths(value, path=""):
    if type(value) is dict:
        for name, child in value.items():
            yield from primitive_paths(child, f"{path}.{name}" if path else name)
    elif type(value) is list:
        for index, child in enumerate(value):
            yield from primitive_paths(child, f"{path}[{index}]")
    else:
        yield path, value


def container_ids(value):
    if type(value) is dict:
        yield id(value)
        for item in value.values():
            yield from container_ids(item)
    elif type(value) is list:
        yield id(value)
        for item in value:
            yield from container_ids(item)


class AnalysisOutputTests(unittest.TestCase):
    def test_full_documented_what_if_payload_matches_real_engine(self):
        _, values, expected = contract_examples()
        expected["metadata"].update(python_version=python_version(), python_implementation=python_implementation())
        actual = analysis_to_dict(analyze_retirement(**values))
        self.assertEqual(actual, expected)
        self.assertEqual(json.loads(json.dumps(actual, allow_nan=False)), expected)

    def test_full_baseline_payload_uses_same_values_with_generated_execution(self):
        _, values, expected = contract_examples()
        expected["metadata"].update(python_version=python_version(), python_implementation=python_implementation(),
                                    what_if_model_version=None)
        expected.update(mode="baseline", scenarios=[])
        expected["baseline"]["execution"]["path_source"] = "generated"
        actual = analysis_to_dict(analyze_retirement(**(values | {"scenarios": []})))
        self.assertEqual(actual, expected)

    def test_all_required_top_level_and_scenario_keys_are_explicit(self):
        result = analysis_to_dict(compare())
        self.assertEqual(set(result), {"contract_version", "mode", "mapping_version", "units", "notices",
                                      "baseline_profile", "metadata", "baseline", "scenarios"})
        for row in result["scenarios"]:
            self.assertEqual(set(row), {"scenario_id", "changes", "profile", "result", "delta"})
            self.assertEqual(set(row["changes"]), {"monthly_savings", "retirement_age", "retirement_expenses"})
            self.assertEqual(set(row["result"]), set(result["baseline"]))
        self.assertEqual(result["units"], {"amount": "KRW", "monthly_amount": "KRW/month", "probability": "fraction",
                                           "probability_delta": "pp", "annual_rate": "fraction", "age": "years"})

    def test_all_summary_and_delta_properties_are_preserved_without_extra_scaling(self):
        analysis = compare()
        output = analysis_to_dict(analysis)
        for model, data in [(analysis.calculation.baseline, output["baseline"])] + [
            (row.result, data["result"]) for row, data in zip(analysis.calculation.scenarios, output["scenarios"])
        ]:
            for name in ("num_paths", "success_count", "depletion_count", "never_depleted_count",
                         "success_probability", "depletion_probability"):
                self.assertEqual(data[name], getattr(model, name))
        for model, data in zip(analysis.calculation.scenarios, output["scenarios"]):
            for name in ("success_count_delta", "depletion_count_delta", "never_depleted_count_delta",
                         "success_probability_delta_pp", "depletion_probability_delta_pp"):
                self.assertEqual(data["delta"][name], getattr(model, name))
        self.assertEqual(output["scenarios"][0]["result"]["success_probability"], 1)
        self.assertEqual(output["scenarios"][0]["delta"]["success_probability_delta_pp"], 100)
        self.assertEqual(output["scenarios"][0]["delta"]["final_shortfall_delta"]["p50"], -2000)

    def test_notice_order_duplicates_and_versions_are_not_dropped_or_rewritten(self):
        original = compare()
        notices = original.mapped_input.notices
        model = replace(original, mapped_input=replace(original.mapped_input, notices=notices[::-1] + notices[:1]))
        output = analysis_to_dict(model)
        self.assertEqual(output["notices"], [{"code": n.code, "field_name": n.field_name} for n in model.mapped_input.notices])
        self.assertEqual(output["mapping_version"], "retirement-input-v1")
        self.assertEqual(output["contract_version"], "retirement-analysis-v1")
        self.assertEqual(output["metadata"]["mc_model_version"], "retirement-mc-v1")
        self.assertEqual(output["metadata"]["what_if_model_version"], "retirement-what-if-v1")

    def test_other_assets_preserve_seeded_baseline_and_what_if_except_notices(self):
        cases = ([], [{"scenario_id": "keep", "changes": {}}],
                 [{"scenario_id": "zero", "changes": {"monthly_savings": 0}}],
                 [{"scenario_id": "save_more", "changes": {"monthly_savings": 900_000}}])
        for kind, scenarios in product(("기타", " 전세보증금 ", "예금적금", "PRIVATE_ASSET"), cases):
            with self.subTest(kind=kind, scenarios=scenarios):
                values = {
                    "saved_data": saved_data(),
                    "assumption_values": assumption_data(annual_return_mean=0.04, annual_return_volatility=0.1,
                                                         num_paths=20, seed=42),
                    "scenarios": scenarios,
                }
                rng_state = random.getstate()
                expected = analysis_to_dict(analyze_retirement(**values))
                values["saved_data"]["financial_assets"].insert(0, {
                    "id": 91, "asset_type": kind, "current_value": 900_000_000,
                    "monthly_savings": 99_000_000, "name": "PRIVATE_OTHER_ASSET",
                })
                values["saved_data"]["financial_assets"].append({"id": 73, "asset_type": "회원권"})
                original = deepcopy(values)
                model = analyze_retirement(**values)
                output = analysis_to_dict(model)
                self.assertEqual(output["notices"], [{"code": notice.code, "field_name": notice.field_name}
                                                    for notice in model.mapped_input.notices])
                self.assertEqual([notice for notice in output["notices"]
                                  if notice["code"] == "other_financial_asset_not_modeled"], [
                    {"code": "other_financial_asset_not_modeled", "field_name": "financial_assets[0]"},
                    {"code": "other_financial_asset_not_modeled", "field_name": "financial_assets[3]"},
                ])
                self.assertEqual([notice for notice in output["notices"]
                                  if notice["code"] != "other_financial_asset_not_modeled"], expected["notices"])
                self.assertEqual({key: value for key, value in output.items() if key != "notices"},
                                 {key: value for key, value in expected.items() if key != "notices"})
                self.assertEqual(json.loads(json.dumps(output, allow_nan=False)), output)
                self.assertNotIn("PRIVATE_OTHER_ASSET", json.dumps(output))
                self.assertNotIn("PRIVATE_ASSET", json.dumps(output))
                self.assertEqual(analysis_to_dict(analyze_retirement(**values)), output)
                self.assertEqual(random.getstate(), rng_state)
                self.assertEqual(values, original)

    def test_only_other_assets_produce_results_without_false_empty_confirmation(self):
        for kind, scenarios in product(("기타", "전세보증금", "예금적금"), ([], [lower_expenses()])):
            with self.subTest(kind=kind, scenarios=scenarios):
                values = request() | {"scenarios": scenarios}
                values["saved_data"]["financial_assets"][0]["asset_type"] = kind
                original = deepcopy(values)
                output = analysis_to_dict(analyze_retirement(**values))
                self.assertEqual(output["baseline_profile"]["liquid_assets"], 0)
                self.assertEqual(output["baseline_profile"]["monthly_savings"], 0)
                self.assertEqual(output["baseline"]["final_balance_percentiles"]["p50"], -12_000)
                self.assertIn({"code": "other_financial_asset_not_modeled", "field_name": "financial_assets[0]"},
                              output["notices"])
                self.assertNotIn("confirmed_empty_financial_assets", [notice["code"] for notice in output["notices"]])
                if scenarios:
                    self.assertEqual(output["scenarios"][0]["profile"]["liquid_assets"], 0)
                    self.assertEqual(output["scenarios"][0]["result"]["final_balance_percentiles"]["p50"], -9_600)
                self.assertEqual(json.loads(json.dumps(output, allow_nan=False)), output)
                self.assertEqual(values, original)

    def test_invalid_asset_after_unmodeled_preserves_public_error_contract(self):
        cases = (
            ({}, "missing_field", "asset_type"),
            ({"asset_type": None}, "missing_value", "asset_type"),
            ({"asset_type": " \t"}, "missing_value", "asset_type"),
            ({"asset_type": {"name": "PRIVATE_ASSET"}}, "invalid_type", "asset_type"),
            ({"asset_type": "주식", "current_value": "PRIVATE_AMOUNT"}, "invalid_value", "current_value"),
        )
        for (row, code, field), scenarios in product(cases, ([], [lower_expenses()])):
            with self.subTest(code=code, field=field, scenarios=scenarios):
                values = request() | {"scenarios": scenarios}
                values["saved_data"]["financial_assets"] = [{"asset_type": "전세보증금"}, row]
                original = deepcopy(values)
                with self.assertRaises(RetirementAnalysisError) as raised:
                    analyze_retirement(**values)
                output = analysis_error_to_dict(raised.exception)
                self.assertEqual(output["error"]["code"], "input_mapping_failed")
                self.assertEqual(output["error"]["reason_code"], code)
                self.assertEqual(output["error"]["field_name"], f"financial_assets[1].{field}")
                self.assertEqual(set(output), {"contract_version", "error"})
                self.assertNotIn("PRIVATE", json.dumps(output, allow_nan=False))
                self.assertEqual(values, original)

    def test_missing_and_null_changes_normalize_to_three_null_fields(self):
        values = request()
        missing = analysis_to_dict(analyze_retirement(**values, scenarios=[{"scenario_id": "keep", "changes": {}}]))
        nulls = analysis_to_dict(analyze_retirement(**values, scenarios=[{"scenario_id": "keep", "changes": {
            "monthly_savings": None, "retirement_age": None, "retirement_expenses": None,
        }}]))
        self.assertEqual(missing, nulls)
        self.assertEqual(missing["scenarios"][0]["changes"], {"monthly_savings": None, "retirement_age": None, "retirement_expenses": None})

    def test_explicit_zero_savings_and_expenses_are_preserved(self):
        values = lower_expenses()
        values["changes"].update(monthly_savings=0)
        values["changes"]["retirement_expenses"][0]["monthly_amount"] = 0
        data = analysis_to_dict(analyze_retirement(**request(), scenarios=[values]))["scenarios"][0]
        self.assertEqual(data["changes"]["monthly_savings"], 0)
        self.assertEqual(data["changes"]["retirement_expenses"][0]["monthly_amount"], 0)
        self.assertEqual(data["profile"]["monthly_savings"], 0)

    def test_scenario_and_definition_period_order_are_preserved(self):
        values = [
            {"scenario_id": "last", "changes": {"retirement_expenses": [
                {"from_age": 75, "to_age": 90, "monthly_amount": 1_000_000},
                {"from_age": 60, "to_age": 74, "monthly_amount": 2_000_000},
            ]}},
            {"scenario_id": "first", "changes": {}},
        ]
        output = analysis_to_dict(analyze_retirement(saved_data(), assumption_data(), scenarios=values))
        self.assertEqual([row["scenario_id"] for row in output["scenarios"]], ["last", "first"])
        self.assertEqual([row["from_age"] for row in output["scenarios"][0]["changes"]["retirement_expenses"]], [75, 60])
        self.assertEqual([row["from_age"] for row in output["scenarios"][0]["profile"]["retirement_expenses"]], [60, 75])

    def test_horizon_zero_counts_and_pension_schedule_are_not_summarized_away(self):
        model = analyze_retirement(saved_data(), assumption_data())
        result = analysis_to_dict(model)
        self.assertEqual([p["age"] for p in result["baseline"]["asset_percentiles"]], list(range(40, 91)))
        self.assertEqual([p["age"] for p in result["baseline"]["first_depletion_counts"]], list(range(40, 91)))
        self.assertEqual(sum(p["count"] == 0 for p in result["baseline"]["first_depletion_counts"]), 50)
        self.assertEqual(result["baseline_profile"]["pension_incomes"], [{"start_age": 65, "end_age": 90, "monthly_amount": 1_000_000}])

    def test_ten_scenario_output_is_complete_and_round_trips(self):
        values = [{"scenario_id": f"case_{i}", "changes": {}} for i in range(10)]
        result = analysis_to_dict(analyze_retirement(**request(), scenarios=values))
        self.assertEqual(len(result["scenarios"]), 10)
        self.assertEqual(json.loads(json.dumps(result, allow_nan=False)), result)

    def test_seeded_batch_seed_and_child_provenance_remain_distinct(self):
        values = request() | {"assumption_values": assumption_data(annual_return_volatility=0.1, seed=42, num_paths=20)}
        baseline = analysis_to_dict(analyze_retirement(**values))
        result = analysis_to_dict(analyze_retirement(**values, scenarios=[lower_expenses()]))
        for data in (baseline, result):
            self.assertEqual(data["metadata"]["effective_seed"], 42)
            self.assertEqual(data["metadata"]["assumptions"]["seed"], 42)
            self.assertEqual(data["metadata"]["return_model"], "lognormal_gross_return")
            self.assertEqual(data["metadata"]["rng_algorithm"], "python.random.Random.gauss")
        self.assertEqual(baseline["baseline"]["execution"], {"path_source": "generated", "effective_seed": 42})
        self.assertEqual(result["baseline"]["execution"], {"path_source": "provided", "effective_seed": None})
        self.assertEqual(result["scenarios"][0]["result"]["execution"], result["baseline"]["execution"])

    def test_zero_volatility_keeps_configured_but_unused_seed(self):
        data = analysis_to_dict(compare())["metadata"]
        self.assertEqual(data["assumptions"]["seed"], 7)
        self.assertIsNone(data["effective_seed"])
        self.assertIsNone(data["rng_algorithm"])
        self.assertEqual(data["return_model"], "constant")

    def test_recorded_python_environment_is_not_replaced_by_current_runtime(self):
        model = compare()
        metadata = [model.calculation.metadata, model.calculation.baseline.metadata,
                    *(row.result.metadata for row in model.calculation.scenarios)]
        for item in metadata:
            object.__setattr__(item, "python_version", "3.12.99")
            object.__setattr__(item, "python_implementation", "RecordedPython")
        result = analysis_to_dict(model)
        self.assertEqual(result["metadata"]["python_version"], "3.12.99")
        self.assertEqual(result["metadata"]["python_implementation"], "RecordedPython")

    def test_all_output_containers_are_fresh_and_do_not_alias_each_other(self):
        model = compare()
        first, second = analysis_to_dict(model), analysis_to_dict(model)
        left, right = list(container_ids(first)), list(container_ids(second))
        self.assertEqual(len(left), len(set(left)))
        self.assertEqual(len(right), len(set(right)))
        self.assertTrue(set(left).isdisjoint(right))
        original = deepcopy(model)
        first["units"]["amount"] = "PRIVATE_MUTATION"
        first["notices"].clear()
        first["metadata"]["assumptions"]["seed"] = 1
        first["scenarios"][0]["result"]["final_balance_percentiles"]["p50"] = 999
        first["baseline_profile"]["retirement_expenses"].clear()
        self.assertEqual(model, original)
        self.assertEqual(analysis_to_dict(model), second)

    def test_raw_customer_fields_and_internal_objects_are_not_exposed(self):
        values = saved_data()
        values["customer_name"] = "PRIVATE_CUSTOMER"
        values["current_form"] = {"secret": "PRIVATE_FORM"}
        values["financial_assets"][0]["product_name"] = "PRIVATE_PRODUCT"
        values["pension_assets"][0]["account_number"] = "PRIVATE_ACCOUNT"
        output = analysis_to_dict(analyze_retirement(values, assumption_data()))
        text = json.dumps(output, allow_nan=False)
        self.assertNotIn("PRIVATE", text)
        for path, value in primitive_paths(output):
            self.assertIn(type(value), (str, int, float, bool, type(None)), path)
        for key in ("saved_data", "current_form", "customer_name", "account_number", "id", "calculation", "mapped_input"):
            self.assertNotIn(f'"{key}":', text)

    def test_conversion_does_not_run_engines_rng_io_json_encoding_or_metadata_constructors(self):
        model, state = compare(), random.getstate()
        stdout, stderr = StringIO(), StringIO()
        targets = (
            "retirement_simulation.analysis.calculate_monte_carlo", "retirement_simulation.analysis.calculate_what_if",
            "retirement_simulation.what_if_engine.calculate_monte_carlo_paths", "retirement_simulation.what_if_engine.generate_return_paths",
            "retirement_simulation.monte_carlo.generate_return_paths", "retirement_simulation.cashflow.calculate_cashflow_path",
            "retirement_simulation.return_paths.Random", "retirement_simulation.monte_carlo.MonteCarloMetadata.__init__",
            "builtins.open", "os.getenv", "socket.socket", "subprocess.run", "time.time", "random.Random",
            "dataclasses.asdict", "json.dumps",
        )
        with ExitStack() as stack, redirect_stdout(stdout), redirect_stderr(stderr):
            guards = [stack.enter_context(patch(name, side_effect=AssertionError(name))) for name in targets]
            first = analysis_to_dict(model)
            self.assertEqual(analysis_to_dict(model), first)
            error = analysis_error_to_dict(RetirementAnalysisError("calculation_failed", "calculation"))
            self.assertEqual(error["error"]["code"], "calculation_failed")
        for guard in guards:
            guard.assert_not_called()
        self.assertEqual(random.getstate(), state)
        self.assertEqual((stdout.getvalue(), stderr.getvalue()), ("", ""))

    def test_numeric_output_paths_are_accepted_by_output_error_constructor(self):
        model = analyze_retirement(saved_data(), assumption_data(annual_return_volatility=0.1, num_paths=10), scenarios=[{
            "scenario_id": "save_more", "changes": {"monthly_savings": 1_000_000},
        }])
        output = analysis_to_dict(model)
        for path, value in primitive_paths(output):
            if type(value) in (int, float):
                self.assertEqual(RetirementAnalysisError("unsafe_json_number", path).field_name, path)
            RetirementAnalysisError("invalid_analysis_result", path)


class AnalysisErrorOutputTests(unittest.TestCase):
    def test_documented_error_payload_is_exact(self):
        expected, _, _ = contract_examples()
        error = RetirementAnalysisError("input_mapping_failed", "pension_assets", reason_code="empty_confirmation_required")
        self.assertEqual(analysis_error_to_dict(error), expected)

    def test_all_seven_error_codes_have_fixed_valid_json(self):
        cases = (
            ("input_mapping_failed", "input", "basic_info.age", "invalid_value", "Retirement input mapping failed."),
            ("assumption_mapping_failed", "assumptions", "assumptions.seed", "invalid_value", "Simulation assumption mapping failed."),
            ("scenario_mapping_failed", "scenarios", "scenarios[0].scenario_id", "duplicate_id", "What-if scenario mapping failed."),
            ("scenario_application_failed", "scenarios", "scenarios[0].changes.retirement_age", "invalid_profile", "What-if scenario application failed."),
            ("calculation_failed", "calculation", "calculation.baseline.cashflow.liquid_assets", None, "Retirement calculation failed."),
            ("invalid_analysis_result", "output", "analysis", None, "Analysis result is invalid."),
            ("unsafe_json_number", "output", "baseline.final_balance_percentiles.p50", None, "Analysis contains an unsafe JSON number."),
        )
        for code, stage, path, reason, message in cases:
            error = RetirementAnalysisError(code, path, reason_code=reason)
            output = analysis_error_to_dict(error)
            self.assertEqual(output, {"contract_version": "retirement-analysis-v1", "error": {
                "code": code, "stage": stage, "field_name": path, "reason_code": reason, "message": message,
            }})
            self.assertEqual(json.loads(json.dumps(output, allow_nan=False)), output)

    def test_exception_args_context_notes_and_extra_attributes_are_not_serialized(self):
        error = RetirementAnalysisError("calculation_failed", "calculation")
        error.args = ("PRIVATE_ARGS",)
        error.customer_name = "PRIVATE_CUSTOMER"
        error.__cause__ = ValueError("PRIVATE_CAUSE")
        error.__context__ = ValueError("PRIVATE_CONTEXT")
        error.add_note("PRIVATE_NOTE")
        output = analysis_error_to_dict(error)
        self.assertNotIn("PRIVATE", json.dumps(output))
        self.assertEqual(output["error"]["message"], "Retirement calculation failed.")

    def test_error_output_containers_are_detached(self):
        error = RetirementAnalysisError("calculation_failed", "calculation")
        first, second = analysis_error_to_dict(error), analysis_error_to_dict(error)
        first["error"]["message"] = "modified"
        self.assertEqual(analysis_error_to_dict(error), second)
        self.assertEqual(error.message, "Retirement calculation failed.")

    def test_non_analysis_exceptions_are_rejected_without_stringifying(self):
        class PrivateException(Exception):
            def __str__(self):
                raise AssertionError("must not stringify")
        for value in (None, {}, "PRIVATE", ValueError("PRIVATE"), ModelValidationError("PRIVATE", "PRIVATE"), PrivateException()):
            with self.assertRaises(ModelValidationError) as raised:
                analysis_error_to_dict(value)
            self.assertEqual(raised.exception.field_name, "error")
            self.assertNotIn("PRIVATE", str(raised.exception))

    def test_manually_tampered_error_fields_are_revalidated(self):
        for name, value, field in (("_code", "PRIVATE", "error.code"),
                                   ("_field_name", "PRIVATE", "error.field_name"),
                                   ("_reason_code", "PRIVATE", "error.reason_code")):
            error = RetirementAnalysisError("calculation_failed", "calculation")
            setattr(error, name, value)
            with self.assertRaises(ModelValidationError) as raised:
                analysis_error_to_dict(error)
            self.assertEqual(raised.exception.field_name, field)
            self.assertNotIn("PRIVATE", str(raised.exception))

    def test_missing_diagnostic_fields_are_model_errors(self):
        for name in ("_code", "_field_name", "_reason_code"):
            error = RetirementAnalysisError("calculation_failed", "calculation")
            delattr(error, name)
            with self.assertRaises(ModelValidationError):
                analysis_error_to_dict(error)

    def test_output_error_paths_reject_unknown_keys_and_wrong_leaf_types(self):
        for path in ("PRIVATE", "analysis.PRIVATE", "metadata.customer_id", "scenarios[10].result",
                     "scenarios[-1].changes", "scenarios[01].delta", "baseline.final_balance_percentiles.PRIVATE"):
            for code in ("invalid_analysis_result", "unsafe_json_number"):
                with self.assertRaises(ModelValidationError):
                    RetirementAnalysisError(code, path)
        for path in ("analysis", "metadata", "mode", "notices[0].field_name", "scenarios[0].scenario_id"):
            RetirementAnalysisError("invalid_analysis_result", path)
            with self.assertRaises(ModelValidationError):
                RetirementAnalysisError("unsafe_json_number", path)
        for code in ("invalid_analysis_result", "unsafe_json_number"):
            with self.assertRaises(ModelValidationError):
                RetirementAnalysisError(code, "baseline.success_count", reason_code="invalid_value")

    def test_python_function_arguments_remain_strict(self):
        for function in (analysis_to_dict, analysis_error_to_dict):
            with self.assertRaises(TypeError):
                function()
            with self.assertRaises(TypeError):
                function(None, strict=False)


if __name__ == "__main__":
    unittest.main()
