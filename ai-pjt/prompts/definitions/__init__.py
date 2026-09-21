"""The current production prompt set, one immutable definition per module."""

from prompts.definitions.aggregator_compose import PROMPT as AGGREGATOR_COMPOSE
from prompts.definitions.chart_interpret_explain import PROMPT as CHART_INTERPRET_EXPLAIN
from prompts.definitions.elicit_respond import PROMPT as ELICIT_RESPOND
from prompts.definitions.error_review_analyze import PROMPT as ERROR_REVIEW_ANALYZE
from prompts.definitions.input_assist_extract import PROMPT as INPUT_ASSIST_EXTRACT
from prompts.definitions.input_assist_review import PROMPT as INPUT_ASSIST_REVIEW
from prompts.definitions.input_assist_respond import PROMPT as INPUT_ASSIST_RESPOND
from prompts.definitions.planner_plan import PROMPT as PLANNER_PLAN
from prompts.definitions.qa_plan import PROMPT as QA_PLAN
from prompts.definitions.qa_answer import PROMPT as QA_ANSWER
from prompts.definitions.qa_judge import PROMPT as QA_JUDGE
from prompts.definitions.summary_extract import PROMPT as SUMMARY_EXTRACT


ALL_PROMPTS = (
    AGGREGATOR_COMPOSE,
    CHART_INTERPRET_EXPLAIN,
    ELICIT_RESPOND,
    ERROR_REVIEW_ANALYZE,
    INPUT_ASSIST_EXTRACT,
    INPUT_ASSIST_REVIEW,
    INPUT_ASSIST_RESPOND,
    PLANNER_PLAN,
    QA_PLAN,
    QA_ANSWER,
    QA_JUDGE,
    SUMMARY_EXTRACT,
)

__all__ = ("ALL_PROMPTS",)
