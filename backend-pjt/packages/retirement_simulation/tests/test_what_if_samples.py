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
    ModelValidationError, PensionIncome, RetirementExpensePeriod, RetirementProfile,
    SimulationAssumptions, WhatIfCalculationError, WhatIfChanges, WhatIfScenario,
    calculate_what_if, create_what_if_profile, generate_return_paths,
)
from retirement_simulation.samples import build_sample_cases  # noqa: E402
from retirement_simulation.what_if_samples import WhatIfSampleCase, build_what_if_cases, main  # noqa: E402


CLI = "retirement_simulation.what_if_samples."


def run_main(argv):
    output, errors = StringIO(), StringIO()
    with redirect_stdout(output), redirect_stderr(errors):
        status = main(argv)
    return status, output.getvalue(), errors.getvalue()


def expense(amount=100, start=60, end=61):
    return RetirementExpensePeriod(from_age=start, to_age=end, monthly_amount=amount)


def scenario(name="unchanged", **changes):
    return WhatIfScenario(scenario_id=name, changes=WhatIfChanges(**changes))


def short_case():
    return WhatIfSampleCase(
        "hand_checked", RetirementProfile(
            current_age=58, retirement_age=60, end_age=61, liquid_assets=1200, monthly_savings=100,
            retirement_expenses=(expense(),),
            pension_incomes=(PensionIncome(start_age=61, end_age=61, monthly_amount=50),),
        ),
        SimulationAssumptions(annual_return_mean=0, annual_return_volatility=0,
                              annual_inflation_rate=0, num_paths=3, seed=99),
        (scenario(), scenario("save", monthly_savings=200),
         scenario("early", retirement_age=59, retirement_expenses=(expense(start=59),))),
    )


def case_section(output, name):
    return output.split(f"[{name}]\n", 1)[1].split("\n[", 1)[0].split("\nCalculated", 1)[0]


def scenario_section(output, name):
    return output.split(f"scenario {name}:\n", 1)[1].split("\nscenario ", 1)[0].split("\nCalculated", 1)[0]


class WhatIfSampleInputTests(unittest.TestCase):
    def test_profiles_and_demo_assumptions_reuse_existing_inputs_without_mutation(self):
        original = build_sample_cases()
        cases = build_what_if_cases()
        self.assertEqual(tuple(case.name for case in cases), tuple(case.name for case in original))
        self.assertEqual(tuple(case.profile for case in cases), tuple(case.profile for case in original))
        self.assertEqual(build_sample_cases(), original)
        self.assertEqual(tuple(len(case.scenarios) for case in cases), (5, 3, 3))
        for case in cases:
            config = case.assumptions
            self.assertEqual((config.annual_return_mean, config.annual_return_volatility,
                              config.annual_inflation_rate, config.num_paths, config.seed),
                             (0.05, 0.10, 0.02, 1000, 42))
        self.assertTrue(all(case.assumptions.annual_return_mean == 0 for case in original))

    def test_fresh_frozen_cases_accept_boundaries_without_allocating_paths(self):
        first, second = build_what_if_cases(num_paths=1, seed=0), build_what_if_cases(num_paths=1, seed=0)
        self.assertEqual(first, second)
        for left, right in zip(first, second):
            self.assertIsNot(left.profile, right.profile)
            self.assertIsNot(left.assumptions, right.assumptions)
            self.assertIsNot(left.scenarios[0], right.scenarios[0])
            self.assertIsInstance(left.scenarios, tuple)
            with self.assertRaises(FrozenInstanceError):
                left.name = "modified"
            with self.assertRaises(FrozenInstanceError):
                left.scenarios[0].changes.monthly_savings = 123
        for case in build_what_if_cases(num_paths=100_000, seed=2**256 + 1):
            self.assertEqual((case.assumptions.num_paths, case.assumptions.seed), (100_000, 2**256 + 1))

    def test_explicit_overrides_cover_early_retirement_and_do_not_shift_pensions(self):
        cases = build_what_if_cases()
        snapshot = deepcopy(cases)
        working, retired, gap = cases
        self.assertEqual(tuple(item.scenario_id for item in working.scenarios),
                         ("unchanged", "save_more", "retire_earlier", "retire_later", "lower_expenses"))
        self.assertEqual(working.scenarios[1].changes.monthly_savings, 750_000)
        early = create_what_if_profile(working.profile, working.scenarios[2].changes)
        self.assertEqual(early.retirement_age, 58)
        self.assertEqual(early.retirement_expenses, (expense(2_000_000, 58, 74), expense(1_700_000, 75, 90)))
        late = create_what_if_profile(working.profile, working.scenarios[3].changes)
        self.assertEqual(late.retirement_age, 62)
        self.assertEqual(late.monthly_savings, working.profile.monthly_savings)  # Not accumulated from save_more.
        self.assertEqual(late.retirement_expenses, working.profile.retirement_expenses)
        self.assertEqual(working.scenarios[4].changes.retirement_expenses,
                         (expense(1_800_000, 60, 74), expense(1_500_000, 75, 90)))
        self.assertTrue(all(item.changes.retirement_age is None for item in retired.scenarios))
        self.assertEqual(retired.scenarios[1].changes.retirement_expenses, (expense(1_200_000, 65, 90),))
        self.assertEqual(retired.scenarios[2].changes.monthly_savings, 500_000)
        self.assertEqual(gap.scenarios[1].changes.monthly_savings, 0)
        self.assertEqual(gap.scenarios[2].changes.retirement_age, 65)
        for case in cases:
            for item in case.scenarios:
                changed = create_what_if_profile(case.profile, item.changes)
                self.assertEqual((changed.current_age, changed.end_age, changed.liquid_assets, changed.pension_incomes),
                                 (case.profile.current_age, case.profile.end_age, case.profile.liquid_assets, case.profile.pension_incomes))
        self.assertEqual(cases, snapshot)

    def test_programmatic_builder_preserves_model_validation(self):
        for options in ({"num_paths": 0}, {"num_paths": 100_001}, {"num_paths": True}, {"num_paths": "3"},
                        {"seed": -1}, {"seed": True}, {"seed": 1.5}):
            with self.subTest(options=options), self.assertRaises(ModelValidationError):
                build_what_if_cases(**options)

    def test_building_cases_does_not_calculate_or_use_randomness(self):
        state = random.getstate()
        with patch(CLI + "calculate_what_if") as engine, patch("retirement_simulation.return_paths.Random") as rng:
            build_what_if_cases()
        engine.assert_not_called()
        rng.assert_not_called()
        self.assertEqual(random.getstate(), state)


class WhatIfSampleOutputTests(unittest.TestCase):
    def test_default_cli_calls_real_comparison_and_one_generator_per_case(self):
        with (
            patch(CLI + "calculate_what_if", wraps=calculate_what_if) as engine,
            patch("retirement_simulation.what_if_engine.generate_return_paths", wraps=generate_return_paths) as generate,
        ):
            status, output, errors = run_main([])
        self.assertEqual((status, errors), (0, ""))
        self.assertEqual(engine.call_count, 3)
        self.assertEqual(generate.call_count, 3)
        for call, case in zip(engine.call_args_list, build_what_if_cases()):
            self.assertEqual(call.args, (case.profile, case.assumptions, case.scenarios))
        self.assertIn("Calculated 3 synthetic What-if cases; 11 scenario comparisons.", output)
        self.assertNotIn("age | balance_p10", output)

    def test_baseline_and_effective_profiles_are_printed_with_units_and_complete_schedules(self):
        _, output, _ = run_main(["--num-paths", "1"])
        for case in build_what_if_cases(num_paths=1):
            section = case_section(output, case.name)
            profiles = (case.profile,) + tuple(create_what_if_profile(case.profile, item.changes) for item in case.scenarios)
            pieces = (section.split("baseline:\n", 1)[1].split("\nscenario ", 1)[0],) + tuple(
                scenario_section(section, item.scenario_id) for item in case.scenarios
            )
            for value, piece in zip(profiles, pieces):
                self.assertIn(f"current_age={value.current_age}, retirement_age={value.retirement_age}, "
                              f"end_age={value.end_age}, liquid_assets={value.liquid_assets:,} KRW, "
                              f"monthly_savings={value.monthly_savings:,} nominal KRW/month", piece)
                expenses = ", ".join(f"{row.from_age}..{row.to_age}:{row.monthly_amount:,}" for row in value.retirement_expenses)
                self.assertIn(f"retirement_expenses (inclusive ages:current-age KRW/month): {expenses}\n", piece)
                pensions = ", ".join(f"{row.start_age}..{row.end_age}:{row.monthly_amount:,}" for row in value.pension_incomes) or "none"
                self.assertIn(f"pension_incomes (inclusive ages:nominal KRW/month): {pensions}\n", piece)
        self.assertIn("overrides (absolute; omitted fields unchanged): monthly_savings=0 nominal KRW/month", output)
        self.assertIn("current-age KRW/month (full replacement)", output)

    def test_every_summary_delta_and_provenance_matches_actual_calculation(self):
        _, output, _ = run_main(["--num-paths", "11", "--seed", "0"])
        for case in build_what_if_cases(num_paths=11, seed=0):
            result = calculate_what_if(case.profile, case.assumptions, case.scenarios)
            section = case_section(output, case.name)
            self.assertIn("num_paths=11, configured_seed=0, effective_seed=0, path_source=generated", section)
            self.assertIn("what_if_model_version=retirement-what-if-v1, mc_model_version=retirement-mc-v1", section)
            self.assertIn("return_model=lognormal_gross_return, rng_algorithm=python.random.Random.gauss", section)
            self.assertIn(f"python={result.metadata.python_implementation} {result.metadata.python_version}", section)
            pieces = (section.split("baseline:\n", 1)[1].split("\nscenario ", 1)[0],) + tuple(
                scenario_section(section, item.scenario_id) for item in result.scenarios
            )
            for summary, piece in zip((result.baseline,) + tuple(item.result for item in result.scenarios), pieces):
                self.assertIn("result_source: path_source=provided, effective_seed=None, rng_algorithm=None", piece)
                for name in ("success", "depletion"):
                    probability, count = getattr(summary, name + "_probability"), getattr(summary, name + "_count")
                    self.assertIn(f"{name}_probability={probability:.6f} ({probability:.3%}), {name}_count={count}/11", piece)
                self.assertIn(f"never_depleted_count={summary.never_depleted_count}", piece)
                for label, values in (("final_balance", summary.final_balance_percentiles),
                                      ("final_shortfall", summary.final_shortfall_percentiles)):
                    self.assertIn(f"{label} (nominal KRW): p10={values.p10:,.2f}, p50={values.p50:,.2f}, p90={values.p90:,.2f}", piece)
                histogram = ", ".join(f"{row.age}:{row.count}" for row in summary.first_depletion_counts if row.count) or "none"
                self.assertIn(f"first_depletion_counts (age:count, nonzero only): {histogram}", piece.splitlines())
            for item, piece in zip(result.scenarios, pieces[1:]):
                for name in ("success_probability_delta_pp", "depletion_probability_delta_pp"):
                    self.assertIn(f"{name}={getattr(item, name):+.3f}pp", piece)
                for name in ("success_count_delta", "depletion_count_delta", "never_depleted_count_delta"):
                    self.assertIn(f"{name}={getattr(item, name):+d}", piece)
                for name in ("final_balance_delta", "final_shortfall_delta"):
                    values = getattr(item, name)
                    self.assertIn(f"{name} (nominal KRW): p10={values.p10:+,.2f}, p50={values.p50:+,.2f}, p90={values.p90:+,.2f}", piece)
                histogram = ", ".join(f"{row.age}:{row.count:+d}" for row in item.first_depletion_deltas if row.count) or "none"
                self.assertIn(f"first_depletion_deltas (age:signed count, nonzero only): {histogram}", piece.splitlines())

    def test_details_include_all_baseline_scenario_and_delta_rows_in_order(self):
        status, output, errors = run_main(["--details", "--num-paths", "7"])
        self.assertEqual((status, errors), (0, ""))
        rows = [line for line in output.splitlines() if line.split(" | ")[0].isdigit()]
        expected = []
        for case in build_what_if_cases(num_paths=7):
            result = calculate_what_if(case.profile, case.assumptions, case.scenarios)
            for index, summary in enumerate((result.baseline,) + tuple(item.result for item in result.scenarios)):
                expected.extend(f"{row.age} | {row.balance.p10:,.2f} | {row.balance.p50:,.2f} | "
                                f"{row.balance.p90:,.2f} | {count.count}"
                                for row, count in zip(summary.asset_percentiles, summary.first_depletion_counts))
                if index:
                    item = result.scenarios[index - 1]
                    expected.extend(f"{row.age} | {row.balance.p10:+,.2f} | {row.balance.p50:+,.2f} | "
                                    f"{row.balance.p90:+,.2f} | {count.count:+d}"
                                    for row, count in zip(item.asset_deltas, item.first_depletion_deltas))
        self.assertEqual(rows, expected)
        self.assertEqual(len(rows), 946)  # 526 summaries + 420 deltas, not path ledgers.
        self.assertEqual(output.count("age | balance_p10"), 14)
        self.assertEqual(output.count("age | balance_delta_p10"), 11)
        self.assertTrue(any(row.endswith(" | 0") for row in rows))
        self.assertTrue(any(row.endswith(" | +0") for row in rows))

    def test_output_explains_scope_units_assumptions_and_non_guarantees(self):
        _, output, _ = run_main(["--num-paths", "1"])
        for notice in (
            "Not market forecasts", "not calibrated market estimates or investment recommendations",
            "annual decimal fractions", "annual_return_mean=0.05", "annual_return_volatility=0.1",
            "annual_inflation_rate=0.02", "All deltas are changed minus baseline",
            "not the previous scenario", "common paths once", "children report provided paths",
            "not aligned markets despite using the same seed", "no negative year-end balance over the entire horizon",
            "recovery does not erase depletion", "unmet funding gaps, not loans",
            "Display rounding does not change depletion or counts", "include all paths at each age",
            "not a single trajectory or a confidence interval", "not quantiles of paired path differences",
            "they need not be sorted", "percentage points (pp), not relative percent changes",
            "Negative shortfall deltas mean smaller gaps", "inflation stays anchored to current age",
            "Pensions do not shift", "changing them after retirement has no effect",
            "No recommendation or ranking", "0%/100% are not guarantees or BE readiness scores",
            "same inputs, code, Python version and execution environment",
            "Real-data adapters, Agent routing and service integration are not implemented here",
        ):
            with self.subTest(notice=notice):
                self.assertIn(notice, output)

    def test_case_selection_and_large_seed_calculate_only_selected_case(self):
        for case in build_what_if_cases(num_paths=1, seed=2**256 + 1):
            with self.subTest(case=case.name), patch(CLI + "calculate_what_if", wraps=calculate_what_if) as engine:
                status, output, errors = run_main(["--case", case.name, "--num-paths", "1", "--seed", str(2**256 + 1)])
            self.assertEqual((status, errors), (0, ""))
            engine.assert_called_once_with(case.profile, case.assumptions, case.scenarios)
            self.assertEqual(output.count("\nbaseline:\n"), 1)
            self.assertEqual(output.count("\nscenario "), len(case.scenarios))
            self.assertIn(f"Calculated 1 synthetic What-if case; {len(case.scenarios)} scenario comparisons.", output)

    def test_repeated_output_preserves_inputs_and_ignores_global_random_activity(self):
        cases = build_what_if_cases(num_paths=13, seed=17)
        snapshot = deepcopy(cases)
        original = random.getstate()
        try:
            with patch(CLI + "build_what_if_cases", return_value=cases):
                expected = run_main(["--details"])
                self.assertEqual(random.getstate(), original)
                for _ in range(19):
                    random.random()
                after_draws = random.getstate()
                self.assertEqual(run_main(["--details"]), expected)
                self.assertEqual(random.getstate(), after_draws)
            self.assertEqual(cases, snapshot)
        finally:
            random.setstate(original)

    def test_no_op_and_retired_savings_are_displayed_as_zero_effect_not_recommendations(self):
        _, output, _ = run_main(["--num-paths", "3"])
        for case in build_what_if_cases(num_paths=3):
            section = case_section(output, case.name)
            names = ("unchanged", "savings_no_effect") if case.name == "already_retired_without_pension" else ("unchanged",)
            for name in names:
                piece = scenario_section(section, name)
                self.assertIn("success_probability_delta_pp=+0.000pp", piece)
                self.assertIn("final_balance_delta (nominal KRW): p10=+0.00, p50=+0.00, p90=+0.00", piece)
                self.assertIn("final_shortfall_delta (nominal KRW): p10=+0.00, p50=+0.00, p90=+0.00", piece)
                self.assertIn("first_depletion_deltas (age:signed count, nonzero only): none", piece)

    def test_hand_checked_fixed_cases_show_signed_changes_and_unused_seed(self):
        with patch(CLI + "build_what_if_cases", return_value=(short_case(),)):
            status, output, errors = run_main(["--details"])
        self.assertEqual((status, errors), (0, ""))
        self.assertIn("configured_seed=99, effective_seed=None", output)
        self.assertIn("return_model=constant, rng_algorithm=None", output)
        save, early = scenario_section(output, "save"), scenario_section(output, "early")
        self.assertIn("final_balance_delta (nominal KRW): p10=+2,400.00, p50=+2,400.00, p90=+2,400.00", save)
        self.assertIn("success_probability_delta_pp=-100.000pp", early)
        self.assertIn("final_shortfall_delta (nominal KRW): p10=+600.00, p50=+600.00, p90=+600.00", early)
        self.assertIn("61 | -2,400.00 | -2,400.00 | -2,400.00 | +3", early)

    def test_controlled_common_paths_show_pp_gain_despite_recovery_and_zero_shortfall(self):
        case = short_case()
        case = replace(case, profile=replace(case.profile, current_age=60, liquid_assets=120, monthly_savings=0,
                       retirement_expenses=(expense(10),),
                       pension_incomes=(PensionIncome(start_age=61, end_age=61, monthly_amount=20),)),
                       assumptions=replace(case.assumptions, num_paths=4, annual_return_volatility=0.1),
                       scenarios=(scenario("lower", retirement_expenses=(expense(5),)),))
        paths = ((-0.5, 0), (0, 0), (0.5, 0), (1, 0))
        # Controlled generator fixture tests rendering of real engine results, not a market sample.
        with (
            patch(CLI + "build_what_if_cases", return_value=(case,)),
            patch("retirement_simulation.what_if_engine.generate_return_paths", return_value=paths),
        ):
            _, output, _ = run_main(["--details"])
        self.assertIn("success_probability=0.750000 (75.000%), success_count=3/4", output)
        self.assertIn("success_probability_delta_pp=+25.000pp", output)
        self.assertIn("final_balance_delta (nominal KRW): p10=+120.00, p50=+120.00, p90=+120.00", output)
        self.assertIn("final_shortfall_delta (nominal KRW): p10=+0.00, p50=+0.00, p90=+0.00", output)
        self.assertIn("60 | +60.00 | +60.00 | +60.00 | -1", output)

    def test_rounded_negative_zero_retains_actual_failure_count(self):
        case = short_case()
        case = replace(case, profile=replace(case.profile, current_age=60, end_age=60, liquid_assets=12,
                       retirement_expenses=(expense(1, 60, 60),), pension_incomes=()),
                       assumptions=replace(case.assumptions, annual_return_mean=-1e-15), scenarios=(scenario(),))
        with patch(CLI + "build_what_if_cases", return_value=(case,)):
            _, output, _ = run_main(["--details"])
        self.assertIn("final_balance (nominal KRW): p10=-0.00, p50=-0.00, p90=-0.00", output)
        self.assertIn("depletion_count=3/3", output)
        self.assertIn("60 | -0.00 | -0.00 | -0.00 | 3", output)

    def test_descending_marginal_deltas_are_not_sorted_by_display(self):
        case = short_case()
        case = replace(case, profile=replace(case.profile, current_age=59, end_age=60, liquid_assets=120,
                       monthly_savings=10, retirement_expenses=(expense(10, 60, 60),), pension_incomes=()),
                       scenarios=(scenario("stop", monthly_savings=0),))
        paths = ((0, -0.5), (0, 0), (0, 0.5))
        with (
            patch(CLI + "build_what_if_cases", return_value=(case,)),
            patch("retirement_simulation.what_if_engine.generate_return_paths", return_value=paths),
        ):
            _, output, _ = run_main(["--details"])
        self.assertIn("final_balance_delta (nominal KRW): p10=-72.00, p50=-120.00, p90=-168.00", output)
        self.assertIn("60 | -72.00 | -120.00 | -168.00 | +1", output)


class WhatIfSampleCliTests(unittest.TestCase):
    def test_invalid_options_fail_before_calculation_without_results(self):
        invalid = (["--num-paths", "0"], ["--num-paths", "-1"], ["--num-paths", "100001"],
                   ["--num-paths", "1.5"], ["--num-paths", "NaN"], ["--seed", "-1"], ["--seed", "1.5"],
                   ["--seed", "invalid"], ["--case", "unknown"], ["--num-paths"], ["--unknown"],
                   ["--num", "3"], ["--monthly-savings", "100"], ["--input", "customer.json"])
        with patch(CLI + "calculate_what_if") as engine:
            for options in invalid:
                output, errors = StringIO(), StringIO()
                with (
                    self.subTest(options=options), redirect_stdout(output), redirect_stderr(errors),
                    self.assertRaises(SystemExit) as caught,
                ):
                    main(options)
                self.assertEqual(caught.exception.code, 2)
                self.assertEqual(output.getvalue(), "")
                self.assertIn("error:", errors.getvalue())
        engine.assert_not_called()

    def test_maximum_count_reaches_engine_without_executing_maximum_demo(self):
        failure = WhatIfCalculationError("return_generation", "annual_returns", scope="market")
        with patch(CLI + "calculate_what_if", side_effect=failure) as engine:
            status, output, errors = run_main(["--num-paths", "100000", "--seed", "0"])
        self.assertEqual((status, output), (1, ""))
        self.assertEqual(engine.call_count, 1)
        self.assertEqual((engine.call_args.args[1].num_paths, engine.call_args.args[1].seed), (100_000, 0))
        self.assertIn("market.return_generation.annual_returns", errors)

    def test_later_case_error_discards_all_selected_case_output_and_stops(self):
        calls = 0

        def fail_second(profile, config, scenarios):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise WhatIfCalculationError("cashflow", "closing_balance", scope="scenario",
                                             scenario_index=1, path_index=3, age=70)
            return calculate_what_if(profile, config, scenarios)

        with patch(CLI + "calculate_what_if", side_effect=fail_second):
            status, output, errors = run_main(["--num-paths", "5", "--details"])
        self.assertEqual((status, output, calls), (1, "", 2))
        self.assertIn("scenario.cashflow.closing_balance: calculation failed at scenario 1 at path 3 at age 70", errors)
        self.assertIn("No results were printed", errors)
        self.assertNotIn("Traceback", errors)
        self.assertNotIn("100000000", errors)

    def test_actual_scenario_numeric_failure_returns_one_without_input_values(self):
        case = short_case()
        huge = 10**400 + 987654321
        case = replace(case, scenarios=(scenario(), scenario("invalid", retirement_expenses=(expense(huge),))))
        with patch(CLI + "build_what_if_cases", return_value=(case,)):
            status, output, errors = run_main([])
        self.assertEqual((status, output), (1, ""))
        self.assertIn("scenario.cashflow.annual_expenses: calculation failed at scenario 1 at path 0 at age 60", errors)
        self.assertNotIn(str(huge), errors)
        self.assertNotIn("Traceback", errors)

    def test_invalid_model_and_scenario_schedule_return_safe_errors_without_results(self):
        with patch(CLI + "build_what_if_cases", side_effect=ModelValidationError("num_paths", "is outside the supported range")):
            with patch(CLI + "calculate_what_if") as engine:
                status, output, errors = run_main([])
        self.assertEqual((status, output), (1, ""))
        self.assertIn("num_paths: is outside the supported range", errors)
        engine.assert_not_called()
        case = replace(short_case(), scenarios=(scenario("invalid", retirement_age=59),))
        with (
            patch(CLI + "build_what_if_cases", return_value=(case,)),
            patch("retirement_simulation.what_if_engine.generate_return_paths") as generate,
        ):
            status, output, errors = run_main([])
        self.assertEqual((status, output), (1, ""))
        self.assertIn("scenarios[0].changes.retirement_expenses", errors)
        generate.assert_not_called()

    def test_help_describes_options_and_limits_without_calculation(self):
        output = StringIO()
        with patch(CLI + "calculate_what_if") as engine, redirect_stdout(output), self.assertRaises(SystemExit) as caught:
            main(["--help"])
        self.assertEqual(caught.exception.code, 0)
        engine.assert_not_called()
        for text in ("--case", "--num-paths", "--seed", "--details", "No customer files", "not a memory or response-time guarantee"):
            self.assertIn(text, output.getvalue())

    def test_module_default_details_help_and_invalid_options_work_without_site_packages(self):
        runs = (([], 0), (["--case", "pension_start_gap", "--num-paths", "1", "--seed", "0", "--details"], 0),
                (["--help"], 0), (["--num-paths", "0"], 2))
        for options, status in runs:
            with self.subTest(options=options):
                completed = subprocess.run([sys.executable, "-S", "-B", "-m", "retirement_simulation.what_if_samples", *options],
                                           cwd=AI_PROJECT_ROOT, capture_output=True, text=True, timeout=60, check=False)
                self.assertEqual(completed.returncode, status, completed.stderr)
                if not options:
                    self.assertIn("Calculated 3 synthetic What-if cases; 11 scenario comparisons.", completed.stdout)
                    self.assertEqual(completed.stdout.count("num_paths=1000"), 3)
                elif "--details" in options:
                    self.assertIn("age | balance_delta_p10", completed.stdout)
                    self.assertIn("Calculated 1 synthetic What-if case; 3 scenario comparisons.", completed.stdout)
                elif status == 2:
                    self.assertEqual(completed.stdout, "")
                    self.assertIn("--num-paths", completed.stderr)
                else:
                    self.assertIn("--details", completed.stdout)
                if status == 0:
                    self.assertEqual(completed.stderr, "")


if __name__ == "__main__":
    unittest.main()
