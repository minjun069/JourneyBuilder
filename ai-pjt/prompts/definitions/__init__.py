"""The current production prompt set, one immutable definition per module."""

from prompts.definitions.aggregator_compose import PROMPT as AGGREGATOR_COMPOSE
from prompts.definitions.aggregator_counseling import PROMPT as AGGREGATOR_COUNSELING
from prompts.definitions.chart_interpret_explain import PROMPT as CHART_INTERPRET_EXPLAIN
from prompts.definitions.elicit_respond import PROMPT as ELICIT_RESPOND
from prompts.definitions.elicit_values import PROMPT as ELICIT_VALUES
from prompts.definitions.elicit_past_experience import PROMPT as ELICIT_PAST_EXPERIENCE
from prompts.definitions.elicit_current_situation import PROMPT as ELICIT_CURRENT_SITUATION
from prompts.definitions.elicit_current_challenge import PROMPT as ELICIT_CURRENT_CHALLENGE
from prompts.definitions.elicit_invest_philosophy import PROMPT as ELICIT_INVEST_PHILOSOPHY
from prompts.definitions.elicit_future_goals import PROMPT as ELICIT_FUTURE_GOALS
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
    AGGREGATOR_COUNSELING,
    CHART_INTERPRET_EXPLAIN,
    ELICIT_RESPOND,
    ELICIT_VALUES,
    ELICIT_PAST_EXPERIENCE,
    ELICIT_CURRENT_SITUATION,
    ELICIT_CURRENT_CHALLENGE,
    ELICIT_INVEST_PHILOSOPHY,
    ELICIT_FUTURE_GOALS,
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
