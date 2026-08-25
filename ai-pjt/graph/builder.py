from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from agents import chart_interpret, elicit, error_review, fallback, input_assist, qa
from graph.router import router
from graph.state import ChatState


def build_graph(checkpointer: BaseCheckpointSaver):
    agents = {
        "qa":              qa.graph,
        "input_assist":    input_assist.graph,
        "elicit":          elicit.graph,
        "error_review":    error_review.graph,
        "chart_interpret": chart_interpret.graph,
        "fallback":        fallback.graph,
    }

    builder = StateGraph(ChatState)
    builder.add_node("router", router)
    for name, graph in agents.items():
        builder.add_node(name, graph)

    builder.add_edge(START, "router")
    builder.add_conditional_edges(
        "router",
        lambda state: state["next"],
        {name: name for name in agents},
    )
    for name in agents:
        builder.add_edge(name, END)

    return builder.compile(checkpointer=checkpointer)
