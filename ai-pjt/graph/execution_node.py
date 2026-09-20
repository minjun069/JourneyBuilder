"""ChatState adapter for validated route-plan execution."""

from __future__ import annotations

from collections.abc import Mapping

from core.runtime_instrumentation_adapter import RuntimeInstrumentationAdapter
from graph.aggregation import AggregateComposer
from graph.executor import execute_route_plan
from graph.runtime_types import RuntimeExecutor
from graph.state import ChatState


def build_execution_node(
    *,
    instrumentation: RuntimeInstrumentationAdapter,
    agent_executors: Mapping[str, RuntimeExecutor],
    tool_executors: Mapping[str, RuntimeExecutor],
    response_composer: AggregateComposer | None,
):
    """Bind runtime dependencies and return the execution graph node."""

    async def execute_plan(state: ChatState) -> dict:
        route_plan = state.get("route_plan", {})
        if state.get("plan_issues") and not route_plan.get("tasks"):
            return {"task_results": {}, "output": state["output"]}

        customer_info = state.get("customer_info", {})
        observed = instrumentation.bind(
            request_id=str(state.get("request_id") or "request-unknown"),
            thread_id=str(state.get("thread_id") or "thread-unknown"),
            step_id=str(state.get("step_id") or "step-unknown"),
        )
        report = await execute_route_plan(
            plan=route_plan,
            step_id=state["step_id"],
            customer_info=(
                customer_info if isinstance(customer_info, Mapping) else {}
            ),
            agent_executors=agent_executors,
            tool_executors=tool_executors,
            request_id=str(state.get("request_id") or "request-unknown"),
            instrumentation=observed,
            response_composer=response_composer,
        )
        return {
            "task_results": report.task_results,
            "output": report.output,
        }

    return execute_plan
