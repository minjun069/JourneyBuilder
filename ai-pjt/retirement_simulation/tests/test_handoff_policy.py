"""MVP policy and source-only handoff fixtures; no BE/FE imports or mutations."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
import random
import sys
import unittest
from unittest.mock import patch

AI_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(AI_ROOT))

from retirement_simulation import analyze_retirement, analysis_to_dict, RetirementAnalysisError
from retirement_simulation.tests import handoff_samples as samples
from retirement_simulation.what_if_engine import generate_return_paths, calculate_monte_carlo_paths


def invoke(*args):
    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        status = samples.main(list(args))
    return status, json.loads(out.getvalue()), err.getvalue()


class HandoffPolicyTests(unittest.TestCase):
    def test_frozen_decisions_are_explicit_without_claiming_deployment(self):
        policy = samples.load_policy()
        self.assertEqual(policy["policy_version"], "simulation-mvp-2026-09-10.v1")
        self.assertEqual(policy["status"], "mvp_scope_decided_not_deployed")
        self.assertIsNone(policy["calibration_period"])
        self.assertEqual(policy["max_comparison_scenarios"], 1)
        self.assertIs(policy["changed_readiness_in_scope"], False)
        self.assertIs(policy["partial_analysis_with_notices"], True)
        self.assertIs(policy["external_market_api_required"], False)
        self.assertEqual(policy["assumption_values"], {
            "annual_return_mean": 0.04, "annual_return_volatility": 0.10,
            "annual_inflation_rate": 0.02, "num_paths": 1000, "seed": 42,
        })

    def test_policy_metadata_is_not_an_engine_input_or_a_fallback(self):
        request = samples.build_request("baseline")
        request["assumption_values"] = samples.load_policy()
        with self.assertRaises(RetirementAnalysisError) as raised:
            analyze_retirement(**request)
        self.assertEqual(raised.exception.code, "assumption_mapping_failed")
        request["assumption_values"] = {}
        with self.assertRaises(RetirementAnalysisError):
            analyze_retirement(**request)

    def test_fixtures_are_fresh_and_do_not_modify_legacy_demo_settings(self):
        from retirement_simulation.analysis_samples import build_analysis_case
        left, right = samples.build_request(), samples.build_request()
        expected = deepcopy(right)
        left["saved_data"]["financial_assets"].clear()
        left["assumption_values"]["seed"] = 999
        left["scenarios"][0]["changes"]["retirement_expenses"].clear()
        self.assertEqual(right, expected)
        self.assertEqual(build_analysis_case("saved_working").assumption_values["annual_return_mean"], 0.05)

    def test_all_seven_combinations_have_one_explicit_complete_change(self):
        for name, (savings, retirement, expenses) in samples.COMBINATIONS.items():
            with self.subTest(case=name):
                request = samples.build_request(name)
                self.assertEqual(len(request["scenarios"]), 1)
                change = request["scenarios"][0]["changes"]
                self.assertEqual("monthly_savings" in change, savings)
                self.assertEqual("retirement_age" in change, retirement)
                self.assertEqual("retirement_expenses" in change, retirement or expenses)
                if retirement:
                    self.assertEqual(change["retirement_expenses"][0]["from_age"], change["retirement_age"])
                self.assertEqual(request["assumption_values"], samples.load_policy()["assumption_values"])

    def test_unknown_case_and_broken_policy_never_fall_back(self):
        for value in (None, [], "customer-file.json"):
            with self.assertRaises(ValueError):
                samples.build_request(value)
        with patch.object(samples, "load_policy", side_effect=ValueError("invalid policy")):
            with self.assertRaises(ValueError):
                samples.build_request()


class HandoffExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.outputs = {}
        for name in ("baseline", "unchanged", *samples.COMBINATIONS, "retired", "pension_gap"):
            status, payload = samples.execute(samples.build_request(name))
            if status:
                raise AssertionError(f"synthetic case failed: {name}")
            cls.outputs[name] = payload

    def test_baseline_matches_every_comparison_except_path_provenance(self):
        baseline = dict(self.outputs["baseline"]["baseline"])
        self.assertEqual(baseline.pop("execution")["path_source"], "generated")
        for name in ("unchanged", *samples.COMBINATIONS):
            actual = dict(self.outputs[name]["baseline"])
            self.assertEqual(actual.pop("execution"), {"path_source": "provided", "effective_seed": None})
            self.assertEqual(actual, baseline)

    def test_counts_units_and_full_horizon_are_consistent(self):
        for name, payload in self.outputs.items():
            with self.subTest(case=name):
                meta = payload["metadata"]
                self.assertEqual(meta["assumptions"], samples.load_policy()["assumption_values"])
                self.assertEqual(meta["effective_seed"], 42)
                baseline = payload["baseline"]
                self.assertEqual(len(baseline["asset_percentiles"]), meta["end_age"] - meta["current_age"] + 1)
                self.assertEqual(baseline["success_count"] + baseline["depletion_count"], 1000)
                self.assertEqual(baseline["success_probability"], baseline["success_count"] / 1000)
                for item in payload["scenarios"]:
                    self.assertAlmostEqual(item["delta"]["success_probability_delta_pp"],
                        (item["result"]["success_probability"] - baseline["success_probability"]) * 100)
                self.assertTrue(payload["notices"])
                self.assertEqual(json.loads(json.dumps(payload, allow_nan=False)), payload)

    def test_unchanged_has_zero_deltas_and_identical_summary(self):
        payload = self.outputs["unchanged"]
        scenario = payload["scenarios"][0]
        self.assertEqual(scenario["result"], payload["baseline"])
        self.assertEqual(scenario["delta"]["success_probability_delta_pp"], 0)
        self.assertEqual(scenario["delta"]["final_balance_delta"], dict.fromkeys(("p10", "p50", "p90"), 0))

    def test_savings_and_expenses_monotonic_for_same_market_and_other_inputs(self):
        for name in ("save_more", "lower_expenses", "save_and_spend"):
            payload = self.outputs[name]
            scenario = payload["scenarios"][0]
            self.assertGreaterEqual(scenario["delta"]["success_probability_delta_pp"], 0)
            self.assertTrue(all(value >= 0 for value in scenario["delta"]["final_balance_delta"].values()))

    def test_earlier_retirement_never_moves_pension_or_baseline_expenses(self):
        payload = self.outputs["combined"]
        profile = payload["scenarios"][0]["profile"]
        self.assertEqual(profile["retirement_age"], 58)
        self.assertEqual(profile["pension_incomes"], payload["baseline_profile"]["pension_incomes"])
        self.assertEqual(payload["baseline_profile"]["retirement_expenses"][0]["from_age"], 60)
        self.assertEqual(profile["retirement_expenses"][0]["from_age"], 58)

    def test_partial_scope_and_pension_notices_are_retained(self):
        codes = {item["code"] for item in self.outputs["retired"]["notices"]}
        self.assertTrue({"single_portfolio_assumption", "partial_financial_scope", "real_estate_not_modeled",
                         "liabilities_not_modeled", "milestones_not_modeled", "confirmed_empty_pensions"} <= codes)
        self.assertIn("pension_outside_horizon", {item["code"] for item in self.outputs["pension_gap"]["notices"]})

    def test_one_batch_two_cashflow_runs_and_shared_path_identity(self):
        with (patch("retirement_simulation.what_if_engine.generate_return_paths", wraps=generate_return_paths) as gen,
              patch("retirement_simulation.what_if_engine.calculate_monte_carlo_paths", wraps=calculate_monte_carlo_paths) as calc):
            samples.execute(samples.build_request())
        self.assertEqual(gen.call_count, 1)
        self.assertEqual(calc.call_count, 2)
        self.assertIs(calc.call_args_list[0].kwargs["annual_return_paths"], calc.call_args_list[1].kwargs["annual_return_paths"])

    def test_repeat_input_and_global_rng_are_unchanged(self):
        request = samples.build_request()
        original, state = deepcopy(request), random.getstate()
        first = samples.execute(request)
        second = samples.execute(request)
        self.assertEqual(first, second)
        self.assertEqual(request, original)
        self.assertEqual(random.getstate(), state)

    def test_failures_have_no_success_payload(self):
        for name in ("unconfirmed_pension", "missing_expenses"):
            status, payload = samples.execute(samples.build_request(name))
            self.assertEqual(status, 1)
            self.assertIn("error", payload)
            self.assertNotIn("baseline", payload)

    def test_zero_and_null_are_distinct_and_empty_expenses_are_invalid(self):
        request = samples.build_request("unchanged")
        request["scenarios"][0]["changes"] = {"monthly_savings": None}
        status, unchanged = samples.execute(request)
        self.assertEqual(status, 0)
        self.assertEqual(unchanged["scenarios"][0]["delta"]["success_probability_delta_pp"], 0)
        request["scenarios"][0]["changes"] = {"monthly_savings": 0}
        status, stopped = samples.execute(request)
        self.assertEqual(status, 0)
        self.assertEqual(stopped["scenarios"][0]["profile"]["monthly_savings"], 0)
        self.assertLessEqual(stopped["scenarios"][0]["delta"]["success_probability_delta_pp"], 0)
        request["scenarios"][0]["changes"] = {"retirement_expenses": []}
        self.assertEqual(samples.execute(request)[0], 1)

    def test_earlier_retirement_without_new_expense_coverage_is_rejected(self):
        request = samples.build_request("retire_earlier")
        del request["scenarios"][0]["changes"]["retirement_expenses"]
        self.assertEqual(samples.execute(request)[0], 1)

    def test_full_103_year_horizon_is_not_truncated_to_benchmark_period(self):
        request = samples.build_request("baseline")
        request["saved_data"]["basic_info"] = {"age": 18, "retirement_age": 60, "end_age": 120}
        request["saved_data"]["goal_periods"][-1]["to_age"] = 120
        # Boundary semantics check, not a service performance measurement.
        request["assumption_values"]["num_paths"] = 5
        status, payload = samples.execute(request)
        self.assertEqual(status, 0)
        self.assertEqual(len(payload["baseline"]["asset_percentiles"]), 103)
        self.assertEqual(payload["baseline"]["asset_percentiles"][-1]["age"], 120)

    def test_zero_volatility_has_independent_inflation_and_return_hand_check(self):
        request = samples.build_request("unconfirmed_pension")
        request["confirmed_empty_sections"] = ["pension_assets"]
        request["saved_data"]["basic_info"]["end_age"] = 62
        request["saved_data"]["goal_periods"][0]["to_age"] = 62
        request["saved_data"]["financial_assets"][0]["current_value"] = 100000
        request["assumption_values"].update(annual_return_volatility=0, num_paths=5)
        status, payload = samples.execute(request)
        self.assertEqual(status, 0)
        self.assertIsNone(payload["metadata"]["effective_seed"])
        self.assertEqual(payload["metadata"]["assumptions"]["seed"], 42)
        for point, expected in zip(payload["baseline"]["asset_percentiles"], (92000, 83440, 74292.8)):
            self.assertAlmostEqual(point["balance"]["p50"], expected)


class HandoffToolTests(unittest.TestCase):
    def test_cli_shows_policy_request_without_running_calculation(self):
        with patch.object(samples, "execute", side_effect=AssertionError("unexpected execution")):
            status, payload, notice = invoke("--case", "combined", "--request")
        self.assertEqual((status, payload), (0, samples.build_request()))
        self.assertEqual(notice, samples.NOTICE + "\n")

    def test_cli_full_success_and_error_follow_existing_json_contract(self):
        for name in ("baseline", "combined", "unconfirmed_pension"):
            status, payload, _ = invoke("--case", name, "--pretty")
            self.assertEqual((status, payload), samples.execute(samples.build_request(name)))

    def test_invalid_cli_arguments_never_execute(self):
        for args in (("--case", "private"), ("--study", "--case", "combined"),
                     ("--study", "--request"), ("--seed", "42"), ("--input", "customer.json")):
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()), patch.object(samples, "execute") as calc:
                with self.assertRaises(SystemExit) as raised:
                    samples.main(args)
            self.assertEqual(raised.exception.code, 2)
            calc.assert_not_called()

    def test_study_is_bounded_and_does_not_mutate_policy(self):
        original = samples.load_policy()
        def fake(request):
            return {"assumptions": dict(request["assumption_values"]), "baseline_probability": 0.5,
                    "changed_probability": 0.6, "delta_pp": 10.0}
        with patch.object(samples, "_measurement", side_effect=fake) as measure:
            result = samples.run_study()
        self.assertEqual(measure.call_count, 37)
        self.assertEqual(len(result["paired_runs"]), 30)
        self.assertEqual(len(result["sensitivity_runs"]), 7)
        self.assertEqual(samples.load_policy(), original)
        for call in measure.call_args_list:
            request = call.args[0]
            self.assertLessEqual(request["assumption_values"]["num_paths"], 5000)
            self.assertEqual(len(request["scenarios"]), 1)
        self.assertEqual({row["case"] for row in result["paired_runs"]}, set(samples.STUDY_CASES))

    def test_unexpected_errors_are_not_success_or_known_analysis_errors(self):
        for failure in (RuntimeError("bug"), MemoryError()):
            with patch.object(samples, "analyze_retirement", side_effect=failure):
                with self.assertRaises(type(failure)):
                    samples.execute(samples.build_request())


class HandoffRecordedArtifactTests(unittest.TestCase):
    def test_full_examples_match_fresh_public_execution(self):
        artifact = json.loads((samples.POLICY_PATH.parent / "handoff-examples.json").read_text())
        self.assertEqual(artifact["kind"], "synthetic_python_contract_examples_not_http")
        self.assertEqual(artifact["policy_version"], samples.load_policy()["policy_version"])
        self.assertEqual([row["case"] for row in artifact["examples"]], ["baseline", "combined", "unconfirmed_pension"])
        for row in artifact["examples"]:
            with self.subTest(case=row["case"]):
                request = samples.build_request(row["case"])
                self.assertEqual(row["python_arguments"], request)
                status, actual = samples.execute(request)
                expected = deepcopy(row["module_output"])
                self.assertEqual(status, row["cli_exit_code"])
                if "metadata" in expected:
                    for name in ("python_version", "python_implementation"):
                        expected["metadata"][name] = actual["metadata"][name]
                self.assertEqual(actual, expected)

    def test_recorded_diagnostic_matrix_is_complete_and_consistent(self):
        report = json.loads((samples.POLICY_PATH.parent / "handoff-validation-results.json").read_text())
        self.assertEqual(report["policy_version"], samples.load_policy()["policy_version"])
        self.assertEqual(report["kind"], "synthetic_handoff_diagnostics_v1")
        expected_keys = {(case, count, seed) for case in samples.STUDY_CASES
                         for count in samples.STUDY_PATHS for seed in samples.STUDY_SEEDS}
        rows = report["paired_runs"]
        keys = [(row["case"], row["assumptions"]["num_paths"], row["assumptions"]["seed"]) for row in rows]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(set(keys), expected_keys)
        self.assertEqual({row["case"] for row in report["sensitivity_runs"]}, {row[0] for row in samples.SENSITIVITY})
        for row in rows + report["sensitivity_runs"]:
            self.assertTrue(0 <= row["baseline_probability"] <= 1)
            self.assertTrue(0 <= row["changed_probability"] <= 1)
            self.assertAlmostEqual(row["delta_pp"], (row["changed_probability"] - row["baseline_probability"]) * 100)
            self.assertGreaterEqual(row["elapsed_seconds"], 0)
        for summary in report["seed_ranges"]:
            selected = [row for row in rows if row["case"] == summary["case"]
                        and row["assumptions"]["num_paths"] == summary["num_paths"]]
            for field in ("baseline_probability", "changed_probability", "delta_pp"):
                self.assertEqual(summary[field + "_range"], [min(row[field] for row in selected), max(row[field] for row in selected)])


if __name__ == "__main__":
    unittest.main()
