"""F07/F08 production-model baseline runner with a hard application-call budget."""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager, contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
from time import monotonic
from typing import Any

AI_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = AI_ROOT.parent
DEFAULT_FIXTURE = AI_ROOT / "evals" / "fixtures" / "f07-f08-model-baseline.json"
EXPECTED_ACTIVE_INDEXES = {
    "financial-knowledge": "financial-knowledge__official-catalog-2-1-0",
    "interface-guides": "interface-guides__official-catalog-2-1-0",
}


class ModelCallLimitError(RuntimeError):
    pass


@dataclass
class ModelCallBudget:
    limit: int
    used: int = 0
    prior_used: int = 0
    timeout_seconds: float = 60.0
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def invoke(self, label: str, delegate: Any, *args: Any, **kwargs: Any) -> Any:
        if self.used >= self.limit:
            raise ModelCallLimitError(f"model call limit exceeded: {self.limit}")
        self.used += 1
        started = monotonic()
        record = {
            "sequence": self.used,
            "operation": label,
            "status": "started",
        }
        self.calls.append(record)
        try:
            result = await asyncio.wait_for(
                delegate.ainvoke(*args, **kwargs),
                timeout=self.timeout_seconds,
            )
        except Exception as exc:
            record.update({
                "status": "error",
                "latency_ms": round((monotonic() - started) * 1000, 3),
                "error_type": type(exc).__name__,
            })
            raise
        record.update({
            "status": "completed",
            "latency_ms": round((monotonic() - started) * 1000, 3),
        })
        return result

    async def record_external_call(self, label: str, function: Callable[[], Any]) -> Any:
        if self.used >= self.limit:
            raise ModelCallLimitError(f"model call limit exceeded: {self.limit}")
        self.used += 1
        started = monotonic()
        record = {
            "sequence": self.used,
            "operation": label,
            "status": "started",
        }
        self.calls.append(record)
        try:
            result = await asyncio.wait_for(
                function(),
                timeout=self.timeout_seconds,
            )
        except Exception as exc:
            record.update({
                "status": "error",
                "latency_ms": round((monotonic() - started) * 1000, 3),
                "error_type": type(exc).__name__,
            })
            raise
        record.update({
            "status": "completed",
            "latency_ms": round((monotonic() - started) * 1000, 3),
        })
        return result


class BudgetedRunnable:
    def __init__(self, budget: ModelCallBudget, label: str, delegate: Any) -> None:
        self.budget = budget
        self.label = label
        self.delegate = delegate

    async def ainvoke(self, *args: Any, **kwargs: Any) -> Any:
        return await self.budget.invoke(self.label, self.delegate, *args, **kwargs)


@contextmanager
def instrument_agent_models(budget: ModelCallBudget):
    """Count each production LangChain invocation without changing its prompt."""

    from agents.chart_interpret import service as chart_interpret
    from agents.chart_interpret import model as chart_model
    from agents.elicit import service as elicit
    from agents.elicit import model as elicit_model
    from agents.error_review import service as error_review
    from agents.error_review import model as error_review_model
    from agents.input_assist import service as input_assist
    from agents.qa import service as qa

    targets = (
        (qa, "_specify_chain"),
        (qa, "_decision_chain"),
        (qa, "_judge_chain"),
        (qa, "_refine_chain"),
        (qa, "llm"),
        (input_assist, "_classify_chain"),
        (input_assist, "_identify_chain"),
        (input_assist, "_resolve_chain"),
        (input_assist, "_error_check_2_chain"),
        (input_assist, "llm"),
        (error_review_model, "_review_chain"),
        (chart_model, "llm"),
        (elicit_model, "llm"),
    )
    originals: list[tuple[Any, str, Any]] = []
    try:
        for module, attribute in targets:
            original = getattr(module, attribute)
            originals.append((module, attribute, original))
            setattr(
                module,
                attribute,
                BudgetedRunnable(budget, f"llm:{module.__name__}.{attribute}", original),
            )
        yield
    finally:
        for module, attribute, original in reversed(originals):
            setattr(module, attribute, original)


class RecordingRetriever:
    def __init__(self, delegate: Any, budget: ModelCallBudget) -> None:
        self.delegate = delegate
        self.budget = budget
        self.queries: list[dict[str, Any]] = []

    async def aretrieve(self, query: str, *, top_k: int | None = None) -> Any:
        async def retrieve() -> Any:
            return await self.delegate.aretrieve(query, top_k=top_k)

        results = await self.budget.record_external_call(
            "embedding:financial-knowledge.query",
            retrieve,
        )
        self.queries.append({
            "query": query,
            "top_k": top_k,
            "result_count": len(results),
            "source_ids": sorted({
                result.chunk.metadata.source_id
                for result in results
                if getattr(getattr(result, "chunk", None), "metadata", None) is not None
            }),
        })
        return results


def load_fixture(path: Path = DEFAULT_FIXTURE) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("fixture_kind") != "f07-f08-model-baseline":
        raise ValueError("unsupported agent baseline fixture")
    if payload.get("schema_version") != "1.0":
        raise ValueError("unsupported agent baseline schema")
    limit = payload.get("model_call_limit")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
        raise ValueError("model_call_limit must be between 1 and 200")
    if payload.get("quality_disposition") != "baseline_only":
        raise ValueError("model quality must remain baseline_only")
    if payload.get("chart_quality_disposition") != "quality_gate":
        raise ValueError("chart quality must use explicit acceptance thresholds")
    return payload


def _field_registry(step_id: str) -> list[dict[str, Any]]:
    from core.field_registry import load_field_registry

    return load_field_registry(step_id)


def _walk_path(value: Any, path: str) -> list[Any]:
    parts = path.split(".") if path else []
    values = [value]
    for part in parts:
        is_list = part.endswith("[]")
        key = part[:-2] if is_list else part
        next_values: list[Any] = []
        for current in values:
            if not isinstance(current, Mapping) or key not in current:
                continue
            child = current[key]
            if is_list:
                if isinstance(child, list):
                    next_values.extend(child)
            else:
                next_values.append(child)
        values = next_values
    return values


def present_field_counts(
    current_form: Mapping[str, Any],
    registry: list[dict[str, Any]],
) -> dict[str, int]:
    """Count populated user-domain field instances by canonical field."""

    counts: dict[str, int] = defaultdict(int)
    for spec in registry:
        if spec.get("sensitivity") == "operational":
            continue
        section = str(spec.get("section", ""))
        path = str(spec.get("path", ""))
        cardinality = spec.get("cardinality")
        if cardinality == "single":
            values = _walk_path(current_form, path)
        else:
            rows = (
                current_form.get(section, [])
                if section in {"goal_periods", "goal_milestones"}
                else current_form.get("assets", [])
            )
            values = _walk_path({"rows": rows}, f"rows[].{path}")
        counts[f"{section}.{path}"] += sum(value is not None for value in values)
    return dict(counts)


def summarize_false_positives(
    normal_results: list[dict[str, Any]],
) -> dict[str, Any]:
    by_field: dict[str, dict[str, Any]] = {}
    totals = {"evaluated": 0, "low": 0, "medium": 0, "high": 0}
    for case in normal_results:
        for field_name, count in case.get("evaluated_fields", {}).items():
            entry = by_field.setdefault(
                field_name,
                {"evaluated": 0, "low": 0, "medium": 0, "high": 0},
            )
            entry["evaluated"] += count
            totals["evaluated"] += count
        for item in case.get("raw_items", []):
            field_name = f"{item.get('section')}.{item.get('path')}"
            level = item.get("confidence_level")
            if field_name not in by_field or level not in {"low", "medium", "high"}:
                continue
            by_field[field_name][level] += 1
            totals[level] += 1
    for entry in by_field.values():
        denominator = entry["evaluated"]
        entry["raw_false_positive_rate"] = (
            (entry["low"] + entry["medium"] + entry["high"]) / denominator
            if denominator else None
        )
        entry["visible_false_positive_rate"] = (
            (entry["medium"] + entry["high"]) / denominator
            if denominator else None
        )
    denominator = totals["evaluated"]
    return {
        "unit": "field_instance",
        "confidence_bands": {
            "low": {"range": "0.00<=confidence<0.60", "visible": False},
            "medium": {"range": "0.60<=confidence<0.85", "visible": True},
            "high": {"range": "0.85<=confidence<=1.00", "visible": True},
        },
        "totals": {
            **totals,
            "raw_false_positive_rate": (
                (totals["low"] + totals["medium"] + totals["high"]) / denominator
                if denominator else None
            ),
            "visible_false_positive_rate": (
                (totals["medium"] + totals["high"]) / denominator
                if denominator else None
            ),
        },
        "by_field": dict(sorted(by_field.items())),
        "acceptance_threshold": None,
    }


def _error_payload(exc: Exception) -> dict[str, str]:
    return {"error_type": type(exc).__name__, "message": str(exc)[:300]}


def _contains_number(text: str, expected: int | float) -> bool:
    compact = text.replace(",", "").replace(" ", "")
    candidates = {str(expected)}
    if isinstance(expected, int) and expected % 10_000 == 0:
        candidates.add(f"{expected // 10_000}만")
    return any(candidate in compact for candidate in candidates)


@asynccontextmanager
async def _qa_retriever(budget: ModelCallBudget) -> AsyncIterator[RecordingRetriever]:
    from agents.qa import service as qa
    from rag.retriever import get_retriever

    recorder = RecordingRetriever(get_retriever(), budget)
    original = qa.get_retriever
    qa.get_retriever = lambda: recorder
    try:
        yield recorder
    finally:
        qa.get_retriever = original


async def _run_qa_cases(cases: list[dict[str, Any]], budget: ModelCallBudget) -> dict[str, Any]:
    from agents.qa import service as qa

    results = []
    for case in cases:
        started_calls = budget.used
        try:
            async with _qa_retriever(budget) as retriever:
                state = await qa.graph.ainvoke({
                    "message": case["message"],
                    "step_id": case["step_id"],
                    "customer_info": deepcopy(case["customer_info"]),
                    "task_input": {
                        "description": "평가 fixture의 검증된 입력",
                        "bindings": deepcopy(case.get("bindings", {})),
                    },
                })
            response = str(state.get("output", {}).get("response", ""))
            signals = case.get("signals", {})
            results.append({
                "id": case["id"],
                "status": "completed",
                "model_calls": budget.used - started_calls,
                "retrievals": retriever.queries,
                "response": response,
                "observations": {
                    "response_nonempty": bool(response.strip()),
                    "terms_matched": {
                        term: term in response for term in signals.get("terms", [])
                    },
                    "bound_value_mentioned": (
                        _contains_number(response, signals["bound_value"])
                        if "bound_value" in signals else None
                    ),
                    "tool_only_notice": (
                        "추정" in response and ("기준값" in response or "공식" in response)
                        if signals.get("expects_tool_only_notice") else None
                    ),
                },
            })
        except Exception as exc:
            results.append({
                "id": case["id"],
                "status": "error",
                "model_calls": budget.used - started_calls,
                **_error_payload(exc),
            })
    return {"quality_disposition": "baseline_only", "cases": results}


async def _run_input_assist_cases(
    cases: list[dict[str, Any]], budget: ModelCallBudget
) -> dict[str, Any]:
    from agents import input_assist

    results = []
    for case in cases:
        started_calls = budget.used
        try:
            state = await input_assist.graph.ainvoke({
                "message": case["message"],
                "step_id": case["step_id"],
                "customer_info": deepcopy(case["customer_info"]),
            })
            output = state.get("output", {})
            action = output.get("action") if isinstance(output, Mapping) else None
            changes = action.get("changes", []) if isinstance(action, Mapping) else []
            expected = case.get("signals", {})
            results.append({
                "id": case["id"],
                "status": "completed",
                "model_calls": budget.used - started_calls,
                "response": output.get("response"),
                "action": action,
                "observations": {
                    "action_present": isinstance(action, Mapping),
                    "expected_action": expected.get("expects_action"),
                    "paths": [change.get("path") for change in changes],
                    "expected_paths": expected.get("expected_paths", []),
                    "expected_value_present": (
                        any(change.get("value") == expected["expected_value"] for change in changes)
                        if "expected_value" in expected else None
                    ),
                },
            })
        except Exception as exc:
            results.append({
                "id": case["id"],
                "status": "error",
                "model_calls": budget.used - started_calls,
                **_error_payload(exc),
            })
    return {"quality_disposition": "baseline_only", "cases": results}


async def _run_error_review_cases(
    fixture: dict[str, Any], budget: ModelCallBudget
) -> dict[str, Any]:
    from agents.error_review import analyze_review_items
    from agents.error_review.confidence import visible_review_items

    normal_results = []
    for case in fixture["normal_cases"]:
        registry = _field_registry(case["step_id"])
        started_calls = budget.used
        try:
            manual, raw_items = await analyze_review_items({
                "step_id": case["step_id"],
                "customer_info": {"current_form": deepcopy(case["current_form"])},
            })
            normal_results.append({
                "id": case["id"],
                "step_id": case["step_id"],
                "status": "completed",
                "model_calls": budget.used - started_calls,
                "manual": {"document_id": manual.document_id, "version": manual.version},
                "evaluated_fields": present_field_counts(case["current_form"], registry),
                "raw_items": raw_items,
                "visible_items": visible_review_items(raw_items),
            })
        except Exception as exc:
            normal_results.append({
                "id": case["id"],
                "step_id": case["step_id"],
                "status": "error",
                "model_calls": budget.used - started_calls,
                "evaluated_fields": present_field_counts(case["current_form"], registry),
                "raw_items": [],
                "visible_items": [],
                **_error_payload(exc),
            })

    suspicious_results = []
    for case in fixture["suspicious_cases"]:
        registry = _field_registry(case["step_id"])
        started_calls = budget.used
        try:
            manual, raw_items = await analyze_review_items({
                "step_id": case["step_id"],
                "customer_info": {"current_form": deepcopy(case["current_form"])},
            })
            visible = visible_review_items(raw_items)
            raw_fields = {f"{item['section']}.{item['path']}" for item in raw_items}
            visible_fields = {f"{item['section']}.{item['path']}" for item in visible}
            expected = set(case["expected_fields"])
            suspicious_results.append({
                "id": case["id"],
                "step_id": case["step_id"],
                "status": "completed",
                "model_calls": budget.used - started_calls,
                "manual": {"document_id": manual.document_id, "version": manual.version},
                "expected_fields": sorted(expected),
                "raw_items": raw_items,
                "visible_items": visible,
                "observations": {
                    "raw_expected_detected": sorted(expected & raw_fields),
                    "visible_expected_detected": sorted(expected & visible_fields),
                    "unexpected_fields": sorted(raw_fields - expected),
                },
            })
        except Exception as exc:
            suspicious_results.append({
                "id": case["id"],
                "step_id": case["step_id"],
                "status": "error",
                "model_calls": budget.used - started_calls,
                "expected_fields": case["expected_fields"],
                **_error_payload(exc),
            })

    expected_count = sum(len(case["expected_fields"]) for case in fixture["suspicious_cases"])
    raw_detected = sum(
        len(case.get("observations", {}).get("raw_expected_detected", []))
        for case in suspicious_results
    )
    visible_detected = sum(
        len(case.get("observations", {}).get("visible_expected_detected", []))
        for case in suspicious_results
    )
    return {
        "quality_disposition": "baseline_only",
        "normal_cases": normal_results,
        "suspicious_cases": suspicious_results,
        "false_positive": summarize_false_positives(normal_results),
        "suspicious_field_detection": {
            "unit": "canonical_field",
            "expected": expected_count,
            "raw_detected": raw_detected,
            "visible_detected": visible_detected,
            "raw_recall": raw_detected / expected_count if expected_count else None,
            "visible_recall": visible_detected / expected_count if expected_count else None,
            "acceptance_threshold": None,
        },
    }


def _numeric_lexemes(value: Any) -> set[str]:
    tokens = {
        _normalized_numeric_token(token)
        for token in re.findall(r"-?\d+(?:\.\d+)?", json.dumps(value, ensure_ascii=False))
    }
    for number in _walk_numbers(value):
        if isinstance(number, float) and 0 < abs(number) <= 1:
            tokens.add(_normalized_numeric_token(number * 100))
    return tokens


def _walk_numbers(value: Any):
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        yield value
        return
    if isinstance(value, Mapping):
        for child in value.values():
            yield from _walk_numbers(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_numbers(child)


def _normalized_numeric_token(value: str | int | float) -> str:
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return format(number, ".12g")


async def _run_chart_cases(cases: list[dict[str, Any]], budget: ModelCallBudget) -> dict[str, Any]:
    from agents import chart_interpret
    from evals.chart_quality import (
        build_evaluation_payload,
        evaluate_chart_response,
        evaluation_case_index,
        load_chart_evaluation_fixture,
        summarize_chart_quality,
    )

    chart_fixture = load_chart_evaluation_fixture()
    indexed_cases = evaluation_case_index(chart_fixture)
    results = []
    for case in cases:
        evaluation_case = indexed_cases[case["evaluation_case"]]
        payload = build_evaluation_payload(evaluation_case)
        started_calls = budget.used
        try:
            state = await chart_interpret.graph.ainvoke({
                "message": evaluation_case["message"],
                "step_id": evaluation_case["step_id"],
                "request_mode": evaluation_case.get("request_mode", "chat"),
                "chart_payload": payload,
                "customer_info": deepcopy(evaluation_case.get("customer_info", {
                    "saved_data": {},
                    "current_form": {},
                })),
            })
            output = state.get("output", {})
            response = str(output.get("response", ""))
            observations = evaluate_chart_response(
                case=evaluation_case,
                payload=payload,
                response=response,
                action=output.get("action"),
            )
            results.append({
                "id": case["id"],
                "status": "completed",
                "model_calls": budget.used - started_calls,
                "response": response,
                "observations": observations,
            })
        except Exception as exc:
            results.append({
                "id": case["id"],
                "status": "error",
                "model_calls": budget.used - started_calls,
                **_error_payload(exc),
            })
    return summarize_chart_quality(results, chart_fixture)


async def _run_elicit_cases(cases: list[dict[str, Any]], budget: ModelCallBudget) -> dict[str, Any]:
    from agents import elicit

    results = []
    for case in cases:
        started_calls = budget.used
        try:
            state = await elicit.graph.ainvoke({
                "message": case["message"],
                "step_id": case.get("step_id", "financial-goals"),
                "conversation_context": deepcopy(case.get("conversation_context", {})),
            })
            output = state.get("output", {})
            response = str(output.get("response", ""))
            results.append({
                "id": case["id"],
                "status": "completed",
                "model_calls": budget.used - started_calls,
                "response": response,
                "observations": {
                    "response_nonempty": bool(response.strip()),
                    "action_absent": output.get("action") is None,
                },
            })
        except Exception as exc:
            results.append({
                "id": case["id"],
                "status": "error",
                "model_calls": budget.used - started_calls,
                **_error_payload(exc),
            })
    return {
        "quality_disposition": "baseline_only",
        "cases": results,
        "acceptance_threshold": None,
    }


def _source_revision() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _active_indexes() -> dict[str, str | None]:
    from core.config import settings
    from rag.pipeline.store import PostgresIndexStore

    store = PostgresIndexStore(settings.db_url)
    return {
        logical: store.active_collection(logical)
        for logical in sorted(EXPECTED_ACTIVE_INDEXES)
    }


async def build_baseline(
    fixture: dict[str, Any],
    *,
    prior_model_calls: int = 0,
) -> dict[str, Any]:
    from core.config import settings

    limit = fixture["model_call_limit"]
    if isinstance(prior_model_calls, bool) or not 0 <= prior_model_calls < limit:
        raise ValueError("prior_model_calls must be below the model call limit")
    budget = ModelCallBudget(
        limit=limit,
        used=prior_model_calls,
        prior_used=prior_model_calls,
    )
    active_indexes = _active_indexes()
    if active_indexes != EXPECTED_ACTIVE_INDEXES:
        raise RuntimeError(
            f"approved active indexes mismatch: expected={EXPECTED_ACTIVE_INDEXES}, actual={active_indexes}"
        )

    with instrument_agent_models(budget):
        qa = await _run_qa_cases(fixture["qa_cases"], budget)
        input_assist = await _run_input_assist_cases(fixture["input_assist_cases"], budget)
        error_review = await _run_error_review_cases(fixture["error_review"], budget)
        chart = await _run_chart_cases(fixture["chart_cases"], budget)
        elicit = await _run_elicit_cases(fixture["elicit_response_cases"], budget)

    from prompts import get_prompt_manifest

    return {
        "report_kind": "f07-f08-production-model-baseline",
        "schema_version": "1.0",
        "quality_disposition": "baseline_only",
        "quality_acceptance_thresholds": None,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_revision": _source_revision(),
        "runtime": {
            "llm_provider": settings.llm_provider,
            "llm_model": settings.llm_model,
            "llm_temperature": settings.llm_temperature,
            "prompt_release": settings.prompt_release,
            "prompt_manifest": get_prompt_manifest(),
            "embedding_provider": settings.embedding_provider,
            "embedding_model": settings.embedding_model,
            "rag_min_dense_score": settings.rag_min_dense_score,
            "active_indexes": active_indexes,
        },
        "model_call_budget": {
            "limit": budget.limit,
            "used": budget.used,
            "used_in_this_run": budget.used - budget.prior_used,
            "prior_attempt_calls": budget.prior_used,
            "remaining": budget.limit - budget.used,
            "counting_unit": "application-level LLM or query-embedding invocation",
            "calls": budget.calls,
        },
        "results": {
            "f07": {
                "qa": qa,
                "input_assist": input_assist,
                "error_review": error_review,
            },
            "f08": {
                "chart": chart,
                "elicit": elicit,
                "failure_responder_and_aggregation": {
                    "quality_disposition": "deterministic_contract_only",
                    "actual_model_calls": 0,
                },
            },
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prior-model-calls", type=int, default=0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    fixture = load_fixture(args.fixture)
    report = asyncio.run(build_baseline(
        fixture,
        prior_model_calls=args.prior_model_calls,
    ))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "output": str(args.output),
        "model_calls": report["model_call_budget"]["used"],
        "limit": report["model_call_budget"]["limit"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
