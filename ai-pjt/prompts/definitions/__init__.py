"""The current production prompt set, one immutable definition per module."""

from prompts.definitions.aggregator_compose import PROMPT as AGGREGATOR_COMPOSE
from prompts.definitions.chart_interpret_explain import PROMPT as CHART_INTERPRET_EXPLAIN
from prompts.definitions.elicit_analyze import PROMPT as ELICIT_ANALYZE
from prompts.definitions.error_review_analyze import PROMPT as ERROR_REVIEW_ANALYZE
from prompts.definitions.input_assist_classify import PROMPT as INPUT_ASSIST_CLASSIFY
from prompts.definitions.input_assist_identify import PROMPT as INPUT_ASSIST_IDENTIFY
from prompts.definitions.input_assist_merge import PROMPT as INPUT_ASSIST_MERGE
from prompts.definitions.input_assist_resolve import PROMPT as INPUT_ASSIST_RESOLVE
from prompts.definitions.input_assist_validate_resolution import (
    PROMPT as INPUT_ASSIST_VALIDATE_RESOLUTION,
)
from prompts.definitions.planner_plan import PROMPT as PLANNER_PLAN
from prompts.definitions.qa_answer_direct import PROMPT as QA_ANSWER_DIRECT
from prompts.definitions.qa_answer_with_evidence import PROMPT as QA_ANSWER_WITH_EVIDENCE
from prompts.definitions.qa_decide_retrieval import PROMPT as QA_DECIDE_RETRIEVAL
from prompts.definitions.qa_judge import PROMPT as QA_JUDGE
from prompts.definitions.qa_merge import PROMPT as QA_MERGE
from prompts.definitions.qa_refine_query import PROMPT as QA_REFINE_QUERY
from prompts.definitions.qa_specify import PROMPT as QA_SPECIFY
from prompts.definitions.summary_extract import PROMPT as SUMMARY_EXTRACT


ALL_PROMPTS = (
    AGGREGATOR_COMPOSE,
    CHART_INTERPRET_EXPLAIN,
    ELICIT_ANALYZE,
    ERROR_REVIEW_ANALYZE,
    INPUT_ASSIST_CLASSIFY,
    INPUT_ASSIST_IDENTIFY,
    INPUT_ASSIST_MERGE,
    INPUT_ASSIST_RESOLVE,
    INPUT_ASSIST_VALIDATE_RESOLUTION,
    PLANNER_PLAN,
    QA_ANSWER_DIRECT,
    QA_ANSWER_WITH_EVIDENCE,
    QA_DECIDE_RETRIEVAL,
    QA_JUDGE,
    QA_MERGE,
    QA_REFINE_QUERY,
    QA_SPECIFY,
    SUMMARY_EXTRACT,
)

__all__ = ("ALL_PROMPTS",)
