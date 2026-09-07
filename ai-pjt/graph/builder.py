from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from core.context import (
    ConversationContext,
    ConversationTurn,
    ContextScope,
    advance_context,
    build_context_window,
    resolve_confirmed_facts,
)
from graph.executor import (
    RuntimeExecutor,
    execute_route_plan,
    validate_agent_executor_registry,
)
from graph.planner import create_route_plan, plan_tasks_with_model
from graph.router import classify_task_with_model
from graph.state import ChatState
from graph.task_contracts import failed_task_result
from tools.output_validation import validate_form_update, validate_form_update_contract


SAFE_PLAN_FAILURE_RESPONSE = (
    "요청을 안전한 실행 계획으로 나누지 못했습니다. "
    "원하는 작업을 조금 더 구체적으로 나누어 다시 요청해 주세요."
)


def _default_agent_graphs() -> dict[str, Any]:
    # Keep provider-dependent Agent imports behind the production build boundary.
    from agents import chart_interpret, elicit, error_review, fallback, input_assist, qa

    return {
        "qa": qa.graph,
        "input_assist": input_assist.graph,
        "elicit": elicit.graph,
        "error_review": error_review.graph,
        "chart_interpret": chart_interpret.graph,
        "fallback": fallback.graph,
    }


def _agent_executor(agent_graph: Any) -> RuntimeExecutor:
    async def execute(payload: dict[str, Any]) -> Mapping[str, Any]:
        task = payload["task"]
        task_id = str(task.get("id", "task-unknown"))
        agent_state = {
            "message": payload["message"],
            "step_id": payload["step_id"],
            "customer_info": payload["customer_info"],
            "field_registry": payload["field_registry"],
            "bound_inputs": payload["bound_inputs"],
            "current_task": deepcopy(task),
            "next": payload["next"],
        }
        result = await agent_graph.ainvoke(agent_state)
        raw_output = result.get("output") if isinstance(result, Mapping) else None
        validation = validate_form_update_contract({
            **agent_state,
            "output": raw_output,
        })
        if not validation.valid:
            return failed_task_result(task_id, "output_schema_invalid")

        output = validation.output
        action = output.get("action")
        if isinstance(action, Mapping):
            artifact = deepcopy(dict(action))
        else:
            artifact = {
                "type": "text_response",
                "text": output["response"],
            }
        return {
            "task_id": task_id,
            "status": "completed",
            "response": output["response"],
            "artifact": artifact,
        }

    return execute


def _flatten_customer_info(
    customer_info: Mapping[str, Any],
    *,
    field_registry: list[dict[str, Any]],
    step_id: str,
) -> dict[str, Any]:
    """Build the F02 current-snapshot facts without exposing an unbounded blob."""

    flattened: dict[str, Any] = {}

    def visit(prefix: str, value: Any, target: dict[str, Any]) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                path = f"{prefix}.{key}" if prefix else str(key)
                visit(path, child, target)
            return
        if prefix:
            target[prefix] = deepcopy(value)

    saved_data = customer_info.get("saved_data", {})
    current_form = customer_info.get("current_form", {})
    if isinstance(saved_data, Mapping):
        visit("", saved_data, flattened)
    if not isinstance(current_form, Mapping):
        current_form = {}

    active_paths: list[str] = []
    canonical_current: dict[str, Any] = {}
    for field in field_registry:
        if not isinstance(field, Mapping) or field.get("step_id") != step_id:
            continue
        section = field.get("section")
        path = field.get("path")
        if not isinstance(section, str) or not isinstance(path, str):
            continue
        canonical_path = f"{section}.{path}"
        active_paths.append(canonical_path)
        if canonical_path in current_form:
            canonical_current[canonical_path] = deepcopy(current_form[canonical_path])
        elif path in current_form:
            canonical_current[canonical_path] = deepcopy(current_form[path])
        else:
            section_form = current_form.get(section)
            if isinstance(section_form, Mapping) and path in section_form:
                canonical_current[canonical_path] = deepcopy(section_form[path])

    return dict(resolve_confirmed_facts(
        active_step_fields=active_paths,
        saved_data=flattened,
        current_form=canonical_current,
    ))


def build_graph(
    checkpointer: BaseCheckpointSaver,
    *,
    planner=plan_tasks_with_model,
    classifier=classify_task_with_model,
    agent_graphs: Mapping[str, Any] | None = None,
    tool_executors: Mapping[str, RuntimeExecutor] | None = None,
):
    """Build the planner → sequential executor → validator orchestration graph."""

    active_graphs = dict(
        _default_agent_graphs() if agent_graphs is None else agent_graphs
    )
    agent_executors = {
        name: _agent_executor(graph)
        for name, graph in active_graphs.items()
    }
    validate_agent_executor_registry(agent_executors)
    active_tools = dict(tool_executors or {})

    def plan_request(state: ChatState) -> dict[str, Any]:
        context_payload = state.get("conversation_context")
        try:
            context = (
                ConversationContext.from_state(context_payload)
                if isinstance(context_payload, Mapping)
                else ConversationContext()
            )
            customer_info = state.get("customer_info", {})
            registry = state.get("field_registry", [])
            confirmed_facts = (
                _flatten_customer_info(
                    customer_info,
                    field_registry=registry if isinstance(registry, list) else [],
                    step_id=state["step_id"],
                )
                if isinstance(customer_info, Mapping)
                else {}
            )
            window = build_context_window(
                context=context,
                current_confirmed_facts=confirmed_facts,
            )
        except (TypeError, ValueError):
            return {
                "route_plan": {"schema_version": "1.0", "tasks": []},
                "plan_issues": [{
                    "code": "CONTEXT_INVALID",
                    "path": "conversation_context",
                    "message": "대화 문맥을 안전하게 복원하지 못했습니다.",
                }],
                "output": {
                    "response": SAFE_PLAN_FAILURE_RESPONSE,
                    "step_id": state["step_id"],
                    "action": None,
                },
            }

        request_id = state.get("request_id")
        plan_id = (
            f"plan-{request_id}"
            if isinstance(request_id, str) and request_id.strip()
            else None
        )
        validation = create_route_plan(
            message=state["message"],
            step_id=state["step_id"],
            context_window=window,
            planner=planner,
            classifier=classifier,
            registered_tool_capabilities=tuple(active_tools),
            plan_id=plan_id,
        )
        if not validation.valid:
            return {
                "route_plan": validation.plan,
                "plan_issues": [issue.to_dict() for issue in validation.issues],
                "output": {
                    "response": SAFE_PLAN_FAILURE_RESPONSE,
                    "step_id": state["step_id"],
                    "action": None,
                },
            }
        return {
            "route_plan": validation.plan,
            "plan_issues": [],
        }

    async def execute_plan(state: ChatState) -> dict[str, Any]:
        if state.get("plan_issues"):
            return {"task_results": {}, "output": state["output"]}
        customer_info = state.get("customer_info", {})
        registry = state.get("field_registry", [])
        budget_state = state.get("execution_budget", {})
        task_limit = (
            budget_state.get("task_limit", 3)
            if isinstance(budget_state, Mapping)
            else 3
        )
        report = await execute_route_plan(
            plan=state["route_plan"],
            step_id=state["step_id"],
            customer_info=customer_info if isinstance(customer_info, Mapping) else {},
            field_registry=registry if isinstance(registry, list) else [],
            agent_executors=agent_executors,
            tool_executors=active_tools,
            task_limit=task_limit,
        )
        updated_budget = (
            {**budget_state, "tasks_used": report.tasks_used}
            if isinstance(budget_state, Mapping)
            else {"task_limit": task_limit, "tasks_used": report.tasks_used}
        )
        return {
            "task_results": report.task_results,
            "output": report.output,
            "execution_budget": updated_budget,
        }

    def persist_context(state: ChatState) -> dict[str, Any]:
        scope_payload = state.get("context_scope")
        context_payload = state.get("conversation_context")
        if not isinstance(scope_payload, Mapping) or not isinstance(
            context_payload,
            Mapping,
        ):
            return {}
        ContextScope.from_state(scope_payload)
        context = ConversationContext.from_state(context_payload)
        output = state.get("output", {})
        response = output.get("response", "") if isinstance(output, Mapping) else ""
        request_id = state.get("request_id")
        if not isinstance(request_id, str) or not request_id.strip():
            return {}
        customer_info = state.get("customer_info", {})
        registry = state.get("field_registry", [])
        confirmed_facts = (
            _flatten_customer_info(
                customer_info,
                field_registry=registry if isinstance(registry, list) else [],
                step_id=state["step_id"],
            )
            if isinstance(customer_info, Mapping)
            else {}
        )
        updated = advance_context(
            context=context,
            new_turn=ConversationTurn(
                turn_id=request_id,
                user_message=state["message"],
                assistant_message=response if isinstance(response, str) else "",
                confirmed_facts=confirmed_facts,
            ),
        )
        return {"conversation_context": updated.to_state()}

    builder = StateGraph(ChatState)
    builder.add_node("plan", plan_request)
    builder.add_node("execute", execute_plan)
    builder.add_node("validate", validate_form_update)
    builder.add_node("persist_context", persist_context)
    builder.add_edge(START, "plan")
    builder.add_edge("plan", "execute")
    builder.add_edge("execute", "validate")
    builder.add_edge("validate", "persist_context")
    builder.add_edge("persist_context", END)
    return builder.compile(checkpointer=checkpointer)
