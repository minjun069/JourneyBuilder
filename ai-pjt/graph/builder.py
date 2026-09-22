"""Top-level graph assembly and dependency injection only."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from core.runtime_instrumentation_adapter import RuntimeInstrumentationAdapter
from graph.agent_registry import build_agent_executor, default_agent_graphs
from graph.aggregation import AggregateComposer, compose_aggregate_response
from graph.context_node import persist_context
from graph.execution_node import build_execution_node
from graph.executor import validate_agent_executor_registry
from graph.runtime_types import RuntimeExecutor
from graph.plan_node import build_plan_node
from graph.planner import plan_tasks_with_model
from graph.state import ChatState
from tools.output_policy import validate_form_update


def build_graph(
    checkpointer: BaseCheckpointSaver,
    *,
    instrumentation: RuntimeInstrumentationAdapter,
    planner=plan_tasks_with_model,
    agent_graphs: Mapping[str, Any] | None = None,
    tool_executors: Mapping[str, RuntimeExecutor] | None = None,
    response_composer: AggregateComposer | None = compose_aggregate_response,
    counseling_enricher: AggregateComposer | None = None,
):
    """Wire planner, executor, validation, and context nodes."""

    if not isinstance(instrumentation, RuntimeInstrumentationAdapter):
        raise TypeError("build_graph requires a RuntimeInstrumentationAdapter")

    active_graphs = dict(
        default_agent_graphs() if agent_graphs is None else agent_graphs
    )
    agent_executors = {
        name: build_agent_executor(name, graph)
        for name, graph in active_graphs.items()
    }
    validate_agent_executor_registry(agent_executors)
    active_tools = dict(tool_executors or {})

    plan_node = build_plan_node(
        instrumentation=instrumentation,
        planner=planner,
        registered_tool_capabilities=tuple(active_tools),
    )
    execute_node = build_execution_node(
        instrumentation=instrumentation,
        agent_executors=agent_executors,
        tool_executors=active_tools,
        response_composer=response_composer,
        counseling_enricher=counseling_enricher,
    )

    builder = StateGraph(ChatState)
    builder.add_node("plan", plan_node)
    builder.add_node("execute", execute_node)
    builder.add_node("validate", validate_form_update)
    builder.add_node("persist_context", persist_context)
    builder.add_edge(START, "plan")
    builder.add_edge("plan", "execute")
    builder.add_edge("execute", "validate")
    builder.add_edge("validate", "persist_context")
    builder.add_edge("persist_context", END)
    return builder.compile(checkpointer=checkpointer)
