"""Minimal internal models; public request/response contracts remain unchanged.

Money inputs are integer KRW, contributions and payments are monthly, and
assumption rates are annual decimal fractions. All age intervals are inclusive.
No database access, snapshot merging, random sampling, or financial calculation
is performed here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from typing import TypeVar


MAX_AGE = 120
MAX_PATHS = 100_000


class ModelValidationError(ValueError):
    """An internal validation error that does not include the rejected value."""

    def __init__(self, field_name: str, message: str) -> None:
        self.field_name = field_name
        self.message = message
        super().__init__(f"{field_name}: {message}")


def _integer(
    name: str,
    value: object,
    *,
    minimum: int,
    maximum: int | None = None,
) -> None:
    # bool is an int subclass, but never a valid amount, age, seed, or count.
    if not isinstance(value, int) or isinstance(value, bool):
        raise ModelValidationError(name, "must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        raise ModelValidationError(name, "is outside the supported range")


def _finite_number(name: str, value: object) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ModelValidationError(name, "must be a finite number")
    try:
        number = float(value)
    except OverflowError:
        raise ModelValidationError(name, "must be a finite number") from None
    if not isfinite(number):
        raise ModelValidationError(name, "must be a finite number")
    return number


def _age_interval(start_name: str, start: int, end_name: str, end: int) -> None:
    _integer(start_name, start, minimum=1, maximum=MAX_AGE)
    _integer(end_name, end, minimum=1, maximum=MAX_AGE)
    if start > end:
        raise ModelValidationError(end_name, "must not precede the start age")


@dataclass(frozen=True, slots=True, kw_only=True)
class RetirementExpensePeriod:
    """Total monthly retirement spending, in today's KRW, for an age range.

This is an already aggregated schedule, not an individual goal_periods item.
An explicit zero amount is valid; an absent age range is not a zero expense.
"""

    from_age: int
    to_age: int
    monthly_amount: int = field(repr=False)

    def __post_init__(self) -> None:
        _age_interval("from_age", self.from_age, "to_age", self.to_age)
        _integer("monthly_amount", self.monthly_amount, minimum=0)


@dataclass(frozen=True, slots=True, kw_only=True)
class PensionIncome:
    """A supplied monthly cash payment, not a pension benefit estimator.

Amounts are nominal KRW received during the inclusive start/end age range.
Multiple pensions may overlap. Account balances and lump sums are not included.
"""

    start_age: int
    end_age: int
    monthly_amount: int = field(repr=False)

    def __post_init__(self) -> None:
        _age_interval("start_age", self.start_age, "end_age", self.end_age)
        _integer("monthly_amount", self.monthly_amount, minimum=0)


_Period = TypeVar("_Period", RetirementExpensePeriod, PensionIncome)


def _periods(name: str, value: object, period_type: type[_Period]) -> tuple[_Period, ...]:
    if not isinstance(value, (list, tuple)):
        raise ModelValidationError(name, "must be a list or tuple of period models")
    if any(not isinstance(item, period_type) for item in value):
        raise ModelValidationError(name, "contains an unsupported period model")
    # Take an immutable copy; later What-if changes must not mutate caller data.
    return tuple(value)


@dataclass(frozen=True, slots=True, kw_only=True)
class RetirementProfile:
    """Calculation-only input, without identity, ORM, or conversation state.

liquid_assets is the opening financial balance at current_age. monthly_savings
is the total monthly contribution to that balance before retirement, not an
increment and not an additional income-minus-expenses surplus. Pension account
contributions are excluded from this first model.
"""

    current_age: int
    retirement_age: int
    end_age: int
    liquid_assets: int = field(repr=False)
    monthly_savings: int = field(repr=False)
    retirement_expenses: tuple[RetirementExpensePeriod, ...]
    pension_incomes: tuple[PensionIncome, ...]

    def __post_init__(self) -> None:
        _integer("current_age", self.current_age, minimum=18, maximum=100)
        _integer("retirement_age", self.retirement_age, minimum=18, maximum=100)
        _integer("end_age", self.end_age, minimum=18, maximum=MAX_AGE)
        if self.end_age < max(self.current_age, self.retirement_age):
            raise ModelValidationError(
                "end_age", "must not precede current_age or retirement_age"
            )
        _integer("liquid_assets", self.liquid_assets, minimum=0)
        _integer("monthly_savings", self.monthly_savings, minimum=0)

        expenses = _periods(
            "retirement_expenses", self.retirement_expenses, RetirementExpensePeriod
        )
        pensions = _periods("pension_incomes", self.pension_incomes, PensionIncome)
        expenses = tuple(sorted(expenses, key=lambda period: period.from_age))
        for previous, current in zip(expenses, expenses[1:]):
            if current.from_age <= previous.to_age:
                raise ModelValidationError(
                    "retirement_expenses", "age ranges must not overlap"
                )

        # Do not turn missing retirement spending into a misleading zero.
        first_retirement_year = max(self.current_age, self.retirement_age)
        for age in range(first_retirement_year, self.end_age + 1):
            if not any(period.from_age <= age <= period.to_age for period in expenses):
                raise ModelValidationError(
                    "retirement_expenses",
                    "must cover every retirement year in the planning horizon",
                )

        object.__setattr__(self, "retirement_expenses", expenses)
        object.__setattr__(self, "pension_incomes", pensions)


@dataclass(frozen=True, slots=True, kw_only=True)
class SimulationAssumptions:
    """Explicit model parameters, not recommended or inferred market values.

annual_return_mean refers to an arithmetic simple-return mean, not a log-return
parameter. Distribution choice and rate conversion belong to later stages.
num_paths has a provisional resource cap; no paths are allocated at this stage.
"""

    annual_return_mean: float
    annual_return_volatility: float
    annual_inflation_rate: float
    num_paths: int = 1_000
    seed: int = 0

    def __post_init__(self) -> None:
        mean = _finite_number("annual_return_mean", self.annual_return_mean)
        volatility = _finite_number(
            "annual_return_volatility", self.annual_return_volatility
        )
        inflation = _finite_number("annual_inflation_rate", self.annual_inflation_rate)
        if mean <= -1:
            raise ModelValidationError("annual_return_mean", "must be greater than -1")
        if volatility < 0:
            raise ModelValidationError(
                "annual_return_volatility", "must not be negative"
            )
        if inflation <= -1:
            raise ModelValidationError(
                "annual_inflation_rate", "must be greater than -1"
            )
        _integer("num_paths", self.num_paths, minimum=1, maximum=MAX_PATHS)
        _integer("seed", self.seed, minimum=0)
        object.__setattr__(self, "annual_return_mean", mean)
        object.__setattr__(self, "annual_return_volatility", volatility)
        object.__setattr__(self, "annual_inflation_rate", inflation)


@dataclass(frozen=True, slots=True, kw_only=True)
class AssetPoint:
    """Minimal internal output: one year's closing liquid financial balance.

Finite negative balances are retained to represent a shortfall. Zero alone
does not imply depletion. Whole-path success and probability aggregation are
not implemented by this point model.
"""

    age: int
    liquid_assets: float = field(repr=False)

    def __post_init__(self) -> None:
        _integer("age", self.age, minimum=1, maximum=MAX_AGE)
        amount = _finite_number("liquid_assets", self.liquid_assets)
        object.__setattr__(self, "liquid_assets", amount)

    @property
    def is_depleted(self) -> bool:
        return self.liquid_assets < 0
