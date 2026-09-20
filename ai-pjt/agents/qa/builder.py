"""QA subgraph assembly only."""

from langgraph.graph import END, START, StateGraph

from .service import (
    QAState,
    _decide,
    _generate,
    _init,
    _judge,
    _merge,
    _next_query,
    _route_after_judge,
    _specify,
)


def build_graph():
    builder = StateGraph(QAState)
    builder.add_node("init", _init)
    builder.add_node("specify", _specify)
    builder.add_node("next_query", _next_query)
    builder.add_node("decide", _decide)
    builder.add_node("generate", _generate)
    builder.add_node("judge", _judge)
    builder.add_node("merge", _merge)
    builder.add_edge(START, "init")
    builder.add_edge("init", "specify")
    builder.add_edge("specify", "next_query")
    builder.add_edge("next_query", "decide")
    builder.add_edge("decide", "generate")
    builder.add_edge("generate", "judge")
    builder.add_conditional_edges("judge", _route_after_judge)
    builder.add_edge("merge", END)
    return builder.compile()


graph = build_graph()
