"""Chart Interpret model and approved-knowledge adapter."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from core.llm import llm
from core.model_boundary import ModelPurpose, prompt_messages, secure_ainvoke
from prompts import get_prompt
from rag.chart_knowledge import ChartKnowledgeError, load_chart_knowledge


CHART_PROMPT = get_prompt("chart_interpret.explain")


async def explain_chart(
    state: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    entry: bool,
    previous_response: str = "",
    feedback: tuple[str, ...] = (),
) -> str:
    try:
        knowledge = load_chart_knowledge(
            str(state["step_id"]),
            payload.get("knowledge_scope", {}).get("source_ids"),
            question="" if entry else str(state["message"]),
        )
    except ChartKnowledgeError:
        knowledge = ""

    result = await secure_ainvoke(
        llm,
        prompt_messages(CHART_PROMPT, {
            "user_request": state["message"],
            "chart_facts": payload,
            "chart_guide": knowledge,
            "response_mode": "initial_entry" if entry else "detail",
            "previous_response": previous_response,
            "feedback": list(feedback),
        }),
        purpose=ModelPurpose.CHART_INTERPRET,
        operation_name="chart_interpret.generate",
        sensitive_payload={
            "message": state["message"],
            "chart_facts": payload,
            "previous_response": previous_response,
        },
        prompt=CHART_PROMPT,
    )
    return str(result.content)
