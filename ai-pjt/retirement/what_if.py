"""Immutable What-if inputs; no calculation, RNG, context, or service changes.

See docs/what-if-rules.md for absolute-value overrides, full expense replacement,
and restrictions on changing past retirement ages. Comparison lives separately
in what_if_engine; profile creation itself does not run calculations.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from .models import (
    ModelValidationError,
    RetirementExpensePeriod,
    RetirementProfile,
    _integer,
    _periods,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class WhatIfChanges:
    """Absolute overrides; omitted/None means unchanged, while zero is explicit.

Expense lists are copied to tuples immediately, not merged with a baseline.
Coverage, overlap, and baseline-dependent ages are validated when applying the
changes. An empty tuple is preserved here, then rejected as missing coverage.
These fields are internal inputs, not an external JSON patch or an HTTP DTO.
"""

    monthly_savings: int | None = field(default=None, repr=False)
    retirement_age: int | None = field(default=None, repr=False)
    retirement_expenses: tuple[RetirementExpensePeriod, ...] | None = field(
        default=None, repr=False,
    )

    def __post_init__(self) -> None:
        if self.monthly_savings is not None:
            _integer("monthly_savings", self.monthly_savings, minimum=0)
        if self.retirement_age is not None:
            _integer("retirement_age", self.retirement_age, minimum=18, maximum=100)
        if self.retirement_expenses is not None:
            object.__setattr__(self, "retirement_expenses", _periods(
                "retirement_expenses", self.retirement_expenses, RetirementExpensePeriod,
            ))


def create_what_if_profile(
    profile: RetirementProfile,
    changes: WhatIfChanges,
) -> RetirementProfile:
    """Return a newly validated profile without mutating either input.

All overrides are applied together before RetirementProfile validates expense
coverage. No missing age is filled, no pension is shifted, and no assumptions
are accepted here. Even a no-op returns a new profile equal to the baseline.
Unsupported model inputs raise ModelValidationError without echoing values.
"""
    if not isinstance(profile, RetirementProfile):
        raise ModelValidationError("profile", "must be a RetirementProfile model")
    if not isinstance(changes, WhatIfChanges):
        raise ModelValidationError("changes", "must be a WhatIfChanges model")

    retirement_age = (
        profile.retirement_age if changes.retirement_age is None else changes.retirement_age
    )
    if retirement_age != profile.retirement_age:
        if profile.retirement_age < profile.current_age:
            raise ModelValidationError(
                "retirement_age", "must not change an already past retirement age",
            )
        if retirement_age < profile.current_age:
            raise ModelValidationError(
                "retirement_age", "must not precede current_age in a What-if change",
            )

    # Explicit selection preserves 0 and () rather than falling back to baseline.
    # A single replace validates combined retirement + expense changes atomically.
    return replace(
        profile,
        monthly_savings=(
            profile.monthly_savings if changes.monthly_savings is None else changes.monthly_savings
        ),
        retirement_age=retirement_age,
        retirement_expenses=(
            profile.retirement_expenses
            if changes.retirement_expenses is None else changes.retirement_expenses
        ),
    )
