"""One pure saved-input-to-analysis call; no JSON, services, or new arithmetic."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .analysis_errors import RetirementAnalysisError, _calculation_path
from .input_adapter import (
    InputMappingError, ProfileMappingResult, map_retirement_profile, map_simulation_assumptions,
)
from .models import ModelValidationError, RetirementProfile
from .monte_carlo import MonteCarloCalculationError, MonteCarloResult, calculate_monte_carlo
from .what_if import create_what_if_profile
from .what_if_adapter import map_what_if_scenarios
from .what_if_engine import (
    MAX_WHAT_IF_SCENARIOS, WhatIfCalculationError, WhatIfResult, WhatIfScenario,
    _matching_result, calculate_what_if,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class RetirementAnalysisResult:
    """Detached inputs/notices, independent profiles, and one complete result.

    Construction checks structure, profile application, and shared metadata but
    never repeats a financial calculation. It does not prove actual execution,
    ownership, completeness, or that manually supplied summaries are genuine.
    """

    mapped_input: ProfileMappingResult = field(repr=False)
    scenario_definitions: tuple[WhatIfScenario, ...] = field(repr=False)
    scenario_profiles: tuple[RetirementProfile, ...] = field(repr=False)
    calculation: MonteCarloResult | WhatIfResult = field(repr=False)
    contract_version: str = field(default="retirement-analysis-v1", init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.mapped_input, ProfileMappingResult):
            raise ModelValidationError("mapped_input", "must be a ProfileMappingResult")
        for name, kind in (("scenario_definitions", WhatIfScenario),
                           ("scenario_profiles", RetirementProfile)):
            values = getattr(self, name)
            if not isinstance(values, (list, tuple)) or len(values) > MAX_WHAT_IF_SCENARIOS:
                raise ModelValidationError(name, "must be a list or tuple with 0-10 models")
            if any(not isinstance(item, kind) for item in values):
                raise ModelValidationError(name, "contains an unsupported model")
            object.__setattr__(self, name, tuple(values))
        if len(self.scenario_profiles) != len(self.scenario_definitions):
            raise ModelValidationError("scenario_profiles", "must match the definitions count")
        ids = tuple(item.scenario_id for item in self.scenario_definitions)
        if len(set(ids)) != len(ids):
            raise ModelValidationError("scenario_definitions", "must contain unique identifiers")

        expected_type = WhatIfResult if ids else MonteCarloResult
        if not isinstance(self.calculation, expected_type):
            raise ModelValidationError("calculation", "must match the mode determined by definitions")
        baseline, market = self.mapped_input.profile, self.calculation.metadata
        if (market.current_age != baseline.current_age or market.end_age != baseline.end_age
                or market.path_source != "generated" or market.model_version != "retirement-mc-v1"):
            raise ModelValidationError("calculation.metadata", "must match the generated baseline horizon")

        for index, (definition, applied) in enumerate(zip(self.scenario_definitions, self.scenario_profiles)):
            path = f"scenario_profiles[{index}]"
            try:
                expected = create_what_if_profile(baseline, definition.changes)
            except ModelValidationError:
                raise ModelValidationError(path, "must be a valid independent application") from None
            if applied != expected:
                raise ModelValidationError(path, "must equal the independent application to the baseline")

        if isinstance(self.calculation, WhatIfResult):
            comparison = self.calculation
            if (comparison.model_version != "retirement-what-if-v1"
                    or tuple(item.scenario_id for item in comparison.scenarios) != ids):
                raise ModelValidationError("calculation.scenarios", "must match the definition IDs and order")
            _matching_result("calculation.baseline", comparison.baseline, market)
            for index, item in enumerate(comparison.scenarios):
                path = f"calculation.scenarios[{index}]"
                _matching_result(path, item.result, market)
                if item.baseline != comparison.baseline:
                    raise ModelValidationError(path, "must use the same baseline summary")

    @property
    def mode(self) -> Literal["baseline", "what_if"]:
        return "what_if" if self.scenario_definitions else "baseline"


def analyze_retirement(
    saved_data: object, assumption_values: object, *,
    scenarios: object = (), confirmed_empty_sections: object = (),
) -> RetirementAnalysisResult:
    """Map, validate every profile, then invoke exactly one generated-path engine.

    Empty scenarios select baseline MC; 1-10 select a common-market comparison.
    Known mapping/application/numeric errors become RetirementAnalysisError with
    safe locations. Unexpected bugs, memory exhaustion, and interrupts propagate.
    No partial result, fallback assumptions, source writes, or I/O is performed.
    """
    try:
        mapped = map_retirement_profile(saved_data, confirmed_empty_sections=confirmed_empty_sections)
    except InputMappingError as error:
        raise RetirementAnalysisError(
            "input_mapping_failed", error.field_name, reason_code=error.code,
        ) from None
    try:
        assumptions = map_simulation_assumptions(assumption_values)
    except InputMappingError as error:
        raise RetirementAnalysisError(
            "assumption_mapping_failed", error.field_name, reason_code=error.code,
        ) from None
    try:
        definitions = map_what_if_scenarios(scenarios)
    except InputMappingError as error:
        raise RetirementAnalysisError(
            "scenario_mapping_failed", error.field_name, reason_code=error.code,
        ) from None

    profiles = []
    for index, definition in enumerate(definitions):
        try:
            profiles.append(create_what_if_profile(mapped.profile, definition.changes))
        except ModelValidationError as error:
            path = f"scenarios[{index}].changes"
            if error.field_name in ("retirement_age", "end_age"):
                path += ".retirement_age"
            elif error.field_name in ("monthly_savings", "retirement_expenses"):
                path += f".{error.field_name}"
            raise RetirementAnalysisError(
                "scenario_application_failed", path, reason_code="invalid_profile",
            ) from None
    try:
        calculation = (calculate_what_if(mapped.profile, assumptions, definitions)
                       if definitions else calculate_monte_carlo(mapped.profile, assumptions))
    except (MonteCarloCalculationError, WhatIfCalculationError) as error:
        raise RetirementAnalysisError("calculation_failed", _calculation_path(error)) from None

    # Detect a broken engine contract without silently packaging a different
    # request's assumptions. There is deliberately no second source in the model.
    result = RetirementAnalysisResult(
        mapped_input=mapped, scenario_definitions=definitions,
        scenario_profiles=tuple(profiles), calculation=calculation,
    )
    if calculation.metadata.assumptions != assumptions:
        raise ModelValidationError("calculation.metadata.assumptions", "must match the requested assumptions")
    return result
