"""Synthetic CLI contracts, real execution, streams, and failure isolation."""

from __future__ import annotations

from contextlib import ExitStack, redirect_stderr, redirect_stdout
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
import platform
import random
import re
import subprocess
import sys
import unittest
from unittest.mock import patch


AI_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(AI_PROJECT_ROOT))

from retirement_simulation import (  # noqa: E402
    ModelValidationError, RetirementAnalysisError, analyze_retirement, analysis_to_dict,
    analysis_error_to_dict, map_what_if_scenarios,
)
from retirement_simulation import analysis_samples as samples  # noqa: E402
from retirement_simulation.input_samples import build_input_cases  # noqa: E402
from retirement_simulation.models import MAX_PATHS  # noqa: E402
from retirement_simulation.tests.test_analysis import compare  # noqa: E402
from retirement_simulation.what_if_engine import calculate_monte_carlo_paths, generate_return_paths  # noqa: E402


def invoke(*args):
    stdout, stderr = StringIO(), StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        status = samples.main(list(args))
    return status, json.loads(stdout.getvalue()), stderr.getvalue(), stdout.getvalue()


class AnalysisSampleFixtureTests(unittest.TestCase):
    def test_saved_fixtures_reuse_original_inputs_and_explicit_scenario_selection(self):
        for original in build_input_cases(num_paths=2, seed=7):
            case = samples.build_analysis_case(original.name, num_paths=2, seed=7)
            self.assertEqual(case.saved_data, original.saved_data)
            self.assertEqual(case.assumption_values, original.assumption_values)
            self.assertEqual(case.confirmed_empty_sections, original.confirmed_empty_sections)
            expected = original.scenarios[1:] if original.name == "saved_working" else original.scenarios
            self.assertEqual(map_what_if_scenarios(case.scenario_values), expected)
            self.assertEqual(len(case.scenario_values), 3)
        self.assertEqual(len(build_input_cases(num_paths=2)[0].scenarios), 4)  # Legacy fixture unchanged.

    def test_hand_check_and_error_fixture_differ_only_in_empty_confirmation(self):
        valid = samples.build_analysis_case("hand_check", num_paths=2, seed=7)
        invalid = samples.build_analysis_case("unconfirmed_pension", num_paths=2, seed=7)
        self.assertEqual(valid.saved_data, invalid.saved_data)
        self.assertEqual(valid.scenario_values, invalid.scenario_values)
        self.assertEqual(valid.assumption_values, invalid.assumption_values)
        self.assertEqual(valid.confirmed_empty_sections, ("pension_assets",))
        self.assertEqual(invalid.confirmed_empty_sections, ())
        self.assertEqual(valid.assumption_values["annual_return_volatility"], 0)

    def test_fixture_builds_are_detached_and_hide_nested_data_in_repr(self):
        for name in samples.CASE_NAMES:
            first = samples.build_analysis_case(name, num_paths=2)
            second = samples.build_analysis_case(name, num_paths=2)
            saved = deepcopy(second)
            first.saved_data["financial_assets"].clear()
            first.assumption_values["seed"] = 0
            first.scenario_values[0]["changes"]["monthly_savings"] = 0
            self.assertEqual(second, saved)
            self.assertNotIn("current_value", repr(second))

    def test_sample_only_limits_reject_invalid_builds_without_changing_engine_limits(self):
        self.assertEqual(MAX_PATHS, 100_000)
        self.assertEqual(samples.MAX_SAMPLE_PATHS, 1_000)
        for name in (None, [], "PRIVATE"):
            with self.assertRaises(ModelValidationError):
                samples.build_analysis_case(name)
        for value in (0, -1, 1_001, True, None, "2", 2.5):
            with self.assertRaises(ModelValidationError):
                samples.build_analysis_case("hand_check", num_paths=value)
        for value in (-1, True, None, "7", 1.5):
            with self.assertRaises(ModelValidationError):
                samples.build_analysis_case("hand_check", seed=value)
        self.assertEqual(samples.build_analysis_case("hand_check", seed=10**100).assumption_values["seed"], 10**100)

    def test_building_fixtures_never_maps_calculates_or_uses_rng(self):
        with ExitStack() as stack:
            for target in ("retirement_simulation.analysis_samples.analyze_retirement",
                           "retirement_simulation.input_samples.map_retirement_profile",
                           "retirement_simulation.input_samples.calculate_what_if",
                           "retirement_simulation.input_samples.calculate_monte_carlo", "random.Random"):
                stack.enter_context(patch(target, side_effect=AssertionError(target)))
            for name in samples.CASE_NAMES:
                samples.build_analysis_case(name, num_paths=2)


class AnalysisSampleCLITests(unittest.TestCase):
    def test_hand_check_json_exactly_matches_the_full_contract_example(self):
        doc = (AI_PROJECT_ROOT / "retirement_simulation/docs/analysis-contract.md").read_text()
        expected = json.loads(re.findall(r'```json\n(.*?)\n```', doc, re.S)[2])
        expected["metadata"].update(python_version=platform.python_version(), python_implementation=platform.python_implementation())
        status, payload, stderr, raw = invoke("--case", "hand_check", "--num-paths", "2", "--seed", "7")
        self.assertEqual(status, 0)
        self.assertEqual(payload, expected)
        self.assertEqual(stderr, samples._NOTICE + "\n")
        self.assertEqual(raw.count("\n"), 1)

    def test_every_success_case_runs_both_modes_with_full_output(self):
        for name in samples.CASE_NAMES[:-1]:
            case = samples.build_analysis_case(name, num_paths=2)
            for mode in ("monte-carlo", "what-if"):
                with self.subTest(case=name, mode=mode):
                    status, payload, _, _ = invoke("--case", name, "--mode", mode, "--num-paths", "2")
                    expected = analysis_to_dict(analyze_retirement(
                        case.saved_data, case.assumption_values,
                        scenarios=case.scenario_values if mode == "what-if" else (),
                        confirmed_empty_sections=case.confirmed_empty_sections,
                    ))
                    self.assertEqual((status, payload), (0, expected))
                    self.assertEqual(payload["mode"], "what_if" if mode == "what-if" else "baseline")
                    self.assertTrue(payload["notices"])
                    self.assertEqual(payload["metadata"]["num_paths"], 2)

    def test_default_is_one_working_case_with_three_explicit_features(self):
        status, payload, _, _ = invoke()
        self.assertEqual(status, 0)
        self.assertEqual(payload["metadata"]["num_paths"], 1000)
        self.assertEqual(payload["metadata"]["assumptions"]["seed"], 42)
        self.assertEqual([s["scenario_id"] for s in payload["scenarios"]], ["save_more", "retire_earlier", "lower_expenses"])
        self.assertEqual(payload["baseline_profile"]["current_age"], 40)

    def test_pretty_changes_whitespace_only(self):
        args = ("--case", "hand_check", "--num-paths", "2")
        compact = invoke(*args)
        pretty = invoke(*args, "--pretty")
        self.assertEqual(compact[:3], pretty[:3])
        self.assertGreater(pretty[3].count("\n"), compact[3].count("\n"))

    def test_unconfirmed_pension_is_one_error_json_with_exit_one_in_both_modes(self):
        expected = analysis_error_to_dict(RetirementAnalysisError(
            "input_mapping_failed", "pension_assets", reason_code="empty_confirmation_required"))
        for mode in ("monte-carlo", "what-if"):
            status, payload, stderr, raw = invoke("--case", "unconfirmed_pension", "--mode", mode, "--num-paths", "2")
            self.assertEqual((status, payload), (1, expected))
            self.assertEqual(stderr, samples._NOTICE + "\n")
            self.assertEqual(raw.count("\n"), 1)
            self.assertNotIn("baseline", payload)

    def test_unsafe_seed_reaches_real_output_error_without_truncation(self):
        status, payload, stderr, raw = invoke("--case", "hand_check", "--num-paths", "2", "--seed", "9007199254740992")
        self.assertEqual(status, 1)
        self.assertEqual(payload, analysis_error_to_dict(RetirementAnalysisError(
            "unsafe_json_number", "metadata.assumptions.seed")))
        self.assertNotIn("9007199254740992", raw + stderr)

    def test_what_if_uses_one_integrated_call_and_one_shared_market(self):
        with (patch.object(samples, "analyze_retirement", wraps=analyze_retirement) as call,
              patch.object(samples, "analysis_to_dict", wraps=analysis_to_dict) as output,
              patch("retirement_simulation.what_if_engine.generate_return_paths", wraps=generate_return_paths) as generate,
              patch("retirement_simulation.what_if_engine.calculate_monte_carlo_paths", wraps=calculate_monte_carlo_paths) as mc):
            status, payload, _, _ = invoke("--num-paths", "2")
        self.assertEqual(status, 0)
        self.assertEqual((call.call_count, output.call_count, generate.call_count, mc.call_count), (1, 1, 1, 4))
        self.assertEqual(payload["baseline"]["execution"]["path_source"], "provided")
        self.assertEqual(payload["metadata"]["path_source"], "generated")

    def test_sample_source_and_global_rng_remain_unchanged(self):
        case = samples.build_analysis_case("saved_working", num_paths=2)
        original, state = deepcopy(case), random.getstate()
        with patch.object(samples, "build_analysis_case", return_value=case):
            left = invoke("--num-paths", "2")
            right = invoke("--num-paths", "2")
        self.assertEqual(left, right)
        self.assertEqual(case, original)
        self.assertEqual(random.getstate(), state)

    def test_partial_scope_notices_and_post_retirement_no_effect_are_preserved(self):
        _, payload, _, _ = invoke("--case", "saved_retired_no_pension", "--num-paths", "2")
        codes = {n["code"] for n in payload["notices"]}
        self.assertTrue({"real_estate_not_modeled", "liabilities_not_modeled", "milestones_not_modeled",
                         "confirmed_empty_pensions"}.issubset(codes))
        noop = next(row for row in payload["scenarios"] if row["scenario_id"] == "savings_no_effect")
        self.assertEqual(noop["changes"]["monthly_savings"], 500000)
        self.assertEqual(noop["delta"]["final_balance_delta"], {"p10": 0, "p50": 0, "p90": 0})

    def test_invalid_arguments_are_standard_cli_errors_without_calculation(self):
        choices = (["--case", "all"], ["--mode", "both"], ["--num-paths", "0"], ["--num-paths", "1001"],
                   ["--num-paths", "1.5"], ["--seed", "-1"], ["--seed", "bad"], ["--num-p", "2"],
                   ["--input", "customer.json"], ["--output", "result.json"], ["--details"], ["--case"])
        for args in choices:
            stdout, stderr = StringIO(), StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr), patch.object(samples, "analyze_retirement") as run:
                with self.assertRaises(SystemExit) as raised:
                    samples.main(args)
                self.assertEqual(raised.exception.code, 2)
                run.assert_not_called()
            self.assertEqual(stdout.getvalue(), "")
            self.assertNotIn(samples._NOTICE, stderr.getvalue())

    def test_help_exits_without_running_and_documents_streams_and_guards(self):
        stdout = StringIO()
        with redirect_stdout(stdout), patch.object(samples, "build_analysis_case") as build:
            with self.assertRaises(SystemExit) as raised:
                samples.main(["--help"])
            self.assertEqual(raised.exception.code, 0)
            build.assert_not_called()
        for text in ("stdout", "stderr", "1000", "--pretty", "unconfirmed_pension"):
            self.assertIn(text, stdout.getvalue())

    def test_late_output_failure_never_writes_partial_success_json(self):
        broken = compare()
        object.__setattr__(broken.calculation.scenarios[1].result.final_shortfall_percentiles, "p50", float("inf"))
        with patch.object(samples, "analyze_retirement", return_value=broken):
            status, payload, stderr, _ = invoke("--case", "hand_check", "--num-paths", "2")
        self.assertEqual(status, 1)
        self.assertEqual(set(payload), {"contract_version", "error"})
        self.assertEqual(payload["error"]["field_name"], "scenarios[1].result.final_shortfall_percentiles.p50")
        self.assertNotIn("Traceback", stderr)

    def test_invalid_output_model_uses_the_output_adapter_error(self):
        with patch.object(samples, "analyze_retirement", return_value=None):
            status, payload, _, _ = invoke("--case", "hand_check", "--num-paths", "2")
        self.assertEqual(status, 1)
        self.assertEqual(payload["error"]["code"], "invalid_analysis_result")

    def test_unexpected_bugs_memory_interruptions_and_encoding_failures_are_not_repackaged(self):
        for error in (RuntimeError("PRIVATE"), MemoryError(), KeyboardInterrupt(), SystemExit(4)):
            stdout = StringIO()
            with (redirect_stdout(stdout), redirect_stderr(StringIO()),
                  patch.object(samples, "analyze_retirement", side_effect=error)):
                with self.assertRaises(type(error)) as raised:
                    samples.main(["--num-paths", "2"])
                self.assertIs(raised.exception, error)
            self.assertEqual(stdout.getvalue(), "")
        stdout = StringIO()
        with (redirect_stdout(stdout), redirect_stderr(StringIO()),
              patch.object(samples, "analysis_to_dict", return_value={"invalid": float("nan")})):
            with self.assertRaises(ValueError):
                samples.main(["--case", "hand_check", "--num-paths", "2"])
        self.assertEqual(stdout.getvalue(), "")

    def test_no_customer_files_stdin_network_or_application_runtime_is_used(self):
        with ExitStack() as stack:
            for name in ("builtins.open", "builtins.input", "socket.socket", "subprocess.run", "os.getenv", "time.time"):
                stack.enter_context(patch(name, side_effect=AssertionError(name)))
            stack.enter_context(patch.object(sys.stdin, "read", side_effect=AssertionError("stdin")))
            self.assertEqual(invoke("--case", "hand_check", "--num-paths", "2")[0], 0)

    def test_real_subprocess_streams_and_statuses_without_site_packages(self):
        for name, status in (("hand_check", 0), ("unconfirmed_pension", 1)):
            result = subprocess.run([sys.executable, "-S", "-B", "-m", "retirement_simulation.analysis_samples",
                                     "--case", name, "--num-paths", "2", "--seed", "7"],
                                    cwd=AI_PROJECT_ROOT, capture_output=True, text=True, timeout=10, check=False)
            self.assertEqual(result.returncode, status, result.stderr)
            self.assertEqual(result.stderr, samples._NOTICE + "\n")
            payload = json.loads(result.stdout)
            self.assertEqual(payload["contract_version"], "retirement-analysis-v1")
            self.assertEqual("error" in payload, status == 1)


if __name__ == "__main__":
    unittest.main()
