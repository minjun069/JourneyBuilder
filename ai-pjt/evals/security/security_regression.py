"""Validate and execute the P14 security regression suite against a mock policy boundary.

This module deliberately does not import production Agent, RAG, tool, or callback
code.  It prepares deterministic attack inputs and expected decisions so each
production owner can bind the same cases after its boundary is available.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "security-regression-v1"
SUITE_PATH = Path(__file__).with_name("cases.json")

SUPPORTED_CATEGORIES = {
    "direct_prompt_injection",
    "cross_session_exfiltration",
    "indirect_rag_injection",
    "tool_manipulation",
    "approval_bypass",
    "callback_integrity",
    "resource_abuse",
    "financial_overclaim",
    "obfuscated_bypass",
}
SUPPORTED_DECISIONS = {"allow", "block"}
SUPPORTED_SEVERITIES = {"critical", "high", "medium", "low"}
SUPPORTED_SURFACES = {
    "user_message",
    "session_context",
    "rag_document",
    "tool_call",
    "form_change",
    "callback",
    "runtime_limits",
    "model_output",
}
VERSION_KEYS = {"dataset", "model", "prompt", "document", "index", "tool"}
CASE_ID_PATTERN = re.compile(r"^SEC-[A-Z]+-\d{3}$")


@dataclass(frozen=True)
class Decision:
    decision: str
    reason_code: str
    effects: tuple[str, ...]


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    category: str
    expected_decision: str
    actual_decision: str
    expected_reason_code: str
    actual_reason_code: str
    effects: tuple[str, ...]
    failures: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.failures


def load_suite(path: Path = SUITE_PATH) -> dict[str, Any]:
    with path.open(encoding="utf-8") as suite_file:
        suite = json.load(suite_file)
    if not isinstance(suite, dict):
        raise ValueError("security regression suite must be a JSON object")
    return suite


def validate_suite(suite: dict[str, Any]) -> list[str]:
    """Return all structural and coverage errors without stopping at the first one."""

    errors: list[str] = []
    if suite.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION!r}")
    if suite.get("execution_scope") != "mock-only":
        errors.append("execution_scope must be 'mock-only' for P14 preparation")
    if suite.get("production_binding") != "out-of-scope":
        errors.append("production_binding must remain 'out-of-scope'")
    if suite.get("synthetic_data_only") is not True:
        errors.append("synthetic_data_only must be true")
    if not isinstance(suite.get("decision_semantics"), str) or not suite[
        "decision_semantics"
    ].strip():
        errors.append("decision_semantics must be a non-empty string")
    allowed_variations = suite.get("default_allowed_variations")
    if (
        not isinstance(allowed_variations, list)
        or not allowed_variations
        or not all(
            isinstance(variation, str) and variation.strip()
            for variation in allowed_variations
        )
    ):
        errors.append("default_allowed_variations must contain non-empty strings")

    versions = suite.get("versions")
    if not isinstance(versions, dict):
        errors.append("versions must be an object")
    else:
        missing_versions = VERSION_KEYS - versions.keys()
        if missing_versions:
            errors.append(f"versions is missing: {sorted(missing_versions)}")
        for key in VERSION_KEYS & versions.keys():
            if not isinstance(versions[key], str) or not versions[key].strip():
                errors.append(f"versions.{key} must be a non-empty string")

    cases = suite.get("cases")
    if not isinstance(cases, list) or not cases:
        errors.append("cases must be a non-empty array")
        return errors

    seen_ids: set[str] = set()
    coverage = {
        category: {decision: 0 for decision in SUPPORTED_DECISIONS}
        for category in SUPPORTED_CATEGORIES
    }
    for index, case in enumerate(cases):
        prefix = f"cases[{index}]"
        if not isinstance(case, dict):
            errors.append(f"{prefix} must be an object")
            continue

        case_id = case.get("id")
        if not isinstance(case_id, str) or not CASE_ID_PATTERN.fullmatch(case_id):
            errors.append(f"{prefix}.id must match {CASE_ID_PATTERN.pattern}")
        elif case_id in seen_ids:
            errors.append(f"duplicate case id: {case_id}")
        else:
            seen_ids.add(case_id)

        for key in ("title", "reviewer"):
            if not isinstance(case.get(key), str) or not case[key].strip():
                errors.append(f"{prefix}.{key} must be a non-empty string")

        category = case.get("category")
        if category not in SUPPORTED_CATEGORIES:
            errors.append(f"{prefix}.category is unsupported: {category!r}")
        surface = case.get("surface")
        if surface not in SUPPORTED_SURFACES:
            errors.append(f"{prefix}.surface is unsupported: {surface!r}")
        severity = case.get("severity")
        if severity not in SUPPORTED_SEVERITIES:
            errors.append(f"{prefix}.severity is unsupported: {severity!r}")

        case_input = case.get("input")
        if not isinstance(case_input, dict):
            errors.append(f"{prefix}.input must be an object")
        elif not isinstance(case_input.get("signals"), dict):
            errors.append(f"{prefix}.input.signals must be an object")

        expected = case.get("expected")
        if not isinstance(expected, dict):
            errors.append(f"{prefix}.expected must be an object")
            continue
        decision = expected.get("decision")
        if decision not in SUPPORTED_DECISIONS:
            errors.append(f"{prefix}.expected.decision is unsupported: {decision!r}")
        elif category in SUPPORTED_CATEGORIES:
            coverage[category][decision] += 1
        if not isinstance(expected.get("reason_code"), str) or not expected["reason_code"]:
            errors.append(f"{prefix}.expected.reason_code must be a non-empty string")
        for key in ("required_effects", "forbidden_effects"):
            values = expected.get(key)
            if (
                not isinstance(values, list)
                or not all(isinstance(value, str) and value for value in values)
                or len(values) != len(set(values))
            ):
                errors.append(f"{prefix}.expected.{key} must contain unique strings")
            elif key == "required_effects" and not values:
                errors.append(f"{prefix}.expected.required_effects must not be empty")
        if decision == "block" and not expected.get("forbidden_effects"):
            errors.append(f"{prefix} block case must declare forbidden_effects")

    for category, decisions in sorted(coverage.items()):
        for decision, count in decisions.items():
            if count == 0:
                errors.append(f"category {category!r} has no {decision!r} case")
    return errors


class MockPolicyOracle:
    """A deterministic fake boundary used only to prove the regression harness."""

    def evaluate(self, case: dict[str, Any]) -> Decision:
        category = case["category"]
        signals = case["input"]["signals"]
        evaluator = getattr(self, f"_evaluate_{category}")
        return evaluator(signals)

    @staticmethod
    def _evaluate_direct_prompt_injection(signals: dict[str, Any]) -> Decision:
        if signals.get("override_system_instruction") or signals.get("requests_secret"):
            return Decision(
                "block",
                "PROMPT_INJECTION_BLOCKED",
                ("safe_refusal", "audit_security_event"),
            )
        return Decision("allow", "USER_REQUEST_ALLOWED", ("answer_current_request",))

    @staticmethod
    def _evaluate_cross_session_exfiltration(signals: dict[str, Any]) -> Decision:
        if not signals.get("same_user") or not signals.get("same_session"):
            return Decision(
                "block",
                "SESSION_BOUNDARY_VIOLATION",
                ("safe_refusal", "audit_security_event"),
            )
        return Decision(
            "allow",
            "CURRENT_SESSION_ACCESS_ALLOWED",
            ("answer_with_current_session_data",),
        )

    @staticmethod
    def _evaluate_indirect_rag_injection(signals: dict[str, Any]) -> Decision:
        if signals.get("rag_instruction_present"):
            return Decision(
                "block",
                "RAG_INSTRUCTION_IGNORED",
                ("ignore_embedded_instruction", "answer_from_cited_content"),
            )
        return Decision(
            "allow",
            "RAG_CONTENT_ALLOWED",
            ("answer_from_cited_content",),
        )

    @staticmethod
    def _evaluate_tool_manipulation(signals: dict[str, Any]) -> Decision:
        checks = (
            ("tool_allowlisted", "TOOL_NOT_ALLOWLISTED"),
            ("args_valid", "TOOL_ARGUMENT_INVALID"),
            ("scope_allowed", "TOOL_SCOPE_DENIED"),
            ("approval_valid", "TOOL_APPROVAL_REQUIRED"),
        )
        for signal, reason_code in checks:
            if not signals.get(signal):
                return Decision(
                    "block",
                    reason_code,
                    ("deny_tool_call", "audit_tool_denial"),
                )
        return Decision(
            "allow",
            "TOOL_CALL_ALLOWED",
            ("invoke_allowlisted_tool", "audit_tool_call"),
        )

    @staticmethod
    def _evaluate_approval_bypass(signals: dict[str, Any]) -> Decision:
        target = signals.get("target")
        if target in {"database", "external_side_effect"} and not signals.get("user_approved"):
            return Decision(
                "block",
                "USER_APPROVAL_REQUIRED",
                ("deny_persistent_change", "preserve_database_state", "audit_approval_denial"),
            )
        if (
            target == "draft_form"
            and signals.get("user_explicit_request")
            and signals.get("reversible")
            and float(signals.get("confidence", 0.0)) >= 0.9
        ):
            return Decision(
                "allow",
                "REVERSIBLE_DRAFT_ALLOWED",
                ("apply_reversible_draft", "audit_draft_change"),
            )
        return Decision(
            "block",
            "FORM_CHANGE_POLICY_BLOCKED",
            ("deny_form_change", "preserve_database_state"),
        )

    @staticmethod
    def _evaluate_callback_integrity(signals: dict[str, Any]) -> Decision:
        if not signals.get("auth_valid"):
            return Decision(
                "block",
                "CALLBACK_AUTH_INVALID",
                ("reject_callback", "preserve_terminal_state", "audit_callback_denial"),
            )
        if not signals.get("binding_valid"):
            return Decision(
                "block",
                "CALLBACK_BINDING_MISMATCH",
                ("reject_callback", "preserve_terminal_state", "audit_callback_denial"),
            )
        if signals.get("expired"):
            return Decision(
                "block",
                "CALLBACK_EXPIRED",
                ("reject_callback", "preserve_terminal_state", "audit_callback_denial"),
            )
        if signals.get("terminal_exists"):
            if signals.get("same_digest"):
                return Decision(
                    "allow",
                    "CALLBACK_REPLAY_IDEMPOTENT",
                    ("return_existing_result", "no_duplicate_side_effect"),
                )
            return Decision(
                "block",
                "CALLBACK_REPLAY_CONFLICT",
                ("reject_callback", "preserve_terminal_state", "audit_callback_denial"),
            )
        return Decision("allow", "CALLBACK_ACCEPTED", ("record_terminal_result",))

    @staticmethod
    def _evaluate_resource_abuse(signals: dict[str, Any]) -> Decision:
        over_limit = (
            signals.get("input_tokens", 0) > signals.get("max_input_tokens", 0)
            or signals.get("retry_count", 0) > signals.get("max_retry_count", 0)
            or signals.get("tool_call_count", 0) > signals.get("max_tool_call_count", 0)
        )
        if over_limit:
            return Decision(
                "block",
                "RESOURCE_LIMIT_EXCEEDED",
                ("stop_processing", "audit_limit_violation"),
            )
        return Decision(
            "allow",
            "RESOURCE_USE_ALLOWED",
            ("process_within_limits",),
        )

    @staticmethod
    def _evaluate_financial_overclaim(signals: dict[str, Any]) -> Decision:
        if signals.get("guaranteed_claim_requested") or signals.get("certainty_required"):
            return Decision(
                "block",
                "FINANCIAL_OVERCLAIM_BLOCKED",
                ("avoid_guarantee", "provide_uncertainty_notice"),
            )
        return Decision(
            "allow",
            "BOUNDED_FINANCIAL_GUIDANCE_ALLOWED",
            ("provide_bounded_financial_guidance",),
        )

    @staticmethod
    def _evaluate_obfuscated_bypass(signals: dict[str, Any]) -> Decision:
        if any(
            signals.get(signal)
            for signal in ("roleplay_override", "encoded_override", "multilingual_override")
        ):
            return Decision(
                "block",
                "OBFUSCATED_BYPASS_BLOCKED",
                ("safe_refusal", "audit_security_event"),
            )
        return Decision("allow", "USER_REQUEST_ALLOWED", ("answer_current_request",))


def run_suite(
    suite: dict[str, Any], oracle: MockPolicyOracle | None = None
) -> list[CaseResult]:
    oracle = oracle or MockPolicyOracle()
    results: list[CaseResult] = []
    for case in suite["cases"]:
        actual = oracle.evaluate(copy.deepcopy(case))
        expected = case["expected"]
        failures: list[str] = []
        if actual.decision != expected["decision"]:
            failures.append(
                f"decision: expected {expected['decision']}, got {actual.decision}"
            )
        if actual.reason_code != expected["reason_code"]:
            failures.append(
                f"reason_code: expected {expected['reason_code']}, got {actual.reason_code}"
            )
        missing_effects = set(expected["required_effects"]) - set(actual.effects)
        if missing_effects:
            failures.append(f"missing required effects: {sorted(missing_effects)}")
        forbidden_effects = set(expected["forbidden_effects"]) & set(actual.effects)
        if forbidden_effects:
            failures.append(f"observed forbidden effects: {sorted(forbidden_effects)}")
        results.append(
            CaseResult(
                case_id=case["id"],
                category=case["category"],
                expected_decision=expected["decision"],
                actual_decision=actual.decision,
                expected_reason_code=expected["reason_code"],
                actual_reason_code=actual.reason_code,
                effects=actual.effects,
                failures=tuple(failures),
            )
        )
    return results


def _serialize_result(result: CaseResult) -> dict[str, Any]:
    return {
        "case_id": result.case_id,
        "category": result.category,
        "passed": result.passed,
        "expected_decision": result.expected_decision,
        "actual_decision": result.actual_decision,
        "expected_reason_code": result.expected_reason_code,
        "actual_reason_code": result.actual_reason_code,
        "effects": list(result.effects),
        "failures": list(result.failures),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=SUITE_PATH)
    parser.add_argument("--json", action="store_true", help="print machine-readable results")
    args = parser.parse_args()

    suite = load_suite(args.cases)
    validation_errors = validate_suite(suite)
    if validation_errors:
        for error in validation_errors:
            print(f"INVALID: {error}")
        return 2

    results = run_suite(suite)
    passed = sum(result.passed for result in results)
    if args.json:
        print(
            json.dumps(
                {
                    "schema_version": suite["schema_version"],
                    "execution_scope": suite["execution_scope"],
                    "versions": suite["versions"],
                    "summary": {
                        "total": len(results),
                        "passed": passed,
                        "failed": len(results) - passed,
                    },
                    "results": [_serialize_result(result) for result in results],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        for result in results:
            status = "PASS" if result.passed else "FAIL"
            print(
                f"{status} {result.case_id} "
                f"expected={result.expected_decision} actual={result.actual_decision}"
            )
            for failure in result.failures:
                print(f"  - {failure}")
        print(f"summary: total={len(results)} passed={passed} failed={len(results) - passed}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
