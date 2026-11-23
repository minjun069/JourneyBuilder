"""Translate scenario_runner results without importing or executing product code."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

from evals.counseling_judge_contract import EvaluationInput, Turn

SUCCESS = {"completed", "completed_with_task_failures", "completed_with_plan_issues", "policy_blocked"}
FAILURE = {"error", "timeout"}


def _positive_int(value: Any) -> int:
    if type(value) is not int or value < 1:
        raise ValueError("expected a positive integer")
    return value


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("expected nonempty text")
    return value


def _snapshot_turns(context: dict[str, Any]) -> list[Turn]:
    turns = context.get("turns", [])
    if not isinstance(turns, list):
        raise ValueError("invalid conversation turns")
    result = []
    for turn in turns:
        if not isinstance(turn, dict):
            raise ValueError("invalid conversation turn")
        # Both historical and current scenario contexts store paired utterances.
        for key, role in (("user_message", "user"), ("assistant_message", "assistant")):
            if key not in turn or not isinstance(turn[key], str):
                raise ValueError("invalid snapshot utterance")
            if turn[key]:
                result.append(Turn(role=role, content=turn[key]))
    return result


def adapt_scenario_results(
    rows: list[Any], *, manifest: dict[str, Any], candidate_version: str,
    source_sha256: str,
) -> list[EvaluationInput]:
    """Use only preceding turns within the same thread/repetition, never future rows."""
    candidate_version = _text(candidate_version)
    run_id = _text(manifest.get("run_id", source_sha256[:16]))
    histories: dict[tuple[int, str], list[Turn]] = {}
    history_gaps: set[tuple[int, str]] = set()
    last_ordinals: dict[tuple[int, str], int] = {}
    cases, seen_requests = [], set()
    for index, row in enumerate(rows):
        try:
            if not isinstance(row, dict):
                raise ValueError("row must be an object")
            ordinal = _positive_int(row["ordinal"])
            repetition = _positive_int(row["repetition"])
            request_id = _text(row["request_id"])
            if request_id in seen_requests:
                raise ValueError("duplicate request")
            seen_requests.add(request_id)
            state = row["graph_input"]
            if not isinstance(state, dict):
                raise ValueError("graph input must be an object")
            thread = _text(state["thread_id"])
            group = (repetition, thread)
            if ordinal <= last_ordinals.get(group, 0):
                raise ValueError("turns must be strictly increasing within a conversation")
            request = _text(row["message"])
            if state.get("message") != request or state.get("request_id") != request_id:
                raise ValueError("row and graph request disagree")
            customer = state["customer_info"]
            if not isinstance(customer, dict):
                raise ValueError("customer info must be an object")
            snapshot = state.get("conversation_context")
            missing = []
            if not isinstance(snapshot, dict):
                snapshot = {}
                missing.append("graph_input.conversation_context가 누락되어 모델의 입력 문맥을 확인할 수 없음")
            snapshot_turns = _snapshot_turns(snapshot)
            if group not in histories:
                histories[group] = snapshot_turns
                if ordinal > 1 and not snapshot_turns and not snapshot.get("summary"):
                    history_gaps.add(group)
            elif ordinal != last_ordinals[group] + 1:
                # Do not silently stitch over a missing result and assert full history.
                history_gaps.add(group)
            if group in history_gaps:
                missing.append("동일 대화의 이전 실행 결과 또는 초기 대화·요약이 누락됨")
            last_ordinals[group] = ordinal
            status = row["status"]
            if status not in SUCCESS | FAILURE:
                raise ValueError("unsupported execution status")
            output = row.get("output")
            answer = output.get("response") if isinstance(output, dict) else None
            if not isinstance(answer, str):
                if status in SUCCESS:
                    raise ValueError("completed execution must contain output.response")
                answer = ""
                missing.append("실행 실패로 output.response가 없어 현재 상담 답변을 평가할 수 없음")
            report_supplied = row["report_supplied"]
            if type(report_supplied) is not bool:
                raise ValueError("report_supplied must be boolean")
            diagnostics = {"report_supplied": report_supplied}
            for key in ("chart_payload", "tool_context"):
                if key in state:
                    diagnostics[key] = deepcopy(state[key])
            saved_data = customer.get("saved_data", {})
            if isinstance(saved_data, dict) and "report_snapshot" in saved_data:
                diagnostics["report_snapshot"] = deepcopy(saved_data["report_snapshot"])
            if report_supplied and not any(diagnostics.get(k) for k in (
                "chart_payload", "tool_context", "report_snapshot",
            )):
                missing.append("보고서 제공 기록은 있으나 graph_input의 보고서 원문이 누락됨")
            context = {
                "previous_dialogue": [turn.model_dump() for turn in histories[group]],
                "customer_info": deepcopy(customer), "diagnostic_results": diagnostics,
                "missing_information": missing,
                "execution_context": {
                    "step_id": row["step_id"], "process": row.get("process"),
                    "request_mode": state.get("request_mode"), "status": status,
                    "summary": deepcopy(snapshot.get("summary", {})),
                    "structured_state": deepcopy(snapshot.get("structured_state", {})),
                    "structured_context": deepcopy(snapshot.get("structured_context", [])),
                    "model_visible_dialogue": [turn.model_dump() for turn in snapshot_turns],
                    "output_action": deepcopy(output.get("action")) if isinstance(output, dict) else None,
                    "plan_issues": deepcopy(row.get("plan_issues", [])),
                    "task_results": deepcopy(row.get("task_results", {})),
                },
            }
            cases.append(EvaluationInput(
                schema_version="counseling-judge-input.v1",
                case_id=request_id, candidate_version=candidate_version,
                request=request, answer=answer, context=context,
                source_metadata={
                    "format": "scenario-results", "run_id": run_id,
                    "results_sha256": source_sha256, "row_index": index,
                    "ordinal": ordinal, "repetition": repetition,
                    "request_id": request_id, "step_id": row["step_id"],
                    "step_turn": row.get("step_turn"), "status": status,
                    "trace_id": row.get("trace_id"), "trace_url": row.get("trace_url"),
                    "revision": deepcopy(manifest.get("revision", {})),
                    "row_sha256": hashlib.sha256(json.dumps(
                        row, ensure_ascii=False, sort_keys=True, allow_nan=False,
                    ).encode()).hexdigest(),
                },
            ))
            if status in SUCCESS:
                histories[group].append(Turn(role="user", content=request))
                if answer:
                    histories[group].append(Turn(role="assistant", content=answer))
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"invalid scenario result at row {index + 1}") from error
    return cases
