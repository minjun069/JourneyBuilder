"""Compare immutable scenarios on one common market batch; no external I/O.

Cashflow and Monte Carlo arithmetic remain in their existing engines. Deltas
are differences of marginal summaries, not percentiles of paired path changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from re import fullmatch
from typing import TypeVar

from .models import (
    MAX_AGE, MAX_PATHS, ModelValidationError, RetirementProfile,
    SimulationAssumptions, _finite_number, _integer,
)
from .monte_carlo import (
    AmountPercentiles, MonteCarloCalculationError, MonteCarloMetadata,
    MonteCarloResult, _validated_paths, calculate_monte_carlo_paths,
)
from .return_paths import ReturnPathGenerationError, generate_return_paths
from .what_if import WhatIfChanges, create_what_if_profile


MAX_WHAT_IF_SCENARIOS = 10  # Provisional batch guard, not a performance guarantee.


class WhatIfCalculationError(ArithmeticError):
    """Whole-comparison numeric failure, with location but no customer values."""

    def __init__(
        self, stage: str, field_name: str, *, scope: str = "scenario",
        scenario_index: int | None = None, path_index: int | None = None,
        age: int | None = None,
    ) -> None:
        self.stage = stage
        self.field_name = field_name
        self.scope = scope
        self.scenario_index = scenario_index
        self.path_index = path_index
        self.age = age
        location = "" if scenario_index is None else f" at scenario {scenario_index}"
        if path_index is not None:
            location += f" at path {path_index}"
        if age is not None:
            location += f" at age {age}"
        super().__init__(f"{scope}.{stage}.{field_name}: calculation failed{location}")


def _scenario_id(value: object) -> None:
    if (not isinstance(value, str)
            or fullmatch(r"[a-z][a-z0-9_-]{0,63}", value) is None
            or value == "baseline"):
        raise ModelValidationError(
            "scenario_id", "must be a 1-64 character ASCII identifier other than baseline",
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class WhatIfScenario:
    """A unique technical identifier and absolute changes; not a free-text label."""

    scenario_id: str = field(repr=False)
    changes: WhatIfChanges = field(repr=False)

    def __post_init__(self) -> None:
        _scenario_id(self.scenario_id)
        if not isinstance(self.changes, WhatIfChanges):
            raise ModelValidationError("changes", "must be a WhatIfChanges model")


@dataclass(frozen=True, slots=True, kw_only=True)
class AmountPercentileDelta:
    """Finite KRW differences of P10/P50/P90; ordering is deliberately NOT required."""

    p10: float = field(repr=False)
    p50: float = field(repr=False)
    p90: float = field(repr=False)

    def __post_init__(self) -> None:
        for name in ("p10", "p50", "p90"):
            object.__setattr__(self, name, _finite_number(name, getattr(self, name)))


@dataclass(frozen=True, slots=True, kw_only=True)
class AssetPercentileDelta:
    age: int
    balance: AmountPercentileDelta = field(repr=False)

    def __post_init__(self) -> None:
        _integer("age", self.age, minimum=18, maximum=MAX_AGE)
        if not isinstance(self.balance, AmountPercentileDelta):
            raise ModelValidationError("balance", "must be AmountPercentileDelta")


@dataclass(frozen=True, slots=True, kw_only=True)
class FirstDepletionDelta:
    age: int
    count: int

    def __post_init__(self) -> None:
        _integer("age", self.age, minimum=18, maximum=MAX_AGE)
        _integer("count", self.count, minimum=-MAX_PATHS, maximum=MAX_PATHS)


def _matching_result(name: str, value: object, market: MonteCarloMetadata) -> None:
    if not isinstance(value, MonteCarloResult):
        raise ModelValidationError(name, "must be a MonteCarloResult")
    if value.metadata.path_source != "provided":
        raise ModelValidationError(name, "must describe the supplied common paths")
    for attribute in (
        "current_age", "end_age", "assumptions", "model_version",
        "python_version", "python_implementation",
    ):
        if getattr(value.metadata, attribute) != getattr(market, attribute):
            raise ModelValidationError(name, "must match the common market metadata")


def _amount_delta(
    baseline: AmountPercentiles, changed: AmountPercentiles, *, field_name: str, age: int,
) -> AmountPercentileDelta:
    differences = {}
    for name in ("p10", "p50", "p90"):
        difference = getattr(changed, name) - getattr(baseline, name)
        if not isfinite(difference):
            raise WhatIfCalculationError("comparison", f"{field_name}.{name}", age=age)
        differences[name] = difference
    return AmountPercentileDelta(**differences)


@dataclass(frozen=True, slots=True, kw_only=True)
class WhatIfScenarioResult:
    """Summary pair with eager validated amount deltas and derived count/pp deltas.

All pairs share the same immutable baseline object in an engine-produced batch.
Manual construction checks structural consistency, not actual path provenance.
"""

    scenario_id: str = field(repr=False)
    baseline: MonteCarloResult = field(repr=False)
    result: MonteCarloResult = field(repr=False)
    asset_deltas: tuple[AssetPercentileDelta, ...] = field(init=False, repr=False)
    final_shortfall_delta: AmountPercentileDelta = field(init=False, repr=False)
    first_depletion_deltas: tuple[FirstDepletionDelta, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        _scenario_id(self.scenario_id)
        if not isinstance(self.baseline, MonteCarloResult):
            raise ModelValidationError("baseline", "must be a MonteCarloResult")
        _matching_result("baseline", self.baseline, self.baseline.metadata)
        _matching_result("result", self.result, self.baseline.metadata)
        object.__setattr__(self, "asset_deltas", tuple(
            AssetPercentileDelta(age=base.age, balance=_amount_delta(
                base.balance, changed.balance, field_name="asset_deltas", age=base.age,
            )) for base, changed in zip(self.baseline.asset_percentiles, self.result.asset_percentiles)
        ))
        object.__setattr__(self, "final_shortfall_delta", _amount_delta(
            self.baseline.final_shortfall_percentiles, self.result.final_shortfall_percentiles,
            field_name="final_shortfall_delta", age=self.baseline.metadata.end_age,
        ))
        object.__setattr__(self, "first_depletion_deltas", tuple(
            FirstDepletionDelta(age=base.age, count=changed.count - base.count)
            for base, changed in zip(self.baseline.first_depletion_counts,
                                     self.result.first_depletion_counts)
        ))

    @property
    def final_balance_delta(self) -> AmountPercentileDelta:
        return self.asset_deltas[-1].balance

    @property
    def success_count_delta(self) -> int:
        return self.result.success_count - self.baseline.success_count

    @property
    def depletion_count_delta(self) -> int:
        return self.result.depletion_count - self.baseline.depletion_count

    @property
    def never_depleted_count_delta(self) -> int:
        return self.success_count_delta

    @property
    def success_probability_delta_pp(self) -> float:
        return (self.result.success_probability - self.baseline.success_probability) * 100.0

    @property
    def depletion_probability_delta_pp(self) -> float:
        return (self.result.depletion_probability - self.baseline.depletion_probability) * 100.0


@dataclass(frozen=True, slots=True, kw_only=True)
class WhatIfResult:
    """Common-market provenance and summaries; no customer profiles or paths retained.

metadata.model_version identifies Monte Carlo; model_version identifies What-if.
Child Monte Carlo results correctly remain provided even for generated markets.
"""

    metadata: MonteCarloMetadata
    baseline: MonteCarloResult = field(repr=False)
    scenarios: tuple[WhatIfScenarioResult, ...] = field(repr=False)
    model_version: str = field(default="retirement-what-if-v1", init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.metadata, MonteCarloMetadata):
            raise ModelValidationError("metadata", "must be MonteCarloMetadata")
        _matching_result("baseline", self.baseline, self.metadata)
        scenarios = _scenario_items(self.scenarios, WhatIfScenarioResult)
        for index, item in enumerate(scenarios):
            if item.baseline != self.baseline:
                raise ModelValidationError(
                    f"scenarios[{index}].baseline", "must equal the common baseline",
                )
        object.__setattr__(self, "scenarios", scenarios)

    @property
    def num_paths(self) -> int:
        return self.metadata.num_paths


_ScenarioItem = TypeVar("_ScenarioItem", WhatIfScenario, WhatIfScenarioResult)


def _scenario_items(values: object, item_type: type[_ScenarioItem]) -> tuple[_ScenarioItem, ...]:
    if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= MAX_WHAT_IF_SCENARIOS:
        raise ModelValidationError("scenarios", "must be a list or tuple with 1-10 scenarios")
    items = tuple(values)
    seen = set()
    for index, item in enumerate(items):
        if not isinstance(item, item_type):
            raise ModelValidationError(f"scenarios[{index}]", "has an unsupported scenario type")
        if item.scenario_id in seen:
            raise ModelValidationError(f"scenarios[{index}].scenario_id", "must be unique")
        seen.add(item.scenario_id)
    return items


def _prepare(
    profile: RetirementProfile, assumptions: SimulationAssumptions, scenarios: object,
) -> tuple[tuple[WhatIfScenario, ...], tuple[RetirementProfile, ...]]:
    if not isinstance(profile, RetirementProfile):
        raise ModelValidationError("profile", "must be a RetirementProfile model")
    if not isinstance(assumptions, SimulationAssumptions):
        raise ModelValidationError("assumptions", "must be a SimulationAssumptions model")
    items = _scenario_items(scenarios, WhatIfScenario)
    profiles = []
    for index, item in enumerate(items):
        try:
            profiles.append(create_what_if_profile(profile, item.changes))
        except ModelValidationError as error:
            raise ModelValidationError(
                f"scenarios[{index}].changes.{error.field_name}", error.message,
            ) from None
    return items, tuple(profiles)


def _simulate(
    profile: RetirementProfile, assumptions: SimulationAssumptions,
    paths: tuple[tuple[float, ...], ...], *, scenario_index: int | None = None,
) -> MonteCarloResult:
    try:
        return calculate_monte_carlo_paths(profile, assumptions, annual_return_paths=paths)
    except MonteCarloCalculationError as error:
        raise WhatIfCalculationError(
            error.stage, error.field_name,
            scope="baseline" if scenario_index is None else "scenario",
            scenario_index=scenario_index, path_index=error.path_index, age=error.age,
        ) from None


def _compare(
    profile: RetirementProfile, assumptions: SimulationAssumptions,
    items: tuple[WhatIfScenario, ...], profiles: tuple[RetirementProfile, ...],
    paths: tuple[tuple[float, ...], ...], *, metadata: MonteCarloMetadata,
) -> WhatIfResult:
    baseline = _simulate(profile, assumptions, paths)
    comparisons = []
    for index, (item, changed) in enumerate(zip(items, profiles)):
        result = _simulate(changed, assumptions, paths, scenario_index=index)
        try:
            comparisons.append(WhatIfScenarioResult(
                scenario_id=item.scenario_id, baseline=baseline, result=result,
            ))
        except WhatIfCalculationError as error:
            raise WhatIfCalculationError(
                error.stage, error.field_name, scope="scenario", scenario_index=index,
                path_index=error.path_index, age=error.age,
            ) from None
    return WhatIfResult(metadata=metadata, baseline=baseline, scenarios=tuple(comparisons))


def calculate_what_if(
    profile: RetirementProfile, assumptions: SimulationAssumptions,
    scenarios: list[WhatIfScenario] | tuple[WhatIfScenario, ...],
) -> WhatIfResult:
    """Validate all scenarios, generate one shared market, then compare all or fail."""
    items, profiles = _prepare(profile, assumptions, scenarios)
    try:
        paths = generate_return_paths(profile, assumptions)
    except ReturnPathGenerationError as error:
        raise WhatIfCalculationError(
            "return_generation", error.field_name, scope="market",
            path_index=error.path_index, age=error.age,
        ) from None
    return _compare(profile, assumptions, items, profiles, paths, metadata=MonteCarloMetadata(
        current_age=profile.current_age, end_age=profile.end_age,
        assumptions=assumptions, path_source="generated",
    ))


def calculate_what_if_paths(
    profile: RetirementProfile, assumptions: SimulationAssumptions,
    scenarios: list[WhatIfScenario] | tuple[WhatIfScenario, ...], *,
    annual_return_paths: list[list[float] | tuple[float, ...]]
    | tuple[list[float] | tuple[float, ...], ...],
) -> WhatIfResult:
    """Validate/copy the entire supplied batch before any calculation; never use RNG."""
    items, profiles = _prepare(profile, assumptions, scenarios)
    paths = _validated_paths(
        annual_return_paths, num_paths=assumptions.num_paths,
        num_years=profile.end_age - profile.current_age + 1,
    )
    return _compare(profile, assumptions, items, profiles, paths, metadata=MonteCarloMetadata(
        current_age=profile.current_age, end_age=profile.end_age,
        assumptions=assumptions, path_source="provided",
    ))
