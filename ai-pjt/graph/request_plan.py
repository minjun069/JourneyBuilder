"""Compile semantic requests into validated execution plans without model-made wiring."""

from collections.abc import Mapping
from typing import Any

from graph.plan_contracts import MAX_PLAN_TASKS
from graph.task_catalog import TASK_TYPE_CONTRACTS


def compile_request_plan(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Compile semantic requests; agents own any reference-tool use."""
    if set(raw) - {"status", "requests"}:
        raise ValueError("요청 계획에 실행 정보가 포함되어 있습니다.")
    status = raw.get("status")
    requests = raw.get("requests")
    if status not in {"executable", "ambiguous", "unsupported"} or not isinstance(requests, list):
        raise ValueError("요청 상태와 목록을 확인해 주세요.")
    if status != "executable":
        if requests:
            raise ValueError("실행하지 않는 요청에는 작업을 넣지 않습니다.")
        return {"status": status, "tasks": []}
    if not 1 <= len(requests) <= MAX_PLAN_TASKS:
        raise ValueError("같은 종류의 요청을 모아 최대 세 작업으로 정리해 주세요.")
    tasks: list[dict[str, Any]] = []
    for request in requests:
        if not isinstance(request, Mapping) or set(request) - {
            "type", "description",
        }:
            raise ValueError("작업 종류와 설명만 선택해 주세요.")
        kind, description = request.get("type"), request.get("description")
        contract = TASK_TYPE_CONTRACTS.get(kind)
        if not contract or contract["executor_kind"] != "agent":
            raise ValueError("제공된 상담 작업 중에서 선택해 주세요.")
        if not isinstance(description, str) or not description.strip():
            raise ValueError("처리할 요청의 조건을 description에 보존해 주세요.")
        task = {
            "id": f"task-{len(tasks) + 1}", "type": kind,
            "description": description.strip(),
            "depends_on": [],
            "bindings": {},
        }
        tasks.append(task)
    return {"status": status, "tasks": tasks}
