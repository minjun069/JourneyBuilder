"""Stable public contracts for secure model invocation."""

from enum import Enum


class ModelPurpose(str, Enum):
    PLANNER = "planner"
    AGGREGATOR = "aggregator"
    QA = "qa"
    INPUT_ASSIST = "input_assist"
    ERROR_REVIEW = "error_review"
    CHART_INTERPRET = "chart_interpret"
    ELICIT = "elicit"
    SUMMARY = "summary"


class ModelOutputBlockedError(ValueError):
    def __init__(self, message: str, *, reason_code: str = "DATA_POLICY_BLOCKED") -> None:
        super().__init__(message)
        self.reason_code = reason_code


class StructuredModelOutputError(RuntimeError):
    """Raised after bounded structured-output attempts are exhausted."""
