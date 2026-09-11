"""Plan/task-aware production binding for provider-neutral Function tools."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from time import monotonic
from typing import Any, Mapping

from .contracts import (
    AuditSink,
    KnowledgeSearch,
    ToolAuditEvent,
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

        audit_sink = payload.get("tool_audit_sink")
        if not callable(audit_sink):
            return _runtime_failure(
                request.tool_name,
                ToolErrorCode.INTERNAL_ERROR,
                "마스킹된 도구 감사 경로가 runtime에 연결되지 않았습니다.",
            )

        definition = self._registry.definition_for(request.tool_name)
        task_type = task.get("type")
        route = task.get("route")
        from graph.planner import TASK_TYPE_CONTRACTS

        task_contract = TASK_TYPE_CONTRACTS.get(str(task_type))
        common_context_matches = (
            isinstance(task_type, str)
            and task_contract is not None
            and task.get("step_id") == step_id
            and task.get("executor_kind") == task_contract.get("executor_kind")
            and task.get("produces") == task_contract.get("produces")
            and isinstance(route, Mapping)
            and route.get("executor_kind") == task_contract.get("executor_kind")
            and definition is not None
            and request.agent in definition.allowed_agents
            and task_type in definition.allowed_task_types
        )
        if task_contract is not None and task_contract.get("executor_kind") == "tool":
            route_context_matches = (
                isinstance(route, Mapping)
                and task.get("capability") == task_contract.get("capability")
                and task.get("tool_name") == task_contract.get("tool_name")
                and task.get("authorized_agent")
                == task_contract.get("authorized_agent")
                and request.tool_name == task_contract.get("tool_name")
                and request.agent == task_contract.get("authorized_agent")
                and route.get("executor") == task_contract.get("capability")
            )
        else:
            route_context_matches = (
                route.get("executor") == request.agent
                if isinstance(route, Mapping)
                else False
            )
        canonical_context_matches = (
            common_context_matches and route_context_matches
        )
        if not canonical_context_matches:
            result = _runtime_failure(
                request.tool_name,
                ToolErrorCode.CONTEXT_MISMATCH,
                "검증된 task route와 도구 호출 문맥이 일치하지 않습니다.",
            )
            _audit_runtime_decision(
                audit_sink,
                request=request,
                request_id=request_id,
                plan_id=plan_id,
                task_id=task_id,
                step_id=step_id,
                task_type=str(task_type or "unknown"),
                tool_version=definition.version if definition else "unknown",
                argument_classes=definition.argument_classes if definition else (),
                result=result,
                decision="denied",
            )
            return result

        budget = payload.get("tool_call_budget")
        if not isinstance(budget, ToolCallBudget):
            return _runtime_failure(
                request.tool_name,
                ToolErrorCode.INTERNAL_ERROR,
                "도구 호출 예산이 runtime에 연결되지 않았습니다.",
            )
        if not budget.consume():
            result = _runtime_failure(
                request.tool_name,
                ToolErrorCode.BUDGET_EXCEEDED,
                "요청당 도구 호출 예산을 초과했습니다.",
            )
            assert definition is not None
            _audit_runtime_decision(
                audit_sink,
                request=request,
                request_id=request_id,
                plan_id=plan_id,
                task_id=task_id,
                step_id=step_id,
                task_type=task_type,
                tool_version=definition.version,
                argument_classes=definition.argument_classes,
                result=result,
                decision="allowed",
            )
            return result

        snapshot = payload.get("tool_context")
        if not isinstance(snapshot, Mapping):
            snapshot = {}
        context = ToolExecutionContext(
            request_id=request_id,
            plan_id=plan_id,
            task_id=task_id,
            step_id=step_id,
            agent=request.agent,
            task_type=task_type,
            call_source=TOOL_CALL_SOURCE,
            field_registry=tuple(snapshot.get("field_specs", ())),
            dashboard_metrics=dict(snapshot.get("dashboard_metrics", {})),
            retirement_scenarios=dict(snapshot.get("retirement_scenarios", {})),
            available_reports=tuple(snapshot.get("available_reports", ())),
            section_completion=dict(snapshot.get("section_completion", {})),
            knowledge_search=self._knowledge_search,
            deadline_at_monotonic=_deadline_at_monotonic(payload.get("deadline_at")),
            audit_sink=audit_sink,
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
    _validate_task_tool_authorizations(registry)
    return FunctionToolRuntime(registry, knowledge_search=knowledge_search)


def _validate_task_tool_authorizations(registry: FunctionToolRegistry) -> None:
    """Fail startup when canonical task and tool allowlists drift apart."""

    from graph.planner import TASK_TYPE_CONTRACTS

    for task_type, contract in TASK_TYPE_CONTRACTS.items():
        if contract.get("executor_kind") != "tool":
            continue
        tool_name = str(contract.get("tool_name", ""))
        agent = str(contract.get("authorized_agent", ""))
        definition = registry.definition_for(tool_name)
        if definition is None:
            raise ValueError(
                f"tool task authorization references an unknown tool: {task_type}"
            )
        if (
            agent not in definition.allowed_agents
            or task_type not in definition.allowed_task_types
        ):
            raise ValueError(
                f"tool task authorization is inconsistent: {task_type}"
            )


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


def _audit_runtime_decision(
    audit_sink: AuditSink,
    *,
    request: RuntimeToolRequest,
    request_id: str,
    plan_id: str,
    task_id: str,
    step_id: str,
    task_type: str,
    tool_version: str,
    argument_classes: tuple[str, ...],
    result: ToolCallResult,
    decision: str,
) -> None:
    audit_sink(ToolAuditEvent(
        request_id=request_id,
        plan_id=plan_id,
        task_id=task_id,
        step_id=step_id,
        task_type=task_type,
        tool_name=request.tool_name,
        tool_version=tool_version,
        agent=request.agent,
        decision=decision,
        status="failed",
        duration_ms=0,
        argument_classes=argument_classes,
        call_source=TOOL_CALL_SOURCE,
        error_code=result.error.code.value if result.error else None,
    ))
