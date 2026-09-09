"""Benchmark-method regressions; no wall-clock performance gates or large runs."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import gc
from io import StringIO
import json
from pathlib import Path
import random
from statistics import median
import subprocess
import sys
import tracemalloc
import unittest
from unittest.mock import Mock, patch


AI_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(AI_PROJECT_ROOT))

from retirement_simulation import (  # noqa: E402
    analyze_retirement, analysis_to_dict, create_what_if_profile,
    map_retirement_profile, map_simulation_assumptions, map_what_if_scenarios,
)
from retirement_simulation import analysis_benchmark as benchmark  # noqa: E402
from retirement_simulation.models import MAX_PATHS  # noqa: E402
from retirement_simulation.what_if_engine import MAX_WHAT_IF_SCENARIOS  # noqa: E402


SMALL = benchmark.BenchmarkCase(2, 2, 1)


def worker_result(*, environment=None, output=None):
    return subprocess.CompletedProcess([], 0, stdout=json.dumps({
        "environment": {"python_version": "synthetic"} if environment is None else environment,
        "measurement": {"output": {"sha256": "synthetic", "utf8_bytes": 100} if output is None else output},
    }), stderr="")


class BenchmarkFixtureTests(unittest.TestCase):
    def test_case_limits_and_work_budget_do_not_change_engine_caps(self):
        self.assertEqual(MAX_PATHS, 100_000)
        self.assertEqual(MAX_WHAT_IF_SCENARIOS, 10)
        for arguments in ((0, 11, 1), (5001, 11, 1), (10, 0, 1), (10, 82, 1),
                          (10, 11, -1), (10, 11, 11), (5000, 81, 10)):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                benchmark.BenchmarkCase(*arguments)
        self.assertEqual(benchmark.BenchmarkCase(1000, 31, 3).path_year_evaluations, 124_000)

    def test_case_types_are_strict(self):
        for value in (True, None, 1.0, "1"):
            for index in range(3):
                args = [10, 11, 1]
                args[index] = value
                with self.assertRaises(ValueError):
                    benchmark.BenchmarkCase(*args)
        for value in (0, 1, "false", None):
            with self.assertRaises(ValueError):
                benchmark.BenchmarkCase(10, 11, 1, value)
        with self.assertRaises(ValueError):
            benchmark.build_benchmark_request({})

    def test_all_presets_map_and_apply_without_running_large_calculations(self):
        self.assertEqual(len(benchmark.CASES), 14)
        self.assertEqual(len(set(benchmark.CASES.values())), 14)
        for name, case in benchmark.CASES.items():
            with self.subTest(case=name):
                values = benchmark.build_benchmark_request(case)
                mapped = map_retirement_profile(values["saved_data"],
                                                confirmed_empty_sections=values["confirmed_empty_sections"])
                config = map_simulation_assumptions(values["assumption_values"])
                scenarios = map_what_if_scenarios(values["scenarios"])
                self.assertEqual(config.num_paths, case.num_paths)
                self.assertEqual(mapped.profile.end_age - mapped.profile.current_age + 1, case.num_years)
                self.assertEqual(len(scenarios), case.num_scenarios)
                self.assertLessEqual(case.path_year_evaluations, 600_000)
                for scenario in scenarios:
                    create_what_if_profile(mapped.profile, scenario.changes)

    def test_fixture_counts_synthetic_rates_and_seed_are_explicit(self):
        for constant in (True, False):
            values = benchmark.build_benchmark_request(benchmark.BenchmarkCase(2, 51, 3, constant))
            self.assertEqual(values["saved_data"]["basic_info"], {"age": 40, "retirement_age": 50, "end_age": 90})
            self.assertEqual(values["assumption_values"], {"annual_return_mean": .05,
                "annual_return_volatility": 0 if constant else .10, "annual_inflation_rate": .02,
                "num_paths": 2, "seed": 42})
            self.assertEqual(len(values["saved_data"]["financial_assets"]), 1)
            self.assertEqual(len(values["saved_data"]["pension_assets"]), 1)
            self.assertEqual(len(values["saved_data"]["goal_periods"]), 1)
            self.assertEqual([row["changes"]["monthly_savings"] for row in values["scenarios"]], [800000, 850000, 900000])

    def test_fixtures_are_fresh_and_real_execution_preserves_inputs_and_global_rng(self):
        first = benchmark.build_benchmark_request(SMALL)
        second = benchmark.build_benchmark_request(SMALL)
        original, state = deepcopy(first), random.getstate()
        payload = analysis_to_dict(analyze_retirement(**first))
        self.assertEqual(first, original)
        self.assertEqual(state, random.getstate())
        self.assertEqual(payload["mode"], "what_if")
        first["scenarios"][0]["changes"]["monthly_savings"] = 0
        first["saved_data"]["financial_assets"].clear()
        self.assertEqual(second, original)


class BenchmarkMeasurementTests(unittest.TestCase):
    def test_timing_boundaries_and_statistics_use_independent_sample_totals(self):
        with patch.object(benchmark, "perf_counter", side_effect=[10, 11, 13, 16, 20, 22, 26, 32]):
            result = benchmark.measure_timing(SMALL, repeats=2)
        self.assertEqual(result["samples"], [
            {"analysis_s": 1, "conversion_s": 2, "encoding_s": 3, "total_s": 6},
            {"analysis_s": 2, "conversion_s": 4, "encoding_s": 6, "total_s": 12},
        ])
        self.assertEqual(result["summary"]["total_s"], {"min": 6, "median": 9, "max": 12})
        self.assertEqual(result["summary"]["analysis_s"], {"min": 1, "median": 1.5, "max": 2})

    def test_timing_calls_real_pipeline_once_per_warmup_and_sample_without_tracing(self):
        with (patch.object(benchmark, "analyze_retirement", wraps=analyze_retirement) as calculate,
              patch.object(benchmark, "analysis_to_dict", wraps=analysis_to_dict) as convert,
              patch.object(benchmark.tracemalloc, "start", side_effect=AssertionError("must not trace"))):
            result = benchmark.measure_timing(SMALL, repeats=2)
        self.assertEqual(calculate.call_count, 3)
        self.assertEqual(convert.call_count, 3)
        self.assertEqual(len(result["samples"]), 2)
        for sample in result["samples"]:
            self.assertGreaterEqual(sample["total_s"], 0)
            self.assertAlmostEqual(sample["total_s"], sum(sample[p] for p in benchmark._PHASES[:-1]))

    def test_memory_peaks_and_upstream_retention_are_labeled_without_timing_metrics(self):
        result = benchmark.measure_memory(SMALL)
        self.assertFalse(tracemalloc.is_tracing())
        self.assertNotIn("samples", result)
        self.assertEqual(result["pipeline_peak_bytes"], max(result[key]["peak_bytes"]
                                                           for key in ("analysis", "conversion", "encoding")))
        for key in ("analysis", "conversion", "encoding"):
            item = result[key]
            self.assertEqual(item["incremental_peak_bytes"], item["peak_bytes"] - item["start_bytes"])
            self.assertGreaterEqual(item["peak_bytes"], item["end_bytes"])
            self.assertGreaterEqual(item["incremental_peak_bytes"], 0)
        self.assertGreater(result["conversion"]["start_bytes"], 0)
        self.assertGreater(result["encoding"]["start_bytes"], 0)

    def test_memory_tracing_is_stopped_on_calculation_failure(self):
        model = analyze_retirement(**benchmark.build_benchmark_request(SMALL))
        with patch.object(benchmark, "analyze_retirement", side_effect=[model, RuntimeError("test failure")]):
            with self.assertRaisesRegex(RuntimeError, "test failure"):
                benchmark.measure_memory(SMALL)
        self.assertFalse(tracemalloc.is_tracing())

    def test_preexisting_tracing_is_never_reset_or_stopped(self):
        tracemalloc.start()
        try:
            for function in (benchmark.measure_timing, benchmark.measure_memory):
                with self.assertRaises(RuntimeError):
                    function(SMALL)
                self.assertTrue(tracemalloc.is_tracing())
        finally:
            tracemalloc.stop()

    def test_disabled_gc_is_rejected_without_changing_callers_state(self):
        gc.disable()
        try:
            for function in (benchmark.measure_timing, benchmark.measure_memory):
                with self.assertRaises(RuntimeError):
                    function(SMALL)
                self.assertFalse(gc.isenabled())
        finally:
            gc.enable()

    def test_timing_and_memory_outputs_match_the_real_engine_and_do_not_leak_result_values(self):
        values = benchmark.build_benchmark_request(SMALL)
        payload = analysis_to_dict(analyze_retirement(**values))
        expected = benchmark._proof(payload, benchmark._encode(payload), SMALL)
        self.assertEqual(benchmark.measure_timing(SMALL, repeats=1)["output"], expected)
        self.assertEqual(benchmark.measure_memory(SMALL)["output"], expected)
        self.assertEqual(set(expected), {"sha256", "utf8_bytes"})
        self.assertEqual(len(expected["sha256"]), 64)

    def test_proof_rejects_incomplete_payloads_and_encoding_mismatches(self):
        payload = analysis_to_dict(analyze_retirement(**benchmark.build_benchmark_request(SMALL)))
        with self.assertRaises(RuntimeError):
            benchmark._proof(payload, b'{}', SMALL)
        for field, value in (("notices", []), ("scenarios", []), ("mode", "baseline")):
            changed = payload | {field: value}
            with self.assertRaises(RuntimeError):
                benchmark._proof(changed, benchmark._encode(changed), SMALL)

    def test_rss_units_are_converted_only_on_linux(self):
        with patch.object(benchmark.sys, "platform", "win32"):
            self.assertIsNone(benchmark._peak_rss())
        if sys.platform == "linux":
            with patch("resource.getrusage", return_value=Mock(ru_maxrss=123)):
                self.assertEqual(benchmark._peak_rss(), 123 * 1024)

    def test_environment_reports_source_fingerprint_without_host_or_customer_identity(self):
        environment = benchmark._environment()
        self.assertEqual(len(environment["runtime_sources_sha256"]), 64)
        self.assertEqual(environment["timer"], "time.perf_counter")
        self.assertTrue(environment["gc_enabled"])
        self.assertNotIn("hostname", environment)
        self.assertNotIn("username", environment)


class BenchmarkRunnerTests(unittest.TestCase):
    def test_recorded_measurements_have_all_cases_and_matching_outputs(self):
        path = AI_PROJECT_ROOT / "retirement_simulation/docs/analysis-performance-results.json"
        artifact = json.loads(path.read_text())
        self.assertEqual(artifact["artifact_version"], "retirement-analysis-performance-record-v1")
        self.assertEqual(len(artifact["environment"]["runtime_sources_sha256"]), 64)
        self.assertEqual(artifact["method"]["timing_repeats"], 3)
        self.assertEqual(artifact["method"]["memory_repeats"], 1)
        self.assertEqual([run["case"]["name"] for run in artifact["runs"]], list(benchmark.CASES))
        for run in artifact["runs"]:
            self.assertEqual(run["benchmark_version"], benchmark.BENCHMARK_VERSION)
            self.assertEqual(run["timing"]["output"], run["memory"]["output"])
            self.assertEqual(len(run["timing"]["samples"]), 3)
            self.assertEqual(run["case"]["path_year_evaluations"],
                             benchmark.CASES[run["case"]["name"]].path_year_evaluations)

    def test_recorded_statistics_and_memory_arithmetic_are_consistent(self):
        path = AI_PROJECT_ROOT / "retirement_simulation/docs/analysis-performance-results.json"
        for run in json.loads(path.read_text())["runs"]:
            samples, summary, memory = run["timing"]["samples"], run["timing"]["summary"], run["memory"]
            for phase in benchmark._PHASES:
                values = [row[phase] for row in samples]
                self.assertEqual(summary[phase], {"min": min(values), "median": median(values), "max": max(values)})
                self.assertGreaterEqual(min(values), 0)
            for row in samples:
                self.assertAlmostEqual(row["total_s"], sum(row[phase] for phase in benchmark._PHASES[:-1]))
            self.assertEqual(memory["pipeline_peak_bytes"], max(memory[key]["peak_bytes"]
                                                               for key in ("analysis", "conversion", "encoding")))
            for key in ("analysis", "conversion", "encoding"):
                self.assertEqual(memory[key]["incremental_peak_bytes"], memory[key]["peak_bytes"] - memory[key]["start_bytes"])

    def test_parent_runs_two_sequential_isolated_workers_with_timeouts(self):
        with patch.object(benchmark.subprocess, "run", side_effect=[worker_result(), worker_result()]) as run:
            result = benchmark.run_benchmark("smoke_baseline", repeats=2, timeout=10)
        self.assertEqual(run.call_count, 2)
        for call, phase in zip(run.call_args_list, ("timing", "memory")):
            command = call.args[0]
            self.assertEqual(command[:4], [sys.executable, "-S", "-B", "-m"])
            self.assertEqual(command[-2:], ["--_worker", phase])
            self.assertEqual(call.kwargs["timeout"], 10)
            self.assertEqual(call.kwargs["cwd"], AI_PROJECT_ROOT)
            self.assertFalse(call.kwargs.get("shell", False))
        self.assertEqual(result["method"]["timing_repeats"], 2)
        self.assertEqual(result["case"]["path_year_evaluations"], 110)

    def test_bad_runner_options_are_rejected_before_starting_workers(self):
        with patch.object(benchmark.subprocess, "run", side_effect=AssertionError("must not execute")):
            for name in (None, [], "unknown"):
                with self.assertRaises(ValueError):
                    benchmark.run_benchmark(name)
            for key, values in (("repeats", (0, 6, True, 1.5)), ("timeout", (0, 61, True, "45"))):
                for value in values:
                    with self.assertRaises(ValueError):
                        benchmark.run_benchmark("smoke_baseline", **{key: value})

    def test_worker_timeout_or_failure_stops_the_run_without_partial_report(self):
        for error in (subprocess.TimeoutExpired([], 1), subprocess.CalledProcessError(1, []), OSError("test")):
            with patch.object(benchmark.subprocess, "run", side_effect=error) as run:
                with self.assertRaises(RuntimeError):
                    benchmark.run_benchmark("smoke_baseline")
                self.assertEqual(run.call_count, 1)
        with patch.object(benchmark.subprocess, "run", side_effect=[worker_result(), subprocess.TimeoutExpired([], 1)]):
            with self.assertRaisesRegex(RuntimeError, "memory worker timed out"):
                benchmark.run_benchmark("smoke_baseline")

    def test_code_environment_or_output_mismatch_rejects_combined_report(self):
        for bad in (worker_result(environment={"python_version": "different"}), worker_result(output={"sha256": "different"})):
            with patch.object(benchmark.subprocess, "run", side_effect=[worker_result(), bad]):
                with self.assertRaisesRegex(RuntimeError, "workers differ"):
                    benchmark.run_benchmark("smoke_baseline")

    def test_cli_rejects_invalid_flags_or_limits_without_running_workers(self):
        for args in (["--case", "unknown"], ["--num-paths", "100000"], ["--input", "customer.json"],
                     ["--repeat", "1"], ["--repeats", "0"], ["--timeout", "61"]):
            with redirect_stderr(StringIO()), patch.object(benchmark, "run_benchmark") as run:
                with self.assertRaises(SystemExit) as raised:
                    benchmark.main(args)
                self.assertEqual(raised.exception.code, 2)
                run.assert_not_called()

    def test_cli_failure_has_no_stdout_and_returns_nonzero(self):
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr), patch.object(benchmark, "run_benchmark", side_effect=RuntimeError("test")):
            self.assertEqual(benchmark.main([]), 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("Benchmark failed", stderr.getvalue())

    def test_real_cli_smoke_report_runs_without_site_packages_and_matches_both_workers(self):
        completed = subprocess.run([sys.executable, "-S", "-B", "-m", "retirement_simulation.analysis_benchmark",
                                    "--case", "smoke_what_if", "--repeats", "1", "--timeout", "10"],
                                   cwd=AI_PROJECT_ROOT, capture_output=True, text=True, timeout=25, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        result = json.loads(completed.stdout)
        self.assertEqual(result["benchmark_version"], benchmark.BENCHMARK_VERSION)
        self.assertEqual(result["timing"]["output"], result["memory"]["output"])
        self.assertEqual(len(result["timing"]["samples"]), 1)
        for name in ("saved_data", "baseline_profile", "assumption_values", "success_probability"):
            self.assertNotIn(f'"{name}":', completed.stdout)


if __name__ == "__main__":
    unittest.main()
