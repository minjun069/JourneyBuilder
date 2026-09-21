"""One extraction, code validation, optional batch review, and one response."""

from langgraph.graph import END, START, StateGraph

from .service import InputAssistState, _extract, _respond, _review, _validate


def build_graph():
    builder = StateGraph(InputAssistState)
    builder.add_node("extract", _extract)
    builder.add_node("validate", _validate)
    builder.add_node("review", _review)
    builder.add_node("respond", _respond)
    builder.add_edge(START, "extract")
    builder.add_edge("extract", "validate")
    builder.add_conditional_edges(
        "validate", lambda state: "review" if state["review_candidates"] else "respond",
    )
    builder.add_edge("review", "respond")
    builder.add_edge("respond", END)
    return builder.compile()


graph = build_graph()
