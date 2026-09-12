"""Validate the value-free F14-T05 production rollback evidence."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REPORT = ROOT / "ai-pjt/evals/artifacts/f14-rollback-rehearsal.json"
POLICY_PATH = ROOT / "contract-fixtures/financial-calculation/v1/policy.json"

SHA40 = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")
SNAPSHOT_ID = re.compile(r"\d{8}T\d{6}Z-[0-9a-f]{12}")

TOP_LEVEL_KEYS = {
    "schema_version",
    "task",
    "candidate",
    "executed_at_utc",
    "production_environment",
    "config_snapshot",
    "database",
    "deployment",
    "independent_verification",
    "configuration_source_embedded",
    "secret_values_in_artifact",
    "result",
}


def _require_exact_keys(value: Mapping[str, Any], expected: set[str], name: str) -> None:
    actual = set(value)
    if actual != expected:
        raise ValueError(f"{name} keys differ: missing={sorted(expected - actual)}, extra={sorted(actual - expected)}")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(report: Mapping[str, Any]) -> dict[str, Any]:
    _require_exact_keys(report, TOP_LEVEL_KEYS, "report")
    if report["schema_version"] != "f14-rollback-evidence-v1" or report["task"] != "F14-T05":
        raise ValueError("unexpected F14 rollback evidence schema or task")
    if not SHA40.fullmatch(str(report["candidate"])):
        raise ValueError("candidate must be a full Git SHA")
    if report["result"] != "PASS":
        raise ValueError("production rehearsal did not pass")
    if report["configuration_source_embedded"] is not False or report["secret_values_in_artifact"] is not False:
        raise ValueError("configuration source or secret values must not be embedded")

    environment = report["production_environment"]
    _require_exact_keys(
        environment,
        {
            "server_host",
            "deployment_checkout_commit",
            "calculation_policy_version",
            "calculation_policy_sha256",
        },
        "production_environment",
    )
    if not SHA40.fullmatch(str(environment["deployment_checkout_commit"])):
        raise ValueError("deployment checkout commit must be a full Git SHA")
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    if environment["calculation_policy_version"] != policy["policy_version"]:
        raise ValueError("production and candidate calculation policy versions differ")
    if environment["calculation_policy_sha256"] != _sha256(POLICY_PATH):
        raise ValueError("production and candidate calculation policy hashes differ")

    snapshot = report["config_snapshot"]
    _require_exact_keys(
        snapshot,
        {
            "snapshot_id",
            "path",
            "sha256",
            "owner",
            "directory_mode",
            "file_mode",
            "filesystem_immutable",
            "recovery_owner",
            "source_unchanged",
            "values_recorded",
        },
        "config_snapshot",
    )
    snapshot_id = str(snapshot["snapshot_id"])
    if not SNAPSHOT_ID.fullmatch(snapshot_id):
        raise ValueError("invalid snapshot ID")
    if snapshot["path"] != f"/srv/s15p21b109/secrets/snapshots/{snapshot_id}":
        raise ValueError("snapshot path is outside the restricted server root")
    if not SHA256.fullmatch(str(snapshot["sha256"])):
        raise ValueError("snapshot digest must be SHA-256")
    expected_snapshot = {
        "owner": "root:root",
        "directory_mode": "0500",
        "file_mode": "0400",
        "filesystem_immutable": True,
        "recovery_owner": "root",
        "source_unchanged": True,
        "values_recorded": False,
    }
    for key, expected in expected_snapshot.items():
        if snapshot[key] != expected:
            raise ValueError(f"config snapshot {key} differs")

    database = report["database"]
    _require_exact_keys(
        database,
        {"scope", "customer_data_access", "dump_restore_exact", "dump_sha256", "residual_schema_count"},
        "database",
    )
    if database["scope"] != "dedicated_synthetic_schema":
        raise ValueError("database rehearsal must use a dedicated synthetic schema")
    if database["customer_data_access"] is not False or database["dump_restore_exact"] is not True:
        raise ValueError("database rehearsal isolation or restore failed")
    if database["residual_schema_count"] != 0 or not SHA256.fullmatch(str(database["dump_sha256"])):
        raise ValueError("database rehearsal cleanup or digest failed")

    deployment = report["deployment"]
    _require_exact_keys(
        deployment,
        {
            "failure_injected",
            "expected_exit_nonzero",
            "services_restored_to_last_good",
            "web_health",
            "backend_health",
            "ai_health",
            "http_status",
            "api_status",
        },
        "deployment",
    )
    if deployment["failure_injected"] is not True or deployment["expected_exit_nonzero"] is not True:
        raise ValueError("failure path was not exercised")
    if deployment["services_restored_to_last_good"] != ["web", "backend", "ai-pjt", "migrate"]:
        raise ValueError("not all built services were restored")
    if [deployment["web_health"], deployment["backend_health"], deployment["ai_health"]] != [
        "healthy",
        "healthy",
        "healthy",
    ]:
        raise ValueError("service health was not restored")
    if deployment["http_status"] != 200 or not 100 <= deployment["api_status"] < 500:
        raise ValueError("HTTP/API health was not restored")

    verification = report["independent_verification"]
    _require_exact_keys(
        verification,
        {
            "snapshot_hash_permissions_and_immutable_flag",
            "running_images_equal_last_good",
            "database_cleanup",
            "final_health",
            "firewall_unchanged_ports",
        },
        "independent_verification",
    )
    if any(verification[key] != "PASS" for key in (
        "snapshot_hash_permissions_and_immutable_flag",
        "running_images_equal_last_good",
        "database_cleanup",
        "final_health",
    )):
        raise ValueError("independent production verification failed")
    if verification["firewall_unchanged_ports"] != [22, 80, 443]:
        raise ValueError("firewall state differs from the supplied server runbook")

    return {
        "task": report["task"],
        "candidate": report["candidate"],
        "snapshot_id": snapshot_id,
        "policy_version": environment["calculation_policy_version"],
        "restored_services": len(deployment["services_restored_to_last_good"]),
        "result": "PASS",
    }


def load_and_validate(path: Path = DEFAULT_REPORT) -> dict[str, Any]:
    return validate(json.loads(path.read_text(encoding="utf-8")))


if __name__ == "__main__":
    print(json.dumps(load_and_validate(), ensure_ascii=False, sort_keys=True))
