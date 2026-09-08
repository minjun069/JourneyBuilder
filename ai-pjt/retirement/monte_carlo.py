"""Annual Monte Carlo orchestration and summaries, without service integration.

Return generation and cashflow arithmetic stay in their existing modules.
Every requested path contributes to the denominator and amount percentiles.
See docs/monte-carlo-rules.md for the model assumptions and interpretation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import floor, isfinite
from platform import python_implementation, python_version
from typing import Literal

from .cashflow import CashflowCalculationError, calculate_cashflow_path
from .models import (
    MAX_AGE,
    MAX_PATHS,
    ModelValidationError,
    RetirementProfile,
    SimulationAssumptions,
    _finite_number,
    _integer,
)
from .return_paths import ReturnPathGenerationError, generate_return_paths


class MonteCarloCalculationError(ArithmeticError):
    """Numeric execution failure, never a depleted path or a partial result."""

    def __init__(
        self,
        stage: str,
        field_name: str,
        *,
        path_index: int | None = None,
        age: int | None = None,
    ) -> None:
        self.stage = stage
        self.field_name = field_name
        self.path_index = path_index
        self.age = age
        location = "" if path_index is None else f" at path {path_index}"
        if age is not None:
            location += f" at age {age}"
        super().__init__(f"{stage}.{field_name}: calculation failed{location}")


@dataclass(frozen=True, slots=True, kw_only=True)
class AmountPercentiles:
    """Unrounded finite KRW P10/P50/P90, including negative balances."""

    p10: float = field(repr=False)
    p50: float = field(repr=False)
    p90: float = field(repr=False)

    def __post_init__(self) -> None:
        for name in ("p10", "p50", "p90"):
            object.__setattr__(self, name, _finite_number(name, getattr(self, name)))
        if not self.p10 <= self.p50 <= self.p90:
            raise ModelValidationError("percentiles", "must be nondecreasing")


@dataclass(frozen=True, slots=True, kw_only=True)
class AssetPercentiles:
    """One age-year's closing-balance percentiles, not an individual path."""

    age: int
    balance: AmountPercentiles = field(repr=False)

    def __post_init__(self) -> None:
        _integer("age", self.age, minimum=18, maximum=MAX_AGE)
        if not isinstance(self.balance, AmountPercentiles):
            raise ModelValidationError("balance", "must be AmountPercentiles")


@dataclass(frozen=True, slots=True, kw_only=True)
class FirstDepletionCount:
    """Number of paths that first become negative at this age-year's end."""

    age: int
    count: int

    def __post_init__(self) -> None:
        _integer("age", self.age, minimum=18, maximum=MAX_AGE)
        _integer("count", self.count, minimum=0, maximum=MAX_PATHS)


@dataclass(frozen=True, slots=True, kw_only=True)
class MonteCarloMetadata:
    """Execution context; provided paths are not attributed to a seed or RNG."""

    current_age: int
    end_age: int
    assumptions: SimulationAssumptions = field(repr=False)
    path_source: Literal["generated", "provided"]
    model_version: str = field(default="retirement-mc-v1", init=False)
    python_version: str = field(default_factory=python_version, init=False)
    python_implementation: str = field(default_factory=python_implementation, init=False)

    def __post_init__(self) -> None:
        _integer("current_age", self.current_age, minimum=18, maximum=100)
        _integer("end_age", self.end_age, minimum=18, maximum=MAX_AGE)
        if self.end_age < self.current_age:
            raise ModelValidationError("end_age", "must not precede current_age")
        if not isinstance(self.assumptions, SimulationAssumptions):
            raise ModelValidationError("assumptions", "must be a SimulationAssumptions model")
        if not isinstance(self.path_source, str) or self.path_source not in (
            "generated", "provided",
        ):
            raise ModelValidationError("path_source", "must be generated or provided")

    @property
    def num_paths(self) -> int:
        return self.assumptions.num_paths

    @property
    def return_model(self) -> str | None:
        if self.path_source == "provided":
            return None
        if self.assumptions.annual_return_volatility == 0:
            return "constant"
        return "lognormal_gross_return"

    @property
    def rng_algorithm(self) -> str | None:
        if self.return_model == "lognormal_gross_return":
            return "python.random.Random.gauss"
        return None

    @property
    def seed(self) -> int | None:
        """Effective RNG seed; configured but unused seeds remain in assumptions."""
        return self.assumptions.seed if self.rng_algorithm is not None else None


@dataclass(frozen=True, slots=True, kw_only=True)
class MonteCarloResult:
    """Immutable summaries; counts and final-balance summaries have one source."""

    metadata: MonteCarloMetadata
    asset_percentiles: tuple[AssetPercentiles, ...] = field(repr=False)
    first_depletion_counts: tuple[FirstDepletionCount, ...] = field(repr=False)
    final_shortfall_percentiles: AmountPercentiles = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.metadata, MonteCarloMetadata):
            raise ModelValidationError("metadata", "must be MonteCarloMetadata")
        expected_ages = tuple(range(self.metadata.current_age, self.metadata.end_age + 1))
        for name, item_type in (
            ("asset_percentiles", AssetPercentiles),
            ("first_depletion_counts", FirstDepletionCount),
        ):
            values = getattr(self, name)
            if not isinstance(values, (list, tuple)):
                raise ModelValidationError(name, "must be a list or tuple of result models")
            if any(not isinstance(value, item_type) for value in values):
                raise ModelValidationError(name, "contains an unsupported result model")
            values = tuple(values)
            if tuple(value.age for value in values) != expected_ages:
                raise ModelValidationError(name, "must cover the exact horizon in age order")
            object.__setattr__(self, name, values)
        if self.depletion_count > self.num_paths:
            raise ModelValidationError("first_depletion_counts", "must not exceed num_paths")
        if not isinstance(self.final_shortfall_percentiles, AmountPercentiles):
            raise ModelValidationError("final_shortfall_percentiles", "must be AmountPercentiles")
        if self.final_shortfall_percentiles.p10 < 0:
            raise ModelValidationError("final_shortfall_percentiles", "must not be negative")

    @property
    def num_paths(self) -> int:
        return self.metadata.num_paths

    @property
    def depletion_count(self) -> int:
        return sum(item.count for item in self.first_depletion_counts)

    @property
    def success_count(self) -> int:
        return self.num_paths - self.depletion_count

    @property
    def never_depleted_count(self) -> int:
        return self.success_count

    @property
    def success_probability(self) -> float:
        return self.success_count / self.num_paths

    @property
    def depletion_probability(self) -> float:
        return self.depletion_count / self.num_paths

    @property
    def final_balance_percentiles(self) -> AmountPercentiles:
        return self.asset_percentiles[-1].balance


def _inputs(profile: RetirementProfile, assumptions: SimulationAssumptions) -> None:
    if not isinstance(profile, RetirementProfile):
        raise ModelValidationError("profile", "must be a RetirementProfile model")
    if not isinstance(assumptions, SimulationAssumptions):
        raise ModelValidationError("assumptions", "must be a SimulationAssumptions model")


def _validated_paths(
    paths: object, *, num_paths: int, num_years: int,
) -> tuple[tuple[float, ...], ...]:
    if not isinstance(paths, (list, tuple)):
        raise ModelValidationError("annual_return_paths", "must be a list or tuple of paths")
    if len(paths) != num_paths:
        raise ModelValidationError("annual_return_paths", "must contain exactly num_paths paths")
    copied: list[tuple[float, ...]] = []
    for path_index, path in enumerate(paths):
        name = f"annual_return_paths[{path_index}]"
        if not isinstance(path, (list, tuple)) or len(path) != num_years:
            raise ModelValidationError(name, "must be a list or tuple with one rate per year")
        rates = tuple(_finite_number(f"{name}[{offset}]", rate)
                      for offset, rate in enumerate(path))
        if any(rate <= -1.0 for rate in rates):
            raise ModelValidationError(name, "each rate must be greater than -1")
        copied.append(rates)
    return tuple(copied)


def _linear_quantile(ordered: list[float], q: float) -> float:
    """Type 7 interpolation of a nonempty sorted finite sample, with q in [0, 1]."""
    position = (len(ordered) - 1) * q
    index = floor(position)
    weight = position - index
    lower, upper = ordered[index], ordered[min(index + 1, len(ordered) - 1)]
    if weight == 0.0 or lower == upper:
        return lower
    if lower < 0.0 < upper:
        # The difference could overflow; these weighted opposite signs cannot.
        return (1.0 - weight) * lower + weight * upper
    # Same-sign endpoints have a finite difference. Use the nearer endpoint to
    # avoid unnecessary cancellation; equal samples above stay bit-for-bit equal.
    difference = upper - lower
    return lower + weight * difference if weight <= 0.5 else (
        upper - (1.0 - weight) * difference
    )


def _percentiles(values: list[float], *, field_name: str, age: int) -> AmountPercentiles:
    if not values or any(not isfinite(value) for value in values):
        raise MonteCarloCalculationError("aggregation", field_name, age=age)
    ordered = sorted(values)
    amounts = tuple(_linear_quantile(ordered, q) for q in (0.1, 0.5, 0.9))
    if any(not isfinite(amount) for amount in amounts):
        raise MonteCarloCalculationError("aggregation", field_name, age=age)
    return AmountPercentiles(p10=amounts[0], p50=amounts[1], p90=amounts[2])


def _calculate_paths(
    profile: RetirementProfile,
    assumptions: SimulationAssumptions,
    paths: tuple[tuple[float, ...], ...],
    *,
    path_source: Literal["generated", "provided"],
) -> MonteCarloResult:
    # Generated paths are already validated by their generator. Guard the
    # denominator even if that internal contract regresses; never drop paths.
    if len(paths) != assumptions.num_paths:
        raise MonteCarloCalculationError("aggregation", "num_paths")
    ages = range(profile.current_age, profile.end_age + 1)
    balances: list[list[float]] = [[] for _ in ages]
    depletion_counts = [0] * len(ages)
    shortfalls: list[float] = []
    for path_index, rates in enumerate(paths):
        try:
            cashflow = calculate_cashflow_path(profile, assumptions, annual_returns=rates)
        except CashflowCalculationError as error:
            raise MonteCarloCalculationError(
                "cashflow", error.field_name, path_index=path_index, age=error.age,
            ) from None
        for offset, year in enumerate(cashflow.years):
            balances[offset].append(year.closing_balance)
        first_depletion_age = cashflow.first_depletion_age
        if first_depletion_age is not None:
            depletion_counts[first_depletion_age - profile.current_age] += 1
        shortfalls.append(cashflow.final_shortfall)

    return MonteCarloResult(
        metadata=MonteCarloMetadata(
            current_age=profile.current_age, end_age=profile.end_age,
            assumptions=assumptions, path_source=path_source,
        ),
        asset_percentiles=tuple(
            AssetPercentiles(age=age, balance=_percentiles(
                balances[offset], field_name="asset_percentiles", age=age,
            )) for offset, age in enumerate(ages)
        ),
        first_depletion_counts=tuple(
            FirstDepletionCount(age=age, count=depletion_counts[offset])
            for offset, age in enumerate(ages)
        ),
        final_shortfall_percentiles=_percentiles(
            shortfalls, field_name="final_shortfall_percentiles", age=profile.end_age,
        ),
    )


def calculate_monte_carlo(
    profile: RetirementProfile, assumptions: SimulationAssumptions,
) -> MonteCarloResult:
    """Generate N paths once, run the shared cashflow engine, and summarize all.

    Any numeric failure aborts the whole call. Mid-horizon depletion is never
    cancelled by recovery, and every path contributes to the amount percentiles.
    No individual cashflow ledgers, customer profile, or return paths are retained
    in the result. This is an internal calculation, not an HTTP or Agent endpoint.
    """
    _inputs(profile, assumptions)
    try:
        paths = generate_return_paths(profile, assumptions)
    except ReturnPathGenerationError as error:
        raise MonteCarloCalculationError(
            "return_generation", error.field_name, path_index=error.path_index, age=error.age,
        ) from None
    return _calculate_paths(profile, assumptions, paths, path_source="generated")


def calculate_monte_carlo_paths(
    profile: RetirementProfile,
    assumptions: SimulationAssumptions,
    *,
    annual_return_paths: list[list[float] | tuple[float, ...]]
    | tuple[list[float] | tuple[float, ...], ...],
) -> MonteCarloResult:
    """Summarize explicit paths, without generating or inferring their provenance.

    Validate and copy the whole batch before any cashflow calculation. There must
    be exactly assumptions.num_paths paths and one finite rate > -1 per age-year.
    Mean/volatility/seed stay recorded as supplied but do not affect these rates;
    inflation still affects cashflows. Metadata marks provided paths with no RNG.
    The caller must align horizons and path indices for later What-if comparisons.
    """
    _inputs(profile, assumptions)
    paths = _validated_paths(
        annual_return_paths, num_paths=assumptions.num_paths,
        num_years=profile.end_age - profile.current_age + 1,
    )
    return _calculate_paths(profile, assumptions, paths, path_source="provided")
