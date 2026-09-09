"""Standalone retirement engines and What-if inputs, without an Agent or HTTP API."""

from .cashflow import (
    CashflowCalculationError,
    CashflowResult,
    CashflowYear,
    calculate_cashflow,
    calculate_cashflow_path,
)

from .models import (
    AssetPoint,
    ModelValidationError,
    PensionIncome,
    RetirementExpensePeriod,
    RetirementProfile,
    SimulationAssumptions,
)

from .return_paths import ReturnPathGenerationError, generate_return_paths

from .input_adapter import (
    InputMappingError,
    InputMappingNotice,
    ProfileMappingResult,
    map_retirement_profile,
    map_simulation_assumptions,
)

from .monte_carlo import (
    AmountPercentiles,
    AssetPercentiles,
    FirstDepletionCount,
    MonteCarloCalculationError,
    MonteCarloMetadata,
    MonteCarloResult,
    calculate_monte_carlo,
    calculate_monte_carlo_paths,
)

from .what_if import WhatIfChanges, create_what_if_profile

from .what_if_engine import (
    MAX_WHAT_IF_SCENARIOS,
    AmountPercentileDelta,
    AssetPercentileDelta,
    FirstDepletionDelta,
    WhatIfCalculationError,
    WhatIfResult,
    WhatIfScenario,
    WhatIfScenarioResult,
    calculate_what_if,
    calculate_what_if_paths,
)

from .what_if_adapter import map_what_if_scenarios

__all__ = [
    "MAX_WHAT_IF_SCENARIOS",
    "AmountPercentileDelta",
    "AmountPercentiles",
    "AssetPercentileDelta",
    "AssetPercentiles",
    "AssetPoint",
    "CashflowCalculationError",
    "CashflowResult",
    "CashflowYear",
    "FirstDepletionCount",
    "FirstDepletionDelta",
    "InputMappingError",
    "InputMappingNotice",
    "ModelValidationError",
    "MonteCarloCalculationError",
    "MonteCarloMetadata",
    "MonteCarloResult",
    "PensionIncome",
    "ProfileMappingResult",
    "RetirementExpensePeriod",
    "RetirementProfile",
    "ReturnPathGenerationError",
    "SimulationAssumptions",
    "WhatIfCalculationError",
    "WhatIfChanges",
    "WhatIfResult",
    "WhatIfScenario",
    "WhatIfScenarioResult",
    "calculate_cashflow",
    "calculate_cashflow_path",
    "calculate_monte_carlo",
    "calculate_monte_carlo_paths",
    "calculate_what_if",
    "calculate_what_if_paths",
    "create_what_if_profile",
    "generate_return_paths",
    "map_retirement_profile",
    "map_simulation_assumptions",
    "map_what_if_scenarios",
]
