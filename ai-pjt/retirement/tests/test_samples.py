from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import subprocess
import sys
import unittest


AI_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(AI_PROJECT_ROOT))

from retirement.samples import build_sample_cases, main  # noqa: E402


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

    def test_runner_does_not_claim_to_calculate_financial_results(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            main()
        text = output.getvalue()
        self.assertEqual(text.count("input validation OK"), 3)
        self.assertIn("Simulation engines are not implemented yet.", text)
        self.assertNotIn("100000000", text)
        self.assertNotIn("probability", text)

    def test_package_and_samples_run_without_site_packages(self) -> None:
        result = subprocess.run(
            [sys.executable, "-S", "-B", "-m", "retirement.samples"],
            cwd=AI_PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Validated 3 synthetic cases.", result.stdout)


if __name__ == "__main__":
    unittest.main()
