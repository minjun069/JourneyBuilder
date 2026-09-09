"""Narrow deterministic Fallback Agent for pre-execution and all-failed cases."""

from graph.state import ChatState
from langgraph.graph import END, START, StateGraph

MESSAGES = {
    "ambiguous_intent": (
        "요청의 뜻을 하나로 확정하기 어렵습니다. 궁금한 내용이나 바꾸려는 입력 항목을 "
        "한 가지씩 구체적으로 말씀해 주세요."
    ),
    "unsupported_intent": (
        "현재 상담은 재무정보 입력, 재무진단 결과 설명과 은퇴설계 질문을 지원합니다. "
        "이 범위 안의 요청으로 바꾸어 말씀해 주세요."
    ),
    "plan_invalid": (
        "요청을 안전한 실행 계획으로 만들지 못했습니다. 원하는 작업을 한 가지씩 나누어 "
        "다시 요청해 주세요."
    ),
    "all_tasks_failed": (
        "실행한 작업에서 전달할 수 있는 검증된 결과를 만들지 못했습니다. 입력과 요청을 "
        "확인한 뒤 다시 시도해 주세요."
    ),
}


def _run(state: ChatState) -> dict:
    category = state.get("fallback_category")
    if category not in MESSAGES:
        raise ValueError("unsupported fallback category")
    return {
        "output": {
            "response": MESSAGES[category],
            "step_id": state["step_id"],
            "action": None,
        }
    }


def _build():
    g = StateGraph(ChatState)
    g.add_node("run", _run)
    g.add_edge(START, "run")
    g.add_edge("run", END)
    return g.compile()


graph = _build()
