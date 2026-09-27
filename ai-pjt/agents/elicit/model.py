"""Elicit conversational model adapter."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr
from typing import Literal

from core.llm import llm
from core.model_boundary import ModelPurpose, prompt_messages, secure_ainvoke
from core.prompt_inputs import conversation_for_model
from core.mindset_text import clean_mindset_context, clean_mindset_text
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

    response: StrictStr = Field(min_length=1, description="사용자에게 보여줄 자연어 답변과 후속 질문만. 필드명·판단값·JSON 제외.")
    summary: StrictStr = Field(description="현재 항목의 경제관념 요약 문장만. values: 등 필드명과 sufficient·turn_kind 제외.")
    sufficient: StrictBool = Field(description="조기 종료를 위한 내부 판단값. 표시 문구에 복제하지 않음.")
    turn_kind: Literal["answer", "clarification", "skip"] = Field(description="회차 계산용 내부 분류. 표시 문구에 복제하지 않음.")


async def collect_answer(
    *, field: str, message: str, summary: str, answer_count: int,
    conversation_context: Mapping[str, Any],
) -> MindsetModelOutput:
    prompt = FIELD_PROMPTS[field]
    payload = {
        "current_form": {field: clean_mindset_text(summary, preferred=field)},
        "conversation_context": clean_mindset_context(conversation_for_model(conversation_context)),
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
