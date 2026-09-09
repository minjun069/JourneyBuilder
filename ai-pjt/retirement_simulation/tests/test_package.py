"""Import-boundary regressions for the renamed standalone simulation package."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
import subprocess
import sys
import unittest


AI_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(AI_PROJECT_ROOT))

import retirement_simulation as simulation  # noqa: E402


class PackageBoundaryTests(unittest.TestCase):
    def test_public_exports_resolve_to_the_simulation_package(self) -> None:
        self.assertTrue(simulation.__all__)
        self.assertEqual(len(simulation.__all__), len(set(simulation.__all__)))
        for name in simulation.__all__:
            with self.subTest(name=name):
                value = getattr(simulation, name)
                if name == "MAX_WHAT_IF_SCENARIOS":
                    self.assertEqual(value, 10)
                else:
                    self.assertTrue(value.__module__.startswith("retirement_simulation."))

    def test_engine_modules_resolve_from_the_new_directory(self) -> None:
        for name in ("models", "input_adapter", "cashflow", "return_paths", "monte_carlo",
                     "what_if", "what_if_engine", "what_if_adapter", "analysis", "analysis_errors"):
            with self.subTest(module=name):
                module = import_module(f"retirement_simulation.{name}")
                self.assertEqual(
                    Path(module.__file__).resolve().parent,
                    AI_PROJECT_ROOT / "retirement_simulation",
                )

    def test_calculation_model_versions_are_not_package_names(self) -> None:
        self.assertEqual(
            simulation.MonteCarloMetadata.__dataclass_fields__["model_version"].default,
            "retirement-mc-v1",
        )
        self.assertEqual(
            simulation.WhatIfResult.__dataclass_fields__["model_version"].default,
            "retirement-what-if-v1",
        )
        self.assertEqual(
            simulation.RetirementAnalysisResult.__dataclass_fields__["contract_version"].default,
            "retirement-analysis-v1",
        )

    def test_import_without_site_packages_or_application_dependencies(self) -> None:
        script = """
import sys
import retirement_simulation as simulation

assert simulation.calculate_monte_carlo.__module__ == 'retirement_simulation.monte_carlo'
assert simulation.calculate_what_if.__module__ == 'retirement_simulation.what_if_engine'
assert simulation.map_retirement_profile.__module__ == 'retirement_simulation.input_adapter'
assert simulation.map_simulation_assumptions.__module__ == 'retirement_simulation.input_adapter'
assert simulation.map_what_if_scenarios.__module__ == 'retirement_simulation.what_if_adapter'
assert simulation.analyze_retirement.__module__ == 'retirement_simulation.analysis'
assert simulation.RetirementAnalysisResult.__module__ == 'retirement_simulation.analysis'
assert simulation.RetirementAnalysisError.__module__ == 'retirement_simulation.analysis_errors'
for root in ('retirement', 'django', 'fastapi', 'langgraph', 'numpy', 'llama_index'):
    assert not any(name == root or name.startswith(root + '.') for name in sys.modules), root
print('standalone import OK')
"""
        result = subprocess.run(
            [sys.executable, "-S", "-B", "-c", script],
            cwd=AI_PROJECT_ROOT, capture_output=True, text=True, timeout=10, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "standalone import OK\n")
        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
