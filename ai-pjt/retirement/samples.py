"""Synthetic input examples only; running this file does not simulate results."""

from __future__ import annotations

from dataclasses import dataclass

from .models import (
    PensionIncome,
    RetirementExpensePeriod,
    RetirementProfile,
    SimulationAssumptions,
)


@dataclass(frozen=True, slots=True)
class SampleCase:
    name: str
    profile: RetirementProfile
    assumptions: SimulationAssumptions


def build_sample_cases() -> tuple[SampleCase, ...]:
    """Build fresh immutable samples without reading a DB, file, or environment."""
    assumptions = SimulationAssumptions(
        annual_return_mean=0.0,
        annual_return_volatility=0.0,
        annual_inflation_rate=0.0,
        num_paths=100,
        seed=42,
    )
    return (
        SampleCase(
            name="working_with_phased_expenses",
            profile=RetirementProfile(
                current_age=40,
                retirement_age=60,
                end_age=90,
                liquid_assets=100_000_000,
                monthly_savings=500_000,
                retirement_expenses=(
                    RetirementExpensePeriod(
                        from_age=60, to_age=74, monthly_amount=2_000_000
                    ),
                    RetirementExpensePeriod(
                        from_age=75, to_age=90, monthly_amount=1_700_000
                    ),
                ),
                pension_incomes=(
                    PensionIncome(start_age=65, end_age=90, monthly_amount=1_000_000),
                ),
            ),
            assumptions=assumptions,
        ),
        SampleCase(
            name="already_retired_without_pension",
            profile=RetirementProfile(
                current_age=68,
                retirement_age=65,
                end_age=90,
                liquid_assets=80_000_000,
                monthly_savings=0,
                retirement_expenses=(
                    RetirementExpensePeriod(
                        from_age=65, to_age=90, monthly_amount=1_600_000
                    ),
                ),
                pension_incomes=(),
            ),
            assumptions=assumptions,
        ),
        SampleCase(
            name="pension_start_gap",
            profile=RetirementProfile(
                current_age=59,
                retirement_age=60,
                end_age=90,
                liquid_assets=60_000_000,
                monthly_savings=500_000,
                retirement_expenses=(
                    RetirementExpensePeriod(
                        from_age=60, to_age=90, monthly_amount=2_000_000
                    ),
                ),
                pension_incomes=(
                    PensionIncome(start_age=65, end_age=79, monthly_amount=1_100_000),
                ),
            ),
            assumptions=assumptions,
        ),
    )


def main() -> None:
    cases = build_sample_cases()
    for case in cases:
        print(f"{case.name}: input validation OK")
    print(f"Validated {len(cases)} synthetic cases. This runner validates inputs only.")


if __name__ == "__main__":
    main()
