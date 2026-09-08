"""Standalone retirement cashflow and Monte Carlo, without an Agent or HTTP API."""

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
    "calculate_cashflow",
    "calculate_cashflow_path",
    "calculate_monte_carlo",
    "calculate_monte_carlo_paths",
    "generate_return_paths",
]
