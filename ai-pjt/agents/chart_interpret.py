"""Explanation-only consumer for Backend-verified chart payloads."""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from core.llm import llm
from graph.state import ChatState


SYSTEM_PROMPT = """\
당신은 은퇴 설계 차트 설명 전용 상담사입니다.
아래 JSON은 Backend가 원본 보고서와 대조해 검증한 구조화 payload입니다.

[절대 규칙]
- payload의 주요 수치, 변화가 큰 구간, 적용 가정과 사용자가 주의할 점만 설명합니다.
- schema·ID·version·단위·기간을 다시 검증하지 않습니다.
- 값을 재계산하거나 수정하지 않습니다.
- payload에 없는 수치나 사실을 생성하지 않습니다.
- 입력 변경안이나 FormChangeAction을 만들지 않습니다.
- 수치를 언급할 때 payload의 단위를 그대로 표시합니다.
"""


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
    result = await llm.ainvoke([
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=(
            f"사용자 요청: {state['message']}\n\n"
            "검증된 chart_payload:\n"
            + json.dumps(payload, ensure_ascii=False, sort_keys=True)
        )),
    ])
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
