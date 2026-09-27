"""Elicit conversational model adapter."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr
from typing import Literal

from core.llm import llm
from core.model_boundary import ModelPurpose, prompt_messages, secure_ainvoke
from core.prompt_inputs import conversation_for_model
from prompts import get_prompt


RESPOND_PROMPT = get_prompt("elicit.respond")
FIELD_PROMPTS = {
    "values": get_prompt("elicit.values"),
    "past_experience": get_prompt("elicit.past_experience"),
    "current_situation": get_prompt("elicit.current_situation"),
    "current_challenge": get_prompt("elicit.current_challenge"),
    "invest_philosophy": get_prompt("elicit.invest_philosophy"),
    "future_goals": get_prompt("elicit.future_goals"),
}


class MindsetModelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    response: StrictStr = Field(min_length=1)
    summary: StrictStr
    sufficient: StrictBool
    turn_kind: Literal["answer", "clarification", "skip"]


async def collect_answer(
    *, field: str, message: str, summary: str, answer_count: int,
    conversation_context: Mapping[str, Any],
) -> MindsetModelOutput:
    prompt = FIELD_PROMPTS[field]
    payload = {
        "current_form": {field: summary},
        "conversation_context": conversation_for_model(conversation_context),
        "user_message": message,
        "last_answer": answer_count == 2,
    }
    result = await secure_ainvoke(
        llm.with_structured_output(MindsetModelOutput),
        prompt_messages(prompt, payload, step_id="financial-mindset"),
        purpose=ModelPurpose.ELICIT,
        operation_name=prompt.prompt_id,
        sensitive_payload=payload,
        prompt=prompt,
    )
    return result if isinstance(result, MindsetModelOutput) else MindsetModelOutput.model_validate(result)


async def respond(
    *,
    message: str,
    conversation_context: Mapping[str, Any],
    step_id: str,
) -> str:
    context = conversation_for_model(conversation_context)
    result = await secure_ainvoke(
        llm,
        prompt_messages(RESPOND_PROMPT, {
            "conversation_context": context,
            "user_message": message,
        }, step_id=step_id),
        purpose=ModelPurpose.ELICIT,
        operation_name="elicit.respond",
        sensitive_payload={
            "message": message,
            "conversation_context": context,
        },
        prompt=RESPOND_PROMPT,
    )
    return str(result.content)
