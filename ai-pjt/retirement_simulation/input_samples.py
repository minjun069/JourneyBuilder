"""Built-in saved-shaped input -> adapter -> engine demonstrations, not an API."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import sys

from .input_adapter import (
    InputMappingError, ProfileMappingResult, map_retirement_profile, map_simulation_assumptions,
)
from .models import MAX_PATHS, ModelValidationError, RetirementExpensePeriod, RetirementProfile, _integer
from .monte_carlo import MonteCarloCalculationError, MonteCarloResult, calculate_monte_carlo
from .what_if import WhatIfChanges, create_what_if_profile
from .what_if_engine import WhatIfCalculationError, WhatIfResult, WhatIfScenario, calculate_what_if
# Reuse presentation-only helpers without changing the existing sample CLIs.
from .what_if_samples import (
    _path_count, _print_changes, _print_delta, _print_profile, _print_summary, _seed,
)


_CASE_NAMES = ("saved_working", "saved_retired_no_pension", "saved_pension_gap")
_SECTIONS = ("financial_assets", "pension_assets", "goal_periods",
             "real_estate_assets", "liabilities", "goal_milestones")
_NOTICE_TEXT = {
    "partial_financial_scope": "Only supplied liquid assets, savings, expenses and monthly pensions are modeled.",
    "single_portfolio_assumption": "One portfolio; asset-specific liquidity and correlations are not modeled.",
    "pre_retirement_cashflow_not_recalculated": "Use total monthly savings; income, current spending and before-retirement goals are not recalculated.",
    "pension_account_not_modeled": "Use supplied nominal pension payments; account balances, contributions and withdrawals are not modeled.",
    "real_estate_not_modeled": "Property value/sale, rent, deposits and secured loans are excluded.",
    "liabilities_not_modeled": "Debt principal, interest and repayments are excluded.",
    "milestones_not_modeled": "One-off income and spending goals are excluded.",
    "confirmed_empty_financial_assets": "Synthetic fixture explicitly confirms no financial assets; balance and savings are zero.",
    "confirmed_empty_pensions": "Synthetic fixture explicitly confirms no pensions; no payment schedule is added.",
    "pension_outside_horizon": "Validated pension is outside the planning horizon and is not included in active payments.",
    "pension_horizon_limited": "Pension payments are limited to the planning end age; this is not a lifespan estimate.",
}


@dataclass(frozen=True, slots=True)
class InputSampleCase:
    """Local fixture container, not a validated customer request or immutable dict.

    Nested dicts/lists are fresh per build and intentionally retain saved-data shape.
    They must not be mutated while mapping; the mapped models are detached/frozen.
    """

    name: str
    saved_data: dict = field(repr=False)
    assumption_values: dict = field(repr=False)
    confirmed_empty_sections: tuple[str, ...] = field(repr=False)
    scenarios: tuple[WhatIfScenario, ...] = field(repr=False)


@dataclass(frozen=True, slots=True)
class _SampleExecution:
    case: InputSampleCase = field(repr=False)
    mapped: ProfileMappingResult = field(repr=False)
    result: MonteCarloResult | WhatIfResult = field(repr=False)
    changed_profiles: tuple[RetirementProfile, ...] = field(repr=False)


def _expense(start: int, end: int, amount: int) -> RetirementExpensePeriod:
    return RetirementExpensePeriod(from_age=start, to_age=end, monthly_amount=amount)


def build_input_cases(*, num_paths: int = 1_000, seed: int = 42) -> tuple[InputSampleCase, ...]:
    """Return fresh saved-shaped synthetic fixtures; do not map or calculate yet.

    Rates are demo-only settings here, never defaults added to the input adapter.
    The already-retired fixture follows internal model rules, not BE write eligibility.
    """
    _integer("num_paths", num_paths, minimum=1, maximum=MAX_PATHS)
    _integer("seed", seed, minimum=0)
    config = {"annual_return_mean": 0.05, "annual_return_volatility": 0.10,
              "annual_inflation_rate": 0.02, "num_paths": num_paths, "seed": seed}
    working = {
        "basic_info": {"age": 40, "retirement_age": 60, "end_age": 90},
        "financial_assets": [
            {"id": 1, "asset_type": "예금/적금", "principal": 90_000_000,
             "current_value": 100_000_000, "monthly_savings": 500_000},
            {"id": 2, "asset_type": "펀드/ETF", "principal": 18_000_000,
             "current_value": 20_000_000, "monthly_savings": 250_000},
        ],
        "pension_assets": [
            {"id": 1, "receive_method": "연금형", "pension_start_age": 65,
             "pension_period": "종신", "expected_amount": 1_000_000},
        ],
        "goal_periods": [
            {"id": 1, "phase": "after", "from_age": 60, "to_age": 74,
             "items": [{"id": 1, "amount": 1_500_000}, {"id": 2, "amount": 500_000}]},
            {"id": 2, "phase": "after", "from_age": 75, "to_age": 90,
             "items": [{"id": 3, "amount": 1_500_000}]},
        ],
        "real_estate_assets": [], "liabilities": [], "goal_milestones": [],
    }
    retired = {
        "basic_info": {"age": 65, "retirement_age": 60, "end_age": 70},
        "financial_assets": [
            {"id": 1, "asset_type": "예금/적금", "current_value": 100_000_000, "monthly_savings": 0},
        ],
        "pension_assets": [],
        "goal_periods": [
            {"id": 1, "phase": "after", "from_age": 65, "to_age": 70,
             "items": [{"id": 1, "amount": 1_500_000}]},
        ],
        "real_estate_assets": [
            {"id": 1, "type": "주택/아파트", "current_value": 300_000_000,
             "monthly_rental_income": 500_000, "rental_deposit": 20_000_000,
             "secured_loan": 50_000_000},
        ],
        "liabilities": [{"id": 1, "type": "주택담보대출", "balance": 50_000_000}],
        "goal_milestones": [{"id": 1, "phase": "after", "age": 68, "category": "지출", "amount": 10_000_000}],
    }
    gap = {
        "basic_info": {"age": 58, "retirement_age": 60, "end_age": 75},
        "financial_assets": [
            {"id": 1, "asset_type": "채권", "current_value": 30_000_000, "monthly_savings": 800_000},
        ],
        "pension_assets": [
            {"id": 1, "receive_method": "연금형", "pension_start_age": 65,
             "pension_period": "10년", "payment_period_years": 20, "expected_amount": 800_000},
            {"id": 2, "receive_method": "연금형", "pension_start_age": 80,
             "pension_period": "종신", "expected_amount": 200_000},
        ],
        "goal_periods": [
            {"id": 1, "phase": "after", "from_age": 60, "to_age": 64,
             "items": [{"id": 1, "amount": 1_800_000}]},
            {"id": 2, "phase": "after", "from_age": 65, "to_age": 75,
             "items": [{"id": 2, "amount": 1_300_000}]},
        ],
        "real_estate_assets": [], "liabilities": [], "goal_milestones": [],
    }
    return (
        InputSampleCase(_CASE_NAMES[0], working, dict(config), (), (
            WhatIfScenario(scenario_id="unchanged", changes=WhatIfChanges()),
            WhatIfScenario(scenario_id="save_more", changes=WhatIfChanges(monthly_savings=1_000_000)),
            WhatIfScenario(scenario_id="retire_earlier", changes=WhatIfChanges(retirement_age=58,
                retirement_expenses=(_expense(58, 74, 2_000_000), _expense(75, 90, 1_500_000)))),
            WhatIfScenario(scenario_id="lower_expenses", changes=WhatIfChanges(
                retirement_expenses=(_expense(60, 74, 1_800_000), _expense(75, 90, 1_300_000)))),
        )),
        InputSampleCase(_CASE_NAMES[1], retired, dict(config), ("pension_assets",), (
            WhatIfScenario(scenario_id="unchanged", changes=WhatIfChanges()),
            WhatIfScenario(scenario_id="lower_expenses", changes=WhatIfChanges(
                retirement_expenses=(_expense(65, 70, 1_200_000),))),
            WhatIfScenario(scenario_id="savings_no_effect", changes=WhatIfChanges(monthly_savings=500_000)),
        )),
        InputSampleCase(_CASE_NAMES[2], gap, dict(config), (), (
            WhatIfScenario(scenario_id="unchanged", changes=WhatIfChanges()),
            WhatIfScenario(scenario_id="stop_saving", changes=WhatIfChanges(monthly_savings=0)),
            WhatIfScenario(scenario_id="retire_later", changes=WhatIfChanges(retirement_age=62)),
        )),
    )


def _run_case(case: InputSampleCase, *, mode: str) -> _SampleExecution:
    mapped = map_retirement_profile(case.saved_data, confirmed_empty_sections=case.confirmed_empty_sections)
    config = map_simulation_assumptions(case.assumption_values)
    if mode == "monte-carlo":
        return _SampleExecution(case, mapped, calculate_monte_carlo(mapped.profile, config), ())
    if mode != "what-if":
        raise ModelValidationError("mode", "must select monte-carlo or what-if")
    result = calculate_what_if(mapped.profile, config, case.scenarios)
    changed = tuple(create_what_if_profile(mapped.profile, item.changes) for item in case.scenarios)
    return _SampleExecution(case, mapped, result, changed)


def _print_execution(execution: _SampleExecution, *, mode: str, details: bool) -> None:
    case, mapped, result = execution.case, execution.mapped, execution.result
    meta, config = result.metadata, result.metadata.assumptions
    print(f"\n[{case.name}]")
    counts = ", ".join(f"{name}={len(case.saved_data[name])}" for name in _SECTIONS)
    print(f"saved_data sections (synthetic row counts): {counts}")
    print(f"confirmed_empty_sections (synthetic facts): {', '.join(case.confirmed_empty_sections) or 'none'}")
    print(f"mapping_version={mapped.mapping_version}; saved_data -> validated profile; no current_form merge")
    print("mapping_notices (must accompany analysis results):")
    for notice in mapped.notices:
        explanation = _NOTICE_TEXT.get(notice.code, "See the input mapping specification for this notice.")
        print(f"- {notice.code} at {notice.field_name}: {explanation}")
    print(f"assumptions (annual decimal fractions): annual_return_mean={config.annual_return_mean}, "
          f"annual_return_volatility={config.annual_return_volatility}, annual_inflation_rate={config.annual_inflation_rate}")
    print(f"execution: mode={mode}, num_paths={meta.num_paths}, configured_seed={config.seed}, "
          f"effective_seed={meta.seed}, path_source={meta.path_source}, mc_model_version={meta.model_version}, "
          f"return_model={meta.return_model}, rng_algorithm={meta.rng_algorithm}, "
          f"python={meta.python_implementation} {meta.python_version}")
    if isinstance(result, WhatIfResult):
        print(f"comparison: what_if_model_version={result.model_version}, scenarios={len(result.scenarios)}, "
              "common paths generated once; children report provided paths")
    print("baseline:")
    _print_profile(mapped.profile)
    baseline = result.baseline if isinstance(result, WhatIfResult) else result
    _print_summary(baseline, details=details)
    if isinstance(result, WhatIfResult):
        for definition, profile, item in zip(case.scenarios, execution.changed_profiles, result.scenarios):
            print(f"scenario {item.scenario_id}:")
            _print_changes(definition.changes)
            _print_profile(profile)
            _print_summary(item.result, details=details)
            _print_delta(item, details=details)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m retirement_simulation.input_samples",
        description="Map built-in synthetic saved inputs into Monte Carlo / What-if retirement calculations.",
        epilog=("Demo only: mean=0.05, volatility=0.10, inflation=0.02, not market forecasts. "
                "No customer files, DB, HTTP or service connection. Large path counts cost time/memory; "
                "the engine cap is not a service performance guarantee."),
        allow_abbrev=False,
    )
    parser.add_argument("--case", choices=_CASE_NAMES, help="run one built-in case (default: all three)")
    parser.add_argument("--mode", choices=("monte-carlo", "what-if"), default="what-if",
                        help="baseline MC only, or baseline plus common-path comparisons (default: what-if)")
    parser.add_argument("--num-paths", type=_path_count, default=1_000,
                        help=f"paths per case, 1..{MAX_PATHS} (default: 1000; not an operational limit)")
    parser.add_argument("--seed", type=_seed, default=42, help="non-negative RNG seed (default: 42)")
    parser.add_argument("--details", action="store_true", help="show all age summaries and What-if deltas")
    options = parser.parse_args(argv)
    try:
        cases = build_input_cases(num_paths=options.num_paths, seed=options.seed)
        selected = tuple(case for case in cases if options.case is None or case.name == options.case)
        # Finish every selected mapping/calculation before printing any input or result.
        results = tuple(_run_case(case, mode=options.mode) for case in selected)
    except (InputMappingError, ModelValidationError, MonteCarloCalculationError, WhatIfCalculationError) as error:
        print(f"Input sample execution failed: {error}. No results were printed.", file=sys.stderr)
        return 1

    print("Synthetic saved-input retirement calculations (KRW / percentage points). Not market forecasts.")
    print("Only built-in fixtures are read. Demo rates are not calibrated estimates or service defaults.")
    print("Mapping notices describe partial scope; property, debt and one-off obligations are not silently included.")
    for execution in results:
        _print_execution(execution, mode=options.mode, details=options.details)
    count = sum(len(item.result.scenarios) for item in results if isinstance(item.result, WhatIfResult))
    noun = "case" if len(results) == 1 else "cases"
    print(f"\nCalculated {len(results)} synthetic saved-input {noun}; mode={options.mode}; {count} scenario comparisons.")
    print("Each What-if starts from its original baseline; not the previous scenario. Deltas are changed minus baseline.")
    print("Independent cases do not share aligned markets. What-if uses one common set of paths within each case.")
    print("Success means no negative year-end balance; recovery does not erase depletion. Zero balance is not depleted.")
    print("Negative balances are unmet funding gaps, not loans. Rounding affects display only.")
    print("P10/P50/P90 summarize all paths, not a single trajectory or a confidence interval.")
    print("Quantile deltas are differences of marginal quantiles, not quantiles of paired differences.")
    print("Probability deltas are pp, not relative percent changes; negative shortfall deltas mean smaller gaps.")
    print("Expenses are current-age KRW/month; pensions are nominal and do not shift with retirement age.")
    print("Savings apply only before retirement; changing them after retirement has no effect.")
    print("0%/100% are not guarantees, recommendations or BE readiness scores. Same inputs/code/environment are needed for reproduction.")
    print("Empty-section confirmations are synthetic facts, not inferred from [] or existing BE completion status.")
    print("No original data is saved or patched. Already-retired model support does not change BE write rules.")
    print("Real BE/API/FE integration, partial-scope acceptance, market settings and deployment remain separate work.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
