"""Validate the F14-T06 final E2E evidence used by the G7 gate."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REPORT = REPO_ROOT / "ai-pjt/evals/artifacts/f14-final-e2e.json"
SCHEMA_VERSION = "f14-final-e2e-v1"
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
EXPECTED_SOURCES = {
    "ai-pjt/evals/artifacts/f14-full-regression.json",
    "ai-pjt/evals/artifacts/f14-rollback-rehearsal.json",
    "ai-pjt/rag/artifacts/official__official-catalog-2-1-0.tar.gz",
    "contract-fixtures/financial-calculation/v1/policy.json",
}
EXPECTED_BINDING_FIELDS = {
    "reference_id",
    "age",
    "value",
    "unit",
    "period",
    "population",
    "comparator",
    "source_id",
    "source_version",
    "as_of",
    "calculation_policy_version",
}
def load_report(path: Path = DEFAULT_REPORT) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("F14 final E2E report must be a JSON object")
    return payload


def _load_json(relative_path: str) -> dict[str, Any]:
    payload = json.loads((REPO_ROOT / relative_path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{relative_path} must be a JSON object")
    return payload


def validate(report: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if report.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    if report.get("suite_id") != "AI-F14-T06" or report.get("gate") != "G7":
        errors.append("suite_id and gate must identify F14-T06/G7")
    if COMMIT_PATTERN.fullmatch(str(report.get("candidate", ""))) is None:
        errors.append("candidate must be a full lowercase Git SHA")

    _validate_executions(report, errors)
    sources = _validate_sources(report, errors)
    _validate_policy(report, sources, errors)
    _validate_reference_value(report, errors)
    _validate_rollback(report, sources, errors)
    _validate_rag_reuse(report, errors)
    limits = report.get("known_limits")
    if not isinstance(limits, list) or len(limits) != 3 or any(
        not isinstance(item, str) or not item.strip() for item in limits
    ):
        errors.append("known_limits must contain the three explicit release limits")
    return errors


def _validate_executions(report: Mapping[str, Any], errors: list[str]) -> None:
    executions = report.get("executions")
    if not isinstance(executions, list):
        errors.append("executions must be an array")
        return
    ids = [item.get("id") for item in executions if isinstance(item, dict)]
    if len(ids) != len(set(ids)) or set(ids) != EXPECTED_EXECUTIONS:
        errors.append("execution IDs differ from the final release suite")
    totals = {"tests": 0, "passed": 0, "skipped": 0, "failed": 0}
    for index, execution in enumerate(executions):
        if not isinstance(execution, dict):
            errors.append(f"executions[{index}] must be an object")
            continue
        if not isinstance(execution.get("command"), str) or not execution["command"]:
            errors.append(f"executions[{index}].command is missing")
        valid_counts = True
        for field in totals:
            value = execution.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                errors.append(f"executions[{index}].{field} is invalid")
                valid_counts = False
            else:
                totals[field] += value
        if valid_counts:
            if execution["tests"] != (
                execution["passed"] + execution["skipped"] + execution["failed"]
            ):
                errors.append(f"executions[{index}] counts do not sum")
            if execution["failed"] != 0:
                errors.append(f"executions[{index}] has test failures")
    expected_summary = {"suites": len(executions), **totals}
    if report.get("summary") != expected_summary:
        errors.append(f"summary mismatch: expected {expected_summary}")


def _validate_sources(
    report: Mapping[str, Any],
    errors: list[str],
) -> dict[str, dict[str, Any]]:
    source_items = report.get("source_artifacts")
    if not isinstance(source_items, list):
        errors.append("source_artifacts must be an array")
        return {}
    paths = [item.get("path") for item in source_items if isinstance(item, dict)]
    if len(paths) != len(set(paths)) or set(paths) != EXPECTED_SOURCES:
        errors.append("source artifact paths differ from the G7 allowlist")
    payloads: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(source_items):
        if not isinstance(item, dict):
            errors.append(f"source_artifacts[{index}] must be an object")
            continue
        relative_path = item.get("path")
        if not isinstance(relative_path, str):
            errors.append(f"source_artifacts[{index}].path is invalid")
            continue
        path = REPO_ROOT / relative_path
        if not path.is_file():
            errors.append(f"source artifact does not exist: {relative_path}")
            continue
        actual = sha256(path.read_bytes()).hexdigest()
        if item.get("sha256") != actual:
            errors.append(f"source artifact sha256 mismatch: {relative_path}")
        if path.suffix == ".json":
            payloads[relative_path] = _load_json(relative_path)
    return payloads


def _validate_policy(
    report: Mapping[str, Any],
    sources: Mapping[str, Mapping[str, Any]],
    errors: list[str],
) -> None:
    policy_meta = report.get("calculation_policy")
    policy_path = "contract-fixtures/financial-calculation/v1/policy.json"
    policy = sources.get(policy_path)
    if not isinstance(policy_meta, Mapping) or not isinstance(policy, Mapping):
        errors.append("calculation policy evidence is missing")
        return
    if policy_meta != {
        "path": policy_path,
        "version": policy.get("policy_version"),
        "sha256": sha256((REPO_ROOT / policy_path).read_bytes()).hexdigest(),
    }:
        errors.append("calculation policy version or digest differs from the candidate")
    household = [
        item for item in policy.get("reference_values", [])
        if isinstance(item, Mapping)
        and str(item.get("reference_id", "")).startswith("household_monthly_expense_")
    ]
    if len(household) != 10:
        errors.append("approved writable household reference family must contain 10 values")


def _validate_reference_value(report: Mapping[str, Any], errors: list[str]) -> None:
    evidence = report.get("reference_value_e2e")
    if not isinstance(evidence, Mapping):
        errors.append("reference_value_e2e is missing")
        return
    if evidence.get("approved_reference_count") != 10 or evidence.get("read_only_reference_count") != 8:
        errors.append("reference writable/read-only family counts differ")
    if set(evidence.get("binding_fields", [])) != EXPECTED_BINDING_FIELDS:
        errors.append("sealed reference binding is incomplete")
    target = evidence.get("target")
    expected_target = {
        "step_id": "financial-goals",
        "section": "goal_periods",
        "path": "items[].amount",
        "phase": "before",
        "requires_parent_stable_id": True,
        "requires_item_stable_id": True,
        "requires_exact_item_name": True,
        "requires_empty_amount": True,
        "zero_is_empty": False,
    }
    if target != expected_target:
        errors.append("writable reference target differs from the approved allowlist")
    success = evidence.get("success")
    if not isinstance(success, Mapping) or success.get("reference_id") != "household_monthly_expense_housing_avg":
        errors.append("representative reference success evidence is missing")
    elif (
        success.get("value") != 361000
        or success.get("unit") != "KRW"
        or success.get("period") != "monthly"
        or success.get("population") != "kr_general_households_1plus"
        or success.get("comparator") != "eq"
        or success.get("proposal_mode") != "proposal"
        or success.get("proposal_status") != "pending"
        or success.get("database_value_before_approval") is not None
        or success.get("database_value_after_approval") is not None
        or success.get("database_value_after_form_save") != 361000
        or success.get("callback_replay_equal") is not True
        or success.get("graph_execution_count") != 1
    ):
        errors.append("reference proposal, approval, save, or replay invariant failed")
    blocked = evidence.get("fail_closed")
    if blocked != {
        "request_result_mismatch": "reference_contract_mismatch",
        "target_or_metadata_mismatch": "reference_target_not_allowed",
        "dependent_task_status": "blocked",
        "action_count": 0,
        "approval_count": 0,
        "save_count": 0,
    }:
        errors.append("reference failure path did not remain side-effect free")


def _validate_rollback(
    report: Mapping[str, Any],
    sources: Mapping[str, Mapping[str, Any]],
    errors: list[str],
) -> None:
    evidence = report.get("rollback")
    source = sources.get("ai-pjt/evals/artifacts/f14-rollback-rehearsal.json")
    if not isinstance(evidence, Mapping) or not isinstance(source, Mapping):
        errors.append("rollback evidence is missing")
        return
    if (
        evidence.get("result") != "PASS"
        or source.get("result") != "PASS"
        or evidence.get("configuration_source_embedded") is not False
        or evidence.get("secret_values_in_artifact") is not False
        or evidence.get("customer_data_access") is not False
        or evidence.get("synthetic_dump_restore_exact") is not True
        or evidence.get("services_restored_to_last_good") != 4
        or evidence.get("firewall_unchanged_ports") != [22, 80, 443]
        or source.get("configuration_source_embedded") is not False
        or source.get("secret_values_in_artifact") is not False
    ):
        errors.append("production rollback safety evidence differs")


def _validate_rag_reuse(report: Mapping[str, Any], errors: list[str]) -> None:
    evidence = report.get("rag_reuse")
    if not isinstance(evidence, Mapping):
        errors.append("rag_reuse evidence is missing")
        return
    artifact = "ai-pjt/rag/artifacts/official__official-catalog-2-1-0.tar.gz"
    if (
        evidence.get("action") != "reuse_without_reindex_or_promotion"
        or evidence.get("artifact") != artifact
        or evidence.get("sha256") != sha256((REPO_ROOT / artifact).read_bytes()).hexdigest()
        or set(evidence.get("physical_index_ids", [])) != {
            "financial-knowledge__official-catalog-2-1-0",
            "interface-guides__official-catalog-2-1-0",
        }
        or evidence.get("external_provider_calls") != 0
        or evidence.get("index_writes") != 0
        or evidence.get("pointer_changes") != 0
    ):
        errors.append("RAG artifact reuse contract differs")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = load_report(args.report)
    errors = validate(report)
    result = {
        "suite_id": report.get("suite_id"),
        "gate": report.get("gate"),
        "candidate": report.get("candidate"),
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
