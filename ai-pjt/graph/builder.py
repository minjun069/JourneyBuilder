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
from core.runtime_instrumentation import RuntimeStage, SecurityDecision
from core.runtime_instrumentation_adapter import RuntimeInstrumentationAdapter
from graph.executor import (
    RuntimeExecutor,
    execute_route_plan,
    validate_agent_executor_registry,
)
from graph.planner import create_route_plan, plan_tasks_with_model
from graph.router import classify_task_with_model
from graph.state import ChatState
from graph.task_contracts import failed_task_result
from guardrails.boundary_policy import (
    BOUNDARY_POLICY_VERSION,
    evaluate_user_message,
)
from guardrails.plan_policy import evaluate_route_plan
from tools.output_validation import validate_form_update, validate_form_update_contract
from tools.runtime import ToolCallBudget


def _looks_like_qa(message: str) -> bool:
    compact = message.strip().replace(" ", "")
    if not compact:
        return False
    return (
        "?" in compact
        or compact.endswith(("뭐야", "무엇인가요", "왜요", "인가요", "나요", "까요"))
        or any(token in compact for token in ("설명해줘", "알려줘", "무슨뜻", "어떻게계산"))
    )


def _pause_elicit_state(existing: Mapping[str, Any]) -> dict[str, Any]:
    phase = existing.get("phase")
    if phase not in {"asking", "confirming"}:
        raise ValueError("only an active Elicit state can be paused")
    paused = deepcopy(dict(existing))
    paused["paused_from"] = phase
    paused["phase"] = "paused"
    return paused


def _with_elicit_resume_action(
    output: dict[str, Any],
    elicit_state: Any,
) -> dict[str, Any]:
    if (
        isinstance(elicit_state, Mapping)
        and elicit_state.get("phase") == "paused"
        and output.get("ui_action") is None
    ):
        return {
            **output,
            "ui_action": {"type": "resume_elicit", "label": "이어서 계속"},
        }
    return output


def _direct_agent_plan(
    *,
    request_id: str,
    task_type: str,
    description: str,
    agent: str,
    state: Mapping[str, Any],
    reason_code: str = "trusted_route",
) -> dict[str, Any]:
    produces = "text_response"
    task = {
        "id": "task-1",
        "type": task_type,
        "description": description,
        "depends_on": [],
        "bindings": {},
        "executor_kind": "agent",
        "produces": produces,
        "step_id": state["step_id"],
        "route": {
            "task_id": "task-1",
            "executor_kind": "agent",
            "executor": agent,
            "reason_code": reason_code,
        },
    }
    return {
        "schema_version": "1.0",
        "plan_id": f"plan-{request_id}",
        "tasks": [task],
        "chart_payload": deepcopy(state.get("chart_payload")),
        "request_mode": state.get("request_mode", "chat"),
        "review_trigger": state.get("review_trigger"),
        "elicit_action": state.get("elicit_action"),
        "elicit_state": deepcopy(state.get("elicit_state", {})),
        "context_scope": deepcopy(state.get("context_scope", {})),
        "conversation_context": deepcopy(state.get("conversation_context", {})),
    }


def _policy_blocked_result(
    *,
    state: Mapping[str, Any],
    plan_id: str,
    reason_code: str,
) -> dict[str, Any]:
    return {
        "route_plan": {
            "schema_version": "1.0",
            "plan_id": plan_id,
            "tasks": [],
        },
        "plan_issues": [{
            "code": reason_code,
            "path": "security_policy",
            "message": "보안 정책이 요청의 실행을 차단했습니다.",
        }],
        "output": {
            "response": "요청에 포함된 지시를 안전하게 처리할 수 없어 실행하지 않았습니다.",
            "step_id": str(state.get("step_id", "step-unknown")),
            "action": None,
        },
        "security_blocked": True,
    }


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


def _agent_executor(agent_name: str, agent_graph: Any) -> RuntimeExecutor:
    async def execute(payload: dict[str, Any]) -> Mapping[str, Any]:
        task = payload["task"]
        task_id = str(task.get("id", "task-unknown"))
        is_fallback = agent_name == "fallback"
        agent_state = {
            "message": "" if is_fallback else payload["message"],
            "step_id": payload["step_id"],
            "customer_info": {} if is_fallback else payload["customer_info"],
            "field_registry": [] if is_fallback else payload["field_registry"],
            "bound_inputs": {} if is_fallback else payload["bound_inputs"],
            "current_task": {"id": task_id, "type": "fallback"} if is_fallback else deepcopy(task),
            "next": payload["next"],
            "chart_payload": None if is_fallback else payload.get("chart_payload"),
            "request_mode": payload.get("request_mode", "chat"),
            "review_trigger": payload.get("review_trigger"),
            "elicit_action": payload.get("elicit_action"),
            "elicit_state": {} if is_fallback else deepcopy(payload.get("elicit_state", {})),
            "context_scope": {} if is_fallback else deepcopy(payload.get("context_scope", {})),
            "conversation_context": {} if is_fallback else deepcopy(payload.get("conversation_context", {})),
            "request_id": payload.get("request_id"),
            "fallback_category": task.get("route", {}).get("reason_code"),
        }
        result = await agent_graph.ainvoke(agent_state)
        if (
            agent_name == "input_assist"
            and isinstance(result, Mapping)
            and result.get("reason_code") == "reference_target_not_allowed"
        ):
            return failed_task_result(task_id, "reference_target_not_allowed")
        raw_output = result.get("output") if isinstance(result, Mapping) else None
        validation = validate_form_update_contract({
            **agent_state,
            "output": raw_output,
        })
        if not validation.valid:
            return failed_task_result(task_id, "artifact_invalid")

        output = validation.output
        action = output.get("action")
        if isinstance(action, Mapping):
            artifact = deepcopy(dict(action))
        else:
            artifact = {
                "type": "text_response",
                "text": output["response"],
            }
        task_result = {
            "task_id": task_id,
            "status": "completed",
            "response": output["response"],
            "artifact": artifact,
        }
        for key in ("review", "ui_action"):
            if isinstance(output.get(key), Mapping):
                task_result[key] = deepcopy(dict(output[key]))
        if isinstance(output.get("elicit_capture"), Mapping):
            capture_validation = validate_form_update_contract({
                **agent_state,
                "output": {
                    "response": output["response"],
                    "step_id": payload["step_id"],
                    "action": dict(output["elicit_capture"]),
                },
            })
            if not capture_validation.valid:
                return failed_task_result(task_id, "artifact_invalid")
            task_result["elicit_capture"] = deepcopy(dict(output["elicit_capture"]))
        if isinstance(result, Mapping) and isinstance(result.get("elicit_state"), Mapping):
            task_result["state_update"] = {
                "elicit_state": deepcopy(dict(result["elicit_state"]))
            }
        return task_result

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
    instrumentation: RuntimeInstrumentationAdapter,
    planner=plan_tasks_with_model,
    classifier=classify_task_with_model,
    agent_graphs: Mapping[str, Any] | None = None,
    tool_executors: Mapping[str, RuntimeExecutor] | None = None,
):
    """Build the planner → sequential executor → validator orchestration graph."""

    if not isinstance(instrumentation, RuntimeInstrumentationAdapter):
        raise TypeError("build_graph requires a RuntimeInstrumentationAdapter")

    active_graphs = dict(
        _default_agent_graphs() if agent_graphs is None else agent_graphs
    )
    agent_executors = {
        name: _agent_executor(name, graph)
        for name, graph in active_graphs.items()
    }
    validate_agent_executor_registry(agent_executors)
    active_tools = dict(tool_executors or {})

    def plan_request(state: ChatState) -> dict[str, Any]:
        paused_state = None
        request_id = str(state.get("request_id") or "request-unknown")
        plan_id = f"plan-{request_id}"
        scope = state.get("context_scope", {})
        thread_id = str(
            state.get("thread_id")
            or (scope.get("thread_id") if isinstance(scope, Mapping) else None)
            or "thread-unknown"
        )
        observed = instrumentation.bind(
            request_id=request_id,
            thread_id=thread_id,
            plan_id=plan_id,
            step_id=str(state.get("step_id") or "step-unknown"),
        )
        planner_span = observed.start(RuntimeStage.PLANNER)
        validation_span = observed.start(RuntimeStage.PLAN_VALIDATION)

        def guard_plan(plan: Mapping[str, Any]):
            decision = evaluate_route_plan(
                plan,
                step_id=str(state.get("step_id") or "step-unknown"),
                registered_tool_capabilities=tuple(active_tools),
            )
            observed.record_security_decision(
                task_id="plan",
                boundary="plan_guardrail",
                decision=(
                    SecurityDecision.ALLOWED
                    if decision.allowed
                    else SecurityDecision.DENIED
                ),
                actor_kind="runtime",
                actor_name="route_planner",
                target_name="route_plan",
                target_version="1.0",
                call_source="graph_plan",
                reason_code=decision.reason_code,
            )
            return decision

        input_decision = evaluate_user_message(str(state.get("message", "")))
        observed.record_security_decision(
            task_id="request",
            boundary="input_guardrail",
            decision=(
                SecurityDecision.ALLOWED
                if input_decision.allowed
                else SecurityDecision.DENIED
            ),
            actor_kind="user",
            actor_name="requester",
            target_name="user_message",
            target_version=BOUNDARY_POLICY_VERSION,
            call_source="graph_plan",
            reason_code=input_decision.reason_code,
        )
        if not input_decision.allowed:
            observed.fail(planner_span, reason_code=input_decision.reason_code.lower())
            observed.fail(
                validation_span,
                reason_code=input_decision.reason_code.lower(),
            )
            return _policy_blocked_result(
                state=state,
                plan_id=plan_id,
                reason_code=input_decision.reason_code,
            )
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
            observed.fail(planner_span, reason_code="context_invalid")
            observed.fail(validation_span, reason_code="context_invalid")
            fallback_plan = _direct_agent_plan(
                request_id=str(state.get("request_id", "request-unknown")),
                task_type="fallback",
                description="안전한 대화 문맥을 복원할 수 없음",
                agent="fallback",
                state=state,
                reason_code="plan_invalid",
            )
            decision = guard_plan(fallback_plan)
            if not decision.allowed:
                return _policy_blocked_result(
                    state=state,
                    plan_id=plan_id,
                    reason_code=decision.reason_code,
                )
            return {
                "route_plan": fallback_plan,
                "plan_issues": [{
                    "code": "CONTEXT_INVALID",
                    "path": "conversation_context",
                    "message": "대화 문맥을 안전하게 복원하지 못했습니다.",
                }],
            }

        if state.get("request_mode") == "error_review":
            direct_plan = _direct_agent_plan(
                request_id=str(request_id or "request-unknown"),
                task_type="review_validation_error",
                description="현재 draft의 오기·단위 착오·문맥 불일치 검토",
                agent="error_review",
                state=state,
            )
            decision = guard_plan(direct_plan)
            observed.succeed(planner_span)
            if decision.allowed:
                observed.succeed(validation_span)
            else:
                observed.fail(validation_span, reason_code=decision.reason_code.lower())
                return _policy_blocked_result(
                    state=state,
                    plan_id=plan_id,
                    reason_code=decision.reason_code,
                )
            return {
                "route_plan": direct_plan,
                "plan_issues": [],
            }
        existing_elicit = state.get("elicit_state", {})
        phase = existing_elicit.get("phase") if isinstance(existing_elicit, Mapping) else None
        elicit_action = state.get("elicit_action")
        if elicit_action in {"start", "resume"} or (
            phase in {"asking", "confirming"} and not _looks_like_qa(state["message"])
        ):
            direct_plan = _direct_agent_plan(
                request_id=str(request_id or "request-unknown"),
                task_type="collect_qualitative_info",
                description=state["message"],
                agent="elicit",
                state=state,
            )
            decision = guard_plan(direct_plan)
            observed.succeed(planner_span)
            if decision.allowed:
                observed.succeed(validation_span)
            else:
                observed.fail(validation_span, reason_code=decision.reason_code.lower())
                return _policy_blocked_result(
                    state=state,
                    plan_id=plan_id,
                    reason_code=decision.reason_code,
                )
            return {
                "route_plan": direct_plan,
                "plan_issues": [],
            }
        if phase in {"asking", "confirming"} and _looks_like_qa(state["message"]):
            paused_state = _pause_elicit_state(existing_elicit)
            state = {**state, "elicit_state": paused_state}
        try:
            validation = create_route_plan(
                message=state["message"],
                step_id=state["step_id"],
                context_window=window,
                planner=planner,
                classifier=classifier,
                registered_tool_capabilities=tuple(active_tools),
                plan_id=plan_id,
            )
        except Exception:
            observed.fail(planner_span, reason_code="planner_error")
            observed.fail(validation_span, reason_code="plan_validation_error")
            raise
        if not validation.valid:
            issue_codes = {
                str(issue.code).lower()
                for issue in validation.issues
            }
            if "planner_error" in issue_codes:
                observed.fail(planner_span, reason_code="planner_error")
            else:
                observed.succeed(planner_span)
            observed.fail(
                validation_span,
                reason_code=(next(iter(sorted(issue_codes)), "plan_invalid")),
            )
            fallback_plan = _direct_agent_plan(
                request_id=str(request_id or "request-unknown"),
                task_type="fallback",
                description="유효한 실행 계획을 만들 수 없음",
                agent="fallback",
                state=state,
                reason_code="plan_invalid",
            )
            decision = guard_plan(fallback_plan)
            if not decision.allowed:
                return _policy_blocked_result(
                    state=state,
                    plan_id=plan_id,
                    reason_code=decision.reason_code,
                )
            return {
                "route_plan": fallback_plan,
                "plan_issues": [issue.to_dict() for issue in validation.issues],
            }
        plan = deepcopy(validation.plan)
        plan.update({
            "chart_payload": deepcopy(state.get("chart_payload")),
            "request_mode": state.get("request_mode", "chat"),
            "review_trigger": state.get("review_trigger"),
            "elicit_action": state.get("elicit_action"),
            "elicit_state": deepcopy(state.get("elicit_state", {})),
            "context_scope": deepcopy(state.get("context_scope", {})),
            "conversation_context": deepcopy(state.get("conversation_context", {})),
        })
        decision = guard_plan(plan)
        observed.succeed(planner_span)
        if not decision.allowed:
            observed.fail(validation_span, reason_code=decision.reason_code.lower())
            return {
                **_policy_blocked_result(
                    state=state,
                    plan_id=plan_id,
                    reason_code=decision.reason_code,
                ),
                **({"elicit_state": paused_state} if paused_state is not None else {}),
            }
        observed.succeed(validation_span)
        return {
            "route_plan": plan,
            "plan_issues": [],
            **({"elicit_state": paused_state} if paused_state is not None else {}),
        }

    async def execute_plan(state: ChatState) -> dict[str, Any]:
        if state.get("plan_issues") and not state.get("route_plan", {}).get("tasks"):
            return {"task_results": {}, "output": state["output"]}
        customer_info = state.get("customer_info", {})
        registry = state.get("field_registry", [])
        budget_state = state.get("execution_budget", {})
        task_limit = (
            budget_state.get("task_limit", 3)
            if isinstance(budget_state, Mapping)
            else 3
        )
        plan = state["route_plan"]
        scope = state.get("context_scope", {})
        observed = instrumentation.bind(
            request_id=str(state.get("request_id") or "request-unknown"),
            thread_id=str(
                state.get("thread_id")
                or (scope.get("thread_id") if isinstance(scope, Mapping) else None)
                or "thread-unknown"
            ),
            plan_id=str(plan.get("plan_id") or "plan-unknown"),
            step_id=str(state.get("step_id") or "step-unknown"),
        )
        report = await execute_route_plan(
            plan=plan,
            step_id=state["step_id"],
            customer_info=customer_info if isinstance(customer_info, Mapping) else {},
            field_registry=registry if isinstance(registry, list) else [],
            agent_executors=agent_executors,
            tool_executors=active_tools,
            task_limit=task_limit,
            request_id=str(state.get("request_id", "request-unknown")),
            deadline_at=(
                str(budget_state.get("deadline_at"))
                if isinstance(budget_state, Mapping)
                and budget_state.get("deadline_at") is not None
                else None
            ),
            tool_context=(
                state.get("tool_context", {})
                if isinstance(state.get("tool_context", {}), Mapping)
                else {}
            ),
            tool_call_budget=ToolCallBudget(
                limit=(
                    int(budget_state.get("tool_call_limit", 3))
                    if isinstance(budget_state, Mapping)
                    else 3
                ),
                used=(
                    int(budget_state.get("tool_calls_used", 0))
                    if isinstance(budget_state, Mapping)
                    else 0
                ),
            ),
            instrumentation=observed,
        )
        updated_budget = (
            {
                **budget_state,
                "tasks_used": report.tasks_used,
                "tool_calls_used": report.tool_calls_used,
            }
            if isinstance(budget_state, Mapping)
            else {"task_limit": task_limit, "tasks_used": report.tasks_used}
        )
        state_update = report.state_update or {}
        output = report.output
        current_elicit = state_update.get("elicit_state", state.get("elicit_state"))
        if isinstance(output, dict):
            output = _with_elicit_resume_action(output, current_elicit)
        return {
            "task_results": report.task_results,
            "output": output,
            "execution_budget": updated_budget,
            **state_update,
        }

    def persist_context(state: ChatState) -> dict[str, Any]:
        if state.get("security_blocked") is True:
            return {}
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
