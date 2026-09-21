"""QA subgraph assembly only."""

from langgraph.graph import END, START, StateGraph

from .service import (
    QAState,
    _finish,
    _generate,
    _init,
    _judge,
    _plan,
    _retrieve,
    _route_after_judge,
)


def build_graph():
    builder = StateGraph(QAState)
    builder.add_node("init", _init)
    builder.add_node("plan", _plan)
    builder.add_node("retrieve", _retrieve)
    builder.add_node("generate", _generate)
    builder.add_node("judge", _judge)
    builder.add_node("finish", _finish)
    builder.add_edge(START, "init")
    builder.add_edge("init", "plan")
    builder.add_edge("plan", "retrieve")
    builder.add_edge("retrieve", "generate")
    builder.add_edge("generate", "judge")
    builder.add_conditional_edges("judge", _route_after_judge)
    builder.add_edge("finish", END)
    return builder.compile()


graph = build_graph()
