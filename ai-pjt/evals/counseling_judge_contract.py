"""Temporary input adapter and deterministic validation for the offline judge."""
from __future__ import annotations

from collections import Counter
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

CRITERIA = (
    "trust", "relationship", "communication", "coaching", "advocacy",
    "critical_thinking", "problem_solving", "decision_making", "emotional_intelligence",
)
Criterion = Literal[
    "trust", "relationship", "communication", "coaching", "advocacy",
    "critical_thinking", "problem_solving", "decision_making", "emotional_intelligence",
]
Grade = Literal["상", "중", "하", "해당없음", "보류"]
SCORES = {"상": 3, "중": 2, "하": 1, "해당없음": None, "보류": None}


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Turn(Contract):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class Context(Contract):
    # Required empty containers explicitly mean no relevant records, not lost data.
    previous_dialogue: list[Turn]
    customer_info: dict[str, Any]
    diagnostic_results: dict[str, Any]
    missing_information: list[str]
    execution_context: dict[str, Any] = Field(default_factory=dict)

    @field_validator("missing_information")
    @classmethod
    def nonempty_missing(cls, values):
        if any(not value.strip() for value in values):
            raise ValueError("missing information must describe the missing record")
        return values


class EvaluationInput(Contract):
    schema_version: Literal["counseling-judge-input.v1"]
    case_id: str = Field(min_length=1)
    candidate_version: str = Field(min_length=1)
    request: str = Field(min_length=1)
    answer: str  # Empty actual output is assessable; omitted output is invalid input.
    context: Context
    source_metadata: dict[str, Any] = Field(default_factory=dict)


def adapt_input(value: dict[str, Any]) -> EvaluationInput:
    """Validate the normalized judge input after any source-specific conversion."""
    return EvaluationInput.model_validate(value)


class Evidence(Contract):
    source: Literal["answer", "request", "previous_dialogue"]
    turn_index: StrictInt | None
    quote: str = Field(min_length=1)


class CriterionResult(Contract):
    criterion: Criterion
    grade: Grade
    score: StrictInt | None
    evidence: list[Evidence]
    rationale: str = Field(min_length=1)
    missing_actions: list[str]

    @model_validator(mode="after")
    def check_score(self):
        if self.score != SCORES[self.grade]:
            raise ValueError("grade/score mismatch")
        if not self.rationale.strip():
            raise ValueError("rationale cannot be blank")
        if self.grade in ("상", "중", "하") and not self.evidence:
            raise ValueError("scored criteria require actual utterance evidence")
        if self.grade == "하" and not self.missing_actions:
            raise ValueError("low grade must identify missing or inappropriate behavior")
        if self.grade in ("해당없음", "보류") and self.missing_actions:
            raise ValueError("unscored criteria cannot assert missing behavior")
        if any(not action.strip() for action in self.missing_actions):
            raise ValueError("missing actions cannot be blank")
        return self


class Judgment(Contract):
    status: Literal["evaluated", "deferred"]
    missing_information: list[str]
    criteria: list[CriterionResult] = Field(min_length=9, max_length=9)

    @model_validator(mode="after")
    def check_coverage(self):
        if sorted(item.criterion for item in self.criteria) != sorted(CRITERIA):
            raise ValueError("exactly one result per criterion is required")
        if any(not value.strip() for value in self.missing_information):
            raise ValueError("missing information cannot be blank")
        if self.status == "deferred":
            if not self.missing_information or any(c.grade != "보류" for c in self.criteria):
                raise ValueError("deferred cases need missing records and nine deferred criteria")
        elif self.missing_information or any(c.grade == "보류" for c in self.criteria):
            raise ValueError("incomplete evidence requires whole-case deferral")
        return self


def inspect_evidence(judgment: Judgment, case: EvaluationInput) -> list[dict[str, Any]]:
    """Report quote/source mismatches without rejecting or changing any score."""
    diagnostics = []
    for item in judgment.criteria:
        for position, evidence in enumerate(item.evidence):
            location = {"criterion": item.criterion, "evidence_index": position}
            if evidence.source == "previous_dialogue":
                index = evidence.turn_index
                if index is None or not 0 <= index < len(case.context.previous_dialogue):
                    diagnostics.append({**location, "reason": "invalid_turn_index"})
                    continue
                text = case.context.previous_dialogue[index].content
            else:
                if evidence.turn_index is not None:
                    diagnostics.append({**location, "reason": "unexpected_turn_index"})
                text = getattr(case, evidence.source)
            if not evidence.quote.strip() or evidence.quote not in text:
                diagnostics.append({**location, "reason": "quote_mismatch"})
    return diagnostics


def deferred_judgment(missing: list[str]) -> Judgment:
    return Judgment(status="deferred", missing_information=missing, criteria=[
        CriterionResult(criterion=key, grade="보류", score=None, evidence=[],
                        rationale="평가에 필요한 기록이 누락되어 채점을 보류합니다.",
                        missing_actions=[])
        for key in CRITERIA
    ])


def distribution(judgment: Judgment) -> dict[str, int]:
    counts = Counter(item.grade for item in judgment.criteria)
    return {grade: counts[grade] for grade in SCORES}
