"""Map explicit What-if change dictionaries without applying or calculating them.

See docs/what-if-adapter.md for validation order and the baseline-dependent
checks left to create_what_if_profile. No source objects or defaults are kept.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from .input_adapter import InputMappingError, _int_field, _mapping, _number, _required
from .models import MAX_AGE, ModelValidationError, RetirementExpensePeriod
from .what_if import WhatIfChanges
from .what_if_engine import MAX_WHAT_IF_SCENARIOS, WhatIfScenario, _scenario_id


_SCENARIO_FIELDS = ("scenario_id", "changes")
_CHANGE_FIELDS = ("monthly_savings", "retirement_age", "retirement_expenses")
_EXPENSE_FIELDS = ("from_age", "to_age", "monthly_amount")


def _strict_mapping(
    value: object, path: str, allowed_fields: tuple[str, ...],
) -> Mapping[str, object]:
    row = _mapping(value, path)
    if any(key not in allowed_fields for key in row):
        # Never include caller-supplied keys in a diagnostic path.
        raise InputMappingError("unknown_field", path)
    return row


def _expenses(value: object, path: str) -> tuple[RetirementExpensePeriod, ...]:
    if not isinstance(value, (list, tuple)):
        raise InputMappingError("invalid_type", path)
    if not value:
        raise InputMappingError("missing_value", path)
    periods = []
    for index, item in enumerate(value):
        row_path = f"{path}[{index}]"
        row = _strict_mapping(item, row_path, _EXPENSE_FIELDS)
        start = _int_field(row, "from_age", row_path, 1, MAX_AGE)
        end = _int_field(row, "to_age", row_path, 1, MAX_AGE)
        if start > end:
            raise InputMappingError("invalid_value", f"{row_path}.to_age")
        amount = _int_field(row, "monthly_amount", row_path)
        periods.append(RetirementExpensePeriod(
            from_age=start, to_age=end, monthly_amount=amount,
        ))
    # Keep source order here; profile application owns sorting and coverage.
    return tuple(periods)


def _changes(value: object, path: str) -> WhatIfChanges:
    row = _strict_mapping(value, path, _CHANGE_FIELDS)
    savings = row.get("monthly_savings")
    retirement = row.get("retirement_age")
    expenses = row.get("retirement_expenses")
    return WhatIfChanges(
        monthly_savings=(
            None if savings is None else _number(savings, f"{path}.monthly_savings")
        ),
        retirement_age=(
            None if retirement is None
            else _number(retirement, f"{path}.retirement_age", 18, 100)
        ),
        retirement_expenses=(
            None if expenses is None else _expenses(expenses, f"{path}.retirement_expenses")
        ),
    )


def map_what_if_scenarios(values: object) -> tuple[WhatIfScenario, ...]:
    """Return detached immutable definitions for 0..10 explicit change mappings.

    Rows need unique valid scenario_id and changes keys, with no extra fields.
    Missing/None change values retain the baseline; zero is an absolute override.
    An empty changes mapping is a no-op, but an explicit empty expense list is
    invalid. Only list/tuple collections and Mapping rows are accepted.

    Failures raise InputMappingError with fixed keys and array indices, without
    echoing values or returning partial results. This function does not require
    a baseline, merge snapshots, apply changes, or run any financial calculation.
    Empty output is valid here, unlike calculate_what_if's nonempty batch input.
    """
    if not isinstance(values, (list, tuple)):
        raise InputMappingError("invalid_type", "scenarios")
    if len(values) > MAX_WHAT_IF_SCENARIOS:
        raise InputMappingError("invalid_value", "scenarios")

    scenarios = []
    seen: set[str] = set()
    for index, item in enumerate(values):
        path = f"scenarios[{index}]"
        row = _strict_mapping(item, path, _SCENARIO_FIELDS)
        identifier = _required(row, "scenario_id", f"{path}.scenario_id")
        changes = _required(row, "changes", f"{path}.changes")
        try:
            _scenario_id(identifier)
        except ModelValidationError:
            raise InputMappingError("invalid_value", f"{path}.scenario_id") from None
        identifier = cast(str, identifier)
        if identifier in seen:
            raise InputMappingError("duplicate_id", f"{path}.scenario_id")
        seen.add(identifier)
        scenarios.append(WhatIfScenario(
            scenario_id=identifier, changes=_changes(changes, f"{path}.changes"),
        ))
    return tuple(scenarios)
