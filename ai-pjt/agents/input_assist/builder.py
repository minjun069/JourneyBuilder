"""Input Assist subgraph assembly only."""

from langgraph.graph import END, START, StateGraph

from .service import (
    InputAssistState,
    _classify,
    _error_check_1,
    _error_check_2,
    _identify,
    _init,
    _merge,
    _next_field,
    _resolve,
    _route_after_error_check_1,
    _route_after_error_check_2,
    _route_after_identify,
)


def build_graph():
    builder = StateGraph(InputAssistState)
    builder.add_node("init", _init)
    builder.add_node("classify", _classify)
    builder.add_node("identify", _identify)
    builder.add_node("next_field", _next_field)
    builder.add_node("error_check_1", _error_check_1)
    builder.add_node("resolve", _resolve)
    builder.add_node("error_check_2", _error_check_2)
    builder.add_node("merge", _merge)
    builder.add_edge(START, "init")
    builder.add_edge("init", "classify")
    builder.add_edge("classify", "identify")
    builder.add_conditional_edges("identify", _route_after_identify)
    builder.add_edge("next_field", "error_check_1")
    builder.add_conditional_edges("error_check_1", _route_after_error_check_1)
    builder.add_edge("resolve", "error_check_2")
    builder.add_conditional_edges("error_check_2", _route_after_error_check_2)
    builder.add_edge("merge", END)
    return builder.compile()


graph = build_graph()
