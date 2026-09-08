"""Built-in synthetic What-if demonstrations, not a customer or service interface."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import sys

from .models import (
    MAX_PATHS, ModelValidationError, RetirementExpensePeriod, RetirementProfile,
    SimulationAssumptions,
)
from .monte_carlo import AmountPercentiles, MonteCarloResult
from .monte_carlo_samples import build_monte_carlo_cases
from .samples import build_sample_cases
from .what_if import WhatIfChanges, create_what_if_profile
from .what_if_engine import (
    AmountPercentileDelta, WhatIfCalculationError, WhatIfResult, WhatIfScenario,
    WhatIfScenarioResult, calculate_what_if,
)


@dataclass(frozen=True, slots=True)
class WhatIfSampleCase:
    """Internal fixture container; not an externally validated request model."""

    name: str
    profile: RetirementProfile
    assumptions: SimulationAssumptions
    scenarios: tuple[WhatIfScenario, ...]


def build_what_if_cases(*, num_paths: int = 1_000, seed: int = 42) -> tuple[WhatIfSampleCase, ...]:
    """Build fresh demo inputs with explicit overrides, never recommendations.

    Reuse the three original profiles and the Monte Carlo demo-only assumptions
    (mean 0.05, volatility 0.10, inflation 0.02). No market defaults are added to
    the calculation API. Early retirement includes a complete expense schedule.
    """
    working, retired, gap = build_monte_carlo_cases(num_paths=num_paths, seed=seed)
    return (
        WhatIfSampleCase(working.name, working.profile, working.assumptions, (
            WhatIfScenario(scenario_id="unchanged", changes=WhatIfChanges()),
            WhatIfScenario(scenario_id="save_more", changes=WhatIfChanges(monthly_savings=750_000)),
            WhatIfScenario(scenario_id="retire_earlier", changes=WhatIfChanges(
                retirement_age=58, retirement_expenses=(
                    RetirementExpensePeriod(from_age=58, to_age=74, monthly_amount=2_000_000),
                    RetirementExpensePeriod(from_age=75, to_age=90, monthly_amount=1_700_000),
                ),
            )),
            WhatIfScenario(scenario_id="retire_later", changes=WhatIfChanges(retirement_age=62)),
            WhatIfScenario(scenario_id="lower_expenses", changes=WhatIfChanges(retirement_expenses=(
                RetirementExpensePeriod(from_age=60, to_age=74, monthly_amount=1_800_000),
                RetirementExpensePeriod(from_age=75, to_age=90, monthly_amount=1_500_000),
            ))),
        )),
        WhatIfSampleCase(retired.name, retired.profile, retired.assumptions, (
            WhatIfScenario(scenario_id="unchanged", changes=WhatIfChanges()),
            WhatIfScenario(scenario_id="lower_expenses", changes=WhatIfChanges(retirement_expenses=(
                RetirementExpensePeriod(from_age=65, to_age=90, monthly_amount=1_200_000),
            ))),
            WhatIfScenario(scenario_id="savings_no_effect", changes=WhatIfChanges(monthly_savings=500_000)),
        )),
        WhatIfSampleCase(gap.name, gap.profile, gap.assumptions, (
            WhatIfScenario(scenario_id="unchanged", changes=WhatIfChanges()),
            WhatIfScenario(scenario_id="stop_saving", changes=WhatIfChanges(monthly_savings=0)),
            WhatIfScenario(scenario_id="retire_later", changes=WhatIfChanges(retirement_age=65)),
        )),
    )


def _path_count(raw: str) -> int:
    message = f"must be an integer from 1 to {MAX_PATHS}"
    try:
        number = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(message) from None
    if not 1 <= number <= MAX_PATHS:
        raise argparse.ArgumentTypeError(message)
    return number


def _seed(raw: str) -> int:
    message = "must be a non-negative integer"
    try:
        number = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(message) from None
    if number < 0:
        raise argparse.ArgumentTypeError(message)
    return number


def _expenses(periods: tuple[RetirementExpensePeriod, ...]) -> str:
    return ", ".join(f"{row.from_age}..{row.to_age}:{row.monthly_amount:,}" for row in periods)


def _print_profile(profile: RetirementProfile) -> None:
    print(f"input: current_age={profile.current_age}, retirement_age={profile.retirement_age}, "
          f"end_age={profile.end_age}, liquid_assets={profile.liquid_assets:,} KRW, "
          f"monthly_savings={profile.monthly_savings:,} nominal KRW/month")
    print(f"retirement_expenses (inclusive ages:current-age KRW/month): {_expenses(profile.retirement_expenses)}")
    pension = ", ".join(f"{row.start_age}..{row.end_age}:{row.monthly_amount:,}" for row in profile.pension_incomes)
    print(f"pension_incomes (inclusive ages:nominal KRW/month): {pension or 'none'}")


def _print_changes(changes: WhatIfChanges) -> None:
    parts = []
    if changes.monthly_savings is not None:
        parts.append(f"monthly_savings={changes.monthly_savings:,} nominal KRW/month")
    if changes.retirement_age is not None:
        parts.append(f"retirement_age={changes.retirement_age}")
    if changes.retirement_expenses is not None:
        parts.append(f"retirement_expenses=[{_expenses(changes.retirement_expenses)}] current-age KRW/month (full replacement)")
    print(f"overrides (absolute; omitted fields unchanged): {'; '.join(parts) or 'none (unchanged)'}")


def _amounts(values: AmountPercentiles | AmountPercentileDelta, *, signed: bool = False) -> str:
    spec = "+,.2f" if signed else ",.2f"
    return ", ".join(f"{name}={format(getattr(values, name), spec)}" for name in ("p10", "p50", "p90"))


def _print_summary(result: MonteCarloResult, *, details: bool) -> None:
    print(f"result_source: path_source={result.metadata.path_source}, "
          f"effective_seed={result.metadata.seed}, rng_algorithm={result.metadata.rng_algorithm}")
    print(f"success_probability={result.success_probability:.6f} ({result.success_probability:.3%}), "
          f"success_count={result.success_count}/{result.num_paths}")
    print(f"depletion_probability={result.depletion_probability:.6f} ({result.depletion_probability:.3%}), "
          f"depletion_count={result.depletion_count}/{result.num_paths}, "
          f"never_depleted_count={result.never_depleted_count}")
    print(f"final_balance (nominal KRW): {_amounts(result.final_balance_percentiles)}")
    print(f"final_shortfall (nominal KRW): {_amounts(result.final_shortfall_percentiles)}")
    histogram = ", ".join(f"{row.age}:{row.count}" for row in result.first_depletion_counts if row.count)
    print(f"first_depletion_counts (age:count, nonzero only): {histogram or 'none'}")
    if details:
        print("age | balance_p10 | balance_p50 | balance_p90 | first_depletion_count")
        for row, count in zip(result.asset_percentiles, result.first_depletion_counts):
            print(f"{row.age} | {row.balance.p10:,.2f} | {row.balance.p50:,.2f} | "
                  f"{row.balance.p90:,.2f} | {count.count}")


def _print_delta(item: WhatIfScenarioResult, *, details: bool) -> None:
    print(f"success_probability_delta_pp={item.success_probability_delta_pp:+.3f}pp, "
          f"depletion_probability_delta_pp={item.depletion_probability_delta_pp:+.3f}pp")
    print(f"success_count_delta={item.success_count_delta:+d}, depletion_count_delta={item.depletion_count_delta:+d}, "
          f"never_depleted_count_delta={item.never_depleted_count_delta:+d}")
    print(f"final_balance_delta (nominal KRW): {_amounts(item.final_balance_delta, signed=True)}")
    print(f"final_shortfall_delta (nominal KRW): {_amounts(item.final_shortfall_delta, signed=True)}")
    histogram = ", ".join(f"{row.age}:{row.count:+d}" for row in item.first_depletion_deltas if row.count)
    print(f"first_depletion_deltas (age:signed count, nonzero only): {histogram or 'none'}")
    if details:
        print("age | balance_delta_p10 | balance_delta_p50 | balance_delta_p90 | first_depletion_count_delta")
        for row, count in zip(item.asset_deltas, item.first_depletion_deltas):
            print(f"{row.age} | {row.balance.p10:+,.2f} | {row.balance.p50:+,.2f} | "
                  f"{row.balance.p90:+,.2f} | {count.count:+d}")


def _print_case(case: WhatIfSampleCase, profiles: tuple[RetirementProfile, ...],
                result: WhatIfResult, *, details: bool) -> None:
    meta, config = result.metadata, result.metadata.assumptions
    print(f"\n[{case.name}]")
    print(f"common_market: years={meta.end_age - meta.current_age + 1}, scenarios={len(result.scenarios)}, "
          f"num_paths={result.num_paths}, configured_seed={config.seed}, effective_seed={meta.seed}, "
          f"path_source={meta.path_source}")
    print(f"assumptions (annual decimal fractions): annual_return_mean={config.annual_return_mean}, "
          f"annual_return_volatility={config.annual_return_volatility}, annual_inflation_rate={config.annual_inflation_rate}")
    print(f"execution: what_if_model_version={result.model_version}, mc_model_version={meta.model_version}, "
          f"return_model={meta.return_model}, rng_algorithm={meta.rng_algorithm}, "
          f"python={meta.python_implementation} {meta.python_version}")
    print("baseline:")
    _print_profile(case.profile)
    _print_summary(result.baseline, details=details)
    for definition, profile, item in zip(case.scenarios, profiles, result.scenarios):
        print(f"scenario {item.scenario_id}:")
        _print_changes(definition.changes)
        _print_profile(profile)
        _print_summary(item.result, details=details)
        _print_delta(item, details=details)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m retirement.what_if_samples",
        description="Compare built-in synthetic retirement What-if scenarios using common market paths (KRW/pp).",
        epilog=("Demo assumptions: mean=0.05, volatility=0.10, inflation=0.02; not market forecasts. "
                "No customer files, database or service connection. "
                "The path-count cap is not a memory or response-time guarantee."),
        allow_abbrev=False,
    )
    parser.add_argument("--case", choices=tuple(case.name for case in build_sample_cases()),
                        help="run only this built-in case with all its scenarios (default: all three)")
    parser.add_argument("--num-paths", type=_path_count, default=1_000,
                        help=f"common paths per case, 1..{MAX_PATHS} (default: 1000; large runs cost more time/memory)")
    parser.add_argument("--seed", type=_seed, default=42,
                        help="local RNG seed, non-negative integer (default: 42)")
    parser.add_argument("--details", action="store_true",
                        help="show every age's baseline/scenario balance summaries and signed deltas, including zeros")
    options = parser.parse_args(argv)
    try:
        cases = build_what_if_cases(num_paths=options.num_paths, seed=options.seed)
        selected = tuple(case for case in cases if options.case is None or case.name == options.case)
        # Prepare display-only profiles with the same builder used by the engine.
        # Never print a partial success if a later selected case cannot be calculated.
        results = []
        for case in selected:
            result = calculate_what_if(case.profile, case.assumptions, case.scenarios)
            profiles = tuple(create_what_if_profile(case.profile, item.changes) for item in case.scenarios)
            results.append((case, profiles, result))
    except (ModelValidationError, WhatIfCalculationError) as error:
        print(f"What-if sample execution failed: {error}. No results were printed.", file=sys.stderr)
        return 1

    print("Synthetic What-if retirement comparisons (nominal KRW / percentage points). Not market forecasts.")
    print("Built-in conditions and assumptions are not calibrated market estimates or investment recommendations.")
    print("Each scenario starts from its case's baseline, not the previous scenario. All deltas are changed minus baseline.")
    for case, profiles, result in results:
        _print_case(case, profiles, result, details=options.details)
    noun = "case" if len(results) == 1 else "cases"
    comparison_count = sum(len(result.scenarios) for _, _, result in results)
    print(f"\nCalculated {len(results)} synthetic What-if {noun}; {comparison_count} scenario comparisons.")
    print("Each case generates common paths once; children report provided paths received from that common market.")
    print("Different cases are independent, not aligned markets despite using the same seed.")
    print("Success means no negative year-end balance over the entire horizon; recovery does not erase depletion.")
    print("Negative balances are unmet funding gaps, not loans. Display rounding does not change depletion or counts.")
    print("P10/P50/P90 include all paths at each age, not a single trajectory or a confidence interval.")
    print("Quantile deltas are differences of marginal quantiles, not quantiles of paired path differences; "
          "they need not be sorted.")
    print("Probability deltas are percentage points (pp), not relative percent changes. "
          "Negative shortfall deltas mean smaller gaps.")
    print("Retirement expenses are current-age KRW/month; inflation stays anchored to current age. "
          "Pensions do not shift with retirement.")
    print("Savings apply only before retirement; changing them after retirement has no effect. "
          "No recommendation or ranking is computed.")
    print("Finite-sample probabilities depend on assumptions; 0%/100% are not guarantees or BE readiness scores.")
    print("Reproducibility requires the same inputs, code, Python version and execution environment.")
    print("Real-data adapters, Agent routing and service integration are not implemented here.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
