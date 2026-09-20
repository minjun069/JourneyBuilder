"""Elicit subgraph assembly only."""

from langgraph.graph import END, START, StateGraph

from graph.state import ChatState
from .service import _run


def build_graph():
    builder = StateGraph(ChatState)
    builder.add_node("run", _run)
    builder.add_edge(START, "run")
    builder.add_edge("run", END)
    return builder.compile()


graph = build_graph()
