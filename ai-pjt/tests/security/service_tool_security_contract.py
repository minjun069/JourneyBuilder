"""Consumer-neutral F12 service and tool security attack contract.

F12-T01 freezes synthetic inputs and observable outcomes.  It deliberately
does not import production transports, graph executors, or tool handlers; the
F12-T02~T04 owners bind the same cases to those boundaries.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "f12-service-tool-security-v1"
CONTRACT_VERSION = "2.0"
WORK_ID = "F12-T01"
LANE = "W1-L13"

SECURITY_DIRECTORY = Path(__file__).resolve().parent
SUITE_PATH = SECURITY_DIRECTORY / "service-tool-security-cases.json"
REPOSITORY_ROOT = SECURITY_DIRECTORY.parents[2]

REQUIRED_CASE_GROUPS = frozenset(
    {
        "service_bearer",
        "terminal_integrity",
        "replay_idempotency",
        "request_ownership",
        "task_identity",
        "dependency_integrity",
        "binding_integrity",
        "artifact_integrity",
        "tool_authorization",
    }
)
SUPPORTED_BOUNDARIES = frozenset(
    {
        "ai_sse_auth",
        "browser_session",
        "browser_sse",
        "django_terminal_receipt",
        "route_plan_validation",
        "task_result_validation",
        "binding_resolution",
        "artifact_validation",
        "tool_runtime_authorization",
    }
)
SUPPORTED_DECISIONS = frozenset({"allow", "block"})
DOWNSTREAM_OWNERS = frozenset({"F12-T02", "F12-T03", "F12-T04"})
CASE_ID_PATTERN = re.compile(r"^F12-[A-Z]+-\d{3}$")


def load_suite(path: Path = SUITE_PATH) -> dict[str, Any]:
    with path.open(encoding="utf-8") as suite_file:
        suite = json.load(suite_file)
    if not isinstance(suite, dict):
        raise ValueError("F12 security suite must be a JSON object")
    return suite


def load_lifecycle_fixture(suite: dict[str, Any]) -> dict[str, Any]:
    relative_path = suite.get("lifecycle_fixture")
    if not isinstance(relative_path, str) or not relative_path:
        raise ValueError("lifecycle_fixture must be a repository-relative path")
    fixture_path = REPOSITORY_ROOT / relative_path
    with fixture_path.open(encoding="utf-8") as fixture_file:
        fixture = json.load(fixture_file)
    if not isinstance(fixture, dict):
        raise ValueError("lifecycle fixture must be a JSON object")
    return fixture


def cases_for_group(
    suite: dict[str, Any],
    group: str,
) -> tuple[dict[str, Any], ...]:
    return tuple(case for case in suite.get("cases", ()) if case.get("group") == group)


def cases_for_owner(
    suite: dict[str, Any],
    owner: str,
) -> tuple[dict[str, Any], ...]:
    return tuple(
        case
        for case in suite.get("cases", ())
        if case.get("downstream_owner") == owner
    )


def validate_suite(
    suite: dict[str, Any],
    lifecycle: dict[str, Any] | None = None,
) -> list[str]:
    """Return every structural or lifecycle-alignment error in stable order."""

    errors: list[str] = []
    if suite.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION!r}")
    if suite.get("contract_version") != CONTRACT_VERSION:
        errors.append(f"contract_version must be {CONTRACT_VERSION!r}")
    if suite.get("work_id") != WORK_ID:
        errors.append(f"work_id must be {WORK_ID!r}")
    if suite.get("lane") != LANE:
        errors.append(f"lane must be {LANE!r}")
    if suite.get("synthetic_data_only") is not True:
        errors.append("synthetic_data_only must be true")
    if not isinstance(suite.get("decision_semantics"), str) or not suite[
        "decision_semantics"
    ].strip():
        errors.append("decision_semantics must be a non-empty string")

    declared_groups = suite.get("required_case_groups")
    if not isinstance(declared_groups, list):
        errors.append("required_case_groups must be an array")
    elif set(declared_groups) != REQUIRED_CASE_GROUPS:
        errors.append("required_case_groups must match the F12-T01 attack scope")

    cases = suite.get("cases")
    if not isinstance(cases, list) or not cases:
        errors.append("cases must be a non-empty array")
        return errors

    seen_ids: set[str] = set()
    group_decisions = {
        group: {decision: 0 for decision in SUPPORTED_DECISIONS}
        for group in REQUIRED_CASE_GROUPS
    }
    for index, case in enumerate(cases):
        prefix = f"cases[{index}]"
        if not isinstance(case, dict):
            errors.append(f"{prefix} must be an object")
            continue

        case_id = case.get("id")
        if not isinstance(case_id, str) or CASE_ID_PATTERN.fullmatch(case_id) is None:
            errors.append(f"{prefix}.id must match {CASE_ID_PATTERN.pattern}")
        elif case_id in seen_ids:
            errors.append(f"duplicate case id: {case_id}")
        else:
            seen_ids.add(case_id)

        if not isinstance(case.get("title"), str) or not case["title"].strip():
            errors.append(f"{prefix}.title must be a non-empty string")
        group = case.get("group")
        if group not in REQUIRED_CASE_GROUPS:
            errors.append(f"{prefix}.group is unsupported: {group!r}")
        boundary = case.get("boundary")
        if boundary not in SUPPORTED_BOUNDARIES:
            errors.append(f"{prefix}.boundary is unsupported: {boundary!r}")
        owner = case.get("downstream_owner")
        if owner not in DOWNSTREAM_OWNERS:
            errors.append(f"{prefix}.downstream_owner is unsupported: {owner!r}")
        if not isinstance(case.get("input"), dict):
            errors.append(f"{prefix}.input must be an object")

        expected = case.get("expected")
        if not isinstance(expected, dict):
            errors.append(f"{prefix}.expected must be an object")
            continue
        decision = expected.get("decision")
        if decision not in SUPPORTED_DECISIONS:
            errors.append(f"{prefix}.expected.decision is unsupported: {decision!r}")
        elif group in group_decisions:
            group_decisions[group][decision] += 1
        if not isinstance(expected.get("reason_code"), str) or not expected[
            "reason_code"
        ].strip():
            errors.append(f"{prefix}.expected.reason_code must be a non-empty string")
        http_status = expected.get("http_status")
        if http_status is not None and (
            isinstance(http_status, bool)
            or not isinstance(http_status, int)
            or not 100 <= http_status <= 599
        ):
            errors.append(f"{prefix}.expected.http_status must be a valid status")
        for effects_key in ("required_effects", "forbidden_effects"):
            effects = expected.get(effects_key)
            if (
                not isinstance(effects, list)
                or not effects
                or not all(isinstance(effect, str) and effect for effect in effects)
                or len(effects) != len(set(effects))
            ):
                errors.append(
                    f"{prefix}.expected.{effects_key} must contain unique strings"
                )

    for group, decisions in sorted(group_decisions.items()):
        if decisions["block"] == 0:
            errors.append(f"group {group!r} has no block case")
    if not any(decisions["allow"] for decisions in group_decisions.values()):
        errors.append("suite must contain at least one allow control case")
    for owner in sorted(DOWNSTREAM_OWNERS):
        if not cases_for_owner(suite, owner):
            errors.append(f"downstream owner {owner!r} has no case")

    if lifecycle is None:
        try:
            lifecycle = load_lifecycle_fixture(suite)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"lifecycle fixture cannot be loaded: {exc}")
            lifecycle = None
    if lifecycle is not None:
        errors.extend(_validate_lifecycle_alignment(suite, lifecycle))

    errors.extend(_validate_synthetic_credentials(suite))
    return errors


def _validate_lifecycle_alignment(
    suite: dict[str, Any],
    lifecycle: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    if lifecycle.get("contract_version") != suite.get("contract_version"):
        errors.append("lifecycle and F12 contract versions must match")
    lifecycle_cases = lifecycle.get("cases")
    if not isinstance(lifecycle_cases, dict):
        return [*errors, "lifecycle cases must be an object"]

    for case in suite["cases"]:
        source_name = case.get("source_fixture_case")
        if source_name is None:
            continue
        source = lifecycle_cases.get(source_name)
        if not isinstance(source, dict):
            errors.append(f"{case['id']} references unknown lifecycle case {source_name!r}")
            continue
        expected = case["expected"]
        source_response = source.get("response")
        if isinstance(source_response, dict) and "http_status" in expected:
            if source_response.get("status") != expected["http_status"]:
                errors.append(f"{case['id']} http status differs from {source_name}")
            source_error = source_response.get("body", {}).get("error", {})
            source_code = source_error.get("code")
            if source_code is not None and source_code != expected.get("reason_code"):
                errors.append(f"{case['id']} reason code differs from {source_name}")
        source_expected = source.get("expected")
        if (
            isinstance(source_expected, dict)
            and "http_status" in expected
            and source_expected.get("response_status") is not None
            and source_expected["response_status"] != expected["http_status"]
        ):
            errors.append(f"{case['id']} replay status differs from {source_name}")
    return errors


def _validate_synthetic_credentials(suite: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for case in suite["cases"]:
        authorization = case.get("input", {}).get("authorization")
        if authorization is None:
            continue
        if not isinstance(authorization, str) or not re.fullmatch(
            r"Bearer <[A-Z0-9_]+>", authorization
        ):
            errors.append(f"{case['id']} authorization must use a placeholder")
    return errors
