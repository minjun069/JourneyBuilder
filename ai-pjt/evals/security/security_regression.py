"""Run the F13 corpus against production boundary policies and contracts."""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


AI_ROOT = Path(__file__).resolve().parents[2]
if str(AI_ROOT) not in sys.path:
    sys.path.insert(0, str(AI_ROOT))

from core.step_registry import load_step_candidates  # noqa: E402
from core.masking import MaskingSink  # noqa: E402
from core.observability_masking import (  # noqa: E402
    FakeSerializedInstrumentationSink,
    build_masked_runtime_instrumentation,
)
from graph.task_contracts import (  # noqa: E402
    BindingResolutionError,
    resolve_task_bindings,
    seal_validated_artifact,
    validate_task_result,
)
from guardrails.boundary_policy import (  # noqa: E402
    BoundaryDecision,
    evaluate_callback_payload,
    evaluate_financial_output,
    evaluate_rag_content,
    evaluate_resource_usage,
    evaluate_session_access,
    evaluate_user_message,
)
from guardrails.plan_policy import evaluate_route_plan  # noqa: E402
from tools.catalog import default_tool_definitions  # noqa: E402
from tools.contracts import ToolAuditEvent  # noqa: E402
from tools.output_validation import validate_form_update_contract  # noqa: E402
from tools.registry import validate_json_schema  # noqa: E402


SCHEMA_VERSION = "security-regression-v3"
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
    "plan_integrity",
    "dependency_integrity",
    "binding_integrity",
    "route_authorization",
    "task_identity",
    "artifact_integrity",
    "observability_integrity",
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
    "route_plan",
    "task_execution",
    "task_result",
    "artifact",
    "trace_export",
}
SUPPORTED_INPUT_KINDS = {"production_payload"}
SUPPORTED_EVALUATORS = {"production_policy"}
EVALUATOR_INPUT_KINDS = {
    "production_policy": "production_payload",
}
VERSION_KEYS = {"dataset", "model", "prompt", "document", "index", "tool"}
CASE_ID_PATTERN = re.compile(r"^SEC-[A-Z]+-\d{3}$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SHARED_DOC_SOURCE_PATTERN = re.compile(r"^shared-docs:[^#]+\.md#[^#]+$")


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
    if suite.get("suite_id") != "AI-F13-T03":
        errors.append("suite_id must be 'AI-F13-T03'")
    if suite.get("foundation_suite_id") != "AI-P14-D13":
        errors.append("foundation_suite_id must preserve 'AI-P14-D13'")
    baseline_commit = suite.get("baseline_commit")
    if (
        not isinstance(baseline_commit, str)
        or COMMIT_PATTERN.fullmatch(baseline_commit) is None
    ):
        errors.append("baseline_commit must be a 40-character lowercase commit SHA")
    if suite.get("execution_scope") != "production-boundaries":
        errors.append("execution_scope must be 'production-boundaries'")
    if suite.get("production_binding") != (
        "integrated-input-plan-rag-tool-artifact-output-observability"
    ):
        errors.append("production_binding must name the integrated boundaries")
    if suite.get("synthetic_data_only") is not True:
        errors.append("synthetic_data_only must be true")
    for source_name in ("policy_source", "requirements_source"):
        source = suite.get(source_name)
        if not isinstance(source, str) or SHARED_DOC_SOURCE_PATTERN.fullmatch(source) is None:
            errors.append(
                f"{source_name} must reference the external shared-docs namespace"
            )
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

        for key in ("title",):
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

        input_kind = case.get("input_kind")
        if input_kind not in SUPPORTED_INPUT_KINDS:
            errors.append(f"{prefix}.input_kind is unsupported: {input_kind!r}")
        evaluator = case.get("evaluator")
        if evaluator not in SUPPORTED_EVALUATORS:
            errors.append(f"{prefix}.evaluator is unsupported: {evaluator!r}")
        elif input_kind in SUPPORTED_INPUT_KINDS and (
            EVALUATOR_INPUT_KINDS[evaluator] != input_kind
        ):
            errors.append(
                f"{prefix}.evaluator {evaluator!r} does not accept "
                f"input_kind {input_kind!r}"
            )

        case_input = case.get("input")
        if not isinstance(case_input, dict):
            errors.append(f"{prefix}.input must be an object")
        elif not isinstance(case_input.get("payload"), dict):
            errors.append(f"{prefix}.input.payload must be an object")

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


class ProductionSecurityEvaluator:
    """Drive corpus payloads through the same policies used by production."""

    _PLAN_CATEGORIES = {
        "plan_integrity",
        "dependency_integrity",
        "binding_integrity",
        "route_authorization",
    }

    def __init__(self) -> None:
        self._step_candidates = load_step_candidates()
        self._tools = {
            definition.name: definition
            for definition in default_tool_definitions()
        }

    def evaluate(self, case: dict[str, Any]) -> Decision:
        category = case["category"]
        case_input = case["input"]
        payload = copy.deepcopy(case_input["payload"])
        if category in {"direct_prompt_injection", "obfuscated_bypass"}:
            return _from_boundary(evaluate_user_message(payload.get("message", "")))
        if category == "cross_session_exfiltration":
            return _from_boundary(evaluate_session_access(payload))
        if category == "indirect_rag_injection":
            return _from_boundary(evaluate_rag_content(payload.get("content", "")))
        if category == "tool_manipulation":
            return self._evaluate_tool(payload)
        if category == "approval_bypass":
            return self._evaluate_form_change(payload)
        if category == "callback_integrity":
            return _from_boundary(evaluate_callback_payload(payload))
        if category == "resource_abuse":
            return _from_boundary(evaluate_resource_usage(payload))
        if category == "financial_overclaim":
            return _from_boundary(evaluate_financial_output(payload.get("output")))
        if category == "task_identity":
            return self._evaluate_task_result(payload)
        if category == "artifact_integrity":
            return self._evaluate_artifact(payload)
        if category == "observability_integrity":
            return self._evaluate_observability(payload)
        if category in self._PLAN_CATEGORIES:
            if case_input.get("mode") == "dependency_execution":
                return self._evaluate_failed_dependency(payload)
            return self._evaluate_plan(case_input, payload)
        raise ValueError(f"unsupported production security category: {category}")

    def _evaluate_plan(
        self,
        case_input: dict[str, Any],
        payload: dict[str, Any],
    ) -> Decision:
        result = evaluate_route_plan(
            payload,
            step_id=case_input["step_id"],
            step_candidates=self._step_candidates,
            registered_tool_capabilities=tuple(
                case_input.get("registered_tool_capabilities", ())
            ),
        )
        effects = (
            ("execute_validated_plan",)
            if result.allowed
            else ("deny_plan_execution", "audit_security_event")
        )
        return Decision(result.decision, result.reason_code, effects)

    def _evaluate_tool(self, payload: dict[str, Any]) -> Decision:
        definition = self._tools.get(str(payload.get("tool_name")))
        denied_effects = ("deny_tool_call", "audit_tool_denial")
        if definition is None:
            return Decision("block", "TOOL_NOT_ALLOWLISTED", denied_effects)
        if validate_json_schema(definition.input_schema, payload.get("arguments")):
            return Decision("block", "INVALID_TOOL_ARGUMENTS", denied_effects)
        if (
            payload.get("agent") not in definition.allowed_agents
            or payload.get("task_type") not in definition.allowed_task_types
        ):
            return Decision("block", "TOOL_FORBIDDEN", denied_effects)
        return Decision(
            "allow",
            "TOOL_CALL_ALLOWED",
            ("invoke_allowlisted_tool", "audit_tool_call"),
        )

    @staticmethod
    def _evaluate_form_change(payload: dict[str, Any]) -> Decision:
        result = validate_form_update_contract(payload)
        if not result.valid:
            return Decision(
                "block",
                result.issues[0].code,
                (
                    "deny_persistent_change",
                    "preserve_database_state",
                    "audit_approval_denial",
                ),
            )
        return Decision(
            "allow",
            "REVERSIBLE_DRAFT_ALLOWED",
            ("apply_reversible_draft", "audit_draft_change"),
        )

    @staticmethod
    def _evaluate_failed_dependency(payload: dict[str, Any]) -> Decision:
        try:
            resolve_task_bindings(
                task=payload["task"],
                task_results=payload["task_results"],
                expectations=payload["expectations"],
                artifact_seals={},
            )
        except BindingResolutionError as exc:
            return Decision(
                "block",
                exc.code,
                ("block_consumer_task", "audit_security_event"),
            )
        return Decision("allow", "BINDING_RESOLVED", ("invoke_declared_consumer",))

    @staticmethod
    def _evaluate_task_result(payload: dict[str, Any]) -> Decision:
        result = validate_task_result(
            task=payload["task"],
            result=payload["result"],
            step_id=payload["step_id"],
        )
        if not result.valid:
            return Decision(
                "block",
                result.issues[0].code,
                ("mark_task_failed", "discard_untrusted_artifact"),
            )
        return Decision(
            "allow",
            "TASK_RESULT_ALLOWED",
            ("publish_validated_artifact",),
        )

    @staticmethod
    def _evaluate_artifact(payload: dict[str, Any]) -> Decision:
        validation = validate_task_result(
            task=payload["producer_task"],
            result=payload["validated_result"],
            step_id=payload["step_id"],
        )
        if not validation.valid:
            return Decision(
                "block",
                validation.issues[0].code,
                ("block_consumer_task", "audit_artifact_denial"),
            )
        seal = seal_validated_artifact(validation)
        try:
            resolve_task_bindings(
                task=payload["consumer_task"],
                task_results={seal.task_id: payload["current_result"]},
                expectations=payload["expectations"],
                artifact_seals={seal.task_id: seal},
            )
        except BindingResolutionError as exc:
            return Decision(
                "block",
                exc.code,
                ("block_consumer_task", "audit_artifact_denial"),
            )
        return Decision(
            "allow",
            "ARTIFACT_BINDING_ALLOWED",
            ("resolve_validated_binding",),
        )

    @staticmethod
    def _evaluate_observability(payload: dict[str, Any]) -> Decision:
        event_payload = payload.get("event", {})
        if not isinstance(event_payload, dict):
            return Decision(
                "block",
                "UNSAFE_AUDIT_FIELD_REJECTED",
                ("reject_untyped_audit", "preserve_trace_safety"),
            )
        normalized = dict(event_payload)
        argument_classes = normalized.get("argument_classes")
        if isinstance(argument_classes, list):
            normalized["argument_classes"] = tuple(argument_classes)
        try:
            event = ToolAuditEvent(**normalized)
        except TypeError:
            return Decision(
                "block",
                "UNSAFE_AUDIT_FIELD_REJECTED",
                ("reject_untyped_audit", "preserve_trace_safety"),
            )
        downstream = FakeSerializedInstrumentationSink()
        instrumentation, sink = build_masked_runtime_instrumentation(
            pseudonym_key=b"f13-security-regression-key-32bytes",
            downstream=downstream,
            sink=MaskingSink.TRACE_EXPORT,
        )
        instrumentation.bind(
            request_id=event.request_id,
            thread_id="thread-observability",
            plan_id=event.plan_id,
            step_id=event.step_id,
        ).record_tool_audit(event)
        rendered = json.dumps(downstream.payloads, ensure_ascii=False, sort_keys=True)
        if sink.dropped_exports or "SYNTHETIC_RAW_SECRET" in rendered:
            return Decision(
                "block",
                "AUDIT_MASKING_FAILED",
                ("reject_untyped_audit", "preserve_trace_safety"),
            )
        return Decision(
            "allow",
            "AUDIT_EVENT_MASKED",
            ("export_masked_audit", "preserve_trace_safety"),
        )


def _from_boundary(decision: BoundaryDecision) -> Decision:
    return Decision(decision.decision, decision.reason_code, decision.effects)


def run_suite(
    suite: dict[str, Any],
    evaluators: dict[str, Any] | None = None,
) -> list[CaseResult]:
    active_evaluators = (
        {
            "production_policy": ProductionSecurityEvaluator(),
        }
        if evaluators is None
        else evaluators
    )
    results: list[CaseResult] = []
    for case in suite["cases"]:
        evaluator = active_evaluators[case["evaluator"]]
        actual = evaluator.evaluate(copy.deepcopy(case))
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


def build_report(suite: dict[str, Any], results: list[CaseResult]) -> dict[str, Any]:
    passed = sum(result.passed for result in results)
    return {
        "schema_version": suite["schema_version"],
        "suite_id": suite["suite_id"],
        "foundation_suite_id": suite["foundation_suite_id"],
        "execution_scope": suite["execution_scope"],
        "production_binding": suite["production_binding"],
        "evaluator": "production_policy",
        "versions": suite["versions"],
        "summary": {
            "total": len(results),
            "passed": passed,
            "failed": len(results) - passed,
        },
        "results": [_serialize_result(result) for result in results],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=SUITE_PATH)
    parser.add_argument("--json", action="store_true", help="print machine-readable results")
    parser.add_argument("--output", type=Path, help="write the JSON report artifact")
    args = parser.parse_args()

    suite = load_suite(args.cases)
    validation_errors = validate_suite(suite)
    if validation_errors:
        for error in validation_errors:
            print(f"INVALID: {error}")
        return 2

    results = run_suite(suite)
    passed = sum(result.passed for result in results)
    report = build_report(suite, results)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
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
