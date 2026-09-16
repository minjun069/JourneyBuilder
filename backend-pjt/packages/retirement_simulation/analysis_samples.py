"""Built-in saved input -> integrated analysis -> complete success/error JSON.

Synthetic-only CLI, not an HTTP endpoint, customer file reader, or service wrapper.
Existing sample fixtures are reused; all calculation/output rules stay in the
public analysis functions. See docs/analysis-samples.md and docs/be-handoff.md.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
import sys

from .analysis import analyze_retirement
from .analysis_errors import RetirementAnalysisError
from .analysis_output import analysis_error_to_dict, analysis_to_dict
from .input_samples import build_input_cases
from .models import ModelValidationError, _integer
from .what_if_engine import WhatIfScenario
from .what_if_samples import _seed


MAX_SAMPLE_PATHS = 1_000  # Demo-only guard, not an engine/API limit or SLA.
CASE_NAMES = ("saved_working", "saved_retired_no_pension", "saved_pension_gap",
              "hand_check", "unconfirmed_pension")
_WORKING_IDS = ("save_more", "retire_earlier", "lower_expenses")
_NOTICE = ("Synthetic demo only; not customer data, market forecasts or a readiness score. "
           "No BE/API/FE connection. stdout contains one analysis-contract JSON document.")


@dataclass(frozen=True, slots=True)
class AnalysisSampleCase:
    """Fresh local fixture; frozen container does not make nested dicts immutable."""

    name: str
    saved_data: dict = field(repr=False)
    assumption_values: dict = field(repr=False)
    scenario_values: tuple[dict, ...] = field(repr=False)
    confirmed_empty_sections: tuple[str, ...] = field(repr=False)


def _scenario_value(scenario: WhatIfScenario) -> dict:
    # Fixture-only conversion, not a replacement for the public input mapper.
    changes = scenario.changes
    return {"scenario_id": scenario.scenario_id, "changes": {
        "monthly_savings": changes.monthly_savings, "retirement_age": changes.retirement_age,
        "retirement_expenses": None if changes.retirement_expenses is None else [
            {"from_age": row.from_age, "to_age": row.to_age, "monthly_amount": row.monthly_amount}
            for row in changes.retirement_expenses
        ],
    }}


def build_analysis_case(name: str, *, num_paths: int = 1_000, seed: int = 42) -> AnalysisSampleCase:
    """Build one synthetic request, without mapping, calculating, or doing I/O.

    The three saved cases reuse input_samples. The working case selects the three
    explicit feature examples; the legacy CLI still keeps its unchanged scenario.
    The hand-check/error fixtures use 0% rates, never market defaults.
    """
    if not isinstance(name, str) or name not in CASE_NAMES:
        raise ModelValidationError("case", "must select a built-in analysis sample")
    _integer("num_paths", num_paths, minimum=1, maximum=MAX_SAMPLE_PATHS)
    _integer("seed", seed, minimum=0)
    if name in CASE_NAMES[:3]:
        source = next(case for case in build_input_cases(num_paths=num_paths, seed=seed) if case.name == name)
        definitions = source.scenarios
        if name == "saved_working":
            by_id = {item.scenario_id: item for item in definitions}
            definitions = tuple(by_id[identifier] for identifier in _WORKING_IDS)
        return AnalysisSampleCase(name, source.saved_data, source.assumption_values,
                                  tuple(_scenario_value(item) for item in definitions), source.confirmed_empty_sections)

    return AnalysisSampleCase(name, {
        "basic_info": {"age": 60, "retirement_age": 60, "end_age": 60},
        "financial_assets": [{"asset_type": "예금/적금", "current_value": 10_000, "monthly_savings": 0}],
        "pension_assets": [],
        "goal_periods": [{"phase": "after", "from_age": 60, "to_age": 60, "items": [{"amount": 1_000}]}],
        "real_estate_assets": [], "liabilities": [], "goal_milestones": [],
    }, {
        "annual_return_mean": 0, "annual_return_volatility": 0, "annual_inflation_rate": 0,
        "num_paths": num_paths, "seed": seed,
    }, ({"scenario_id": "lower_expenses", "changes": {"retirement_expenses": [
        {"from_age": 60, "to_age": 60, "monthly_amount": 800},
    ]}},), ("pension_assets",) if name == "hand_check" else ())


def _sample_path_count(raw: str) -> int:
    message = f"must be an integer from 1 to {MAX_SAMPLE_PATHS} for this synthetic CLI"
    try:
        value = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(message) from None
    if not 1 <= value <= MAX_SAMPLE_PATHS:
        raise argparse.ArgumentTypeError(message)
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m retirement_simulation.analysis_samples", allow_abbrev=False,
        description="Run one built-in synthetic saved input through the public analysis and JSON adapters.",
        epilog=("No customer files, stdin input, DB, network or service defaults. "
                "Success/error JSON goes to stdout; a synthetic-only notice goes to stderr. "
                "Exit 0=success, 1=known analysis/output error, 2=invalid CLI arguments."),
    )
    parser.add_argument("--case", choices=CASE_NAMES, default="saved_working", help="one fixture (default: saved_working)")
    parser.add_argument("--mode", choices=("monte-carlo", "what-if"), default="what-if",
                        help="baseline only, or baseline plus comparisons (default: what-if)")
    parser.add_argument("--num-paths", type=_sample_path_count, default=MAX_SAMPLE_PATHS,
                        help="paths, 1..1000 (default: 1000; demo guard, not an operational guarantee)")
    parser.add_argument("--seed", type=_seed, default=42,
                        help="non-negative configured seed (default: 42); JSON safety is checked after calculation")
    parser.add_argument("--pretty", action="store_true", help="indent JSON; values and contract are unchanged")
    args = parser.parse_args(argv)
    case = build_analysis_case(args.case, num_paths=args.num_paths, seed=args.seed)
    print(_NOTICE, file=sys.stderr)
    try:
        analysis = analyze_retirement(
            case.saved_data, case.assumption_values,
            scenarios=case.scenario_values if args.mode == "what-if" else (),
            confirmed_empty_sections=case.confirmed_empty_sections,
        )
        payload = analysis_to_dict(analysis)
        status = 0
    except RetirementAnalysisError as error:
        payload = analysis_error_to_dict(error)
        status = 1
    # Complete encoding before writing any JSON. Do not catch unrelated bugs,
    # resource exhaustion, interruptions, encoding or output-stream failures.
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False,
                         indent=2 if args.pretty else None,
                         separators=None if args.pretty else (",", ":"))
    print(encoded)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
