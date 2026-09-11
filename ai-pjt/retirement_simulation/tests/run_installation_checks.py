"""Offline wheel acceptance in a disposable venv; never installs into the caller."""

from __future__ import annotations

import argparse
import base64
import csv
from email import policy
from email.parser import BytesParser
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile


DISTRIBUTION = "retirewise-retirement-simulation"
VERSION = "0.1.2"
DIST_INFO = f"retirewise_retirement_simulation-{VERSION}.dist-info/"
WHEEL_NAME = f"retirewise_retirement_simulation-{VERSION}-py3-none-any.whl"
MODULES = frozenset({
    "__init__", "analysis", "analysis_benchmark", "analysis_errors", "analysis_output",
    "analysis_samples", "cashflow", "input_adapter", "input_samples", "models",
    "monte_carlo", "monte_carlo_samples", "return_paths", "samples", "what_if",
    "what_if_adapter", "what_if_engine", "what_if_samples",
})
WHEEL_FILES = {f"retirement_simulation/{name}.py" for name in MODULES} | {
    DIST_INFO + name for name in ("METADATA", "WHEEL", "top_level.txt", "RECORD")
}


def verify_wheel(wheel: Path, expected_sha256: str) -> None:
    """Check the exact local artifact before pip can read or install its contents."""
    if not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
        raise ValueError("SHA-256 must contain exactly 64 hexadecimal characters")
    if wheel.is_symlink() or not wheel.is_file() or wheel.name != WHEEL_NAME:
        raise ValueError(f"select the regular local {VERSION} wheel file, not a URL, directory, or symlink")
    if hashlib.sha256(wheel.read_bytes()).hexdigest() != expected_sha256.lower():
        raise ValueError("wheel SHA-256 mismatch")
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or set(names) != WHEEL_FILES:
            raise ValueError("wheel must contain exactly 18 modules and four metadata files")
        if any(stat.S_ISLNK(item.external_attr >> 16) for item in archive.infolist()):
            raise ValueError("wheel symlinks are not allowed")
        if archive.testzip() is not None:
            raise ValueError("wheel CRC mismatch")
        metadata = BytesParser(policy=policy.default).parsebytes(archive.read(DIST_INFO + "METADATA"))
        for key, expected in (("Name", DISTRIBUTION), ("Version", VERSION),
                              ("Requires-Python", "<3.14,>=3.13")):
            if metadata.get_all(key) != [expected]:
                raise ValueError(f"unexpected {key} metadata")
        if metadata.get_all("Requires-Dist") or metadata.get_all("Provides-Extra"):
            raise ValueError("runtime dependencies and extras are not allowed")
        wheel_metadata = BytesParser(policy=policy.default).parsebytes(archive.read(DIST_INFO + "WHEEL"))
        if wheel_metadata.get_all("Tag") != ["py3-none-any"] or wheel_metadata["Root-Is-Purelib"] != "true":
            raise ValueError("expected a pure Python wheel")
        if archive.read(DIST_INFO + "top_level.txt") != b"retirement_simulation\n":
            raise ValueError("unexpected import package name")
        rows = list(csv.reader(StringIO(archive.read(DIST_INFO + "RECORD").decode("utf-8"))))
        if (any(len(row) != 3 for row in rows) or len(rows) != len(names)
                or {row[0] for row in rows} != set(names)):
            raise ValueError("wheel RECORD inventory mismatch")
        for name, digest, size in rows:
            if name == DIST_INFO + "RECORD":
                if (digest, size) != ("", ""):
                    raise ValueError("RECORD must not hash itself")
                continue
            data = archive.read(name)
            expected = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")
            if digest != "sha256=" + expected or size != str(len(data)):
                raise ValueError("wheel RECORD hash or size mismatch")


def checked_run(command: list[str], *, cwd: Path, env: dict[str, str], timeout: int = 60):
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True,
                            encoding="utf-8", timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError(f"verification subprocess exited {result.returncode}\n{result.stderr[-6000:]}")
    return result


def run_checks(wheel: Path, expected_sha256: str) -> dict:
    verify_wheel(wheel, expected_sha256)
    project = Path(__file__).resolve().parents[1]
    source_root = next((parent for parent in (project, *project.parents)
                        if (parent / ".git").is_file() or (parent / ".git/HEAD").is_file()), project)
    with tempfile.TemporaryDirectory(prefix="retirement-installed-") as temporary:
        workspace = Path(temporary).resolve()
        if workspace.is_relative_to(source_root):
            raise ValueError("installation checks require a temporary directory outside the source repository")
        candidate = workspace / WHEEL_NAME
        shutil.copyfile(wheel, candidate)
        verify_wheel(candidate, expected_sha256)  # Recheck the exact private copy that pip will install.
        prefix = workspace / "install-env"
        working = workspace / "working"
        working.mkdir()
        # Both the working directory and PYTHONPATH contain a deliberate source-fallback trap.
        shadow = working / "retirement_simulation"
        shadow.mkdir()
        (shadow / "__init__.py").write_text("raise RuntimeError('source fallback trap')\n", encoding="utf-8")
        env = {key: value for key, value in os.environ.items() if not key.startswith("PIP_")}
        env.update(PIP_CONFIG_FILE=os.devnull,
                   PYTHONPATH=os.pathsep.join((str(working), str(project.parent), str(source_root))))
        print("Creating a fresh venv outside the repository...", file=sys.stderr)
        checked_run([sys.executable, "-I", "-B", "-m", "venv", str(prefix)], cwd=working, env=env)
        python = prefix / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        checked_run([str(python), "-I", "-B", "-c",
                     "import importlib.util; assert importlib.util.find_spec('retirement_simulation') is None"],
                    cwd=working, env=env)
        print("Installing the verified wheel without an index or dependencies...", file=sys.stderr)
        checked_run([str(python), "-I", "-B", "-m", "pip", "--isolated", "--disable-pip-version-check",
                     "install", "--no-index", "--no-deps", "--no-compile", "--no-cache-dir", str(candidate)],
                    cwd=working, env=env)
        check_script = workspace / "check_package.py"
        shutil.copyfile(project / "tests/installed/check_package.py", check_script)
        checked = checked_run([str(python), "-I", "-B", str(check_script),
                               "--expected-prefix", str(prefix), "--source-root", str(source_root),
                               "--expected-sha256", expected_sha256.lower()],
                              cwd=working, env=env)
        report = json.loads(checked.stdout)
        if not report.get("successful") or report.get("skipped") or not report.get("tests_run"):
            raise RuntimeError("installed checks did not execute and pass completely")
        print(checked.stderr, end="", file=sys.stderr)
        # A missing required module must fail a NEW process, even with real source on PYTHONPATH.
        package = Path(report["package_root"])
        if not package.is_relative_to(prefix) or package.name != "retirement_simulation":
            raise RuntimeError("unexpected installed package location")
        required = package / "what_if_adapter.py"
        backup = workspace / "what_if_adapter.py.saved"
        required.rename(backup)
        try:
            missing = subprocess.run([str(python), "-I", "-B", "-c", "import retirement_simulation"],
                                     cwd=working, env=env, capture_output=True, text=True, timeout=15, check=False)
            if (missing.returncode == 0 or "ModuleNotFoundError" not in missing.stderr
                    or "retirement_simulation.what_if_adapter" not in missing.stderr):
                raise RuntimeError("missing-module negative control did not fail as expected")
        finally:
            backup.rename(required)
        checked_run([str(python), "-I", "-B", "-c", "import retirement_simulation.what_if_adapter"],
                    cwd=working, env=env)
        report.update(wheel=wheel.name, sha256=expected_sha256.lower(),
                      preinstall_absence=True, missing_module_rejected=True, restored_import=True,
                      no_index=True, no_dependencies=True, temporary_environment_removed_on_return=True)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--wheel", required=True, type=Path, help="trusted local wheel, never an index name or URL")
    parser.add_argument("--sha256", required=True, help="expected SHA-256 recorded when the wheel was built")
    args = parser.parse_args(argv)
    try:
        report = run_checks(args.wheel.absolute(), args.sha256)
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile, subprocess.TimeoutExpired) as error:
        print(f"Installation verification failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
