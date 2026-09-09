"""Executable synthetic fixed-assumption examples, never real customer data."""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from .cashflow import calculate_cashflow

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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Run synthetic fixed-assumption retirement cashflow examples (KRW).",
    )
    parser.add_argument(
        "--details", action="store_true", help="show the annual calculation ledger for each case",
    )
    options = parser.parse_args(argv)
    cases = build_sample_cases()
    print("Synthetic fixed-assumption cashflow examples (KRW). Not market forecasts.")
    for case in cases:
        result = calculate_cashflow(case.profile, case.assumptions)
        depletion = "none" if result.first_depletion_age is None else str(result.first_depletion_age)
        print(
            f"{case.name}: years={len(result.years)}, "
            f"final_balance={result.final_balance:,.2f}, "
            f"final_shortfall={result.final_shortfall:,.2f}, "
            f"first_depletion_age={depletion}"
        )
        if options.details:
            print("age | opening | rate | return | savings | pension | expenses | closing | shortfall")
            for year in result.years:
                print(
                    f"{year.age} | {year.opening_balance:,.2f} | {year.annual_return_rate:.6f} | "
                    f"{year.investment_return:,.2f} | {year.savings:,.2f} | "
                    f"{year.pension_income:,.2f} | {year.expenses:,.2f} | "
                    f"{year.closing_balance:,.2f} | {year.shortfall:,.2f}"
                )
    print(f"Calculated {len(cases)} synthetic cases. This CLI runs fixed cashflow only.")
    print("Monte Carlo samples: python -m retirement_simulation.monte_carlo_samples. "
          "What-if samples: python -m retirement_simulation.what_if_samples. Service integration is not implemented.")
    print("Negative balances are unmet funding gaps, not loan balances. Display rounding does not change depletion.")


if __name__ == "__main__":
    main()
