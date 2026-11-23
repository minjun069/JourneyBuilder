"""Request-scoped mindset collection policy; the current form owns every summary."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator
from core.mindset_text import clean_mindset_text


MindsetField = Literal[
    "values", "past_experience", "current_situation", "current_challenge",
    "invest_philosophy", "future_goals",
]
MINDSET_FIELDS = (
    "values", "past_experience", "current_situation", "current_challenge",
    "invest_philosophy", "future_goals",
)
MAX_ANSWERS = 3
FIELD_LABELS = {
    "values": "재무 가치관", "past_experience": "과거 재무 경험",
    "current_situation": "현재 재무 상황", "current_challenge": "당면한 재무 과제",
    "invest_philosophy": "투자 철학", "future_goals": "미래 인생 목표",
}
FIRST_QUESTIONS = {
    "values": "먼저 재무 가치관을 알아보겠습니다. 재무 가치관은 돈을 쓰거나 모을 때 지키고 싶은 기준을 말해요. 고객님은 돈을 통해 어떤 삶이나 가치를 가장 지키고 싶으신가요?",
    "past_experience": "이번에는 과거 재무 경험을 살펴보겠습니다. 돈에 대한 생각이나 습관에 영향을 준 경험을 말해요. 기억에 남는 경험과 그 경험이 지금의 선택에 준 영향을 들려주시겠어요?",
    "current_situation": "이번에는 현재 재무 상황을 어떻게 느끼시는지 알아보겠습니다. 숫자를 나열하기보다 생활에서 느끼는 여유나 부담을 살펴보는 항목이에요. 요즘 재무 상황에서 가장 만족스럽거나 신경 쓰이는 점은 무엇인가요?",
    "current_challenge": "이번에는 당면한 재무 과제를 정리해 보겠습니다. 지금 해결하고 싶은 돈 문제나 결정이 필요한 일을 말해요. 우선 해결하고 싶은 과제와 그것이 중요한 이유는 무엇인가요?",
    "invest_philosophy": "이번에는 투자 철학을 알아보겠습니다. 투자할 때 중요하게 여기는 기준과 감수할 수 있는 불확실성을 말해요. 투자 경험이 없으셔도 괜찮습니다. 돈을 운용할 때 특히 지키고 싶은 원칙이 있으신가요?",
    "future_goals": "마지막으로 미래 인생 목표를 살펴보겠습니다. 돈 자체의 목표보다 앞으로 이루고 싶은 삶의 모습을 말해요. 앞으로 꼭 이루고 싶은 일이나 생활 모습과, 생각하시는 시기가 있으신가요?",
}


class ElicitationState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event: Literal["start", "answer"]
    field: MindsetField
    answer_count: StrictInt = Field(ge=0, le=2)
    completed_fields: list[MindsetField]

    @model_validator(mode="after")
    def validate_sequence(self) -> Self:
        if self.completed_fields != list(MINDSET_FIELDS[:MINDSET_FIELDS.index(self.field)]):
            raise ValueError("completed_fields must be the ordered prefix before field")
        if self.event == "start" and self.answer_count != 0:
            raise ValueError("start requires answer_count zero")
        return self


class ElicitationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: MindsetField
    summary: StrictStr
    answer_count: StrictInt = Field(ge=0, le=MAX_ANSWERS)
    status: Literal["in_progress", "completed"]
    completion_reason: Literal["sufficient", "limit", "skipped"] | None
    next_field: MindsetField | None

    @model_validator(mode="after")
    def validate_transition(self) -> Self:
        if self.status == "in_progress":
            if self.completion_reason is not None or self.next_field is not None or self.answer_count >= MAX_ANSWERS:
                raise ValueError("in-progress result cannot finish or exceed the answer limit")
        else:
            if self.completion_reason is None or self.next_field != next_mindset_field(self.field):
                raise ValueError("completed result requires a reason and canonical next field")
            if self.completion_reason == "limit" and self.answer_count != MAX_ANSWERS:
                raise ValueError("limit completion requires three answers")
            if self.completion_reason == "sufficient" and self.answer_count == 0:
                raise ValueError("sufficient completion requires an answer")
            if self.completion_reason in ("sufficient", "limit") and not self.summary.strip():
                raise ValueError("answered completion requires a summary")
            if self.completion_reason == "skipped" and self.answer_count >= MAX_ANSWERS:
                raise ValueError("skip cannot consume a third answer")
        return self


def next_mindset_field(field: str) -> str | None:
    index = MINDSET_FIELDS.index(field) + 1
    return MINDSET_FIELDS[index] if index < len(MINDSET_FIELDS) else None


def current_summary(customer_info: Mapping[str, Any], field: str) -> str:
    form = customer_info.get("current_form")
    if not isinstance(form, Mapping):
        raise ValueError("mindset collection requires the current form")
    value = form.get(field, "")
    # The browser's canonical form mapper represents an empty text field as null.
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("current mindset summary must be text")
    return clean_mindset_text(value, preferred=field)


def opening_question(field: str, customer_info: Mapping[str, Any]) -> str:
    if current_summary(customer_info, field).strip():
        return f"{FIELD_LABELS[field]}에 이미 적어 주신 내용이 있네요. 현재도 같은 생각이신가요? 보완하거나 바꾸고 싶은 점이 있다면 말씀해 주세요."
    return FIRST_QUESTIONS[field]


def completed_response(result: ElicitationResult, customer_info: Mapping[str, Any]) -> str:
    label = FIELD_LABELS[result.field]
    if result.completion_reason == "skipped":
        response = f"{label} 질문은 건너뛰겠습니다."
        if result.summary.strip():
            response += (
                " 적어 주신 내용은 그대로 두겠습니다."
                if current_summary(customer_info, result.field).strip()
                else " 답변을 보류하셨다는 점만 정리하겠습니다."
            )
            response += f"\n\n{result.summary}"
    elif result.summary.strip():
        response = f"말씀해 주신 {label}을 이렇게 정리했습니다.\n\n{result.summary}"
    else:
        response = f"{label} 이야기는 여기까지 듣겠습니다. 이 항목은 나중에 직접 적거나 수정하실 수 있어요."
    if result.next_field is not None:
        return response + "\n\n" + opening_question(result.next_field, customer_info)
    return response + "\n\n경제관념 질문을 모두 마쳤습니다. 정리된 내용을 살펴보고 필요한 부분을 직접 수정해 주세요."
