"""Run F14 dependency, SSE timeout, and artifact anomaly checks."""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
from core.terminal_masking import TerminalPayloadMasker  # noqa: E402
from main import _stream_v2_chat_events  # noqa: E402
from transport.contracts import ChatV2Request  # noqa: E402

from f14_mock_attack import (  # noqa: E402
    load_suite as load_attack_suite,
    run_suite as run_attack_suite,
)


SCHEMA_VERSION = "f14-operational-anomalies-v1"
SUITE_PATH = Path(__file__).with_name("f14-anomaly-cases.json")
CASE_ID_PATTERN = re.compile(r"^F14-OPS-\d{3}$")
SCENARIOS = {
    "dependency_failure",
    "request_timeout",
    "artifact_mutation",
}
MASKING_KEY = b"f14-operational-masking-key-32bytes"


@dataclass(frozen=True)
class AnomalyResult:
    case_id: str
    title: str
    expected: dict[str, Any]
    actual: dict[str, Any]
    mismatches: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.mismatches


def load_suite(path: Path = SUITE_PATH) -> dict[str, Any]:
    with path.open(encoding="utf-8") as suite_file:
        suite = json.load(suite_file)
    if not isinstance(suite, dict):
        raise ValueError("F14 anomaly suite must be a JSON object")
    return suite


def validate_suite(suite: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if suite.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION!r}")
    if suite.get("suite_id") != "AI-F14-T04":
        errors.append("suite_id must be 'AI-F14-T04'")
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
        if case.get("scenario") not in SCENARIOS:
            errors.append(f"{prefix}.scenario is unsupported: {case.get('scenario')!r}")
        if not isinstance(case.get("expected"), dict) or not case["expected"]:
            errors.append(f"{prefix}.expected must be a non-empty object")
    return errors


def _instrumentation() -> tuple[RuntimeInstrumentationAdapter, FakeInstrumentationSink]:
    sink = FakeInstrumentationSink()
    return RuntimeInstrumentationAdapter(InstrumentationPort(sink)), sink


def _request(request_id: str, *, deadline_at: datetime) -> ChatV2Request:
    return ChatV2Request.model_validate({
        "contract_version": "2.0",
        "request_id": request_id,
        "payload_digest": "0" * 64,
        "session_id": 1404,
        "thread_id": f"thread-{request_id}",
        "step_id": "basic-info",
        "message": "합성 운영 이상 시나리오",
        "deadline_at": deadline_at,
        "customer_info": {"saved_data": {}, "current_form": {}},
        "chart_payload": None,
    })


async def _request_timeout() -> dict[str, Any]:
    state = {"processor_started": False, "processor_cancelled": False}

    class ContextAPI:
        terminal = None

        async def read_context(self, _scope):
            return {}

        async def read_terminal(self, _scope, _request_id, _payload_digest):
            return self.terminal

        async def begin_execution(self, _scope, _request_id):
            return None

        async def delete_execution(self, _scope, _request_id):
            return None

        async def commit_terminal(
            self, _scope, _request_id, _payload_digest, terminal
        ):
            self.terminal = dict(terminal)
            return self.terminal

        def state_channels(self, _scope, context, retrieved_memories=()):
            return {
                "conversation_context": context,
                "retrieved_memories": list(retrieved_memories),
            }

        def config_for(self, scope, _request_id):
            return {"configurable": {"thread_id": scope.thread_id}}

    class SlowGraph:
        async def astream_events(self, _input, *, config, version):
            state["processor_started"] = True
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                state["processor_cancelled"] = True
                raise
            yield {"event": "unreachable"}

    request = _request(
        "f14-ops-timeout",
        deadline_at=datetime.now(timezone.utc) + timedelta(milliseconds=50),
    )
    frames = [
        frame
        async for frame in _stream_v2_chat_events(
            SlowGraph(),
            ContextAPI(),
            request,
            TerminalPayloadMasker(pseudonym_key=MASKING_KEY),
            instrumentation=_instrumentation()[0],
        )
    ]
    terminal = json.loads(frames[-1].splitlines()[1].removeprefix("data: "))
    return {
        "processor_started": state["processor_started"],
        "processor_cancelled": state["processor_cancelled"],
        "terminal_status": terminal["status"],
        "error_code": terminal["error"]["code"],
        "retryable": terminal["error"]["retryable"],
    }


async def _attack_result(case_id: str) -> dict[str, Any]:
    results = await run_attack_suite(load_attack_suite(), case_id=case_id)
    return results[0].actual


def _mismatches(expected: dict[str, Any], actual: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        f"{key}: expected {expected_value!r}, got {actual.get(key)!r}"
        for key, expected_value in expected.items()
        if actual.get(key) != expected_value
    )


async def run_suite(suite: dict[str, Any]) -> list[AnomalyResult]:
    handlers = {
        "dependency_failure": lambda: _attack_result("F14-ATK-002"),
        "request_timeout": _request_timeout,
        "artifact_mutation": lambda: _attack_result("F14-ATK-004"),
    }
    results: list[AnomalyResult] = []
    for case in suite["cases"]:
        actual = await handlers[case["scenario"]]()
        results.append(AnomalyResult(
            case_id=case["id"],
            title=case["title"],
            expected=deepcopy(case["expected"]),
            actual=actual,
            mismatches=_mismatches(case["expected"], actual),
        ))
    return results


def build_report(
    suite: dict[str, Any],
    results: list[AnomalyResult],
) -> dict[str, Any]:
    return {
        "schema_version": suite["schema_version"],
        "suite_id": suite["suite_id"],
        "summary": {
            "total": len(results),
            "passed": sum(result.passed for result in results),
            "failed": sum(not result.passed for result in results),
        },
        "results": [
            {
                "case_id": result.case_id,
                "title": result.title,
                "passed": result.passed,
                "expected": result.expected,
                "actual": result.actual,
                "mismatches": list(result.mismatches),
            }
            for result in results
        ],
    }


async def async_main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=SUITE_PATH)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    suite = load_suite(args.cases)
    errors = validate_suite(suite)
    if errors:
        for error in errors:
            print(f"INVALID: {error}")
        return 2
    results = await run_suite(suite)
    report = build_report(suite, results)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    if args.json:
        print(rendered, end="")
    else:
        for result in report["results"]:
            print(f"{'PASS' if result['passed'] else 'FAIL'} {result['case_id']} {result['title']}")
        print(
            f"summary: total={report['summary']['total']} "
            f"passed={report['summary']['passed']} failed={report['summary']['failed']}"
        )
    return 0 if report["summary"]["failed"] == 0 else 1


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
