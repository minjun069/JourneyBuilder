"""Deterministic foundation checks for the G1 integration gate."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = REPOSITORY_ROOT / "contract-fixtures/g1/foundation-inputs.json"
CONSISTENCY_PATH = REPOSITORY_ROOT / "contract-fixtures/g1/artifact-consistency.json"
EXPECTED_INPUTS = {"D1-G0", *(f"P{number:02d}" for number in range(1, 15))}
EXPECTED_PRODUCTION_STATUSES = ("active", "deferred", "excluded")


class GateFailure(RuntimeError):
    """Raised when a G1 foundation invariant is not satisfied."""


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GateFailure(f"cannot load JSON fixture: {path.relative_to(REPOSITORY_ROOT)}") from exc


def _require_ancestor(commit: str) -> None:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", commit, "HEAD"],
        cwd=REPOSITORY_ROOT,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        raise GateFailure(f"required input commit is not an ancestor of HEAD: {commit}")


def _validate_input_inventory(
    manifest: dict[str, Any],
    inputs: list[Any],
    *,
    label: str,
) -> set[str]:
    input_ids = [item.get("id") for item in inputs if isinstance(item, dict)]
    retired = manifest.get("retired_inputs", [])
    if (
        len(input_ids) != len(inputs)
        or not isinstance(retired, list)
        or not all(isinstance(input_id, str) for input_id in retired)
    ):
        raise GateFailure(f"{label} input inventory is invalid")
    if len(input_ids) != len(set(input_ids)) or len(retired) != len(set(retired)):
        raise GateFailure(f"{label} input inventory contains duplicate IDs")
    if set(input_ids) & set(retired):
        raise GateFailure(f"{label} cannot both retain and retire an input")
    if set(input_ids) | set(retired) != EXPECTED_INPUTS:
        raise GateFailure(
            f"{label} must classify D1-G0 and P01 through P14 exactly once"
        )
    return set(retired)


def check_manifest() -> list[str]:
    manifest = _load_json(MANIFEST_PATH)
    if not isinstance(manifest, dict):
        raise GateFailure("foundation manifest root must be an object")
    if manifest.get("fixture_kind") != "g1-foundation-inputs":
        raise GateFailure("foundation manifest fixture_kind is unsupported")
    if manifest.get("gate") != "G1":
        raise GateFailure("foundation manifest gate must be G1")
    if manifest.get("consistency_manifest") != str(
        CONSISTENCY_PATH.relative_to(REPOSITORY_ROOT)
    ):
        raise GateFailure("foundation manifest must reference the G1 consistency manifest")

    inputs = manifest.get("inputs")
    if not isinstance(inputs, list):
        raise GateFailure("foundation manifest inputs must be an array")
    _validate_input_inventory(
        manifest,
        inputs,
        label="foundation manifest",
    )

    checked_commits: list[str] = []
    for item in inputs:
        commits = item.get("commits")
        required_paths = item.get("required_paths")
        if not isinstance(commits, list) or not commits:
            raise GateFailure(f"{item['id']} must declare at least one commit")
        if not isinstance(required_paths, list) or not required_paths:
            raise GateFailure(f"{item['id']} must declare at least one required path")
        for commit in commits:
            if not isinstance(commit, str) or len(commit) != 40:
                raise GateFailure(f"{item['id']} contains an invalid commit ID")
            _require_ancestor(commit)
            checked_commits.append(commit)
        for relative_path in required_paths:
            path = REPOSITORY_ROOT / relative_path
            if not path.exists():
                raise GateFailure(f"{item['id']} required path is missing: {relative_path}")
    return checked_commits


def check_consistency_manifest() -> dict[str, int]:
    manifest = _load_json(CONSISTENCY_PATH)
    if not isinstance(manifest, dict):
        raise GateFailure("artifact consistency manifest root must be an object")
    if manifest.get("fixture_kind") != "g1-artifact-consistency":
        raise GateFailure("artifact consistency manifest fixture_kind is unsupported")
    if manifest.get("gate") != "G1":
        raise GateFailure("artifact consistency manifest gate must be G1")
    if manifest.get("status_axis") != "production_binding":
        raise GateFailure("artifact consistency status must describe production binding")

    semantics = manifest.get("pass_semantics")
    if not isinstance(semantics, dict):
        raise GateFailure("artifact consistency manifest must define pass_semantics")
    for key in ("asserts", "does_not_assert"):
        values = semantics.get(key)
        if not isinstance(values, list) or not values:
            raise GateFailure(f"pass_semantics.{key} must be a non-empty array")

    inputs = manifest.get("inputs")
    if not isinstance(inputs, list):
        raise GateFailure("artifact consistency inputs must be an array")
    retired = _validate_input_inventory(
        manifest,
        inputs,
        label="artifact consistency",
    )
    foundation = _load_json(MANIFEST_PATH)
    if retired != set(foundation.get("retired_inputs", [])):
        raise GateFailure("G1 manifests disagree on retired input IDs")

    counts = {status: 0 for status in EXPECTED_PRODUCTION_STATUSES}
    for item in inputs:
        input_id = item["id"]
        status = item.get("production_status")
        if status not in EXPECTED_PRODUCTION_STATUSES:
            raise GateFailure(f"{input_id} has an unsupported production_status")
        counts[status] += 1
        if not isinstance(item.get("artifact_role"), str) or not item["artifact_role"]:
            raise GateFailure(f"{input_id} must declare artifact_role")
        checks = item.get("checks")
        if not isinstance(checks, list) or not checks:
            raise GateFailure(f"{input_id} must declare semantic checks")
        consumers = item.get("current_consumers")
        if not isinstance(consumers, list):
            raise GateFailure(f"{input_id} current_consumers must be an array")
        if status != "excluded" and not consumers:
            raise GateFailure(f"{input_id} must declare current consumers")
        if status == "excluded" and consumers:
            raise GateFailure(f"{input_id} excluded input must have no current consumers")
        for relative_path in consumers:
            if not (REPOSITORY_ROOT / relative_path).exists():
                raise GateFailure(
                    f"{input_id} current consumer is missing: {relative_path}"
                )
        next_activation = item.get("next_activation")
        if not isinstance(next_activation, list):
            raise GateFailure(f"{input_id} next_activation must be an array")
        if status == "deferred" and not next_activation:
            raise GateFailure(f"{input_id} deferred input must name its activation lane")
        if status in {"active", "excluded"} and next_activation:
            raise GateFailure(
                f"{input_id} {status} input must not name a future activation lane"
            )
    return counts


def _collect_key_values(value: Any, key: str) -> list[Any]:
    values: list[Any] = []
    if isinstance(value, dict):
        for child_key, child in value.items():
            if child_key == key:
                values.append(child)
            values.extend(_collect_key_values(child, key))
    elif isinstance(value, list):
        for child in value:
            values.extend(_collect_key_values(child, key))
    return values


def check_step_and_chart_contracts() -> tuple[int, int]:
    fixture_root = REPOSITORY_ROOT / "contract-fixtures/ai-lifecycle/v2"
    registry = _load_json(fixture_root / "step-registry.json")
    steps = registry.get("steps")
    if not isinstance(steps, list) or not steps:
        raise GateFailure("step registry must contain supported steps")
    supported = {step.get("step_id") for step in steps if isinstance(step, dict)}
    fallback_steps = set(registry.get("transport_fallback_step_ids", []))
    if len(supported) != len(steps) or supported & fallback_steps:
        raise GateFailure("step registry supported and transport fallback steps must be unique")
    if fallback_steps != {"general"}:
        raise GateFailure("general must be the explicit transport-only fallback step")
    registered_agents = set(registry.get("agents", []))
    fallback_agents = set(registry.get("transport_fallback_allowed_agents", []))
    if fallback_agents != {"qa"} or not fallback_agents <= registered_agents:
        raise GateFailure("general transport fallback must allow only registered qa")

    process_steps = (
        REPOSITORY_ROOT / "front-pjt/src/views/ai/data/processSteps.js"
    ).read_text(encoding="utf-8")
    frontend_steps = set(re.findall(r"\{ id: '([^']+)'", process_steps))
    if frontend_steps != supported:
        raise GateFailure("Frontend process steps differ from the shared step registry")

    form_actions = _load_json(fixture_root / "form-actions.json")
    form_step_ids = {
        case.get("step_id")
        for case in form_actions.get("cases", {}).values()
        if isinstance(case, dict)
    }
    candidates = {
        step["step_id"]: set(step["allowed_agents"])
        for step in steps
    }
    for step_id in form_step_ids:
        if step_id not in candidates or "input_assist" not in candidates[step_id]:
            raise GateFailure(
                f"form action step does not allow input_assist: {step_id}"
            )

    lifecycle = _load_json(fixture_root / "lifecycle.json")
    companion_fixtures = set(lifecycle.get("companion_fixtures", []))
    expected_companions = {
        "sse.json",
        "step-registry.json",
        "form-actions.json",
        "chart-payloads.json",
    }
    if companion_fixtures != expected_companions:
        raise GateFailure("lifecycle fixture must name all required companion fixtures")
    for companion in companion_fixtures:
        if not (fixture_root / companion).is_file():
            raise GateFailure(f"lifecycle companion fixture is missing: {companion}")
    lifecycle_steps = set(_collect_key_values(lifecycle, "step_id"))
    unknown_lifecycle_steps = lifecycle_steps - supported - fallback_steps
    if unknown_lifecycle_steps:
        raise GateFailure(
            "lifecycle fixture contains unknown steps: "
            + ", ".join(sorted(unknown_lifecycle_steps))
        )

    chart = _load_json(fixture_root / "chart-payloads.json")
    representative = chart.get("chart_contract", {}).get("representative_step_id")
    if representative not in candidates or "chart_interpret" not in candidates[representative]:
        raise GateFailure("representative chart step must allow chart_interpret")
    ready = chart.get("cases", {}).get("lifetime_cashflow_ready", {})
    payload = ready.get("django_ai_request_fragment", {}).get("chart_payload")
    if not isinstance(payload, dict) or payload.get("chart_id") != representative:
        raise GateFailure("ready chart fixture must use the representative step ID")
    for point in payload.get("series", []):
        if point.get("cash_outflow", 1) > 0:
            raise GateFailure("chart cash_outflow must use a negative sign")
        if point.get("net_flow") != point.get("cash_inflow") + point.get("cash_outflow"):
            raise GateFailure("chart net_flow must equal inflow plus outflow")
    return len(supported), len(form_step_ids)


def check_rag_catalog() -> int:
    catalog = _load_json(REPOSITORY_ROOT / "ai-pjt/rag/data/catalog.json")
    documents = catalog.get("documents") if isinstance(catalog, dict) else None
    if not isinstance(documents, list) or not documents:
        raise GateFailure("RAG catalog must contain documents")

    for document in documents:
        relative_path = document.get("path")
        expected_hash = document.get("sha256")
        if not isinstance(relative_path, str) or not isinstance(expected_hash, str):
            raise GateFailure("RAG catalog document metadata is incomplete")
        path = REPOSITORY_ROOT / relative_path
        if not path.is_file():
            raise GateFailure(f"RAG catalog file is missing: {relative_path}")
        actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual_hash != expected_hash:
            raise GateFailure(f"RAG catalog hash mismatch: {relative_path}")
        if document.get("document_type") == "interface-guide":
            locator = document.get("source_locator", "")
            match = re.fullmatch(r"([^@]+)@([0-9a-f]{7,40})", locator)
            if match is None:
                raise GateFailure(f"RAG interface guide source is invalid: {locator}")
            source_path, source_commit = match.groups()
            result = subprocess.run(
                ["git", "diff", "--quiet", source_commit, "HEAD", "--", source_path],
                cwd=REPOSITORY_ROOT,
                check=False,
            )
            if result.returncode != 0:
                raise GateFailure(
                    f"RAG interface guide source changed after cataloging: {source_path}"
                )
    return len(documents)


def check_research_boundary() -> None:
    research_root = REPOSITORY_ROOT / "ai-pjt/experiments/asset-allocation"
    product_code = [
        path.relative_to(REPOSITORY_ROOT)
        for path in research_root.rglob("*")
        if path.is_file() and path.suffix in {".py", ".js", ".ts", ".vue"}
    ]
    if product_code:
        raise GateFailure(
            "P12 diagnosis-only boundary contains executable product code: "
            + ", ".join(map(str, product_code))
        )


def main() -> int:
    try:
        commits = check_manifest()
        status_counts = check_consistency_manifest()
        step_count, form_step_count = check_step_and_chart_contracts()
        document_count = check_rag_catalog()
        check_research_boundary()
    except GateFailure as exc:
        print(f"G1 foundation: FAIL: {exc}", file=sys.stderr)
        return 1

    print(
        "G1 foundation: PASS "
        f"({len(EXPECTED_INPUTS)} inputs, {len(commits)} commits, "
        f"status={status_counts}, {step_count} UI steps, "
        f"{form_step_count} form-action steps, {document_count} governed RAG documents)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
