from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import StrOutputParser

from core.llm import llm
from core.step_registry import load_agent_descriptions, load_step_candidates
from graph.state import ChatState

STEP_CANDIDATES = load_step_candidates()
AGENT_DESCRIPTIONS = load_agent_descriptions()

ROUTER_PROMPT = """\
사용자의 마지막 메시지를 분석하여 아래 후보 에이전트 중 가장 적합한 것을 하나 선택하세요.
반드시 에이전트 이름만 반환하세요. 설명을 포함하지 마세요.

현재 단계: {step_id}

후보 에이전트:
{candidates}
"""

_parser = StrOutputParser()


def router(state: ChatState) -> dict:
    candidates = STEP_CANDIDATES.get(state["step_id"])
    if candidates is None:
        return {"next": "fallback"}

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
