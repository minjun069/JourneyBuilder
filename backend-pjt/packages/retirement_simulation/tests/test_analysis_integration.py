"""Saved inputs -> shared-market analysis -> strict JSON acceptance regressions.

These are module-only synthetic checks, not BE/API/FE or market validation.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import random
import sys
import unittest
from unittest.mock import patch


AI_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(AI_PROJECT_ROOT))

from retirement_simulation import (  # noqa: E402
    RetirementAnalysisError, analyze_retirement, analysis_to_dict, analysis_error_to_dict,
    calculate_monte_carlo, calculate_what_if, map_retirement_profile, map_simulation_assumptions,
    map_what_if_scenarios,
)
from retirement_simulation.analysis_samples import build_analysis_case  # noqa: E402
from retirement_simulation.tests.test_analysis import request, lower_expenses  # noqa: E402


def round_trip(values):
    try:
        payload = analysis_to_dict(analyze_retirement(**values))
    except RetirementAnalysisError as error:
        payload = analysis_error_to_dict(error)
    return json.loads(json.dumps(payload, ensure_ascii=False, allow_nan=False))


def saved_request(name="saved_working", *, num_paths=3):
    case = build_analysis_case(name, num_paths=num_paths)
    return {"saved_data": case.saved_data, "assumption_values": case.assumption_values,
            "scenarios": case.scenario_values, "confirmed_empty_sections": case.confirmed_empty_sections}


class AnalysisIntegrationTests(unittest.TestCase):
    def test_all_saved_cases_match_independent_existing_engine_calls(self):
        for name in ("saved_working", "saved_retired_no_pension", "saved_pension_gap"):
            values = saved_request(name)
            profile = map_retirement_profile(values["saved_data"], confirmed_empty_sections=values["confirmed_empty_sections"])
            config = map_simulation_assumptions(values["assumption_values"])
            definitions = map_what_if_scenarios(values["scenarios"])
            expected = calculate_what_if(profile.profile, config, definitions)
            actual = analyze_retirement(**values)
            self.assertEqual(actual.calculation, expected)
            self.assertEqual(actual.mapped_input, profile)
            payload = round_trip(values)
            self.assertEqual(payload["baseline"]["success_probability"], expected.baseline.success_probability)
            for output, scenario in zip(payload["scenarios"], expected.scenarios):
                self.assertEqual(output["delta"]["success_probability_delta_pp"], scenario.success_probability_delta_pp)

    def test_baseline_and_what_if_share_same_seeded_baseline_numbers_not_execution_labels(self):
        values = saved_request()
        comparison = round_trip(values)
        baseline = round_trip(values | {"scenarios": ()})
        left, right = dict(comparison["baseline"]), dict(baseline["baseline"])
        self.assertEqual(left.pop("execution"), {"path_source": "provided", "effective_seed": None})
        self.assertEqual(right.pop("execution"), {"path_source": "generated", "effective_seed": 42})
        self.assertEqual(left, right)
        direct = calculate_monte_carlo(
            map_retirement_profile(values["saved_data"]).profile,
            map_simulation_assumptions(values["assumption_values"]),
        )
        self.assertEqual(baseline["baseline"]["final_balance_percentiles"]["p50"], direct.final_balance_percentiles.p50)

    def test_combined_zero_savings_early_retirement_and_unsorted_expense_replacement(self):
        values = saved_request()
        values["scenarios"] = [{"scenario_id": "combined", "changes": {
            "monthly_savings": 0, "retirement_age": 58,
            "retirement_expenses": [
                {"from_age": 75, "to_age": 90, "monthly_amount": 1_300_000},
                {"from_age": 58, "to_age": 74, "monthly_amount": 1_800_000},
            ],
        }}]
        original = deepcopy(values)
        payload = round_trip(values)
        changed = payload["scenarios"][0]
        self.assertEqual([p["from_age"] for p in changed["changes"]["retirement_expenses"]], [75, 58])
        self.assertEqual([p["from_age"] for p in changed["profile"]["retirement_expenses"]], [58, 75])
        self.assertEqual(changed["profile"]["retirement_age"], 58)
        self.assertEqual(changed["profile"]["monthly_savings"], 0)
        self.assertEqual(changed["profile"]["pension_incomes"], payload["baseline_profile"]["pension_incomes"])
        self.assertEqual(payload["baseline_profile"]["retirement_age"], 60)
        self.assertEqual(payload["baseline_profile"]["monthly_savings"], 750000)
        self.assertEqual(values, original)
        for q in ("p10", "p50", "p90"):
            self.assertEqual(changed["delta"]["final_balance_delta"][q],
                             changed["result"]["final_balance_percentiles"][q] - payload["baseline"]["final_balance_percentiles"][q])

    def test_reordering_scenarios_changes_only_their_order(self):
        values = saved_request(num_paths=7)
        first = round_trip(values)
        second = round_trip(values | {"scenarios": tuple(reversed(values["scenarios"]))})
        rows = second.pop("scenarios")
        expected = first.pop("scenarios")
        self.assertEqual(first, second)
        self.assertEqual(rows, list(reversed(expected)))

    def test_noop_batch_of_ten_and_eleven_rejection_reach_the_final_json_boundary(self):
        values = request() | {"scenarios": [{"scenario_id": f"keep_{i}", "changes": {}} for i in range(10)]}
        payload = round_trip(values)
        self.assertEqual(len(payload["scenarios"]), 10)
        for row in payload["scenarios"]:
            self.assertEqual(row["result"], payload["baseline"])
            self.assertEqual(row["delta"]["success_probability_delta_pp"], 0)
        values["scenarios"].append({"scenario_id": "eleventh", "changes": {}})
        with patch("retirement_simulation.analysis.calculate_what_if", side_effect=AssertionError("must validate first")):
            error = round_trip(values)
        self.assertEqual(error["error"]["code"], "scenario_mapping_failed")
        self.assertEqual(error["error"]["field_name"], "scenarios")
        self.assertNotIn("baseline", error)

    def test_full_103_year_model_horizon_is_not_cut_to_the_sample_guard(self):
        values = request()
        values["saved_data"]["basic_info"] = {"age": 18, "retirement_age": 60, "end_age": 120}
        values["saved_data"]["goal_periods"] = [{"phase": "after", "from_age": 60, "to_age": 120,
                                                "items": [{"amount": 0}]}]
        values["scenarios"] = [{"scenario_id": "earlier", "changes": {"retirement_age": 18,
            "retirement_expenses": [{"from_age": 18, "to_age": 120, "monthly_amount": 0}]}}]
        payload = round_trip(values)
        for result in (payload["baseline"], payload["scenarios"][0]["result"]):
            self.assertEqual([p["age"] for p in result["asset_percentiles"]], list(range(18, 121)))
            self.assertEqual(len(result["first_depletion_counts"]), 103)
            self.assertEqual(result["depletion_count"], 0)
            self.assertEqual(result["success_probability"], 1)
            self.assertEqual(result["final_balance_percentiles"]["p50"], 10000)

    def test_direct_api_retains_path_counts_above_the_new_cli_guard(self):
        values = request()
        values["assumption_values"]["num_paths"] = 1001
        payload = round_trip(values)
        self.assertEqual(payload["baseline"]["num_paths"], 1001)
        self.assertEqual(payload["baseline"]["depletion_count"], 1001)

    def test_confirmed_empty_assets_and_zero_spending_are_not_missing_values(self):
        values = request()
        values["saved_data"]["financial_assets"] = []
        values["confirmed_empty_sections"] = ("financial_assets", "pension_assets")
        values["saved_data"]["goal_periods"][0]["items"][0]["amount"] = 0
        values["scenarios"] = [{"scenario_id": "spend", "changes": {
            "retirement_expenses": [{"from_age": 60, "to_age": 60, "monthly_amount": 1}]}}]
        payload = round_trip(values)
        self.assertEqual(payload["baseline_profile"]["liquid_assets"], 0)
        self.assertEqual(payload["baseline"]["success_probability"], 1)
        self.assertEqual(payload["scenarios"][0]["result"]["success_probability"], 0)
        self.assertEqual(payload["scenarios"][0]["delta"]["success_probability_delta_pp"], -100)
        self.assertTrue({"confirmed_empty_financial_assets", "confirmed_empty_pensions"}.issubset(
            {notice["code"] for notice in payload["notices"]}))

    def test_serialized_stage_errors_are_real_failures_not_partial_successes(self):
        cases = []
        values = request() | {"confirmed_empty_sections": ()}
        cases.append((values, "input_mapping_failed", "input", "pension_assets"))
        values = request()
        values["assumption_values"]["num_paths"] = 0
        cases.append((values, "assumption_mapping_failed", "assumptions", "assumptions.num_paths"))
        values = request() | {"scenarios": [{"scenario_id": "bad", "changes": {"PRIVATE_KEY": "PRIVATE_VALUE"}}]}
        cases.append((values, "scenario_mapping_failed", "scenarios", "scenarios[0].changes"))
        values = request() | {"scenarios": [{"scenario_id": "later", "changes": {"retirement_age": 61}}]}
        cases.append((values, "scenario_application_failed", "scenarios", "scenarios[0].changes.retirement_age"))
        values = request()
        values["saved_data"]["financial_assets"][0]["current_value"] = 10**400
        cases.append((values, "calculation_failed", "calculation", "calculation.baseline.cashflow.liquid_assets"))
        values = request()
        values["assumption_values"]["seed"] = 10**100
        cases.append((values, "unsafe_json_number", "output", "metadata.assumptions.seed"))
        for values, code, stage, path in cases:
            with self.subTest(code=code):
                original = deepcopy(values)
                payload = round_trip(values)
                self.assertEqual(set(payload), {"contract_version", "error"})
                self.assertEqual((payload["error"]["code"], payload["error"]["stage"], payload["error"]["field_name"]),
                                 (code, stage, path))
                self.assertNotIn("PRIVATE", json.dumps(payload))
                self.assertEqual(values, original)

    def test_earlier_stage_error_wins_even_when_later_inputs_are_invalid(self):
        values = request() | {"confirmed_empty_sections": (), "assumption_values": {}, "scenarios": None}
        with patch("retirement_simulation.analysis.map_simulation_assumptions", side_effect=AssertionError("too late")):
            payload = round_trip(values)
        self.assertEqual(payload["error"]["code"], "input_mapping_failed")

    def test_late_calculation_failure_then_retry_has_no_partial_results_or_state_leak(self):
        good = request() | {"scenarios": [lower_expenses()]}
        expected, state = round_trip(good), random.getstate()
        bad = deepcopy(good)
        changed = lower_expenses()
        changed["scenario_id"] = "overflow"
        changed["changes"]["retirement_expenses"][0]["monthly_amount"] = 10**400
        bad["scenarios"].append(changed)
        payload = round_trip(bad)
        self.assertEqual(payload["error"]["field_name"], "calculation.scenarios[1].cashflow.annual_expenses")
        self.assertNotIn("scenarios", payload)
        self.assertEqual(round_trip(good), expected)
        self.assertEqual(random.getstate(), state)

    def test_null_noop_zero_and_empty_expenses_stay_distinct_through_json(self):
        values = request()
        empty = round_trip(values | {"scenarios": [{"scenario_id": "keep", "changes": {}}]})
        nulls = round_trip(values | {"scenarios": [{"scenario_id": "keep", "changes": {
            "monthly_savings": None, "retirement_age": None, "retirement_expenses": None}}]})
        self.assertEqual(empty, nulls)
        zero = round_trip(values | {"scenarios": [{"scenario_id": "keep", "changes": {"monthly_savings": 0}}]})
        self.assertEqual(zero["scenarios"][0]["changes"]["monthly_savings"], 0)
        bad = round_trip(values | {"scenarios": [{"scenario_id": "keep", "changes": {"retirement_expenses": []}}]})
        self.assertEqual(bad["error"]["reason_code"], "missing_value")

    def test_raw_identity_and_returned_json_mutations_do_not_leak_into_future_calls(self):
        values = saved_request()
        values["saved_data"]["customer_name"] = "PRIVATE_NAME"
        values["saved_data"]["current_form"] = {"note": "PRIVATE_FORM"}
        values["saved_data"]["financial_assets"][0]["account_number"] = "PRIVATE_ACCOUNT"
        original = deepcopy(values)
        payload, expected = round_trip(values), round_trip(values)
        self.assertNotIn("PRIVATE", json.dumps(payload))
        payload["notices"].clear()
        payload["scenarios"][0]["profile"]["retirement_expenses"].clear()
        payload["metadata"]["assumptions"]["seed"] = 0
        self.assertEqual(round_trip(values), expected)
        self.assertEqual(values, original)


if __name__ == "__main__":
    unittest.main()
