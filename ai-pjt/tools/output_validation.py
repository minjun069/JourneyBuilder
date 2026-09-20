"""Backward-compatible facade for the public output policy."""

from .output_policy import (
    OutputValidationResult,
    SAFE_VALIDATION_FAILURE_RESPONSE,
    ValidationIssue,
    validate_form_update,
    validate_form_update_contract,
)

__all__ = [
    "OutputValidationResult",
    "SAFE_VALIDATION_FAILURE_RESPONSE",
    "ValidationIssue",
    "validate_form_update",
    "validate_form_update_contract",
]
