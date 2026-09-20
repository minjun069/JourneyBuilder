"""Simple conversational Agent for qualitative financial-mindset dialogue."""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from core.llm import llm
from core.model_boundary import ModelPurpose, prompt_messages, secure_ainvoke
from graph.state import ChatState
from prompts import get_prompt


RESPOND_PROMPT = get_prompt("elicit.respond")


async def _run(state: ChatState) -> dict[str, Any]:
    context = state.get("conversation_context", {})
    result = await secure_ainvoke(
        llm,
        prompt_messages(RESPOND_PROMPT, {
            "conversation_context": context,
            "user_message": state["message"],
        }),
        purpose=ModelPurpose.ELICIT,
        operation_name="elicit.respond",
        sensitive_payload={
            "message": state["message"],
            "conversation_context": context,
        },
        prompt=RESPOND_PROMPT,
    )
    return {
        "output": {
            "response": result.content,
            "step_id": state["step_id"],
            "action": None,
        }
    }


def _build():
    graph_builder = StateGraph(ChatState)
    graph_builder.add_node("run", _run)
    graph_builder.add_edge(START, "run")
    graph_builder.add_edge("run", END)
    return graph_builder.compile()


graph = _build()
