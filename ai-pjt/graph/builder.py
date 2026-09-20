from __future__ import annotations

import logging
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from core.context import (
    ConversationContext,
    ConversationTurn,
    RetrievedMemory,
    advance_context,
    build_context_window,
    resolve_confirmed_facts,
)
from core.field_registry import load_field_registry
from core.model_boundary import bind_model_call_recorder
from core.operation_observation import bind_operation_recorder
from core.observability_masking import (
    exception_identity_fields,
    masked_exception_fields,
)
from core.prompt_registry import bind_prompt_trace_sink
from core.runtime_instrumentation import RuntimeStage, SecurityDecision
from core.runtime_instrumentation_adapter import RuntimeInstrumentationAdapter
from graph.executor import (
    AggregateComposer,
    RuntimeExecutor,
    compose_aggregate_response,
    execute_route_plan,
    failure_response,
    validate_agent_executor_registry,
)
from graph.planner import context_window_payload, create_route_plan, plan_tasks_with_model
from graph.router import allowed_task_types_for, enrich_route_plan
from graph.state import ChatState
from graph.task_contracts import failed_task_result
from guardrails.boundary_policy import (
    BOUNDARY_POLICY_VERSION,
    evaluate_user_message,
)
from guardrails.plan_policy import evaluate_route_plan
from tools.output_validation import validate_form_update, validate_form_update_contract


logger = logging.getLogger(__name__)


def _direct_planner_output(
    *,
    task_type: str,
    description: str,
) -> dict[str, Any]:
    return {
        "status": "executable",
        "tasks": [{
        "id": "direct",
        "type": task_type,
        "description": description,
        "depends_on": [],
        "bindings": {},
        }],
    }


def _policy_blocked_result(
    *,
    state: Mapping[str, Any],
    reason_code: str,
) -> dict[str, Any]:
    return {
        "route_plan": {
            "schema_version": "1.0",
            "tasks": [],
        },
        "plan_issues": [{
            "code": reason_code,
            "path": "security_policy",
            "message": "보안 정책이 요청의 실행을 차단했습니다.",
            "security_blocked": True,
        }],
        "output": {
            "response": "요청에 포함된 지시를 안전하게 처리할 수 없어 실행하지 않았습니다.",
            "step_id": str(state.get("step_id", "step-unknown")),
            "action": None,
        },
    }


def _plan_failure_result(
    *,
    state: Mapping[str, Any],
    issues: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "route_plan": {
            "schema_version": "1.0",
            "tasks": [],
        },
        "plan_issues": issues,
        "output": {
            "response": failure_response(
                "plan_invalid",
                detail_codes=[issue.get("code", "") for issue in issues],
            ),
            "step_id": str(state.get("step_id", "step-unknown")),
            "action": None,
        },
    }


def _default_agent_graphs() -> dict[str, Any]:
    # Keep provider-dependent Agent imports behind the production build boundary.
    from agents import chart_interpret, elicit, error_review, input_assist, qa

    return {
        "qa": qa.graph,
        "input_assist": input_assist.graph,
        "elicit": elicit.graph,
        "error_review": error_review.graph,
        "chart_interpret": chart_interpret.graph,
    }


def _agent_executor(agent_name: str, agent_graph: Any) -> RuntimeExecutor:
    async def execute(payload: dict[str, Any]) -> Mapping[str, Any]:
        task = payload["task"]
        task_id = str(task.get("id", "task-unknown"))
        agent_state = {
            "message": payload["message"],
            "step_id": payload["step_id"],
            "customer_info": payload["customer_info"],
            "task_input": deepcopy(payload["task_input"]),
            "chart_payload": payload.get("chart_payload"),
            "request_mode": payload.get("request_mode", "chat"),
            "conversation_context": deepcopy(payload.get("conversation_context", {})),
            "request_id": payload.get("request_id"),
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
        for key in ("review",):
            if isinstance(output.get(key), Mapping):
                task_result[key] = deepcopy(dict(output[key]))
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
    agent_graphs: Mapping[str, Any] | None = None,
    tool_executors: Mapping[str, RuntimeExecutor] | None = None,
    response_composer: AggregateComposer | None = compose_aggregate_response,
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
        request_id = str(state.get("request_id") or "request-unknown")
        thread_id = str(state.get("thread_id") or "thread-unknown")
        field_registry = load_field_registry(state["step_id"])
        observed = instrumentation.bind(
            request_id=request_id,
            thread_id=thread_id,
            step_id=str(state.get("step_id") or "step-unknown"),
        )
        planner_input = {
            "message": str(state.get("message", "")),
            "step_id": str(state.get("step_id") or "step-unknown"),
            "request_mode": str(state.get("request_mode", "chat")),
            "customer_info": deepcopy(state.get("customer_info", {})),
            "context": {
                "conversation": deepcopy(state.get("conversation_context", {})),
                "retrieved_memories": deepcopy(
                    state.get("retrieved_memories", [])
                ),
            },
        }
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
            return _policy_blocked_result(
                state=state,
                reason_code=input_decision.reason_code,
            )
        planner_span = observed.start(
            RuntimeStage.PLANNER,
            input=planner_input,
        )
        context_payload = state.get("conversation_context")
        try:
            context = (
                ConversationContext.from_state(context_payload)
                if isinstance(context_payload, Mapping)
                else ConversationContext()
            )
            customer_info = state.get("customer_info", {})
            confirmed_facts = (
                _flatten_customer_info(
                    customer_info,
                    field_registry=field_registry,
                    step_id=state["step_id"],
                )
                if isinstance(customer_info, Mapping)
                else {}
            )
            window = build_context_window(
                context=context,
                current_confirmed_facts=confirmed_facts,
                retrieved_memories=tuple(
                    RetrievedMemory.from_state(item)
                    for item in state.get("retrieved_memories", [])
                    if isinstance(item, Mapping)
                ),
            )
        except (TypeError, ValueError) as error:
            error_identity = exception_identity_fields(error)
            validation_span = observed.start(RuntimeStage.PLAN_VALIDATION)
            observed.fail(
                validation_span,
                reason_code="context_invalid",
                output={"reason_code": "context_invalid"},
                **error_identity,
            )
            observed.fail(
                planner_span,
                reason_code="context_invalid",
                output={"reason_code": "context_invalid"},
                **error_identity,
            )
            return _plan_failure_result(
                state=state,
                issues=[{
                    "code": "CONTEXT_INVALID",
                    "path": "conversation_context",
                    "message": "대화 문맥을 안전하게 복원하지 못했습니다.",
                }],
            )

        request_mode = state.get("request_mode")
        direct_request = {
            "error_review": (
                "review_validation_error",
                "현재 draft의 오기·단위 착오·문맥 불일치 검토",
            ),
            "presentation_entry": (
                "interpret_chart",
                "Presentation 단계 최초 진입 핵심 안내",
            ),
        }.get(request_mode)
        if direct_request is not None:
            task_type, description = direct_request
            direct_output = _direct_planner_output(
                task_type=task_type,
                description=description,
            )
            direct_candidate = enrich_route_plan(
                raw_plan=direct_output,
                step_id=state["step_id"],
                registered_tool_capabilities=tuple(active_tools),
            )
            validation_span = observed.start(RuntimeStage.PLAN_VALIDATION)
            decision = guard_plan(direct_candidate)
            if decision.allowed:
                observed.succeed(
                    validation_span,
                    output={"valid": True, "issues": []},
                )
            else:
                observed.fail(
                    validation_span,
                    reason_code=decision.reason_code.lower(),
                    output=decision.to_dict(),
                )
                observed.fail(
                    planner_span,
                    reason_code=decision.reason_code.lower(),
                    output=decision.to_dict(),
                )
                return _plan_failure_result(
                    state=state,
                    issues=[
                        violation.to_dict()
                        for violation in decision.violations
                    ],
                )
            direct_plan = deepcopy(decision.plan)
            direct_plan.update({
                "chart_payload": deepcopy(state.get("chart_payload")),
                "request_mode": state.get("request_mode", "chat"),
                "conversation_context": context_window_payload(window),
            })
            observed.succeed(planner_span, output=direct_plan)
            return {
                "route_plan": direct_plan,
                "plan_issues": [],
            }
        try:
            with bind_operation_recorder(observed.record_operation):
                with bind_model_call_recorder(observed.record_model_call):
                    with bind_prompt_trace_sink(
                        observed.record_prompt_invocation
                    ):
                        validation = create_route_plan(
                            message=state["message"],
                            step_id=state["step_id"],
                            context_window=window,
                            planner=planner,
                            enricher=enrich_route_plan,
                            evaluator=guard_plan,
                            allowed_task_types=allowed_task_types_for(
                                step_id=state["step_id"],
                                registered_tool_capabilities=tuple(active_tools),
                            ),
                            registered_tool_capabilities=tuple(active_tools),
                        )
        except Exception as error:
            error_identity = exception_identity_fields(error)
            logger.error(
                "plan request raised",
                extra={
                    "event": "planner_error",
                    "detail": masked_exception_fields(
                        error,
                        request_id=request_id,
                        step_id=str(state.get("step_id", "step-unknown")),
                    ),
                },
            )
            validation_span = observed.start(RuntimeStage.PLAN_VALIDATION)
            observed.fail(
                validation_span,
                reason_code="planner_error",
                output={"reason_code": "planner_error"},
                **error_identity,
            )
            observed.fail(
                planner_span,
                reason_code="planner_error",
                output={"reason_code": "planner_error"},
                **error_identity,
            )
            raise
        validation_span = observed.start(RuntimeStage.PLAN_VALIDATION)
        if not validation.valid:
            issue_codes = {
                str(issue.code).lower()
                for issue in validation.issues
            }
            diagnostic = next(
                (
                    item
                    for item in validation.diagnostics
                    if item.get("component") == "planner"
                ),
                None,
            )
            failure_output = {
                "valid": False,
                "issues": [issue.to_dict() for issue in validation.issues],
                "diagnostics": list(validation.diagnostics),
            }
            observed.fail(
                validation_span,
                reason_code=(next(iter(sorted(issue_codes)), "plan_invalid")),
                output=failure_output,
            )
            if "planner_error" in issue_codes:
                observed.fail(
                    planner_span,
                    reason_code="planner_error",
                    output=failure_output,
                    **(
                        {
                            "error_type": diagnostic["error_type"],
                            "error_fingerprint": diagnostic["error_fingerprint"],
                        }
                        if diagnostic is not None
                        else {}
                    ),
                )
            else:
                observed.succeed(planner_span, output=failure_output)
            failure = _plan_failure_result(
                state=state,
                issues=[issue.to_dict() for issue in validation.issues],
            )
            return failure
        plan = deepcopy(validation.plan)
        plan.update({
            "chart_payload": deepcopy(state.get("chart_payload")),
            "request_mode": state.get("request_mode", "chat"),
            "conversation_context": context_window_payload(window),
        })
        planner_output = {
            "plan": validation.plan,
            "diagnostics": list(validation.diagnostics),
        }
        observed.succeed(
            validation_span,
            output={"valid": True, "issues": []},
        )
        observed.succeed(planner_span, output=planner_output)
        return {
            "route_plan": plan,
            "plan_issues": [],
        }

    async def execute_plan(state: ChatState) -> dict[str, Any]:
        if state.get("plan_issues") and not state.get("route_plan", {}).get("tasks"):
            return {"task_results": {}, "output": state["output"]}
        customer_info = state.get("customer_info", {})
        plan = state["route_plan"]
        observed = instrumentation.bind(
            request_id=str(state.get("request_id") or "request-unknown"),
            thread_id=str(state.get("thread_id") or "thread-unknown"),
            step_id=str(state.get("step_id") or "step-unknown"),
        )
        report = await execute_route_plan(
            plan=plan,
            step_id=state["step_id"],
            customer_info=customer_info if isinstance(customer_info, Mapping) else {},
            agent_executors=agent_executors,
            tool_executors=active_tools,
            request_id=str(state.get("request_id", "request-unknown")),
            instrumentation=observed,
            response_composer=response_composer,
        )
        return {
            "task_results": report.task_results,
            "output": report.output,
        }

    def persist_context(state: ChatState) -> dict[str, Any]:
        issues = state.get("plan_issues", [])
        if any(
            isinstance(issue, Mapping) and issue.get("security_blocked") is True
            for issue in issues
        ):
            return {}
        context_payload = state.get("conversation_context")
        if not isinstance(context_payload, Mapping):
            return {}
        context = ConversationContext.from_state(context_payload)
        output = state.get("output", {})
        response = output.get("response", "") if isinstance(output, Mapping) else ""
        request_id = state.get("request_id")
        if not isinstance(request_id, str) or not request_id.strip():
            return {}
        updated = advance_context(
            context=context,
            new_turn=ConversationTurn(
                turn_id=request_id,
                user_message=state["message"],
                assistant_message=response if isinstance(response, str) else "",
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
