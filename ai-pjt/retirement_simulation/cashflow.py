"""Pure annual cashflow calculation; no RNG, Agent, storage, or HTTP calls.

See docs/cashflow-rules.md for timing, inflation, and shortfall semantics.
Negative closing balances carry unmet funding needs, not an actual loan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite

from .models import (
    MAX_AGE,
    AssetPoint,
    ModelValidationError,
    RetirementProfile,
    SimulationAssumptions,
    _finite_number,
    _integer,
)


class CashflowCalculationError(ArithmeticError):
    """Non-finite calculation, with a safe field name and optional year age."""

    def __init__(self, field_name: str, *, age: int | None = None) -> None:
        self.field_name = field_name
        self.age = age
        location = "" if age is None else f" at age {age}"
        super().__init__(f"{field_name}: calculation is not finite{location}")


@dataclass(frozen=True, slots=True, kw_only=True)
class CashflowYear:
    """One complete age-year; amounts are unrounded nominal float KRW."""

    age: int
    opening_balance: float = field(repr=False)
    annual_return_rate: float = field(repr=False)
    investment_return: float = field(repr=False)
    savings: float = field(repr=False)
    pension_income: float = field(repr=False)
    expenses: float = field(repr=False)
    closing_balance: float = field(repr=False)

    def __post_init__(self) -> None:
        _integer("age", self.age, minimum=18, maximum=MAX_AGE)
        for name in (
            "opening_balance", "annual_return_rate", "investment_return",
            "savings", "pension_income", "expenses", "closing_balance",
        ):
            object.__setattr__(self, name, _finite_number(name, getattr(self, name)))
        if self.annual_return_rate <= -1:
            raise ModelValidationError("annual_return_rate", "must be greater than -1")
        for name in ("savings", "pension_income", "expenses"):
            if getattr(self, name) < 0:
                raise ModelValidationError(name, "must not be negative")

    @property
    def shortfall(self) -> float:
        """Outstanding unmet funding at year end; do not sum across years."""
        return max(-self.closing_balance, 0.0)

    @property
    def asset_point(self) -> AssetPoint:
        return AssetPoint(age=self.age, liquid_assets=self.closing_balance)


@dataclass(frozen=True, slots=True, kw_only=True)
class CashflowResult:
    """Immutable single path; summaries are derived, never stored separately."""

    years: tuple[CashflowYear, ...] = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.years, (list, tuple)) or not self.years:
            raise ModelValidationError("years", "must be a non-empty list or tuple")
        if any(not isinstance(year, CashflowYear) for year in self.years):
            raise ModelValidationError("years", "must contain CashflowYear models")
        years = tuple(self.years)
        for previous, current in zip(years, years[1:]):
            if current.age != previous.age + 1:
                raise ModelValidationError("years", "ages must be consecutive and ascending")
            if current.opening_balance != previous.closing_balance:
                raise ModelValidationError("years", "balances must carry between years")
        object.__setattr__(self, "years", years)

    @property
    def asset_path(self) -> tuple[AssetPoint, ...]:
        return tuple(year.asset_point for year in self.years)

    @property
    def final_balance(self) -> float:
        return self.years[-1].closing_balance

    @property
    def final_shortfall(self) -> float:
        return self.years[-1].shortfall

    @property
    def first_depletion_age(self) -> int | None:
        return next((year.age for year in self.years if year.closing_balance < 0), None)


def _inputs(profile: RetirementProfile, assumptions: SimulationAssumptions) -> None:
    if not isinstance(profile, RetirementProfile):
        raise ModelValidationError("profile", "must be a RetirementProfile model")
    if not isinstance(assumptions, SimulationAssumptions):
        raise ModelValidationError("assumptions", "must be a SimulationAssumptions model")


def _checked(name: str, value: int | float, *, age: int | None = None) -> float:
    try:
        number = float(value)
    except OverflowError:
        raise CashflowCalculationError(name, age=age) from None
    if not isfinite(number):
        raise CashflowCalculationError(name, age=age)
    return number


def _expenses(profile: RetirementProfile, inflation: float, age: int) -> float:
    if age < profile.retirement_age:
        return 0.0
    # Profile validation guarantees exactly one covering period. No fallback.
    period = next(
        period for period in profile.retirement_expenses
        if period.from_age <= age <= period.to_age
    )
    if period.monthly_amount == 0:
        return 0.0
    annual_amount = _checked("annual_expenses", period.monthly_amount * 12, age=age)
    try:
        factor = (1.0 + inflation) ** (age - profile.current_age)
    except OverflowError:
        raise CashflowCalculationError("inflation_factor", age=age) from None
    factor = _checked("inflation_factor", factor, age=age)
    return _checked("expenses", annual_amount * factor, age=age)


def calculate_cashflow(
    profile: RetirementProfile,
    assumptions: SimulationAssumptions,
) -> CashflowResult:
    """Apply the same annual_return_mean every year, without random sampling.

Volatility, num_paths, and seed remain validated inputs but are unused here.
This is a fixed-assumption path, not an expected Monte Carlo outcome.
"""
    _inputs(profile, assumptions)
    rates = (assumptions.annual_return_mean,) * (
        profile.end_age - profile.current_age + 1
    )
    return calculate_cashflow_path(profile, assumptions, annual_returns=rates)


def calculate_cashflow_path(
    profile: RetirementProfile,
    assumptions: SimulationAssumptions,
    *,
    annual_returns: list[float] | tuple[float, ...],
) -> CashflowResult:
    """Use one explicit finite return per age-year, replacing the mean.

Only lists/tuples of int/float rates > -1 are accepted; bool is rejected.
The path is validated and copied before any calculation. No input is mutated.
Errors return no partial path, and do not echo supplied financial values.
"""
    _inputs(profile, assumptions)
    if not isinstance(annual_returns, (list, tuple)):
        raise ModelValidationError("annual_returns", "must be a list or tuple")
    if len(annual_returns) != profile.end_age - profile.current_age + 1:
        raise ModelValidationError("annual_returns", "must contain exactly one rate per year")
    rates = tuple(_finite_number("annual_returns", rate) for rate in annual_returns)
    if any(rate <= -1 for rate in rates):
        raise ModelValidationError("annual_returns", "each rate must be greater than -1")

    balance = _checked("liquid_assets", profile.liquid_assets)
    years: list[CashflowYear] = []
    for offset, rate in enumerate(rates):
        age = profile.current_age + offset
        investment = _checked("investment_return", max(balance, 0.0) * rate, age=age)
        savings = _checked(
            "savings", profile.monthly_savings * 12 if age < profile.retirement_age else 0,
            age=age,
        )
        pension = _checked(
            "pension_income",
            12 * sum(
                period.monthly_amount for period in profile.pension_incomes
                if period.start_age <= age <= period.end_age
            ),
            age=age,
        )
        expenses = _expenses(profile, assumptions.annual_inflation_rate, age)
        # Check each intermediate in the documented order, not just the total.
        closing = _checked("balance_after_return", balance + investment, age=age)
        closing = _checked("balance_after_savings", closing + savings, age=age)
        closing = _checked("balance_after_pension", closing + pension, age=age)
        closing = _checked("closing_balance", closing - expenses, age=age)
        years.append(CashflowYear(
            age=age,
            opening_balance=balance,
            annual_return_rate=rate,
            investment_return=investment,
            savings=savings,
            pension_income=pension,
            expenses=expenses,
            closing_balance=closing,
        ))
        balance = closing
    return CashflowResult(years=tuple(years))
