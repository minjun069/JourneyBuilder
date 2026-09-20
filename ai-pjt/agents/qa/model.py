"""QA model schemas, prompt contracts, and structured runnables."""

from typing import Literal

from pydantic import BaseModel

from core.llm import llm
from prompts import get_prompt


class SpecifyResult(BaseModel):
    queries: list[str]


class RetrievalDecision(BaseModel):
    need_retrieval: bool
    requests_new_official_value: bool = False


class JudgeResult(BaseModel):
    verdict: Literal["good", "bad"]
    failure_kind: Literal["none", "answer_quality", "evidence_insufficient"] = "none"
    reason: str


class RefineResult(BaseModel):
    query: str


specify_chain = llm.with_structured_output(SpecifyResult)
decision_chain = llm.with_structured_output(RetrievalDecision)
judge_chain = llm.with_structured_output(JudgeResult)
refine_chain = llm.with_structured_output(RefineResult)

SPECIFY_PROMPT = get_prompt("qa.specify")
DECIDE_PROMPT = get_prompt("qa.decide_retrieval")
ANSWER_PROMPT = get_prompt("qa.answer_with_evidence")
DIRECT_ANSWER_PROMPT = get_prompt("qa.answer_direct")
MERGE_PROMPT = get_prompt("qa.merge")
JUDGE_PROMPT = get_prompt("qa.judge")
REFINE_PROMPT = get_prompt("qa.refine_query")
