"""Fast offline guards for the runner; these tests do NOT build or install wheels."""

from __future__ import annotations

import base64
from contextlib import redirect_stderr
import csv
import hashlib
from io import StringIO
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch
import warnings
import zipfile


AI_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(AI_PROJECT_ROOT))
from retirement_simulation.tests import run_installation_checks as runner  # noqa: E402


class InstallationRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="retirement-wheel-fixture-")
        self.addCleanup(self.temporary.cleanup)
        self.wheel = Path(self.temporary.name) / runner.WHEEL_NAME

    def wheel_fixture(self, *, missing=None, extra=None, headers="", symlink=None,
                      bad_record=False, tamper_content=False, version="0.1.1"):
        # Archive-validation fixture only: never install/execute these dummy modules.
        files = {f"retirement_simulation/{name}.py": b"# synthetic archive member\n" for name in runner.MODULES}
        files.update({
            runner.DIST_INFO + "METADATA": (
                f"Metadata-Version: 2.4\nName: retirewise-retirement-simulation\nVersion: {version}\n"
                "Requires-Python: <3.14,>=3.13\n" + headers + "\n"
            ).encode(),
            runner.DIST_INFO + "WHEEL": b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
            runner.DIST_INFO + "top_level.txt": b"retirement_simulation\n",
        })
        if missing:
            files.pop(missing)
        if extra:
            files[extra] = b"# excluded fixture\n"
        record = StringIO()
        rows = csv.writer(record)
        for name, data in files.items():
            digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
            rows.writerow((name, "sha256=" + digest, str(len(data))))
        rows.writerow((runner.DIST_INFO + "RECORD", "", ""))
        files[runner.DIST_INFO + "RECORD"] = b"invalid,row\n" if bad_record else record.getvalue().encode()
        if tamper_content:
            files["retirement_simulation/cashflow.py"] += b"# changed after RECORD was calculated\n"
        with zipfile.ZipFile(self.wheel, "w") as archive:
            for name, content in files.items():
                entry = zipfile.ZipInfo(name)
                entry.external_attr = ((stat.S_IFLNK if name == symlink else stat.S_IFREG) | 0o644) << 16
                archive.writestr(entry, content)
        return hashlib.sha256(self.wheel.read_bytes()).hexdigest()

    def test_well_formed_archive_is_accepted_without_installation(self):
        digest = self.wheel_fixture()
        with patch.object(runner.subprocess, "run", side_effect=AssertionError("must not execute")):
            self.assertIsNone(runner.verify_wheel(self.wheel, digest))
            self.assertIsNone(runner.verify_wheel(self.wheel, digest.upper()))

    def test_runner_identity_matches_current_packaging_configuration(self):
        config = tomllib.loads((AI_PROJECT_ROOT / "retirement_simulation/pyproject.toml").read_text())
        self.assertEqual((runner.DISTRIBUTION, runner.VERSION),
                         (config["project"]["name"], config["project"]["version"]))
        self.assertEqual(runner.VERSION, "0.1.1")
        self.assertEqual(runner.WHEEL_NAME, "retirewise_retirement_simulation-0.1.1-py3-none-any.whl")
        self.assertEqual(runner.DIST_INFO, "retirewise_retirement_simulation-0.1.1.dist-info/")

    def test_old_version_or_filename_is_rejected_before_installation(self):
        digest = self.wheel_fixture(version="0.1.0")
        with patch.object(runner.tempfile, "TemporaryDirectory") as create:
            with self.assertRaisesRegex(ValueError, "Version metadata"):
                runner.run_checks(self.wheel, digest)
            create.assert_not_called()
        digest = self.wheel_fixture()
        old_name = self.wheel.with_name("retirewise_retirement_simulation-0.1.0-py3-none-any.whl")
        self.wheel.rename(old_name)
        with patch.object(runner.tempfile, "TemporaryDirectory") as create:
            with self.assertRaisesRegex(ValueError, "regular local 0\\.1\\.1 wheel"):
                runner.run_checks(old_name, digest)
            create.assert_not_called()

    def test_wrong_or_malformed_hash_is_rejected_before_temporary_environment(self):
        self.wheel_fixture()
        for digest in ("0" * 64, "g" * 64, "a" * 63, "", "../hash"):
            with self.subTest(digest=digest), patch.object(runner.tempfile, "TemporaryDirectory") as create:
                with self.assertRaises(ValueError):
                    runner.run_checks(self.wheel, digest)
                create.assert_not_called()

    def test_missing_file_wrong_name_directory_and_symlink_are_rejected(self):
        digest = self.wheel_fixture()
        link = self.wheel.parent / "link" / runner.WHEEL_NAME
        link.parent.mkdir()
        link.symlink_to(self.wheel)
        for path in (link, self.wheel.parent, self.wheel.parent / "missing.whl"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                runner.verify_wheel(path, digest)

    def test_missing_required_module_is_rejected(self):
        digest = self.wheel_fixture(missing="retirement_simulation/what_if_adapter.py")
        with self.assertRaisesRegex(ValueError, "exactly 18"):
            runner.verify_wheel(self.wheel, digest)

    def test_extra_module_tests_environment_and_escape_paths_are_rejected(self):
        for name in ("retirement_simulation/tests/test_helpers.py", "retirement_simulation/extra.py",
                     ".env", "../escape.py", "/absolute.py", "startup.pth"):
            with self.subTest(name=name):
                digest = self.wheel_fixture(extra=name)
                with self.assertRaisesRegex(ValueError, "exactly 18"):
                    runner.verify_wheel(self.wheel, digest)

    def test_archive_symlink_is_rejected(self):
        digest = self.wheel_fixture(symlink="retirement_simulation/cashflow.py")
        with self.assertRaisesRegex(ValueError, "symlinks"):
            runner.verify_wheel(self.wheel, digest)

    def test_duplicate_member_is_rejected(self):
        self.wheel_fixture()
        with warnings.catch_warnings(), zipfile.ZipFile(self.wheel, "a") as archive:
            warnings.simplefilter("ignore", UserWarning)
            archive.writestr("retirement_simulation/cashflow.py", b"# duplicate")
        with self.assertRaisesRegex(ValueError, "exactly 18"):
            runner.verify_wheel(self.wheel, hashlib.sha256(self.wheel.read_bytes()).hexdigest())

    def test_runtime_dependency_extra_and_ambiguous_identity_are_rejected(self):
        for headers in ("Requires-Dist: Django\n", "Provides-Extra: ai\n", "Name: other-project\n",
                        "Version: 9.9.9\n", "Requires-Python: >=3.10\n"):
            with self.subTest(headers=headers):
                digest = self.wheel_fixture(headers=headers)
                with self.assertRaises(ValueError):
                    runner.verify_wheel(self.wheel, digest)

    def test_invalid_record_is_rejected(self):
        digest = self.wheel_fixture(bad_record=True)
        with self.assertRaisesRegex(ValueError, "RECORD"):
            runner.verify_wheel(self.wheel, digest)

    def test_stale_record_hash_is_rejected_even_when_outer_sha256_matches(self):
        digest = self.wheel_fixture(tamper_content=True)
        with self.assertRaisesRegex(ValueError, "RECORD hash or size"):
            runner.verify_wheel(self.wheel, digest)

    def test_subprocess_failure_cannot_be_reported_as_success(self):
        failed = subprocess.CompletedProcess(["python"], 1, "", "synthetic failure")
        with patch.object(runner.subprocess, "run", return_value=failed) as run:
            with self.assertRaisesRegex(RuntimeError, "exited 1"):
                runner.checked_run(["python"], cwd=self.wheel.parent, env={})
            self.assertEqual(run.call_args.kwargs["timeout"], 60)

    def test_detached_source_is_excluded_and_failed_venv_setup_is_cleaned(self):
        digest = self.wheel_fixture()
        source = self.wheel.parent / "detached-source"
        source.mkdir()
        temporary_workspaces = []

        def fail_venv(command, *, cwd, env):
            self.assertEqual(command[:5], [sys.executable, "-I", "-B", "-m", "venv"])
            self.assertFalse(cwd.is_relative_to(source))
            self.assertEqual(set(env["PYTHONPATH"].split(os.pathsep)),
                             {str(cwd), str(source.parent), str(source)})
            self.assertEqual(env["PIP_CONFIG_FILE"], os.devnull)
            temporary_workspaces.append(cwd.parent)
            raise RuntimeError("synthetic venv setup failure")

        with (patch.object(runner, "__file__", str(source / "tests/run_installation_checks.py")),
              patch.object(runner, "checked_run", side_effect=fail_venv),
              redirect_stderr(StringIO())):
            with self.assertRaisesRegex(RuntimeError, "synthetic venv setup failure"):
                runner.run_checks(self.wheel, digest)
        self.assertEqual(len(temporary_workspaces), 1)
        self.assertFalse(temporary_workspaces[0].exists())

    def test_cli_failure_returns_one_without_starting_installation(self):
        self.wheel_fixture()
        with redirect_stderr(StringIO()) as stderr, patch.object(runner.tempfile, "TemporaryDirectory") as create:
            self.assertEqual(runner.main(["--wheel", str(self.wheel), "--sha256", "0" * 64]), 1)
        self.assertIn("SHA-256 mismatch", stderr.getvalue())
        create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
