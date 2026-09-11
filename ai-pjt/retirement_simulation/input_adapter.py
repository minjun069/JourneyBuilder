"""Pure saved-data mapping; no ORM, HTTP, form merging, or market defaults.

See docs/input-mapping-rules.md for the deliberately limited financial scope.
Callers must carry the returned notices into their analysis output. A successful
mapping is neither proof of ownership/completeness nor approval for service use.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from re import fullmatch
from typing import cast

from .models import (
    MAX_AGE,
    ModelValidationError,
    PensionIncome,
    RetirementExpensePeriod,
    RetirementProfile,
    SimulationAssumptions,
    _integer,
)


_SourceRows = tuple[Mapping[str, object], ...]
_SourceSections = Mapping[str, _SourceRows]

_SECTIONS = (
    "financial_assets", "pension_assets", "goal_periods",
    "real_estate_assets", "liabilities", "goal_milestones",
)
_EMPTY_SECTIONS = ("financial_assets", "pension_assets")
_MODELED_ASSET_TYPES = ("예금/적금", "주식", "펀드/ETF", "채권", "가상자산")
_ASSUMPTION_FIELDS = (
    "annual_return_mean", "annual_return_volatility", "annual_inflation_rate",
    "num_paths", "seed",
)
_ERROR_MESSAGES = {
    "missing_field": "a required field is missing",
    "missing_value": "a required value is missing",
    "invalid_type": "the input has an unsupported structure",
    "invalid_value": "the value is outside the supported type or range",
    "unsupported_value": "the input is not supported by this mapping",
    "duplicate_id": "a source row identifier is duplicated",
    "empty_confirmation_required": "an empty section requires trusted confirmation",
    "invalid_confirmation": "empty-section confirmation is invalid or contradictory",
    "invalid_profile": "the mapped profile does not satisfy the model rules",
    "unknown_field": "the input contains an unsupported field",
}
_NOTICE_PATHS = {
    "partial_financial_scope": "saved_data",
    "single_portfolio_assumption": "financial_assets",
    "pre_retirement_cashflow_not_recalculated": "financial_assets",
    "pension_account_not_modeled": "pension_assets",
    "real_estate_not_modeled": "real_estate_assets",
    "liabilities_not_modeled": "liabilities",
    "milestones_not_modeled": "goal_milestones",
    "confirmed_empty_financial_assets": "financial_assets",
    "confirmed_empty_pensions": "pension_assets",
    "other_financial_asset_not_modeled": r"financial_assets\[[0-9]+\]",
    "pension_outside_horizon": r"pension_assets\[[0-9]+\]",
    "pension_horizon_limited": r"pension_assets\[[0-9]+\]",
}
_PROFILE_SOURCE_FIELDS = {
    "current_age": "basic_info.age",
    "retirement_age": "basic_info.retirement_age",
    "end_age": "basic_info.end_age",
    "liquid_assets": "financial_assets",
    "monthly_savings": "financial_assets",
    "retirement_expenses": "goal_periods",
    "pension_incomes": "pension_assets",
}


class InputMappingError(ValueError):
    """Mapping failure with a fixed code and source path, never rejected values.

Functions in this module construct paths only from known keys and list indices.
Unknown caller-supplied keys and source IDs are not echoed into errors.
"""

    def __init__(self, code: str, field_name: str) -> None:
        self.code = code
        self.field_name = field_name
        self.message = _ERROR_MESSAGES[code]
        super().__init__(f"{field_name}: {self.message} ({code})")


@dataclass(frozen=True, slots=True, kw_only=True)
class InputMappingNotice:
    """Fixed scope/interpretation code and safe path, not customer content."""

    code: str
    field_name: str

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or self.code not in _NOTICE_PATHS:
            raise ModelValidationError("notice.code", "must be a supported notice code")
        if (not isinstance(self.field_name, str)
                or fullmatch(_NOTICE_PATHS[self.code], self.field_name) is None):
            raise ModelValidationError("notice.field_name", "must match the notice source")


@dataclass(frozen=True, slots=True, kw_only=True)
class ProfileMappingResult:
    """Detached immutable profile plus mandatory scope notices and mapping version."""

    profile: RetirementProfile = field(repr=False)
    notices: tuple[InputMappingNotice, ...] = field(repr=False)
    mapping_version: str = field(default="retirement-input-v1", init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.profile, RetirementProfile):
            raise ModelValidationError("profile", "must be a RetirementProfile model")
        if (not isinstance(self.notices, (list, tuple))
                or any(not isinstance(n, InputMappingNotice) for n in self.notices)):
            raise ModelValidationError("notices", "must contain mapping notices")
        object.__setattr__(self, "notices", tuple(self.notices))


def _mapping(value: object, path: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise InputMappingError("invalid_type", path)
    return value


def _required(row: Mapping[str, object], key: str, path: str) -> object:
    if key not in row:
        raise InputMappingError("missing_field", path)
    return row[key]


def _number(value: object, path: str, minimum: int = 0, maximum: int | None = None) -> int:
    try:
        _integer(path, value, minimum=minimum, maximum=maximum)
    except ModelValidationError:
        raise InputMappingError("invalid_value", path) from None
    return cast(int, value)


def _int_field(
    row: Mapping[str, object], key: str, path: str,
    minimum: int = 0, maximum: int | None = None,
) -> int:
    return _number(_required(row, key, f"{path}.{key}"), f"{path}.{key}", minimum, maximum)


def _text_field(row: Mapping[str, object], key: str, path: str) -> str:
    value = _required(row, key, f"{path}.{key}")
    if value is None or (isinstance(value, str) and not value.strip()):
        raise InputMappingError("missing_value", f"{path}.{key}")
    if not isinstance(value, str):
        raise InputMappingError("invalid_type", f"{path}.{key}")
    return value.strip()


def _rows(
    value: object, path: str, *, seen_ids: set[int] | None = None,
) -> _SourceRows:
    if not isinstance(value, (list, tuple)):
        raise InputMappingError("invalid_type", path)
    seen = set() if seen_ids is None else seen_ids
    rows = []
    for index, item in enumerate(value):
        row_path = f"{path}[{index}]"
        row = _mapping(item, row_path)
        if "id" in row:
            identifier = _int_field(row, "id", row_path, minimum=1)
            if identifier in seen:
                raise InputMappingError("duplicate_id", f"{row_path}.id")
            seen.add(identifier)
        rows.append(row)
    return tuple(rows)


def _confirm_empty(value: object, sections: _SourceSections) -> frozenset[str]:
    if not isinstance(value, (list, tuple)):
        raise InputMappingError("invalid_type", "confirmed_empty_sections")
    confirmed = set()
    for index, name in enumerate(value):
        path = f"confirmed_empty_sections[{index}]"
        if not isinstance(name, str) or name not in _EMPTY_SECTIONS or name in confirmed:
            raise InputMappingError("invalid_confirmation", path)
        if sections[name]:
            raise InputMappingError("invalid_confirmation", path)
        confirmed.add(name)
    for name in _EMPTY_SECTIONS:
        if not sections[name] and name not in confirmed:
            raise InputMappingError("empty_confirmation_required", name)
    return frozenset(confirmed)


def _financial_totals(rows: _SourceRows) -> tuple[int, int, tuple[InputMappingNotice, ...]]:
    """Sum modeled rows only; retain source positions for recognized exclusions."""
    assets = savings = 0
    notices = []
    for index, row in enumerate(rows):
        path = f"financial_assets[{index}]"
        kind = _text_field(row, "asset_type", path)
        if kind not in _MODELED_ASSET_TYPES:
            # Saved classifications are open text; do not infer a modeled type.
            # As with excluded real estate, unused amounts are not read or imputed.
            notices.append(InputMappingNotice(code="other_financial_asset_not_modeled", field_name=path))
            continue
        assets += _int_field(row, "current_value", path)
        savings += _int_field(row, "monthly_savings", path)
    return assets, savings, tuple(notices)


def _expense_periods(rows: _SourceRows) -> tuple[RetirementExpensePeriod, ...]:
    periods = []
    item_ids: set[int] = set()
    for index, row in enumerate(rows):
        path = f"goal_periods[{index}]"
        phase = _required(row, "phase", f"{path}.phase")
        if phase not in ("before", "after"):
            raise InputMappingError("unsupported_value", f"{path}.phase")
        items = _rows(_required(row, "items", f"{path}.items"),
                      f"{path}.items", seen_ids=item_ids)
        if phase == "before":
            continue
        start = _int_field(row, "from_age", path, 1, MAX_AGE)
        end = _int_field(row, "to_age", path, 1, MAX_AGE)
        if start > end:
            raise InputMappingError("invalid_value", f"{path}.to_age")
        if not items:
            raise InputMappingError("missing_value", f"{path}.items")
        amount = sum(_int_field(item, "amount", f"{path}.items[{i}]")
                     for i, item in enumerate(items))
        periods.append(RetirementExpensePeriod(from_age=start, to_age=end, monthly_amount=amount))
    return tuple(periods)


def _pension_incomes(
    rows: _SourceRows, current_age: int, end_age: int,
) -> tuple[tuple[PensionIncome, ...], tuple[InputMappingNotice, ...]]:
    pensions = []
    notices = []
    for index, row in enumerate(rows):
        path = f"pension_assets[{index}]"
        if _text_field(row, "receive_method", path) != "연금형":
            raise InputMappingError("unsupported_value", f"{path}.receive_method")
        start = _int_field(row, "pension_start_age", path, 1, MAX_AGE)
        amount = _int_field(row, "expected_amount", path)
        period = _text_field(row, "pension_period", path)
        actual_end = None
        if period != "종신":
            if fullmatch(r"[1-9][0-9]{0,2}년", period) is None or int(period[:-1]) > MAX_AGE:
                raise InputMappingError("unsupported_value", f"{path}.pension_period")
            actual_end = start + int(period[:-1]) - 1
        if start > end_age or (actual_end is not None and actual_end < current_age):
            notices.append(InputMappingNotice(code="pension_outside_horizon", field_name=path))
            continue
        if actual_end is None or actual_end > end_age:
            notices.append(InputMappingNotice(code="pension_horizon_limited", field_name=path))
        end = end_age if actual_end is None else min(actual_end, end_age)
        pensions.append(PensionIncome(start_age=start, end_age=end, monthly_amount=amount))
    return tuple(pensions), tuple(notices)


def _scope_notices(
    sections: _SourceSections, confirmed: frozenset[str],
) -> list[InputMappingNotice]:
    notices = [InputMappingNotice(code=code, field_name=_NOTICE_PATHS[code]) for code in (
        "partial_financial_scope", "single_portfolio_assumption",
        "pre_retirement_cashflow_not_recalculated",
    )]
    for section, code in (
        ("pension_assets", "pension_account_not_modeled"),
        ("real_estate_assets", "real_estate_not_modeled"),
        ("liabilities", "liabilities_not_modeled"),
        ("goal_milestones", "milestones_not_modeled"),
    ):
        if sections[section]:
            notices.append(InputMappingNotice(code=code, field_name=section))
    for section, code in (
        ("financial_assets", "confirmed_empty_financial_assets"),
        ("pension_assets", "confirmed_empty_pensions"),
    ):
        if section in confirmed:
            notices.append(InputMappingNotice(code=code, field_name=section))
    return notices


def map_retirement_profile(
    saved_data: Mapping[str, object], *,
    confirmed_empty_sections: list[str] | tuple[str, ...] = (),
) -> ProfileMappingResult:
    """Map the saved_data object, not a customer_info wrapper or unsaved form.

Only saved numeric KRW and explicit schedules are accepted. The optional empty
confirmations are trusted caller facts, not inferred from [] or session status.
All failures abort mapping with InputMappingError; no partial profile is returned.
The returned profile/notices contain no references to the supplied mutable rows.
"""
    saved = _mapping(saved_data, "saved_data")
    basic = _mapping(_required(saved, "basic_info", "basic_info"), "basic_info")
    sections = {key: _rows(_required(saved, key, key), key) for key in _SECTIONS}
    confirmed = _confirm_empty(confirmed_empty_sections, sections)
    current_age = _int_field(basic, "age", "basic_info", 18, 100)
    retirement_age = _int_field(basic, "retirement_age", "basic_info", 18, 100)
    end_age = _int_field(basic, "end_age", "basic_info", 18, MAX_AGE)
    if end_age < max(current_age, retirement_age):
        raise InputMappingError("invalid_value", "basic_info.end_age")
    assets, savings, financial_notices = _financial_totals(sections["financial_assets"])
    expenses = _expense_periods(sections["goal_periods"])
    pensions, pension_notices = _pension_incomes(sections["pension_assets"], current_age, end_age)
    try:
        profile = RetirementProfile(
            current_age=current_age, retirement_age=retirement_age, end_age=end_age,
            liquid_assets=assets, monthly_savings=savings,
            retirement_expenses=expenses, pension_incomes=pensions,
        )
    except ModelValidationError as error:
        raise InputMappingError(
            "invalid_profile", _PROFILE_SOURCE_FIELDS.get(error.field_name, "saved_data"),
        ) from None
    notices = (*_scope_notices(sections, confirmed), *financial_notices, *pension_notices)
    return ProfileMappingResult(profile=profile, notices=notices)


def map_simulation_assumptions(values: Mapping[str, object]) -> SimulationAssumptions:
    """Validate five explicit fraction-based settings without inferring defaults.

Unknown keys are rejected without echoing them. Market settings, number of paths,
and seed must all be provided; this does not choose a service resource policy.
"""
    values = _mapping(values, "assumptions")
    if any(key not in _ASSUMPTION_FIELDS for key in values):
        raise InputMappingError("unknown_field", "assumptions")
    fields = {key: _required(values, key, f"assumptions.{key}") for key in _ASSUMPTION_FIELDS}
    try:
        return SimulationAssumptions(**fields)
    except ModelValidationError as error:
        path = (f"assumptions.{error.field_name}"
                if error.field_name in _ASSUMPTION_FIELDS else "assumptions")
        raise InputMappingError("invalid_value", path) from None
