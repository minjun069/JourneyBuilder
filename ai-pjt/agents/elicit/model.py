"""Elicit conversational model adapter."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from core.llm import llm
from core.model_boundary import ModelPurpose, prompt_messages, secure_ainvoke
from core.prompt_inputs import conversation_for_model
from prompts import get_prompt


RESPOND_PROMPT = get_prompt("elicit.respond")


async def respond(
    *,
    message: str,
    conversation_context: Mapping[str, Any],
) -> str:
    context = conversation_for_model(conversation_context)
    result = await secure_ainvoke(
        llm,
        prompt_messages(RESPOND_PROMPT, {
            "conversation_context": context,
            "user_message": message,
        }),
        purpose=ModelPurpose.ELICIT,
        operation_name="elicit.respond",
        sensitive_payload={
            "message": message,
            "conversation_context": context,
        },
        prompt=RESPOND_PROMPT,
    )
    return str(result.content)
