"""Run F14 failure, timeout, retry, concurrency, and artifact anomaly checks."""

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
from typing import Any, Awaitable, Callable

import httpx


AI_ROOT = Path(__file__).resolve().parents[2]
if str(AI_ROOT) not in sys.path:
    sys.path.insert(0, str(AI_ROOT))

from core.callback_masking import CallbackPayloadMasker  # noqa: E402
from core.monitoring import OperationStage  # noqa: E402
from core.runtime_instrumentation import FakeInstrumentationSink, InstrumentationPort  # noqa: E402
from core.runtime_instrumentation_adapter import RuntimeInstrumentationAdapter  # noqa: E402
from transport.contracts import ChatV2Request  # noqa: E402
from transport.runtime import (  # noqa: E402
    AsyncChatTransport,
    CallbackClient,
    CallbackDeliveryContext,
    ExecutionBudget,
    InMemoryRequestCheckpointStore,
)

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
    "callback_retry",
    "concurrent_accept",
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


class _CallbackRecorder:
    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    async def send(
        self,
        payload: dict[str, Any],
        *,
        context: CallbackDeliveryContext,
    ) -> None:
        if not context.request_id.startswith("f14-ops-"):
            raise AssertionError("callback request correlation changed")
        self.payloads.append(deepcopy(payload))


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
    backend = suite.get("backend_concurrency_check")
    if not isinstance(backend, dict) or not backend.get("command") or not backend.get("expected"):
        errors.append("backend_concurrency_check command and expected are required")
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
        "field_registry": [],
        "chart_payload": None,
    })


async def _request_timeout() -> dict[str, Any]:
    store = InMemoryRequestCheckpointStore()
    callback = _CallbackRecorder()
    processor_started = False
    processor_cancelled = False

    async def processor(_: ChatV2Request, __: ExecutionBudget) -> dict[str, Any]:
        nonlocal processor_started, processor_cancelled
        processor_started = True
        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            processor_cancelled = True
            raise
        return {"response": "시간 초과 뒤 도달하면 안 되는 응답"}

    request = _request(
        "f14-ops-timeout",
        deadline_at=datetime.now(timezone.utc) + timedelta(milliseconds=50),
    )
    transport = AsyncChatTransport(
        checkpoint_store=store,
        processor=processor,
        callback_client=callback,  # type: ignore[arg-type]
        callback_masker=CallbackPayloadMasker(pseudonym_key=MASKING_KEY),
        instrumentation=_instrumentation()[0],
        clock=lambda: datetime(2026, 9, 12, tzinfo=timezone.utc),
    )
    await store.claim(request, "synthetic-timeout-digest")
    terminal = await transport.run(request)
    return {
        "processor_started": processor_started,
        "processor_cancelled": processor_cancelled,
        "callback_calls": len(callback.payloads),
        "terminal_status": terminal["status"],
        "error_code": terminal["error"]["code"],
        "retryable": terminal["error"]["retryable"],
    }


async def _callback_retry() -> dict[str, Any]:
    statuses = [503, 503, 200]
    payloads: list[bytes] = []
    backoffs: list[int] = []
    instrumentation, sink = _instrumentation()

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(request.content)
        return httpx.Response(statuses[len(payloads) - 1], request=request)

    async def sleeper(delay: float) -> None:
        backoffs.append(int(delay))

    client = CallbackClient(
        url="http://backend/api/v2/internal/ai/callback/",
        bearer_token="synthetic-token",
        instrumentation=instrumentation,
        sleeper=sleeper,
        jitter=lambda: 0,
        client_factory=lambda **kwargs: httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            **kwargs,
        ),
    )
    await client.send(
        {"request_id": "f14-ops-retry", "status": "completed"},
        context=CallbackDeliveryContext(
            request_id="f14-ops-retry",
            thread_id="thread-f14-ops-retry",
            plan_id="plan-f14-ops-retry",
            step_id="basic-info",
        ),
    )
    events = [
        event for event in sink.baseline_events
        if event.stage is OperationStage.CALLBACK
    ]
    return {
        "attempts": len(payloads),
        "statuses": statuses,
        "identical_payloads": len(set(payloads)) == 1,
        "backoffs": backoffs,
        "attempt_events": [event.status.value for event in events],
    }


async def _concurrent_accept() -> dict[str, Any]:
    store = InMemoryRequestCheckpointStore()
    callback = _CallbackRecorder()

    async def processor(_: ChatV2Request, __: ExecutionBudget) -> dict[str, Any]:
        return {"response": "실행되지 않음"}

    transport = AsyncChatTransport(
        checkpoint_store=store,
        processor=processor,
        callback_client=callback,  # type: ignore[arg-type]
        callback_masker=CallbackPayloadMasker(pseudonym_key=MASKING_KEY),
        instrumentation=_instrumentation()[0],
    )
    request = _request(
        "f14-ops-concurrent",
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30),
    )
    scheduled: list[tuple[Callable[..., Awaitable[Any]], Any]] = []
    schedule = lambda function, argument: scheduled.append((function, argument))
    accepted = await asyncio.gather(*(
        transport.accept(request, schedule) for _ in range(20)
    ))
    checkpoint = await store.get(request.request_id)
    return {
        "accepts": len(accepted),
        "scheduled_runs": len(scheduled),
        "unique_request_ids": len({item.request_id for item in accepted}),
        "checkpoint_status": checkpoint.status if checkpoint else None,
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
        "callback_retry": _callback_retry,
        "concurrent_accept": _concurrent_accept,
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
    *,
    backend_concurrency_passed: bool,
) -> dict[str, Any]:
    return {
        "schema_version": suite["schema_version"],
        "suite_id": suite["suite_id"],
        "summary": {
            "total": len(results) + 1,
            "passed": sum(result.passed for result in results)
            + int(backend_concurrency_passed),
            "failed": sum(not result.passed for result in results)
            + int(not backend_concurrency_passed),
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
        "backend_concurrency_check": {
            **suite["backend_concurrency_check"],
            "passed": backend_concurrency_passed,
        },
    }


async def async_main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=SUITE_PATH)
    parser.add_argument("--backend-concurrency-pass", action="store_true")
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
    report = build_report(
        suite,
        results,
        backend_concurrency_passed=args.backend_concurrency_pass,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    if args.json:
        print(rendered, end="")
    else:
        for result in report["results"]:
            print(f"{'PASS' if result['passed'] else 'FAIL'} {result['case_id']} {result['title']}")
        backend_status = "PASS" if args.backend_concurrency_pass else "NOT_RUN"
        print(f"{backend_status} backend simultaneous callback exactly-once")
        print(
            f"summary: total={report['summary']['total']} "
            f"passed={report['summary']['passed']} failed={report['summary']['failed']}"
        )
    return 0 if report["summary"]["failed"] == 0 else 1


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
