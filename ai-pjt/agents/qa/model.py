"""QA planning and review outputs; answers remain plain text."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.llm import llm
from prompts import get_prompt


class QuestionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    question: str = Field(min_length=1)
    need_retrieval: bool
    query: str | None = None
    reference_id: str | None = None
    reference_age: int | None = Field(default=None, ge=0, le=120)

    @model_validator(mode="after")
    def validate_query(self):
        if self.need_retrieval and not self.query:
            raise ValueError("a question requiring retrieval needs a search query")
        if not self.need_retrieval:
            self.query = None
        return self


class PlanResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    questions: list[QuestionPlan] = Field(min_length=1, max_length=3)


class JudgeResult(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    status: Literal["good", "answer_quality", "evidence_insufficient"]
    reason: str = ""

    @model_validator(mode="after")
    def validate_reason(self):
        if self.status != "good" and not self.reason:
            raise ValueError("a failed review needs an actionable reason")
        if self.status == "good":
            self.reason = ""
        return self


plan_chain = llm.with_structured_output(PlanResult)
judge_chain = llm.with_structured_output(JudgeResult)

PLAN_PROMPT = get_prompt("qa.plan")
ANSWER_PROMPT = get_prompt("qa.answer")
JUDGE_PROMPT = get_prompt("qa.judge")
