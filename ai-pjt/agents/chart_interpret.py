from core.llm import call_llm
from graph.state import ChatState
from langgraph.graph import END, START, StateGraph

SYSTEM_PROMPT = (
    "당신은 은퇴 설계 전문 AI 상담사입니다.\n"
    "현재 단계: {step_id}\n"
    "고객 정보: {customer_info}\n\n"
    "고객에게 보여지는 차트나 데이터를 쉽게 해석하여 설명하고,\n"
    "고객의 상황에 맞는 인사이트를 제공하세요."
)


def _run(state: ChatState) -> dict:
    return call_llm(state, SYSTEM_PROMPT.format(
        step_id=state["step_id"],
        customer_info=state["customer_info"],
    ))


def _build():
    g = StateGraph(ChatState)
    g.add_node("run", _run)
    g.add_edge(START, "run")
    g.add_edge("run", END)
    return g.compile()


graph = _build()
