"""Saved-shaped synthetic CLI fixtures, real engines, notices and failure exits."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
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
    ModelValidationError, MonteCarloCalculationError, WhatIfCalculationError,
    WhatIfChanges, WhatIfScenario, calculate_monte_carlo, calculate_what_if,
    create_what_if_profile, generate_return_paths, map_retirement_profile, map_simulation_assumptions,
)
from retirement_simulation.input_samples import build_input_cases, main  # noqa: E402
from retirement_simulation.tests.test_input_adapter import saved_data  # noqa: E402
from retirement_simulation.tests.test_what_if_samples import case_section, scenario_section  # noqa: E402


CLI = "retirement_simulation.input_samples."


def run_main(argv):
    output, errors = StringIO(), StringIO()
    with redirect_stdout(output), redirect_stderr(errors):
        status = main(argv)
    return status, output.getvalue(), errors.getvalue()


def map_case(case):
    return (map_retirement_profile(case.saved_data, confirmed_empty_sections=case.confirmed_empty_sections),
            map_simulation_assumptions(case.assumption_values))


def summary_section(section):
    return section.split("baseline:\n", 1)[1].split("\nscenario ", 1)[0]


class InputSampleFixtureTests(unittest.TestCase):
    def test_saved_shapes_and_explicit_demo_configuration(self):
        cases = build_input_cases()
        self.assertEqual(tuple(c.name for c in cases),
                         ("saved_working", "saved_retired_no_pension", "saved_pension_gap"))
        self.assertEqual(cases[0].saved_data, saved_data())
        self.assertEqual(tuple(len(c.scenarios) for c in cases), (4, 3, 3))
        for case in cases:
            self.assertIsInstance(case.saved_data, dict)
            self.assertEqual(case.assumption_values, {
                "annual_return_mean": 0.05, "annual_return_volatility": 0.1,
                "annual_inflation_rate": 0.02, "num_paths": 1000, "seed": 42,
            })
            self.assertNotIn("current_form", case.saved_data)

    def test_builds_fresh_nested_fixtures_and_frozen_containers(self):
        first, second = build_input_cases(), build_input_cases()
        self.assertEqual(first, second)
        for left, right in zip(first, second):
            self.assertIsNot(left.saved_data, right.saved_data)
            self.assertIsNot(left.assumption_values, right.assumption_values)
            self.assertIsNot(left.scenarios[0], right.scenarios[0])
            with self.assertRaises(FrozenInstanceError):
                left.name = "changed"
        first[0].saved_data["goal_periods"][0]["items"][0]["amount"] = 0
        first[0].assumption_values["seed"] = 0
        self.assertEqual(second, build_input_cases())
        self.assertEqual(first[1].assumption_values["seed"], 42)

    def test_builder_validates_overrides_without_mapping_or_sampling(self):
        state = random.getstate()
        with (patch(CLI + "map_retirement_profile") as profile_mapper,
              patch(CLI + "map_simulation_assumptions") as assumption_mapper,
              patch(CLI + "calculate_what_if") as engine,
              patch("retirement_simulation.return_paths.Random") as rng):
            for count, seed in ((1, 0), (100_000, 2**256 + 1)):
                cases = build_input_cases(num_paths=count, seed=seed)
                self.assertTrue(all(c.assumption_values["num_paths"] == count and
                                    c.assumption_values["seed"] == seed for c in cases))
            for changes in ({"num_paths": 0}, {"num_paths": 100001}, {"num_paths": True},
                            {"num_paths": "1"}, {"seed": -1}, {"seed": True}, {"seed": 1.0}):
                with self.assertRaises(ModelValidationError):
                    build_input_cases(**changes)
        for mocked in (profile_mapper, assumption_mapper, engine, rng):
            mocked.assert_not_called()
        self.assertEqual(random.getstate(), state)

    def test_mapping_preserves_expected_profiles_and_exclusion_notices(self):
        working, retired, gap = [map_case(c)[0] for c in build_input_cases()]
        self.assertEqual((working.profile.liquid_assets, working.profile.monthly_savings), (120_000_000, 750_000))
        self.assertEqual((retired.profile.current_age, retired.profile.retirement_age), (65, 60))
        self.assertEqual(retired.profile.liquid_assets, 100_000_000)
        self.assertEqual(retired.profile.pension_incomes, ())
        self.assertTrue({"real_estate_not_modeled", "liabilities_not_modeled", "milestones_not_modeled",
                         "confirmed_empty_pensions"} <= {n.code for n in retired.notices})
        self.assertEqual([(p.start_age, p.end_age, p.monthly_amount) for p in gap.profile.pension_incomes],
                         [(65, 74, 800_000)])
        self.assertEqual((gap.notices[-1].code, gap.notices[-1].field_name),
                         ("pension_outside_horizon", "pension_assets[1]"))

    def test_scenarios_are_absolute_and_do_not_shift_or_mutate_saved_inputs(self):
        cases = build_input_cases()
        original = deepcopy(cases)
        for case in cases:
            mapped, _ = map_case(case)
            for scenario in case.scenarios:
                changed = create_what_if_profile(mapped.profile, scenario.changes)
                self.assertEqual(changed.pension_incomes, mapped.profile.pension_incomes)
                self.assertEqual(changed.liquid_assets, mapped.profile.liquid_assets)
        early = create_what_if_profile(map_case(cases[0])[0].profile, cases[0].scenarios[2].changes)
        self.assertEqual((early.retirement_age, early.retirement_expenses[0].from_age), (58, 58))
        self.assertEqual(cases[0].scenarios[1].changes.monthly_savings, 1_000_000)
        self.assertEqual(cases[2].scenarios[1].changes.monthly_savings, 0)
        self.assertEqual(cases, original)


class InputSampleOutputTests(unittest.TestCase):
    def assert_summary(self, piece, result):
        for name in ("success", "depletion"):
            probability = getattr(result, name + "_probability")
            count = getattr(result, name + "_count")
            self.assertIn(f"{name}_probability={probability:.6f} ({probability:.3%}), "
                          f"{name}_count={count}/{result.num_paths}", piece)
        self.assertIn(f"never_depleted_count={result.never_depleted_count}", piece)
        for label, values in (("final_balance", result.final_balance_percentiles),
                              ("final_shortfall", result.final_shortfall_percentiles)):
            self.assertIn(f"{label} (nominal KRW): p10={values.p10:,.2f}, p50={values.p50:,.2f}, p90={values.p90:,.2f}", piece)
        histogram = ", ".join(f"{row.age}:{row.count}" for row in result.first_depletion_counts if row.count) or "none"
        self.assertIn(f"first_depletion_counts (age:count, nonzero only): {histogram}", piece)

    def test_default_mode_maps_once_and_runs_one_common_batch_per_case(self):
        with (patch(CLI + "map_retirement_profile", wraps=map_retirement_profile) as profiles,
              patch(CLI + "map_simulation_assumptions", wraps=map_simulation_assumptions) as assumptions,
              patch(CLI + "calculate_what_if", wraps=calculate_what_if) as engine,
              patch(CLI + "calculate_monte_carlo") as standalone_mc,
              patch("retirement_simulation.what_if_engine.generate_return_paths", wraps=generate_return_paths) as paths):
            status, output, errors = run_main([])
        self.assertEqual((status, errors), (0, ""))
        for mocked in (profiles, assumptions, engine, paths):
            self.assertEqual(mocked.call_count, 3)
        standalone_mc.assert_not_called()
        for call, case in zip(profiles.call_args_list, build_input_cases()):
            self.assertEqual(call.args, (case.saved_data,))
            self.assertEqual(call.kwargs, {"confirmed_empty_sections": case.confirmed_empty_sections})
        self.assertIn("Calculated 3 synthetic saved-input cases; mode=what-if; 10 scenario comparisons.", output)
        self.assertNotIn("age | balance_p10", output)

    def test_mc_mode_calls_only_monte_carlo_after_real_mapping(self):
        with (patch(CLI + "calculate_monte_carlo", wraps=calculate_monte_carlo) as mc,
              patch(CLI + "calculate_what_if") as comparison,
              patch("retirement_simulation.monte_carlo.generate_return_paths", wraps=generate_return_paths) as paths):
            status, output, errors = run_main(["--mode", "monte-carlo", "--num-paths", "3"])
        self.assertEqual((status, errors), (0, ""))
        self.assertEqual((mc.call_count, paths.call_count), (3, 3))
        comparison.assert_not_called()
        self.assertNotIn("\nscenario ", output)
        self.assertIn("mode=monte-carlo; 0 scenario comparisons.", output)

    def test_both_modes_print_actual_summaries_deltas_and_provenance(self):
        for mode in ("monte-carlo", "what-if"):
            status, output, errors = run_main(["--mode", mode, "--num-paths", "7", "--seed", "0"])
            self.assertEqual((status, errors), (0, ""))
            for case in build_input_cases(num_paths=7, seed=0):
                mapped, config = map_case(case)
                section = case_section(output, case.name)
                self.assertIn("mapping_version=retirement-input-v1", section)
                self.assertIn("num_paths=7, configured_seed=0, effective_seed=0, path_source=generated", section)
                self.assertIn("mc_model_version=retirement-mc-v1", section)
                result = (calculate_monte_carlo(mapped.profile, config) if mode == "monte-carlo"
                          else calculate_what_if(mapped.profile, config, case.scenarios))
                baseline = result if mode == "monte-carlo" else result.baseline
                self.assert_summary(summary_section(section), baseline)
                source = "generated, effective_seed=0" if mode == "monte-carlo" else "provided, effective_seed=None"
                self.assertIn(f"result_source: path_source={source}", section)
                if mode == "what-if":
                    self.assertIn("what_if_model_version=retirement-what-if-v1", section)
                    for item in result.scenarios:
                        piece = scenario_section(section, item.scenario_id)
                        self.assert_summary(piece, item.result)
                        self.assertIn(f"success_probability_delta_pp={item.success_probability_delta_pp:+.3f}pp", piece)
                        for label, values in (("final_balance_delta", item.final_balance_delta),
                                              ("final_shortfall_delta", item.final_shortfall_delta)):
                            self.assertIn(f"{label} (nominal KRW): p10={values.p10:+,.2f}, "
                                          f"p50={values.p50:+,.2f}, p90={values.p90:+,.2f}", piece)

    def test_all_mapped_notices_and_normalized_profiles_are_displayed(self):
        _, output, _ = run_main(["--num-paths", "1"])
        for case in build_input_cases(num_paths=1):
            mapped, _ = map_case(case)
            section = case_section(output, case.name)
            for notice in mapped.notices:
                self.assertIn(f"- {notice.code} at {notice.field_name}: ", section)
            profile = mapped.profile
            self.assertIn(f"liquid_assets={profile.liquid_assets:,} KRW, monthly_savings={profile.monthly_savings:,}", section)
        retired = case_section(output, "saved_retired_no_pension")
        self.assertIn("confirmed_empty_sections (synthetic facts): pension_assets", retired)
        for text in ("Property value/sale, rent, deposits and secured loans are excluded",
                     "Debt principal, interest and repayments are excluded", "One-off income and spending goals are excluded"):
            self.assertIn(text, retired)
        self.assertIn("pension_incomes (inclusive ages:nominal KRW/month): 65..74:800,000", output)

    def test_details_print_all_age_summaries_and_comparison_deltas(self):
        for mode, expected_count in (("monte-carlo", 75), ("what-if", 627)):
            _, output, _ = run_main(["--mode", mode, "--num-paths", "2", "--details"])
            actual_rows = [line for line in output.splitlines() if line.split(" | ")[0].isdigit()]
            expected = []
            for case in build_input_cases(num_paths=2):
                mapped, config = map_case(case)
                result = (calculate_monte_carlo(mapped.profile, config) if mode == "monte-carlo"
                          else calculate_what_if(mapped.profile, config, case.scenarios))
                summaries = (result,) if mode == "monte-carlo" else (result.baseline,) + tuple(i.result for i in result.scenarios)
                for index, summary in enumerate(summaries):
                    expected.extend(f"{row.age} | {row.balance.p10:,.2f} | {row.balance.p50:,.2f} | "
                                    f"{row.balance.p90:,.2f} | {count.count}"
                                    for row, count in zip(summary.asset_percentiles, summary.first_depletion_counts))
                    if mode == "what-if" and index:
                        item = result.scenarios[index - 1]
                        expected.extend(f"{row.age} | {row.balance.p10:+,.2f} | {row.balance.p50:+,.2f} | "
                                        f"{row.balance.p90:+,.2f} | {count.count:+d}"
                                        for row, count in zip(item.asset_deltas, item.first_depletion_deltas))
            self.assertEqual(actual_rows, expected)
            self.assertEqual(len(actual_rows), expected_count)

    def test_selected_case_is_the_only_case_mapped_and_calculated(self):
        cases = build_input_cases(num_paths=1, seed=2**256 + 1)
        cases[1].saved_data["basic_info"] = None  # An unselected invalid fixture must not be used.
        with (patch(CLI + "build_input_cases", return_value=cases),
              patch(CLI + "map_retirement_profile", wraps=map_retirement_profile) as mapper):
            status, output, errors = run_main(["--case", "saved_working", "--num-paths", "1"])
        self.assertEqual((status, errors), (0, ""))
        mapper.assert_called_once_with(cases[0].saved_data, confirmed_empty_sections=())
        self.assertNotIn("[saved_retired_no_pension]", output)
        self.assertIn(f"configured_seed={2**256 + 1}", output)

    def test_zero_rate_hand_calculation_and_unused_rng_metadata(self):
        case = build_input_cases(num_paths=3, seed=7)[0]
        case.assumption_values.update(annual_return_mean=0, annual_return_volatility=0, annual_inflation_rate=0)
        with patch(CLI + "build_input_cases", return_value=(case,)):
            _, output, _ = run_main([])
        self.assertIn("effective_seed=None, path_source=generated", output)
        self.assertIn("return_model=constant, rng_algorithm=None", output)
        self.assertIn("p50=-36,000,000.00", summary_section(output))
        self.assertIn("first_depletion_counts (age:count, nonzero only): 85:3", summary_section(output))
        piece = scenario_section(output, "save_more")
        self.assertIn("p50=24,000,000.00", piece)
        self.assertIn("success_probability_delta_pp=+100.000pp", piece)

    def test_no_op_and_retired_savings_show_zero_effect(self):
        _, output, _ = run_main(["--num-paths", "3"])
        for case in build_input_cases(num_paths=3):
            section = case_section(output, case.name)
            names = ("unchanged", "savings_no_effect") if case.name == "saved_retired_no_pension" else ("unchanged",)
            for name in names:
                piece = scenario_section(section, name)
                self.assertIn("success_probability_delta_pp=+0.000pp", piece)
                self.assertIn("final_balance_delta (nominal KRW): p10=+0.00, p50=+0.00, p90=+0.00", piece)

    def test_output_explains_synthetic_scope_units_and_service_limits(self):
        _, output, _ = run_main(["--num-paths", "1"])
        for text in ("Not market forecasts", "not calibrated estimates or service defaults",
                     "must accompany analysis results", "no current_form merge", "annual decimal fractions",
                     "not the previous scenario", "common set of paths", "recovery does not erase depletion",
                     "Zero balance is not depleted", "Rounding affects display only", "not loans",
                     "not a single trajectory or a confidence interval", "differences of marginal quantiles",
                     "pp, not relative percent", "pensions are nominal", "changing them after retirement has no effect",
                     "not guarantees, recommendations or BE readiness scores", "not inferred from []",
                     "does not change BE write rules", "No original data is saved or patched", "deployment remain separate work"):
            self.assertIn(text, output)

    def test_repeated_output_preserves_saved_data_and_global_rng(self):
        cases = build_input_cases(num_paths=5, seed=0)
        original = deepcopy(cases)
        state = random.getstate()
        try:
            with patch(CLI + "build_input_cases", return_value=cases):
                first = run_main(["--details"])
                self.assertEqual(random.getstate(), state)
                random.random()
                after_draw = random.getstate()
                self.assertEqual(run_main(["--details"]), first)
                self.assertEqual(random.getstate(), after_draw)
            self.assertEqual(cases, original)
        finally:
            random.setstate(state)


class InputSampleCliTests(unittest.TestCase):
    def test_invalid_arguments_fail_before_building_or_mapping(self):
        invalid = (["--mode", "both"], ["--case", "unknown"], ["--num-paths", "0"],
                   ["--num-paths", "100001"], ["--num-paths", "1.5"], ["--seed", "-1"],
                   ["--seed", "x"], ["--num-paths"], ["--num", "1"], ["--input", "customer.json"],
                   ["--output", "result.json"], ["--annual-return-mean", "0.05"])
        with patch(CLI + "build_input_cases") as builder:
            for options in invalid:
                output, errors = StringIO(), StringIO()
                with redirect_stdout(output), redirect_stderr(errors), self.assertRaises(SystemExit) as caught:
                    main(options)
                self.assertEqual(caught.exception.code, 2)
                self.assertEqual(output.getvalue(), "")
                self.assertIn("error:", errors.getvalue())
        builder.assert_not_called()

    def test_help_does_not_build_or_map_data(self):
        output = StringIO()
        with (patch(CLI + "build_input_cases") as builder,
              redirect_stdout(output), self.assertRaises(SystemExit) as caught):
            main(["--help"])
        self.assertEqual(caught.exception.code, 0)
        self.assertIn("--mode {monte-carlo,what-if}", output.getvalue())
        builder.assert_not_called()

    def test_later_missing_pension_confirmation_discards_all_output(self):
        cases = list(build_input_cases(num_paths=1))
        cases[1] = replace(cases[1], confirmed_empty_sections=())
        original = deepcopy(cases)
        with (patch(CLI + "build_input_cases", return_value=tuple(cases)),
              patch(CLI + "calculate_what_if", wraps=calculate_what_if) as engine):
            status, output, errors = run_main([])
        self.assertEqual((status, output, engine.call_count), (1, "", 1))
        self.assertIn("empty_confirmation_required", errors)
        self.assertIn("No results were printed", errors)
        self.assertEqual(cases, original)

    def test_later_invalid_assumption_does_not_print_partial_success(self):
        cases = build_input_cases(num_paths=1)
        cases[1].assumption_values["annual_return_mean"] = "PRIVATE_BAD_RATE"
        with patch(CLI + "build_input_cases", return_value=cases):
            status, output, errors = run_main(["--mode", "monte-carlo"])
        self.assertEqual((status, output), (1, ""))
        self.assertIn("assumptions.annual_return_mean", errors)
        self.assertNotIn("PRIVATE_BAD_RATE", errors)
        self.assertNotIn("Traceback", errors)

    def test_later_numeric_failure_discards_all_output_in_each_mode(self):
        for mode, function, failure in (
            ("monte-carlo", calculate_monte_carlo, MonteCarloCalculationError("cashflow", "closing_balance")),
            ("what-if", calculate_what_if, WhatIfCalculationError("cashflow", "closing_balance")),
        ):
            calls = []

            def fail_second(*args):
                calls.append(args)
                if len(calls) == 2:
                    raise failure
                return function(*args)

            with patch(CLI + function.__name__, side_effect=fail_second):
                status, output, errors = run_main(["--mode", mode, "--num-paths", "1"])
            self.assertEqual((status, output, len(calls)), (1, "", 2))
            self.assertIn("closing_balance", errors)

    def test_invalid_virtual_change_is_not_saved_or_reported_as_success(self):
        case = build_input_cases(num_paths=1)[0]
        case = replace(case, scenarios=(WhatIfScenario(scenario_id="past", changes=WhatIfChanges(retirement_age=39)),))
        original = deepcopy(case)
        with patch(CLI + "build_input_cases", return_value=(case,)):
            status, output, errors = run_main([])
        self.assertEqual((status, output), (1, ""))
        self.assertIn("retirement_age", errors)
        self.assertEqual(case, original)

    def test_engine_cap_and_seed_reach_mapping_without_running_large_simulation(self):
        failure = WhatIfCalculationError("return_generation", "annual_returns", scope="market")
        with patch(CLI + "calculate_what_if", side_effect=failure) as engine:
            status, output, _ = run_main(["--num-paths", "100000", "--seed", "0"])
        self.assertEqual((status, output), (1, ""))
        self.assertEqual((engine.call_args.args[1].num_paths, engine.call_args.args[1].seed), (100000, 0))

    def test_module_runs_in_both_modes_without_site_packages(self):
        for mode in ("monte-carlo", "what-if"):
            args = ["--mode", mode, "--case", "saved_retired_no_pension", "--num-paths", "1", "--seed", "0"]
            result = subprocess.run([sys.executable, "-S", "-B", "-m", "retirement_simulation.input_samples", *args],
                                    cwd=AI_PROJECT_ROOT, capture_output=True, text=True, timeout=20, check=False)
            self.assertEqual((result.returncode, result.stderr), (0, ""))
            self.assertEqual(result.stdout, run_main(args)[1])


if __name__ == "__main__":
    unittest.main()
