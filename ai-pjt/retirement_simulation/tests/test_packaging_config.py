"""Standard-library checks for source packaging policy, not wheel installation."""

from __future__ import annotations

from pathlib import Path
import re
import tomllib
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_MODULES = frozenset({
    "__init__.py", "analysis.py", "analysis_benchmark.py", "analysis_errors.py",
    "analysis_output.py", "analysis_samples.py", "cashflow.py", "input_adapter.py",
    "input_samples.py", "models.py", "monte_carlo.py", "monte_carlo_samples.py",
    "return_paths.py", "samples.py", "what_if.py", "what_if_adapter.py",
    "what_if_engine.py", "what_if_samples.py",
})


class PackagingConfigurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    def test_distribution_metadata_is_static_and_separate_from_import_name(self):
        project = self.config["project"]
        self.assertEqual(project["name"], "retirewise-retirement-simulation")
        self.assertEqual(project["version"], "0.1.0")
        self.assertNotIn("dynamic", project)
        self.assertNotIn("dynamic", self.config["tool"]["setuptools"])

    def test_initial_python_support_is_explicit(self):
        self.assertEqual(self.config["project"]["requires-python"], ">=3.13,<3.14")

    def test_runtime_has_no_application_or_build_dependencies(self):
        project = self.config["project"]
        self.assertEqual(project["dependencies"], [])
        self.assertNotIn("optional-dependencies", project)

    def test_build_backend_is_pinned_without_custom_backend_code(self):
        self.assertEqual(self.config["build-system"], {
            "requires": ["setuptools==84.0.0"],
            "build-backend": "setuptools.build_meta",
        })

    def test_only_existing_package_is_selected_without_data_or_auto_discovery(self):
        self.assertEqual(self.config["tool"]["setuptools"], {
            "packages": ["retirement_simulation"],
            "package-dir": {"retirement_simulation": "."},
            "include-package-data": False,
        })

    def test_no_new_entry_points_or_unapproved_license_are_declared(self):
        for key in ("scripts", "gui-scripts", "entry-points", "license", "license-files"):
            with self.subTest(key=key):
                self.assertNotIn(key, self.config["project"])

    def test_readme_is_the_module_readme(self):
        self.assertEqual(self.config["project"]["readme"], "README.md")
        readme = PROJECT_ROOT / self.config["project"]["readme"]
        self.assertTrue(readme.is_file())
        self.assertFalse(readme.is_symlink())
        self.assertTrue(readme.read_text(encoding="utf-8").startswith("# 은퇴 시뮬레이션"))

    def test_root_python_inventory_stays_at_the_existing_eighteen_modules(self):
        modules = tuple(PROJECT_ROOT.glob("*.py"))
        self.assertEqual({path.name for path in modules}, RUNTIME_MODULES)
        for path in modules:
            with self.subTest(module=path.name):
                self.assertTrue(path.is_file())
                self.assertFalse(path.is_symlink())
                self.assertEqual(path.resolve().parent, PROJECT_ROOT)

    def test_manifest_adds_only_module_docs_tests_and_build_metadata(self):
        rules = [line.strip() for line in (PROJECT_ROOT / "MANIFEST.in").read_text(encoding="utf-8").splitlines()
                 if line.strip() and not line.lstrip().startswith("#")]
        self.assertEqual(rules, [
            "include README.md pyproject.toml MANIFEST.in",
            "include tests/__init__.py tests/test_*.py",
            "include tests/run_installation_checks.py tests/installed/check_package.py",
            "include docs/*.md docs/analysis-performance-results.json",
            "prune build",
            "prune dist",
            "global-exclude *.py[cod] *~ .*",
        ])

    def test_documented_toml_matches_the_actual_configuration(self):
        document = (PROJECT_ROOT / "docs/packaging-contract.md").read_text(encoding="utf-8")
        snippets = re.findall(r"```toml\n(.*?)\n```", document, re.S)
        self.assertEqual(len(snippets), 1)
        self.assertEqual(tomllib.loads(snippets[0]), self.config)


if __name__ == "__main__":
    unittest.main()
