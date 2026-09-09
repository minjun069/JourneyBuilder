"""Plan/task-aware production binding for provider-neutral Function tools."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from time import monotonic
from typing import Any, Mapping

from .contracts import (
    KnowledgeSearch,
    ToolCallResult,
    ToolError,
    ToolErrorCode,
    ToolExecutionContext,
)
from .registry import FunctionToolRegistry


TOOL_CALL_SOURCE = "graph_task"


@dataclass
class ToolCallBudget:
    """Request-scoped mutable counter shared by all task tool invocations."""

    limit: int = 3
    used: int = 0

    def consume(self) -> bool:
        if self.used >= self.limit:
            return False
        self.used += 1
        return True


@dataclass(frozen=True)
class RuntimeToolRequest:
    tool_name: str
    arguments: Mapping[str, Any]
    agent: str


class FunctionToolRuntime:
    """Inject trusted graph metadata and invoke the canonical tool registry."""

    def __init__(
        self,
        registry: FunctionToolRegistry,
        *,
        knowledge_search: KnowledgeSearch | None = None,
    ) -> None:
        self._registry = registry
        self._knowledge_search = knowledge_search

    async def invoke(
        self,
        request: RuntimeToolRequest,
        payload: Mapping[str, Any],
    ) -> ToolCallResult:
        budget = payload.get("tool_call_budget")
        if not isinstance(budget, ToolCallBudget):
            return _runtime_failure(
                request.tool_name,
                ToolErrorCode.INTERNAL_ERROR,
                "도구 호출 예산이 runtime에 연결되지 않았습니다.",
            )
        if not budget.consume():
            return _runtime_failure(
                request.tool_name,
                ToolErrorCode.BUDGET_EXCEEDED,
                "요청당 도구 호출 예산을 초과했습니다.",
            )

        task = payload.get("task")
        if not isinstance(task, Mapping):
            return _runtime_failure(
                request.tool_name,
                ToolErrorCode.INTERNAL_ERROR,
                "검증된 task 실행 문맥이 없습니다.",
            )
        request_id = payload.get("request_id")
        plan_id = payload.get("plan_id")
        task_id = task.get("id")
        step_id = payload.get("step_id")
        if not all(
            isinstance(value, str) and value.strip()
            for value in (request_id, plan_id, task_id, step_id)
        ):
            return _runtime_failure(
                request.tool_name,
                ToolErrorCode.INTERNAL_ERROR,
                "도구 호출 식별자 결합이 올바르지 않습니다.",
            )

        snapshot = payload.get("tool_context")
        if not isinstance(snapshot, Mapping):
            snapshot = {}
        context = ToolExecutionContext(
            request_id=request_id,
            plan_id=plan_id,
            task_id=task_id,
            step_id=step_id,
            agent=request.agent,
            call_source=TOOL_CALL_SOURCE,
            field_registry=tuple(snapshot.get("field_specs", ())),
            dashboard_metrics=dict(snapshot.get("dashboard_metrics", {})),
            retirement_scenarios=dict(snapshot.get("retirement_scenarios", {})),
            available_reports=tuple(snapshot.get("available_reports", ())),
            section_completion=dict(snapshot.get("section_completion", {})),
            knowledge_search=self._knowledge_search,
            deadline_at_monotonic=_deadline_at_monotonic(payload.get("deadline_at")),
            audit_sink=payload.get("tool_audit_sink"),
        )
        return await self._registry.invoke(
            request.tool_name,
            request.arguments,
            context,
        )


def build_production_tool_runtime(
    *,
    registry: FunctionToolRegistry | None = None,
    knowledge_search: KnowledgeSearch | None = None,
) -> FunctionToolRuntime:
    """Bind the approved operational retriever without importing it in tests."""

    if registry is None:
        from .catalog import build_default_registry

        registry = build_default_registry()
    if knowledge_search is None:
        from rag.retriever import search_financial_knowledge

        knowledge_search = search_financial_knowledge
    return FunctionToolRuntime(registry, knowledge_search=knowledge_search)


def _deadline_at_monotonic(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        deadline = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return monotonic() - 1
    if deadline.tzinfo is None:
        return monotonic() - 1
    remaining = (
        deadline.astimezone(timezone.utc) - datetime.now(timezone.utc)
    ).total_seconds()
    return monotonic() + max(0.0, remaining)


def _runtime_failure(
    tool_name: str,
    code: ToolErrorCode,
    message: str,
) -> ToolCallResult:
    return ToolCallResult(
        tool_name=tool_name,
        tool_version="unknown",
        ok=False,
        error=ToolError(code=code, message=message),
    )
