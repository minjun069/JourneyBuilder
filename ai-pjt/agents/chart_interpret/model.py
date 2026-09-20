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
) -> str:
    try:
        knowledge = load_chart_knowledge(
            str(state["step_id"]),
            payload.get("knowledge_scope", {}).get("source_ids"),
        )
    except ChartKnowledgeError:
        knowledge = "승인 문서 주입을 확인할 수 없어 payload 사실만 설명합니다."

    result = await secure_ainvoke(
        llm,
        prompt_messages(CHART_PROMPT, {
            "user_request": state["message"],
            "chart_payload": payload,
            "approved_chart_knowledge": knowledge,
            "response_mode": "initial_entry" if entry else "detail",
        }),
        purpose=ModelPurpose.CHART_INTERPRET,
        operation_name="chart_interpret.generate",
        sensitive_payload={
            "message": state["message"],
            "chart_payload": payload,
        },
        prompt=CHART_PROMPT,
    )
    return str(result.content)
