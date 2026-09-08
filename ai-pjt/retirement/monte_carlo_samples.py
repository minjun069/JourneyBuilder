"""Local synthetic Monte Carlo demonstration, not a customer-input or service API."""

from __future__ import annotations

import argparse
from dataclasses import replace
import sys

from .models import MAX_PATHS, ModelValidationError, SimulationAssumptions
from .monte_carlo import (
    AmountPercentiles,
    MonteCarloCalculationError,
    MonteCarloResult,
    calculate_monte_carlo,
)
from .samples import SampleCase, build_sample_cases


def build_monte_carlo_cases(*, num_paths: int = 1_000, seed: int = 42) -> tuple[SampleCase, ...]:
    """Reuse fresh synthetic profiles with explicit demo-only stochastic assumptions.

    These are not calibrated market values or defaults for the calculation API.
    The original fixed-assumption sample builder and its inputs are unchanged.
    """
    assumptions = SimulationAssumptions(
        annual_return_mean=0.05,
        annual_return_volatility=0.10,
        annual_inflation_rate=0.02,
        num_paths=num_paths,
        seed=seed,
    )
    return tuple(replace(case, assumptions=assumptions) for case in build_sample_cases())


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


def _amount_summary(values: AmountPercentiles) -> str:
    return f"p10={values.p10:,.2f}, p50={values.p50:,.2f}, p90={values.p90:,.2f}"


def _print_case(case: SampleCase, result: MonteCarloResult, *, details: bool) -> None:
    meta, config = result.metadata, result.metadata.assumptions
    print(f"\n[{case.name}]")
    print(
        f"horizon: current_age={meta.current_age}, retirement_age={case.profile.retirement_age}, "
        f"end_age={meta.end_age}, years={len(result.asset_percentiles)}"
    )
    print(
        f"assumptions (annual decimal fractions): annual_return_mean={config.annual_return_mean}, "
        f"annual_return_volatility={config.annual_return_volatility}, "
        f"annual_inflation_rate={config.annual_inflation_rate}"
    )
    print(
        f"execution: num_paths={result.num_paths}, configured_seed={config.seed}, "
        f"effective_seed={meta.seed}, path_source={meta.path_source}, "
        f"model_version={meta.model_version}, return_model={meta.return_model}, "
        f"rng_algorithm={meta.rng_algorithm}, python={meta.python_implementation} {meta.python_version}"
    )
    print(
        f"success_probability={result.success_probability:.6f} "
        f"({result.success_probability:.3%}), success_count={result.success_count}/{result.num_paths}"
    )
    print(
        f"depletion_probability={result.depletion_probability:.6f} "
        f"({result.depletion_probability:.3%}), "
        f"depletion_count={result.depletion_count}/{result.num_paths}, "
        f"never_depleted_count={result.never_depleted_count}"
    )
    print(f"final_balance (nominal KRW): {_amount_summary(result.final_balance_percentiles)}")
    print(f"final_shortfall (nominal KRW): {_amount_summary(result.final_shortfall_percentiles)}")
    histogram = ", ".join(f"{item.age}:{item.count}" for item in result.first_depletion_counts
                          if item.count)
    print(f"first_depletion_counts (age:count, nonzero only): {histogram or 'none'}")
    if details:
        print("age | balance_p10 | balance_p50 | balance_p90 | first_depletion_count")
        for point, depletion in zip(result.asset_percentiles, result.first_depletion_counts):
            print(f"{point.age} | {point.balance.p10:,.2f} | {point.balance.p50:,.2f} | "
                  f"{point.balance.p90:,.2f} | {depletion.count}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m retirement.monte_carlo_samples",
        description="Run built-in synthetic Monte Carlo retirement examples (nominal KRW).",
        epilog=("Demo assumptions: mean=0.05, volatility=0.10, inflation=0.02; "
                "not market forecasts. No customer files, database or service connection. "
                "The path-count cap is not a memory or response-time guarantee."),
        allow_abbrev=False,
    )
    parser.add_argument(
        "--case", choices=tuple(case.name for case in build_sample_cases()),
        help="run only this built-in case (default: all three)",
    )
    parser.add_argument(
        "--num-paths", type=_path_count, default=1_000,
        help=f"paths per case, 1..{MAX_PATHS} (default: 1000; large runs use more memory/time)",
    )
    parser.add_argument(
        "--seed", type=_seed, default=42, help="local RNG seed, non-negative integer (default: 42)",
    )
    parser.add_argument(
        "--details", action="store_true",
        help="show every age's balance P10/P50/P90 and first-depletion count, including zeros",
    )
    options = parser.parse_args(argv)
    try:
        cases = build_monte_carlo_cases(num_paths=options.num_paths, seed=options.seed)
        selected = tuple(case for case in cases if options.case is None or case.name == options.case)
        # Keep only small summaries. Print nothing until every selected case succeeds.
        results = tuple((case, calculate_monte_carlo(case.profile, case.assumptions))
                        for case in selected)
    except (ModelValidationError, MonteCarloCalculationError) as error:
        print(f"Monte Carlo sample execution failed: {error}. No results were printed.", file=sys.stderr)
        return 1

    print("Synthetic Monte Carlo retirement examples (nominal KRW). Not market forecasts.")
    print("Built-in demo assumptions are not calibrated market estimates or investment recommendations.")
    for case, result in results:
        _print_case(case, result, details=options.details)
    noun = "case" if len(results) == 1 else "cases"
    print(f"\nCalculated {len(results)} synthetic Monte Carlo {noun}.")
    print("Success means no negative year-end balance over the entire horizon; "
          "recovery does not erase depletion.")
    print("Negative balances are unmet funding gaps, not loans. "
          "Display rounding does not change depletion or counts.")
    print("P10/P50/P90 include all paths at each age, not a single trajectory or a confidence interval.")
    print("Finite-sample probabilities depend on assumptions; "
          "0%/100% are not guarantees or BE readiness scores.")
    print("Reproducibility requires the same inputs, code, Python version and execution environment.")
    print("The same seed across different horizons does not align paths for What-if comparisons.")
    print("What-if samples: python -m retirement.what_if_samples. "
          "Real-data adapters and service integration are not implemented.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
