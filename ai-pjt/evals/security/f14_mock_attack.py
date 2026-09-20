"""Execute F14 integrated mock attacks and emit reproducible findings."""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
from typing import Any


AI_ROOT = Path(__file__).resolve().parents[2]
if str(AI_ROOT) not in sys.path:
    sys.path.insert(0, str(AI_ROOT))

from core.runtime_instrumentation import FakeInstrumentationSink, InstrumentationPort  # noqa: E402
from core.runtime_instrumentation_adapter import RuntimeInstrumentationAdapter  # noqa: E402
from graph.executor import execute_route_plan  # noqa: E402
from security_regression import (  # noqa: E402
    ProductionSecurityEvaluator,
    load_suite as load_f13_suite,
)


SCHEMA_VERSION = "f14-attack-assessment-v1"
SUITE_PATH = Path(__file__).with_name("f14-cases.json")
CASE_ID_PATTERN = re.compile(r"^F14-ATK-\d{3}$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SCENARIOS = {"f13_production_case", "dependency_executor"}


@dataclass(frozen=True)
class AttackResult:
    case_id: str
    title: str
    category: str
    severity: str
    reproduction_input: dict[str, Any]
    expected: dict[str, Any]
    actual: dict[str, Any]
    impact_boundary: str
    owner_paths: tuple[str, ...]
    mismatches: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.mismatches


def load_suite(path: Path = SUITE_PATH) -> dict[str, Any]:
    with path.open(encoding="utf-8") as suite_file:
        suite = json.load(suite_file)
    if not isinstance(suite, dict):
        raise ValueError("F14 attack suite must be a JSON object")
    return suite


def validate_suite(suite: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if suite.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION!r}")
    if suite.get("suite_id") != "AI-F14-T01":
        errors.append("suite_id must be 'AI-F14-T01'")
    if COMMIT_PATTERN.fullmatch(str(suite.get("baseline_commit", ""))) is None:
        errors.append("baseline_commit must be a 40-character lowercase commit SHA")
    if suite.get("execution_scope") != "integrated-production-contracts":
        errors.append("execution_scope must be 'integrated-production-contracts'")
    if suite.get("synthetic_data_only") is not True:
        errors.append("synthetic_data_only must be true")

    cases = suite.get("cases")
    if not isinstance(cases, list) or not cases:
        errors.append("cases must be a non-empty array")
        return errors
    seen: set[str] = set()
    for index, case in enumerate(cases):
        prefix = f"cases[{index}]"
        if not isinstance(case, dict):
            errors.append(f"{prefix} must be an object")
            continue
        case_id = case.get("id")
        if not isinstance(case_id, str) or CASE_ID_PATTERN.fullmatch(case_id) is None:
            errors.append(f"{prefix}.id must match {CASE_ID_PATTERN.pattern}")
        elif case_id in seen:
            errors.append(f"duplicate case id: {case_id}")
        else:
            seen.add(case_id)
        for key in ("title", "category", "severity", "impact_boundary"):
            if not isinstance(case.get(key), str) or not case[key].strip():
                errors.append(f"{prefix}.{key} must be a non-empty string")
        if case.get("scenario") not in SCENARIOS:
            errors.append(f"{prefix}.scenario is unsupported: {case.get('scenario')!r}")
        if not isinstance(case.get("input"), dict):
            errors.append(f"{prefix}.input must be an object")
        if not isinstance(case.get("expected"), dict) or not case["expected"]:
            errors.append(f"{prefix}.expected must be a non-empty object")
        owner_paths = case.get("owner_paths")
        if (
            not isinstance(owner_paths, list)
            or not owner_paths
            or not all(isinstance(path, str) and path for path in owner_paths)
        ):
            errors.append(f"{prefix}.owner_paths must contain repository paths")
    return errors


def _instrumentation() -> RuntimeInstrumentationAdapter:
    return RuntimeInstrumentationAdapter(InstrumentationPort(FakeInstrumentationSink()))


def _reference_task() -> dict[str, Any]:
    return {
        "id": "task-1",
        "type": "lookup_reference_value",
        "description": "승인 기준값 조회",
        "depends_on": [],
        "bindings": {},
        "executor_kind": "tool",
        "produces": "reference_value",
        "route": {
            "task_id": "task-1",
            "executor_kind": "tool",
            "executor": "lookup_reference_value",
            "reason_code": "capability_match",
        },
    }


def _dependent_task() -> dict[str, Any]:
    return {
        "id": "task-2",
        "type": "propose_form_change",
        "description": "조회값을 폼에 제안",
        "depends_on": ["task-1"],
        "bindings": {
            "value": {"task_id": "task-1", "path": "value"},
            "unit": {"task_id": "task-1", "path": "unit"},
        },
        "executor_kind": "agent",
        "produces": "form_change",
        "route": {
            "task_id": "task-2",
            "executor_kind": "agent",
            "executor": "input_assist",
            "reason_code": "intent_match",
        },
    }


def _independent_task() -> dict[str, Any]:
    return {
        "id": "task-3",
        "type": "answer_question",
        "description": "독립 질문",
        "depends_on": [],
        "bindings": {},
        "executor_kind": "agent",
        "produces": "text_response",
        "route": {
            "task_id": "task-3",
            "executor_kind": "agent",
            "executor": "qa",
            "reason_code": "intent_match",
        },
    }


async def _dependency_executor_scenario() -> dict[str, Any]:
    calls = {"dependent": 0, "independent": 0}

    async def failing_lookup(_: dict[str, Any]) -> dict[str, Any]:
        return {"task_id": "task-1", "status": "failed", "reason_code": "provider_timeout"}

    async def dependent(_: dict[str, Any]) -> dict[str, Any]:
        calls["dependent"] += 1
        return {"task_id": "task-2", "status": "failed", "reason_code": "unexpected_call"}

    async def independent(_: dict[str, Any]) -> dict[str, Any]:
        calls["independent"] += 1
        return {
            "task_id": "task-3",
            "status": "completed",
            "artifact": {"type": "text_response", "text": "독립 응답"},
        }

    report = await execute_route_plan(
        plan={"tasks": [
            _reference_task(),
            _dependent_task(),
            _independent_task(),
        ]},
        request_id="request-f14-dependency",
        step_id="financial-goals",
        customer_info={},
        agent_executors={"input_assist": dependent, "qa": independent},
        tool_executors={"lookup_reference_value": failing_lookup},
        instrumentation=_instrumentation().bind(
            request_id="request-f14-dependency",
            thread_id="thread-f14-dependency",
            step_id="financial-goals",
        ),
    )
    return {
        "dependent_calls": calls["dependent"],
        "independent_calls": calls["independent"],
        "dependent_status": report.task_results["task-2"]["status"],
        "independent_status": report.task_results["task-3"]["status"],
        "action": report.output.get("action"),
    }


def _compare(expected: dict[str, Any], actual: dict[str, Any]) -> tuple[str, ...]:
    mismatches: list[str] = []
    for key, expected_value in expected.items():
        actual_value = actual.get(key)
        if actual_value != expected_value:
            mismatches.append(
                f"{key}: expected {expected_value!r}, got {actual_value!r}"
            )
    unexpected = sorted(set(actual) - set(expected))
    if unexpected:
        mismatches.append(f"unexpected actual fields: {unexpected}")
    return tuple(mismatches)


async def run_suite(
    suite: dict[str, Any],
    *,
    case_id: str | None = None,
) -> list[AttackResult]:
    f13_cases = {case["id"]: case for case in load_f13_suite()["cases"]}
    f13_evaluator = ProductionSecurityEvaluator()
    results: list[AttackResult] = []
    for case in suite["cases"]:
        if case_id is not None and case["id"] != case_id:
            continue
        scenario = case["scenario"]
        if scenario == "f13_production_case":
            source_case = deepcopy(f13_cases[case["input"]["f13_case_id"]])
            decision = f13_evaluator.evaluate(source_case)
            actual = {
                "decision": decision.decision,
                "reason_code": decision.reason_code,
                "effects": list(decision.effects),
            }
            reproduction_input = source_case["input"]
        elif scenario == "dependency_executor":
            actual = await _dependency_executor_scenario()
            reproduction_input = deepcopy(case["input"])
        else:  # validate_suite rejects this before execution.
            raise ValueError(f"unsupported scenario: {scenario}")
        results.append(AttackResult(
            case_id=case["id"],
            title=case["title"],
            category=case["category"],
            severity=case["severity"],
            reproduction_input=reproduction_input,
            expected=deepcopy(case["expected"]),
            actual=actual,
            impact_boundary=case["impact_boundary"],
            owner_paths=tuple(case["owner_paths"]),
            mismatches=_compare(case["expected"], actual),
        ))
    if case_id is not None and not results:
        raise ValueError(f"unknown case id: {case_id}")
    return results


def build_report(suite: dict[str, Any], results: list[AttackResult]) -> dict[str, Any]:
    findings = [
        {
            "finding_id": f"FIND-{result.case_id}",
            "title": result.title,
            "severity": result.severity,
            "reproduction": {
                "command": f"python evals/security/f14_mock_attack.py --case {result.case_id} --json",
                "input": result.reproduction_input,
            },
            "expected": result.expected,
            "actual": result.actual,
            "impact_boundary": result.impact_boundary,
            "owner_paths": list(result.owner_paths),
            "mismatches": list(result.mismatches),
        }
        for result in results
        if not result.passed
    ]
    return {
        "schema_version": suite["schema_version"],
        "suite_id": suite["suite_id"],
        "baseline_commit": suite["baseline_commit"],
        "execution_scope": suite["execution_scope"],
        "synthetic_data_only": suite["synthetic_data_only"],
        "summary": {
            "total": len(results),
            "passed": sum(result.passed for result in results),
            "failed": len(findings),
            "findings": len(findings),
        },
        "results": [
            {
                "case_id": result.case_id,
                "title": result.title,
                "category": result.category,
                "severity": result.severity,
                "passed": result.passed,
                "reproduction": {
                    "command": f"python evals/security/f14_mock_attack.py --case {result.case_id} --json",
                    "input": result.reproduction_input,
                },
                "expected": result.expected,
                "actual": result.actual,
                "impact_boundary": result.impact_boundary,
                "owner_paths": list(result.owner_paths),
                "mismatches": list(result.mismatches),
            }
            for result in results
        ],
        "findings": findings,
    }


async def async_main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=SUITE_PATH)
    parser.add_argument("--case", dest="case_id")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    suite = load_suite(args.cases)
    errors = validate_suite(suite)
    if errors:
        for error in errors:
            print(f"INVALID: {error}")
        return 2
    results = await run_suite(suite, case_id=args.case_id)
    report = build_report(suite, results)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    if args.json:
        print(rendered, end="")
    else:
        for result in results:
            print(f"{'PASS' if result.passed else 'FAIL'} {result.case_id} {result.title}")
            for mismatch in result.mismatches:
                print(f"  - {mismatch}")
        print(
            "summary: "
            f"total={report['summary']['total']} "
            f"passed={report['summary']['passed']} "
            f"findings={report['summary']['findings']}"
        )
    return 0 if not report["findings"] else 1


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
