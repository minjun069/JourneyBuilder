"""Immutable annual return paths; no cashflow, aggregation, or external I/O.

Simple-return moments are converted to lognormal gross-return parameters.
See docs/monte-carlo-rules.md for the model assumptions and draw order.
"""

from __future__ import annotations

from math import exp, expm1, isfinite, log, log1p, sqrt
from random import Random

from .models import ModelValidationError, RetirementProfile, SimulationAssumptions


class ReturnPathGenerationError(ArithmeticError):
    """Unsupported numeric range, without supplied values or partial paths."""

    def __init__(
        self,
        field_name: str,
        *,
        path_index: int | None = None,
        age: int | None = None,
    ) -> None:
        self.field_name = field_name
        self.path_index = path_index
        self.age = age
        location = "" if path_index is None else f" at path {path_index}"
        if age is not None:
            location += f" at age {age}"
        super().__init__(f"{field_name}: calculation is outside the numeric range{location}")


def _lognormal_parameters(mean: float, volatility: float) -> tuple[float, float]:
    """Return log mean/stddev for validated simple moments with volatility > 0."""
    gross_mean = 1.0 + mean
    log_gross_mean = log1p(mean)
    if volatility <= gross_mean:
        ratio = volatility / gross_mean
        squared_ratio = ratio * ratio
        log_variance = log1p(squared_ratio)
        # Scaling preserves a representable stddev when its square underflows.
        # log1p(x) / x tends to 1; if the square rounds to zero, use that limit.
        log_stddev = ratio if squared_ratio == 0.0 else (
            ratio * sqrt(log_variance / squared_ratio)
        )
    else:
        # log(1 + ratio**2) without overflowing either ratio or its square.
        log_ratio = log(volatility) - log_gross_mean
        log_variance = 2.0 * log_ratio + log1p(exp(-2.0 * log_ratio))
        log_stddev = sqrt(log_variance)
    log_mean = log_gross_mean - log_variance / 2.0
    if not isfinite(log_mean):
        raise ReturnPathGenerationError("log_return_mean")
    if not isfinite(log_stddev) or log_stddev <= 0.0:
        raise ReturnPathGenerationError("log_return_volatility")
    return log_mean, log_stddev


def _sample_return(
    rng: Random,
    log_mean: float,
    log_stddev: float,
    *,
    path_index: int,
    age: int,
) -> float:
    shock = rng.gauss(0.0, 1.0)
    if not isfinite(shock):
        raise ReturnPathGenerationError("standard_normal", path_index=path_index, age=age)
    log_return = log_mean + log_stddev * shock
    if not isfinite(log_return):
        raise ReturnPathGenerationError("log_return", path_index=path_index, age=age)
    try:
        rate = expm1(log_return)
    except OverflowError:
        raise ReturnPathGenerationError(
            "annual_returns", path_index=path_index, age=age,
        ) from None
    # expm1 can round very small positive gross returns to a simple return of -1.
    if not isfinite(rate) or rate <= -1.0:
        raise ReturnPathGenerationError("annual_returns", path_index=path_index, age=age)
    return rate


def generate_return_paths(
    profile: RetirementProfile,
    assumptions: SimulationAssumptions,
) -> tuple[tuple[float, ...], ...]:
    """Generate all N paths, each covering current_age..end_age inclusively.

Only the profile's horizon and the assumptions' mean, volatility, count, and
seed affect paths. Inflation and customer finances do not affect RNG draws.
One local Random instance advances in path-major, then age-major order.
Zero volatility repeats the exact supplied mean without RNG or log conversion.

The returned nested tuple is immutable and reusable by calculate_cashflow_path.
An invalid sample aborts the entire call: no clipping, resampling, or partial
batch is returned. This eager API retains O(N*T) rates, not cashflow details.
It does not calculate success probabilities or connect to the service.
"""
    if not isinstance(profile, RetirementProfile):
        raise ModelValidationError("profile", "must be a RetirementProfile model")
    if not isinstance(assumptions, SimulationAssumptions):
        raise ModelValidationError("assumptions", "must be a SimulationAssumptions model")

    num_years = profile.end_age - profile.current_age + 1
    if assumptions.annual_return_volatility == 0.0:
        fixed_path = (assumptions.annual_return_mean,) * num_years
        return (fixed_path,) * assumptions.num_paths

    log_mean, log_stddev = _lognormal_parameters(
        assumptions.annual_return_mean, assumptions.annual_return_volatility,
    )
    rng = Random(assumptions.seed)
    return tuple(
        tuple(
            _sample_return(rng, log_mean, log_stddev, path_index=path_index, age=age)
            for age in range(profile.current_age, profile.end_age + 1)
        )
        for path_index in range(assumptions.num_paths)
    )
