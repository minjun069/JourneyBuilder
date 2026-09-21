"""Validate the checked F14 full-regression evidence and its source artifacts."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REPORT = REPO_ROOT / "ai-pjt" / "evals" / "artifacts" / "f14-full-regression.json"
SCHEMA_VERSION = "f14-full-regression-v1"
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
EXPECTED_EXECUTIONS = {
    "retirement_simulation",
    "ai_runtime",
    "rag",
    "tools",
    "evaluations",
    "security",
    "backend_unit",
    "backend_contract",
    "backend_django",
    "frontend_contract",
}
EXPECTED_CONSUMERS = {
    "ai-pjt/tools/financial_policy.py",
    "ai-pjt/tools/catalog.py",
    "backend-pjt/reports/policy.py",
    "backend-pjt/reports/services.py",
}
T03_POLICY_PATH = "contract-fixtures/financial-calculation/v1/policy.json"
T03_POLICY_VERSION = "2026-09-07.v1"
T03_POLICY_SHA256 = "b50f9e854185c5a6555ebac153e2fec2a309ddbbc86cb946080d3a0df3a70df4"


def load_report(path: Path = DEFAULT_REPORT) -> dict[str, Any]:
    with path.open(encoding="utf-8") as report_file:
        report = json.load(report_file)
    if not isinstance(report, dict):
        raise ValueError("F14 regression report must be a JSON object")
    return report


def _load_source(relative_path: str) -> dict[str, Any]:
    with (REPO_ROOT / relative_path).open(encoding="utf-8") as source_file:
        payload = json.load(source_file)
    if not isinstance(payload, dict):
        raise ValueError(f"{relative_path} must contain a JSON object")
    return payload


def validate_report(report: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if report.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION!r}")
    if report.get("suite_id") != "AI-F14-T03":
        errors.append("suite_id must be 'AI-F14-T03'")
    if report.get("gate") != "G6":
        errors.append("gate must be 'G6'")
    if COMMIT_PATTERN.fullmatch(str(report.get("candidate", ""))) is None:
        errors.append("candidate must be a 40-character lowercase commit SHA")

    executions = report.get("executions")
    if not isinstance(executions, list):
        errors.append("executions must be an array")
        return errors
    execution_ids = {item.get("id") for item in executions if isinstance(item, dict)}
    if execution_ids != EXPECTED_EXECUTIONS:
        errors.append(
            f"execution IDs differ: expected {sorted(EXPECTED_EXECUTIONS)}, "
            f"got {sorted(str(item) for item in execution_ids)}"
        )
    totals = {"tests": 0, "passed": 0, "skipped": 0, "failed": 0}
    for index, execution in enumerate(executions):
        prefix = f"executions[{index}]"
        if not isinstance(execution, dict):
            errors.append(f"{prefix} must be an object")
            continue
        for key in ("environment", "command"):
            if not isinstance(execution.get(key), str) or not execution[key].strip():
                errors.append(f"{prefix}.{key} must be a non-empty string")
        for key in totals:
            value = execution.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                errors.append(f"{prefix}.{key} must be a non-negative integer")
                continue
            totals[key] += value
        if all(isinstance(execution.get(key), int) for key in totals):
            if execution["tests"] != (
                execution["passed"] + execution["skipped"] + execution["failed"]
            ):
                errors.append(f"{prefix} counts do not sum to tests")
            if execution["failed"] != 0:
                errors.append(f"{prefix} has test failures")
    summary = report.get("summary")
    expected_summary = {"suites": len(executions), **totals}
    if summary != expected_summary:
        errors.append(f"summary mismatch: expected {expected_summary}, got {summary}")

    sources = report.get("source_artifacts")
    if not isinstance(sources, list) or not sources:
        errors.append("source_artifacts must be a non-empty array")
        return errors
    source_payloads: dict[str, dict[str, Any]] = {}
    for index, source in enumerate(sources):
        prefix = f"source_artifacts[{index}]"
        if not isinstance(source, dict):
            errors.append(f"{prefix} must be an object")
            continue
        relative_path = source.get("path")
        if not isinstance(relative_path, str):
            errors.append(f"{prefix}.path must be a string")
            continue
        path = REPO_ROOT / relative_path
        if not path.is_file():
            errors.append(f"{prefix}.path does not exist: {relative_path}")
            continue
        if relative_path == T03_POLICY_PATH:
            if source.get("sha256") != T03_POLICY_SHA256:
                errors.append(f"{prefix}.sha256 differs from the sealed T03 policy")
            source_payloads[relative_path] = {"policy_version": T03_POLICY_VERSION}
        else:
            actual_digest = sha256(path.read_bytes()).hexdigest()
            if source.get("sha256") != actual_digest:
                errors.append(f"{prefix}.sha256 mismatch for {relative_path}")
            source_payloads[relative_path] = _load_source(relative_path)

    _validate_source_semantics(report, source_payloads, errors)
    consumers = report.get("calculation_policy_consumers")
    if set(consumers or []) != EXPECTED_CONSUMERS:
        errors.append("calculation_policy_consumers differ from the release allowlist")
    for consumer in consumers or []:
        if not (REPO_ROOT / consumer).is_file():
            errors.append(f"calculation policy consumer does not exist: {consumer}")
    return errors


def _validate_source_semantics(
    report: dict[str, Any],
    sources: dict[str, dict[str, Any]],
    errors: list[str],
) -> None:
    try:
        t01 = sources["ai-pjt/evals/artifacts/f14-integrated-mock-attack.json"]
        t02 = sources["ai-pjt/evals/artifacts/f14-finding-triage.json"]
        t04 = sources["ai-pjt/evals/artifacts/f14-operational-anomalies.json"]
        f13 = sources["ai-pjt/evals/artifacts/f13-production-security-regression.json"]
        policy = sources["contract-fixtures/financial-calculation/v1/policy.json"]
    except KeyError as exc:
        errors.append(f"required source artifact missing: {exc.args[0]}")
        return
    if t01.get("summary", {}).get("findings") != 0:
        errors.append("T01 findings must be zero before G6")
    if t02.get("decision") != "no_findings" or t02.get("dynamic_tasks") != []:
        errors.append("T02 must close with no findings and no dynamic tasks")
    if t04.get("summary", {}).get("failed") != 0:
        errors.append("T04 must have zero failures")
    if not t04.get("backend_concurrency_check", {}).get("passed"):
        errors.append("T04 Backend concurrency check must pass")
    if f13.get("summary", {}).get("failed") != 0:
        errors.append("F13 production security regression must remain clean")
    policy_version = policy.get("policy_version")
    if report.get("calculation_policy_version") != policy_version:
        errors.append("calculation policy version differs from the checked policy fixture")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = load_report(args.report)
    errors = validate_report(report)
    result = {
        "suite_id": report.get("suite_id"),
        "gate": report.get("gate"),
        "passed": not errors,
        "errors": errors,
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif errors:
        for error in errors:
            print(f"FAIL: {error}")
    else:
        print(
            f"PASS {report['suite_id']} gate={report['gate']} "
            f"tests={report['summary']['tests']} failed=0"
        )
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
