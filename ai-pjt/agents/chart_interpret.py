"""Explanation-only consumer for Backend-verified chart payloads."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from core.llm import llm
from core.model_boundary import (
    ModelPurpose,
    prompt_messages,
    secure_ainvoke,
)
from graph.state import ChatState
from prompts import get_prompt


CHART_PROMPT = get_prompt("chart_interpret.explain")


async def _run(state: ChatState) -> dict:
    payload = state.get("chart_payload")
    if payload is None:
        return {
            "output": {
                "response": (
                    "아직 설명할 수 있는 검증 완료 차트 보고서가 준비되지 않았습니다. "
                    "보고서 생성이 끝난 뒤 다시 요청해 주세요."
                ),
                "step_id": state["step_id"],
                "action": None,
            }
        }
    result = await secure_ainvoke(
        llm,
        prompt_messages(CHART_PROMPT, {
            "user_request": state["message"],
            "chart_payload": payload,
        }),
        purpose=ModelPurpose.CHART_INTERPRET,
        sensitive_payload={
            "message": state["message"],
            "chart_payload": payload,
        },
        prompt=CHART_PROMPT,
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
