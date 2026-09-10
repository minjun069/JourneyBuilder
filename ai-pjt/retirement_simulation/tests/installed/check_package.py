"""Copied outside the repository and executed only against a fresh installed wheel.

No engine test helpers, repository fixture files, or sys.path edits are used.
This directory deliberately has no __init__.py: source unittest discovery is separate.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from importlib import import_module, metadata, util
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import sysconfig
import unittest


def hand_check(*, working=False):
    return {
        "basic_info": {"age": 59 if working else 60, "retirement_age": 60, "end_age": 61 if working else 60},
        "financial_assets": [{"asset_type": "예금/적금", "current_value": 10_000,
                              "monthly_savings": 1_000 if working else 0}],
        "pension_assets": [],
        "goal_periods": [{"phase": "after", "from_age": 60, "to_age": 61 if working else 60,
                          "items": [{"amount": 1_000}]}],
        "real_estate_assets": [], "liabilities": [], "goal_milestones": [],
    }


def assumptions():
    # Hand-calculation controls, not forecasts or service defaults.
    return {"annual_return_mean": 0, "annual_return_volatility": 0, "annual_inflation_rate": 0,
            "num_paths": 2, "seed": 7}


def lower_expenses(*, end_age=60):
    return {"scenario_id": "lower_expenses", "changes": {"retirement_expenses": [
        {"from_age": 60, "to_age": end_age, "monthly_amount": 800},
    ]}}


def strict_json(text):
    def reject(value):
        raise ValueError(f"nonfinite JSON token: {value}")
    return json.loads(text, parse_constant=reject)


class InstalledPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.simulation = import_module("retirement_simulation")
        cls.distribution = metadata.distribution("retirewise-retirement-simulation")
        cls.package_root = Path(cls.simulation.__file__).resolve().parent
        cls.purelib = Path(sysconfig.get_path("purelib")).resolve()

    def analyze(self, *, working=False, scenarios=()):
        return self.simulation.analysis_to_dict(self.simulation.analyze_retirement(
            hand_check(working=working), assumptions(), scenarios=scenarios,
            confirmed_empty_sections=("pension_assets",),
        ))

    def cli(self, module="analysis_samples", *args):
        return subprocess.run([sys.executable, "-I", "-B", "-m", f"retirement_simulation.{module}", *args],
                              capture_output=True, text=True, encoding="utf-8", timeout=15, check=False)

    def test_isolated_venv_and_import_origin(self):
        self.assertEqual(sys.flags.isolated, 1)
        self.assertEqual(sys.flags.no_site, 0)
        self.assertTrue(sys.flags.dont_write_bytecode)
        self.assertEqual(Path(sys.prefix).resolve(), EXPECTED_PREFIX)
        self.assertNotEqual(sys.prefix, sys.base_prefix)
        self.assertFalse(Path.cwd().resolve().is_relative_to(SOURCE_ROOT))
        self.assertTrue(self.purelib.is_relative_to(EXPECTED_PREFIX))
        self.assertEqual(self.package_root, self.purelib / "retirement_simulation")
        self.assertEqual(Path(self.distribution.locate_file("retirement_simulation/__init__.py")).resolve(),
                         self.package_root / "__init__.py")
        for entry in sys.path:
            self.assertFalse(Path(entry).resolve().is_relative_to(SOURCE_ROOT), entry)
            self.assertNotEqual(Path(entry).resolve(), Path.cwd().resolve())
        self.assertIn(str(SOURCE_ROOT), os.environ.get("PYTHONPATH", ""))
        self.assertIn("include-system-site-packages = false", (EXPECTED_PREFIX / "pyvenv.cfg").read_text())

    def test_all_eighteen_modules_and_public_exports_resolve_from_the_wheel(self):
        names = ("analysis", "analysis_benchmark", "analysis_errors", "analysis_output", "analysis_samples",
                 "cashflow", "input_adapter", "input_samples", "models", "monte_carlo", "monte_carlo_samples",
                 "return_paths", "samples", "what_if", "what_if_adapter", "what_if_engine", "what_if_samples")
        for name in names:
            with self.subTest(module=name):
                module = import_module("retirement_simulation." + name)
                self.assertEqual(Path(module.__file__).resolve(), self.package_root / (name + ".py"))
        self.assertEqual({p.name for p in self.package_root.glob("*.py")},
                         {name + ".py" for name in names} | {"__init__.py"})
        self.assertEqual(len(self.simulation.__all__), 44)
        for name in self.simulation.__all__:
            value = getattr(self.simulation, name)
            if name == "MAX_WHAT_IF_SCENARIOS":
                self.assertEqual(value, 10)
            else:
                self.assertTrue(value.__module__.startswith("retirement_simulation."), name)

    def test_metadata_and_no_application_or_build_dependencies(self):
        self.assertEqual(self.distribution.version, "0.1.0")
        self.assertEqual(self.distribution.metadata["Requires-Python"], "<3.14,>=3.13")
        self.assertFalse(self.distribution.requires)
        self.assertFalse(self.distribution.entry_points)
        self.assertEqual({d.metadata["Name"].lower() for d in metadata.distributions()},
                         {"pip", "retirewise-retirement-simulation"})
        direct_url = json.loads(self.distribution.read_text("direct_url.json"))
        self.assertNotIn("dir_info", direct_url)  # Normal wheel install, never editable.
        self.assertEqual(direct_url["archive_info"]["hashes"]["sha256"], EXPECTED_SHA256)
        for name in ("django", "fastapi", "langgraph", "numpy", "llama_index", "build", "setuptools"):
            self.assertIsNone(util.find_spec(name), name)
        for name in ("tests", "docs", "pyproject.toml", "MANIFEST.in", "__pycache__"):
            self.assertFalse((self.package_root / name).exists(), name)
        self.assertIsNone(util.find_spec("retirement_simulation.tests"))

    def test_baseline_hand_calculation_and_complete_json(self):
        payload = self.analyze()
        self.assertEqual(payload["mode"], "baseline")
        self.assertEqual(payload["scenarios"], [])
        self.assertEqual(payload["baseline"]["success_probability"], 0)
        self.assertEqual(payload["baseline"]["final_balance_percentiles"], dict.fromkeys(("p10", "p50", "p90"), -2000))
        self.assertEqual(payload["baseline"]["final_shortfall_percentiles"], dict.fromkeys(("p10", "p50", "p90"), 2000))
        self.assertEqual(payload["contract_version"], "retirement-analysis-v1")
        self.assertEqual(payload["mapping_version"], "retirement-input-v1")
        self.assertEqual(payload["metadata"]["assumptions"], assumptions())
        self.assertEqual(payload["metadata"]["num_paths"], 2)
        self.assertIsNone(payload["metadata"]["effective_seed"])
        self.assertIsNone(payload["metadata"]["rng_algorithm"])
        self.assertEqual(payload["metadata"]["return_model"], "constant")
        self.assertEqual(payload["metadata"]["mc_model_version"], "retirement-mc-v1")
        self.assertIsNone(payload["metadata"]["what_if_model_version"])
        self.assertEqual(set(payload), {"contract_version", "mode", "mapping_version", "units", "notices",
                                        "baseline_profile", "metadata", "baseline", "scenarios"})
        self.assertEqual(strict_json(json.dumps(payload, ensure_ascii=False, allow_nan=False)), payload)
        self.assertEqual(payload["units"]["probability_delta"], "pp")
        self.assertIn("confirmed_empty_pensions", {notice["code"] for notice in payload["notices"]})

    def test_what_if_hand_calculation_and_percentage_point_delta(self):
        payload = self.analyze(scenarios=[lower_expenses()])
        changed = payload["scenarios"][0]
        self.assertEqual(payload["mode"], "what_if")
        self.assertEqual(changed["result"]["success_probability"], 1)
        self.assertEqual(changed["result"]["final_balance_percentiles"], dict.fromkeys(("p10", "p50", "p90"), 400))
        self.assertEqual(changed["delta"]["success_probability_delta_pp"], 100)
        self.assertEqual(payload["baseline"]["execution"]["path_source"], "provided")
        self.assertEqual(changed["result"]["execution"]["path_source"], "provided")
        self.assertIsNone(payload["baseline"]["execution"]["effective_seed"])
        self.assertIsNone(changed["result"]["execution"]["effective_seed"])
        self.assertIsNone(payload["metadata"]["effective_seed"])
        self.assertEqual(payload["metadata"]["what_if_model_version"], "retirement-what-if-v1")
        self.assertEqual(strict_json(json.dumps(payload, allow_nan=False)), payload)

    def test_three_change_types_are_independent_of_each_other(self):
        scenarios = [
            {"scenario_id": "save_more", "changes": {"monthly_savings": 1200}},
            {"scenario_id": "retire_earlier", "changes": {"retirement_age": 59, "retirement_expenses": [
                {"from_age": 59, "to_age": 61, "monthly_amount": 1000},
            ]}},
            lower_expenses(end_age=61),
        ]
        payload = self.analyze(working=True, scenarios=scenarios)
        self.assertEqual(payload["baseline"]["final_balance_percentiles"]["p50"], -2000)
        self.assertEqual([row["result"]["final_balance_percentiles"]["p50"] for row in payload["scenarios"]],
                         [400, -26000, 2800])
        self.assertEqual([row["profile"]["monthly_savings"] for row in payload["scenarios"]], [1200, 1000, 1000])
        self.assertEqual([row["profile"]["retirement_age"] for row in payload["scenarios"]], [60, 59, 60])

    def test_inputs_rng_and_seeded_results_are_preserved(self):
        saved, config, scenarios = hand_check(working=True), assumptions(), [lower_expenses(end_age=61)]
        config.update(annual_return_mean=0.05, annual_return_volatility=0.1, annual_inflation_rate=0.02)
        original, state = deepcopy((saved, config, scenarios)), random.getstate()
        first = self.simulation.analyze_retirement(saved, config, scenarios=scenarios, confirmed_empty_sections=("pension_assets",))
        second = self.simulation.analyze_retirement(saved, config, scenarios=scenarios, confirmed_empty_sections=("pension_assets",))
        payload = self.simulation.analysis_to_dict(first)
        self.assertEqual(payload, self.simulation.analysis_to_dict(second))
        self.assertEqual(payload["metadata"]["assumptions"]["seed"], 7)
        self.assertEqual(payload["metadata"]["effective_seed"], 7)
        self.assertIsNotNone(payload["metadata"]["rng_algorithm"])
        self.assertEqual((saved, config, scenarios), original)
        self.assertEqual(random.getstate(), state)

    def test_public_input_error_remains_an_error_json_not_zero_success(self):
        with self.assertRaises(self.simulation.RetirementAnalysisError) as caught:
            self.simulation.analyze_retirement(hand_check(), assumptions())
        payload = self.simulation.analysis_error_to_dict(caught.exception)
        self.assertEqual(set(payload), {"contract_version", "error"})
        self.assertEqual(payload["error"]["code"], "input_mapping_failed")
        self.assertEqual(payload["error"]["reason_code"], "empty_confirmation_required")
        self.assertEqual(payload["error"]["field_name"], "pension_assets")
        self.assertEqual(strict_json(json.dumps(payload, allow_nan=False)), payload)

    def test_public_output_error_preserves_json_safety(self):
        config = assumptions()
        config["seed"] = 2**53
        result = self.simulation.analyze_retirement(hand_check(), config, confirmed_empty_sections=("pension_assets",))
        with self.assertRaises(self.simulation.RetirementAnalysisError) as caught:
            self.simulation.analysis_to_dict(result)
        payload = self.simulation.analysis_error_to_dict(caught.exception)
        self.assertEqual(payload["error"]["code"], "unsafe_json_number")
        self.assertNotIn(str(2**53), json.dumps(payload, allow_nan=False))

    def test_analysis_cli_full_output_equals_public_api_in_both_modes(self):
        for mode in ("monte-carlo", "what-if"):
            with self.subTest(mode=mode):
                result = self.cli("analysis_samples", "--case", "hand_check", "--mode", mode,
                                  "--num-paths", "2", "--seed", "7")
                self.assertEqual(result.returncode, 0, result.stderr)
                expected = self.analyze(scenarios=[lower_expenses()] if mode == "what-if" else ())
                self.assertEqual(strict_json(result.stdout), expected)
                self.assertEqual(result.stdout.count("\n"), 1)
                self.assertIn("Synthetic demo only", result.stderr)

    def test_analysis_cli_pretty_output_changes_only_formatting(self):
        args = ("--case", "hand_check", "--num-paths", "2", "--seed", "7")
        compact, pretty = self.cli("analysis_samples", *args), self.cli("analysis_samples", *args, "--pretty")
        self.assertEqual((compact.returncode, pretty.returncode), (0, 0))
        self.assertEqual(strict_json(compact.stdout), strict_json(pretty.stdout))
        self.assertGreater(pretty.stdout.count("\n"), compact.stdout.count("\n"))

    def test_analysis_cli_known_error_exits_one_and_has_no_success_payload(self):
        for mode in ("monte-carlo", "what-if"):
            result = self.cli("analysis_samples", "--case", "unconfirmed_pension", "--mode", mode, "--num-paths", "2")
            self.assertEqual(result.returncode, 1)
            payload = strict_json(result.stdout)
            self.assertEqual(set(payload), {"contract_version", "error"})
            self.assertEqual(payload["error"]["code"], "input_mapping_failed")
            self.assertNotIn("Traceback", result.stderr)

    def test_analysis_cli_argument_error_exits_two_without_json(self):
        for args in (("--num-paths", "0"), ("--case", "not-a-case")):
            result = self.cli("analysis_samples", *args)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertIn("usage:", result.stderr)

    def test_all_five_sample_clis_execute_without_docs_or_source_helpers(self):
        for module in ("samples", "monte_carlo_samples", "what_if_samples", "input_samples", "analysis_samples"):
            with self.subTest(module=module):
                args = () if module == "samples" else ("--num-paths", "2", "--seed", "7")
                result = self.cli(module, *args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(result.stdout)
                self.assertIn("synthetic", (result.stdout + result.stderr).lower())
                self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--expected-prefix", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    args = parser.parse_args()
    EXPECTED_PREFIX, SOURCE_ROOT = args.expected_prefix.resolve(), args.source_root.resolve()
    if not sys.flags.isolated or Path(sys.prefix).resolve() != EXPECTED_PREFIX or sys.prefix == sys.base_prefix:
        parser.error("run only via run_installation_checks.py in its isolated temporary venv")
    EXPECTED_SHA256 = args.expected_sha256
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(InstalledPackageTests))
    package = Path(import_module("retirement_simulation").__file__).resolve().parent
    print(json.dumps({"successful": result.wasSuccessful(), "tests_run": result.testsRun,
                      "skipped": len(result.skipped), "package_root": str(package),
                      "python_version": sys.version.split()[0], "prefix": sys.prefix}, allow_nan=False))
    raise SystemExit(0 if result.wasSuccessful() and not result.skipped else 1)
