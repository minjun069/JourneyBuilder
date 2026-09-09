from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import replace
from io import StringIO
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch


AI_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(AI_PROJECT_ROOT))

from retirement_simulation import RetirementExpensePeriod, calculate_cashflow  # noqa: E402
from retirement_simulation.samples import build_sample_cases, main  # noqa: E402


class SampleTests(unittest.TestCase):
    def test_three_distinct_synthetic_inputs_are_available(self) -> None:
        cases = build_sample_cases()
        self.assertEqual(
            [case.name for case in cases],
            [
                "working_with_phased_expenses",
                "already_retired_without_pension",
                "pension_start_gap",
            ],
        )
        self.assertEqual(len(cases[0].profile.retirement_expenses), 2)
        self.assertGreater(cases[1].profile.current_age, cases[1].profile.retirement_age)
        self.assertEqual(cases[1].profile.pension_incomes, ())
        pension = cases[2].profile.pension_incomes[0]
        self.assertGreater(pension.start_age, cases[2].profile.retirement_age)
        self.assertLess(pension.end_age, cases[2].profile.end_age)

    def test_sample_parameters_are_deterministic_test_settings(self) -> None:
        for case in build_sample_cases():
            with self.subTest(case=case.name):
                self.assertEqual(case.assumptions.annual_return_mean, 0.0)
                self.assertEqual(case.assumptions.annual_return_volatility, 0.0)
                self.assertEqual(case.assumptions.annual_inflation_rate, 0.0)
                self.assertEqual(case.assumptions.seed, 42)

    def test_builds_fresh_equal_inputs_without_shared_mutable_state(self) -> None:
        first = build_sample_cases()
        second = build_sample_cases()
        self.assertEqual(first, second)
        for left, right in zip(first, second):
            self.assertIsNot(left.profile, right.profile)
            self.assertIsNot(left.assumptions, right.assumptions)

    def test_runner_reports_fixed_results_with_scope_and_shortfall_notices(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            main([])
        text = output.getvalue()
        self.assertEqual(text.count("final_balance="), 3)
        self.assertIn("working_with_phased_expenses: years=51, final_balance=-154,400,000.00, "
                      "final_shortfall=154,400,000.00, first_depletion_age=73", text)
        self.assertIn("already_retired_without_pension: years=23, final_balance=-361,600,000.00, "
                      "final_shortfall=361,600,000.00, first_depletion_age=72", text)
        self.assertIn("pension_start_gap: years=32, final_balance=-480,000,000.00, "
                      "final_shortfall=480,000,000.00, first_depletion_age=62", text)
        self.assertIn("Calculated 3 synthetic cases.", text)
        self.assertIn("Not market forecasts.", text)
        self.assertIn("This CLI runs fixed cashflow only.", text)
        self.assertIn("Monte Carlo samples: python -m retirement_simulation.monte_carlo_samples.", text)
        self.assertIn("What-if samples: python -m retirement_simulation.what_if_samples.", text)
        self.assertIn("Service integration is not implemented.", text)
        self.assertNotIn("What-if and service integration are not implemented.", text)
        self.assertIn("unmet funding gaps, not loan balances", text)
        self.assertIn("Display rounding does not change depletion.", text)
        self.assertNotIn("probability", text)
        self.assertNotIn("age | opening", text)

    def test_sample_balances_match_independent_zero_rate_totals(self) -> None:
        # Counts come from inclusive schedules, not the engine's annual results.
        expected = (
            (100_000_000 + 20 * 6_000_000 + 26 * 12_000_000
             - 15 * 24_000_000 - 16 * 20_400_000, 73),
            (80_000_000 - 23 * 19_200_000, 72),
            (60_000_000 + 6_000_000 + 15 * 13_200_000 - 31 * 24_000_000, 62),
        )
        for case, (balance, age) in zip(build_sample_cases(), expected):
            with self.subTest(case=case.name):
                result = calculate_cashflow(case.profile, case.assumptions)
                self.assertEqual(result.final_balance, balance)
                self.assertEqual(result.final_shortfall, -balance)
                self.assertEqual(result.first_depletion_age, age)

    def test_runner_calls_real_engine_once_per_case(self) -> None:
        with patch("retirement_simulation.samples.calculate_cashflow", wraps=calculate_cashflow) as engine:
            with redirect_stdout(StringIO()):
                main([])
        self.assertEqual(engine.call_count, 3)
        for call, case in zip(engine.call_args_list, build_sample_cases()):
            self.assertEqual(call.args, (case.profile, case.assumptions))

    def test_detail_output_contains_every_year_and_is_repeatable(self) -> None:
        outputs = []
        for _ in range(2):
            output = StringIO()
            with redirect_stdout(output):
                main(["--details"])
            outputs.append(output.getvalue())
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(outputs[0].count("age | opening"), 3)
        rows = [line for line in outputs[0].splitlines()
                if " | " in line and line.split(" | ")[0].isdigit()]
        expected_years = [
            year for case in build_sample_cases()
            for year in calculate_cashflow(case.profile, case.assumptions).years
        ]
        self.assertEqual(len(rows), 51 + 23 + 32)
        for row, year in zip(rows, expected_years):
            columns = row.split(" | ")
            self.assertEqual(len(columns), 9)
            self.assertEqual(int(columns[0]), year.age)
            self.assertEqual(columns[1:], [
                f"{year.opening_balance:,.2f}", f"{year.annual_return_rate:.6f}",
                f"{year.investment_return:,.2f}", f"{year.savings:,.2f}",
                f"{year.pension_income:,.2f}", f"{year.expenses:,.2f}",
                f"{year.closing_balance:,.2f}", f"{year.shortfall:,.2f}",
            ])

    def test_display_rounding_does_not_hide_depletion_age(self) -> None:
        original = build_sample_cases()[0]
        value = replace(original.profile, current_age=60, retirement_age=60, end_age=60,
                        liquid_assets=12, monthly_savings=0, pension_incomes=(),
                        retirement_expenses=(RetirementExpensePeriod(
                            from_age=60, to_age=60, monthly_amount=1),))
        config = replace(original.assumptions, annual_return_mean=-1e-15)
        case = replace(original, profile=value, assumptions=config)
        output = StringIO()
        with patch("retirement_simulation.samples.build_sample_cases", return_value=(case,)):
            with redirect_stdout(output):
                main([])
        self.assertIn("final_balance=-0.00", output.getvalue())
        self.assertIn("first_depletion_age=60", output.getvalue())

    def test_cli_help_documents_details_option(self) -> None:
        result = subprocess.run(
            [sys.executable, "-S", "-B", "-m", "retirement_simulation.samples", "--help"],
            cwd=AI_PROJECT_ROOT, capture_output=True, text=True, timeout=10, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--details", result.stdout)
        self.assertIn("synthetic fixed-assumption", result.stdout)

    def test_package_and_samples_run_without_site_packages(self) -> None:
        result = subprocess.run(
            [sys.executable, "-S", "-B", "-m", "retirement_simulation.samples"],
            cwd=AI_PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Calculated 3 synthetic cases.", result.stdout)
        self.assertIn("first_depletion_age=73", result.stdout)


if __name__ == "__main__":
    unittest.main()
