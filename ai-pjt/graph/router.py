from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import StrOutputParser

from core.llm import llm
from graph.state import ChatState

# Frontend의 processSteps.js에 정의된 23개 ID와 동기화한다.
# 서비스 간 import는 하지 않는다. 이 레지스트리는 Frontend 단계 목록 및
# router evaluation fixture와 contract test로 동기화한다.
STEP_CANDIDATES: dict[str, list[str]] = {
    "intro": ["qa", "fallback"],
    "collection": ["qa", "fallback"],
    "basic-info": ["qa", "input_assist", "error_review", "fallback"],
    "financial-mindset": ["qa", "input_assist", "elicit", "error_review", "fallback"],
    "financial-satisfaction": ["qa", "input_assist", "error_review", "fallback"],
    "financial-info": ["qa", "fallback"],
    "financial-assets": ["qa", "input_assist", "error_review", "fallback"],
    "pension-assets": ["qa", "input_assist", "error_review", "fallback"],
    "real-estate": ["qa", "input_assist", "error_review", "fallback"],
    "liabilities": ["qa", "input_assist", "error_review", "fallback"],
    "income": ["qa", "input_assist", "error_review", "fallback"],
    "expenses": ["qa", "input_assist", "error_review", "fallback"],
    "financial-goals": ["qa", "input_assist", "error_review", "fallback"],
    "presentation": ["qa", "fallback"],
    "balance-sheet": ["qa", "chart_interpret", "fallback"],
    "cash-flow": ["qa", "chart_interpret", "fallback"],
    "financial-indicators": ["qa", "chart_interpret", "fallback"],
    "financial-goal-result": ["qa", "chart_interpret", "fallback"],
    "retirement-fund": ["qa", "chart_interpret", "fallback"],
    "retirement-readiness": ["qa", "chart_interpret", "fallback"],
    "pension-schedule": ["qa", "chart_interpret", "fallback"],
    "lifetime-cashflow": ["qa", "chart_interpret", "fallback"],
    "required-savings": ["qa", "chart_interpret", "fallback"],
}

AGENT_DESCRIPTIONS = {
    "qa":              "사용자의 질문에 답변",
    "input_assist":    "사용자 입력을 정리하여 대신 입력",
    "elicit":          "정성 정보를 유도하는 질문",
    "error_review":    "사용자 입력 오류 검토",
    "chart_interpret": "차트 및 데이터 해석",
    "fallback":        "그 외 일반 안내",
}

ROUTER_PROMPT = """\
사용자의 마지막 메시지를 분석하여 아래 후보 에이전트 중 가장 적합한 것을 하나 선택하세요.
반드시 에이전트 이름만 반환하세요. 설명을 포함하지 마세요.

현재 단계: {step_id}

후보 에이전트:
{candidates}
"""

_parser = StrOutputParser()


def router(state: ChatState) -> dict:
    candidates = STEP_CANDIDATES.get(state["step_id"], ["fallback"])

    candidate_lines = "\n".join(
        f"- {name}: {AGENT_DESCRIPTIONS[name]}" for name in candidates
    )
    system = SystemMessage(content=ROUTER_PROMPT.format(
        step_id=state["step_id"],
        candidates=candidate_lines,
    ))

    result = _parser.invoke(llm.invoke([system, HumanMessage(content=state["message"])]))
    chosen = result.strip()

    if chosen not in candidates:
        chosen = "fallback"

    return {"next": chosen}
