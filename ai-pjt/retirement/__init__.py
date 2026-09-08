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

__all__ = [
    "AmountPercentiles",
    "AssetPercentiles",
    "AssetPoint",
    "CashflowCalculationError",
    "CashflowResult",
    "CashflowYear",
    "FirstDepletionCount",
    "ModelValidationError",
    "MonteCarloCalculationError",
    "MonteCarloMetadata",
    "MonteCarloResult",
    "PensionIncome",
    "RetirementExpensePeriod",
    "RetirementProfile",
    "ReturnPathGenerationError",
    "SimulationAssumptions",
    "WhatIfChanges",
    "calculate_cashflow",
    "calculate_cashflow_path",
    "calculate_monte_carlo",
    "calculate_monte_carlo_paths",
    "create_what_if_profile",
    "generate_return_paths",
]
