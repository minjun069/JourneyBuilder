from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import FrozenInstanceError, replace
from io import StringIO
from pathlib import Path
import random
import subprocess
import sys
import unittest
from unittest.mock import patch


AI_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(AI_PROJECT_ROOT))

from retirement_simulation import (  # noqa: E402
    ModelValidationError,
    MonteCarloCalculationError,
    PensionIncome,
    RetirementExpensePeriod,
    RetirementProfile,
    SimulationAssumptions,
    calculate_monte_carlo,
)
from retirement_simulation.monte_carlo_samples import build_monte_carlo_cases, main  # noqa: E402
from retirement_simulation.samples import SampleCase, build_sample_cases  # noqa: E402


def run_main(argv):
    output, errors = StringIO(), StringIO()
    with redirect_stdout(output), redirect_stderr(errors):
        status = main(argv)
    return status, output.getvalue(), errors.getvalue()


def short_case(**profile_changes) -> SampleCase:
    value = RetirementProfile(**({
        "current_age": 60, "retirement_age": 60, "end_age": 63,
        "liquid_assets": 120, "monthly_savings": 0,
        "retirement_expenses": (RetirementExpensePeriod(
            from_age=60, to_age=63, monthly_amount=10,
        ),),
        "pension_incomes": (PensionIncome(start_age=62, end_age=63, monthly_amount=30),),
    } | profile_changes))
    config = SimulationAssumptions(annual_return_mean=0, annual_return_volatility=0,
                                   annual_inflation_rate=0, num_paths=3, seed=99)
    return SampleCase("hand_checked_example", value, config)


class MonteCarloSampleInputTests(unittest.TestCase):
    def test_reuses_the_three_original_profiles_without_changing_fixed_assumptions(self) -> None:
        fixed = build_sample_cases()
        stochastic = build_monte_carlo_cases()
        self.assertEqual(tuple(case.name for case in stochastic), tuple(case.name for case in fixed))
        self.assertEqual(tuple(case.profile for case in stochastic), tuple(case.profile for case in fixed))
        self.assertEqual(build_sample_cases(), fixed)
        for case in fixed:
            self.assertEqual((case.assumptions.annual_return_mean,
                              case.assumptions.annual_return_volatility,
                              case.assumptions.annual_inflation_rate), (0, 0, 0))

    def test_demo_defaults_are_explicit_and_positive_volatility(self) -> None:
        for case in build_monte_carlo_cases():
            config = case.assumptions
            self.assertEqual((config.annual_return_mean, config.annual_return_volatility,
                              config.annual_inflation_rate, config.num_paths, config.seed),
                             (0.05, 0.10, 0.02, 1000, 42))

    def test_fresh_frozen_cases_accept_valid_count_and_seed_overrides(self) -> None:
        first = build_monte_carlo_cases(num_paths=1, seed=0)
        second = build_monte_carlo_cases(num_paths=1, seed=0)
        self.assertEqual(first, second)
        for left, right in zip(first, second):
            self.assertIsNot(left.profile, right.profile)
            self.assertIsNot(left.assumptions, right.assumptions)
            self.assertEqual((left.assumptions.num_paths, left.assumptions.seed), (1, 0))
            with self.assertRaises(FrozenInstanceError):
                left.assumptions.seed = 1
        # Constructing a maximum-count demo does not allocate any paths.
        for case in build_monte_carlo_cases(num_paths=100_000, seed=2**256 + 1):
            self.assertEqual((case.assumptions.num_paths, case.assumptions.seed),
                             (100_000, 2**256 + 1))

    def test_programmatic_builder_preserves_model_validation(self) -> None:
        for options in ({"num_paths": 0}, {"num_paths": 100_001}, {"num_paths": True},
                        {"num_paths": "3"}, {"seed": -1}, {"seed": True}, {"seed": 1.5}):
            with self.subTest(options=options):
                with self.assertRaises(ModelValidationError):
                    build_monte_carlo_cases(**options)


class MonteCarloSampleOutputTests(unittest.TestCase):
    def test_default_runner_calls_real_engine_once_for_each_case(self) -> None:
        with patch("retirement_simulation.monte_carlo_samples.calculate_monte_carlo",
                   wraps=calculate_monte_carlo) as engine:
            status, output, errors = run_main([])
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.assertEqual(engine.call_count, 3)
        for call, case in zip(engine.call_args_list, build_monte_carlo_cases()):
            self.assertEqual(call.args, (case.profile, case.assumptions))
        self.assertIn("Calculated 3 synthetic Monte Carlo cases.", output)

    def test_summary_uses_actual_result_counts_percentiles_histogram_and_metadata(self) -> None:
        status, output, errors = run_main(["--num-paths", "11", "--seed", "0"])
        self.assertEqual((status, errors), (0, ""))
        for case in build_monte_carlo_cases(num_paths=11, seed=0):
            result = calculate_monte_carlo(case.profile, case.assumptions)
            section = output.split(f"[{case.name}]\n")[1].split("\n[")[0]
            self.assertIn(f"current_age={case.profile.current_age}", section)
            self.assertIn(f"retirement_age={case.profile.retirement_age}", section)
            self.assertIn(f"end_age={case.profile.end_age}", section)
            self.assertIn(f"success_count={result.success_count}/11", section)
            self.assertIn(f"depletion_count={result.depletion_count}/11", section)
            self.assertIn(f"never_depleted_count={result.never_depleted_count}", section)
            self.assertIn(f"success_probability={result.success_probability:.6f} "
                          f"({result.success_probability:.3%})", section)
            self.assertIn(f"depletion_probability={result.depletion_probability:.6f} "
                          f"({result.depletion_probability:.3%})", section)
            for name, values in (("final_balance", result.final_balance_percentiles),
                                 ("final_shortfall", result.final_shortfall_percentiles)):
                self.assertIn(f"{name} (nominal KRW): p10={values.p10:,.2f}, "
                              f"p50={values.p50:,.2f}, p90={values.p90:,.2f}", section)
            histogram = ", ".join(f"{item.age}:{item.count}" for item in result.first_depletion_counts
                                  if item.count) or "none"
            self.assertIn(f"first_depletion_counts (age:count, nonzero only): {histogram}\n", section)
            self.assertIn("num_paths=11, configured_seed=0, effective_seed=0, path_source=generated", section)
            self.assertIn("return_model=lognormal_gross_return", section)
            self.assertIn("model_version=retirement-mc-v1", section)
            self.assertIn("rng_algorithm=python.random.Random.gauss", section)
            self.assertIn(f"python={result.metadata.python_implementation} {result.metadata.python_version}",
                          section)
        self.assertNotIn("age | balance_p10", output)

    def test_output_explains_assumptions_rounding_probability_and_service_limits(self) -> None:
        _, output, _ = run_main(["--num-paths", "1"])
        for notice in (
            "Not market forecasts", "not calibrated market estimates or investment recommendations",
            "annual decimal fractions", "annual_return_mean=0.05", "annual_return_volatility=0.1",
            "annual_inflation_rate=0.02", "no negative year-end balance over the entire horizon",
            "recovery does not erase depletion", "unmet funding gaps, not loans",
            "Display rounding does not change depletion or counts", "include all paths at each age",
            "not a single trajectory or a confidence interval", "0%/100% are not guarantees",
            "BE readiness scores", "same inputs, code, Python version and execution environment",
            "same seed across different horizons does not align paths",
            "What-if samples: python -m retirement_simulation.what_if_samples",
            "Real-data adapters and service integration are not implemented",
        ):
            with self.subTest(notice=notice):
                self.assertIn(notice, output)

    def test_case_selection_and_overrides_only_calculate_selected_case(self) -> None:
        for expected in build_monte_carlo_cases(num_paths=1, seed=2**256 + 1):
            with self.subTest(case=expected.name):
                with patch("retirement_simulation.monte_carlo_samples.calculate_monte_carlo",
                           wraps=calculate_monte_carlo) as engine:
                    status, output, errors = run_main([
                        "--case", expected.name, "--num-paths", "1", "--seed", str(2**256 + 1),
                    ])
                self.assertEqual((status, errors), (0, ""))
                engine.assert_called_once_with(expected.profile, expected.assumptions)
                self.assertIn(f"[{expected.name}]", output)
                self.assertEqual(output.count("success_probability="), 1)
                self.assertIn("Calculated 1 synthetic Monte Carlo case.", output)

    def test_details_include_all_106_age_rows_in_order_and_zero_histogram_bins(self) -> None:
        _, output, errors = run_main(["--details", "--num-paths", "7"])
        self.assertEqual(errors, "")
        rows = [line for line in output.splitlines() if line.split(" | ")[0].isdigit()]
        self.assertEqual(len(rows), 106)
        header = "age | balance_p10 | balance_p50 | balance_p90 | first_depletion_count"
        self.assertEqual(output.count(header), 3)
        expected = []
        for case in build_monte_carlo_cases(num_paths=7):
            result = calculate_monte_carlo(case.profile, case.assumptions)
            for point, count in zip(result.asset_percentiles, result.first_depletion_counts):
                expected.append(f"{point.age} | {point.balance.p10:,.2f} | {point.balance.p50:,.2f} | "
                                f"{point.balance.p90:,.2f} | {count.count}")
        self.assertEqual(rows, expected)
        self.assertTrue(any(row.endswith(" | 0") for row in rows))

    def test_repeated_output_does_not_depend_on_global_rng_activity(self) -> None:
        original_state = random.getstate()
        options = ["--details", "--num-paths", "13", "--seed", "17"]
        try:
            expected = run_main(options)
            self.assertEqual(random.getstate(), original_state)
            for _ in range(19):
                random.random()
            changed_state = random.getstate()
            self.assertEqual(run_main(options), expected)
            self.assertEqual(random.getstate(), changed_state)
        finally:
            random.setstate(original_state)

    def test_hand_checked_recovery_is_still_failure_and_unused_seed_is_not_effective(self) -> None:
        case = short_case()
        with patch("retirement_simulation.monte_carlo_samples.build_monte_carlo_cases", return_value=(case,)):
            status, output, errors = run_main(["--details"])
        self.assertEqual((status, errors), (0, ""))
        self.assertIn("success_probability=0.000000 (0.000%), success_count=0/3", output)
        self.assertIn("final_balance (nominal KRW): p10=360.00, p50=360.00, p90=360.00", output)
        self.assertIn("final_shortfall (nominal KRW): p10=0.00, p50=0.00, p90=0.00", output)
        self.assertIn("first_depletion_counts (age:count, nonzero only): 61:3", output)
        self.assertIn("configured_seed=99, effective_seed=None", output)
        self.assertIn("return_model=constant, rng_algorithm=None", output)
        self.assertIn("61 | -120.00 | -120.00 | -120.00 | 3", output)

    def test_rounded_negative_zero_keeps_failure_count_visible(self) -> None:
        case = short_case(end_age=60, liquid_assets=12, pension_incomes=(), retirement_expenses=(
            RetirementExpensePeriod(from_age=60, to_age=60, monthly_amount=1),
        ))
        case = replace(case, assumptions=replace(case.assumptions, annual_return_mean=-1e-15))
        with patch("retirement_simulation.monte_carlo_samples.build_monte_carlo_cases", return_value=(case,)):
            _, output, _ = run_main(["--details"])
        self.assertIn("final_balance (nominal KRW): p10=-0.00, p50=-0.00, p90=-0.00", output)
        self.assertIn("depletion_probability=1.000000 (100.000%), depletion_count=3/3", output)
        self.assertIn("60 | -0.00 | -0.00 | -0.00 | 3", output)

    def test_no_depletion_has_none_histogram_not_a_fabricated_age(self) -> None:
        case = short_case(end_age=60, liquid_assets=0, pension_incomes=(), retirement_expenses=(
            RetirementExpensePeriod(from_age=60, to_age=60, monthly_amount=0),
        ))
        case = replace(case, assumptions=replace(case.assumptions, annual_return_volatility=0.1))
        with patch("retirement_simulation.monte_carlo_samples.build_monte_carlo_cases", return_value=(case,)):
            _, output, _ = run_main(["--details"])
        self.assertIn("success_probability=1.000000 (100.000%), success_count=3/3", output)
        self.assertIn("first_depletion_counts (age:count, nonzero only): none", output)
        self.assertIn("60 | 0.00 | 0.00 | 0.00 | 0", output)


class MonteCarloSampleCliTests(unittest.TestCase):
    def test_invalid_cli_options_fail_before_any_calculation_or_result_output(self) -> None:
        invalid_options = (
            ["--num-paths", "0"], ["--num-paths", "-1"], ["--num-paths", "100001"],
            ["--num-paths", "1.5"], ["--num-paths", "NaN"], ["--seed", "-1"],
            ["--seed", "1.5"], ["--seed", "not-an-integer"], ["--case", "unknown"],
            ["--unknown"], ["--num-paths"], ["--num", "3"],
        )
        with patch("retirement_simulation.monte_carlo_samples.calculate_monte_carlo") as engine:
            for options in invalid_options:
                with self.subTest(options=options):
                    output, errors = StringIO(), StringIO()
                    with redirect_stdout(output), redirect_stderr(errors):
                        with self.assertRaises(SystemExit) as caught:
                            main(options)
                    self.assertEqual(caught.exception.code, 2)
                    self.assertEqual(output.getvalue(), "")
                    self.assertIn("error:", errors.getvalue())
        engine.assert_not_called()

    def test_maximum_count_option_is_accepted_without_running_an_unbounded_demo(self) -> None:
        failure = MonteCarloCalculationError("return_generation", "annual_returns")
        with patch("retirement_simulation.monte_carlo_samples.calculate_monte_carlo", side_effect=failure) as engine:
            status, output, _ = run_main(["--num-paths", "100000", "--seed", "0"])
        self.assertEqual((status, output), (1, ""))
        self.assertEqual(engine.call_args.args[1].num_paths, 100_000)
        self.assertEqual(engine.call_args.args[1].seed, 0)
        self.assertEqual(engine.call_count, 1)

    def test_failure_in_later_case_returns_error_without_printing_earlier_results(self) -> None:
        calls = 0

        def fail_second(value, config):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise MonteCarloCalculationError("cashflow", "closing_balance", path_index=3, age=70)
            return calculate_monte_carlo(value, config)

        with patch("retirement_simulation.monte_carlo_samples.calculate_monte_carlo", side_effect=fail_second):
            status, output, errors = run_main(["--num-paths", "5", "--details"])
        self.assertEqual(calls, 2)
        self.assertEqual((status, output), (1, ""))
        self.assertIn("cashflow.closing_balance: calculation failed at path 3 at age 70", errors)
        self.assertIn("No results were printed", errors)
        self.assertNotIn("Traceback", errors)
        self.assertNotIn("100000000", errors)

    def test_invalid_built_in_model_returns_safe_error_without_results(self) -> None:
        failure = ModelValidationError("num_paths", "is outside the supported range")
        with patch("retirement_simulation.monte_carlo_samples.build_monte_carlo_cases", side_effect=failure):
            with patch("retirement_simulation.monte_carlo_samples.calculate_monte_carlo") as engine:
                status, output, errors = run_main([])
        self.assertEqual((status, output), (1, ""))
        self.assertIn("num_paths: is outside the supported range", errors)
        engine.assert_not_called()

    def test_help_exits_without_calculation_and_describes_options(self) -> None:
        output = StringIO()
        with patch("retirement_simulation.monte_carlo_samples.calculate_monte_carlo") as engine:
            with redirect_stdout(output):
                with self.assertRaises(SystemExit) as caught:
                    main(["--help"])
        self.assertEqual(caught.exception.code, 0)
        engine.assert_not_called()
        for option in ("--case", "--num-paths", "--seed", "--details", "No customer files"):
            self.assertIn(option, output.getvalue())

    def test_module_default_help_and_invalid_arguments_run_without_site_packages(self) -> None:
        for options, status in (([], 0), (["--help"], 0), (["--num-paths", "0"], 2)):
            with self.subTest(options=options):
                completed = subprocess.run(
                    [sys.executable, "-S", "-B", "-m", "retirement_simulation.monte_carlo_samples", *options],
                    cwd=AI_PROJECT_ROOT, capture_output=True, text=True, timeout=30, check=False,
                )
                self.assertEqual(completed.returncode, status, completed.stderr)
                if not options:
                    self.assertIn("Calculated 3 synthetic Monte Carlo cases.", completed.stdout)
                    self.assertEqual(completed.stdout.count("num_paths=1000"), 3)
                    self.assertEqual(completed.stderr, "")
                elif status == 2:
                    self.assertEqual(completed.stdout, "")
                    self.assertIn("--num-paths", completed.stderr)
                else:
                    self.assertIn("--details", completed.stdout)
                    self.assertEqual(completed.stderr, "")


if __name__ == "__main__":
    unittest.main()
