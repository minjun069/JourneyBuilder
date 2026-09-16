"""Source/sdist-only synthetic handoff tooling; not an installed service entry point.

Run from backend-pjt/packages with:
python -S -B -m retirement_simulation.tests.handoff_samples
Only the committed policy file and built-in synthetic fixtures are read. No input
files, DB, network, environment settings, persistent writes or API defaults.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import platform
from time import perf_counter
import sys

from retirement_simulation import (
    RetirementAnalysisError, analyze_retirement, analysis_error_to_dict,
    analysis_to_dict, map_simulation_assumptions,
)
from retirement_simulation.analysis_samples import build_analysis_case


POLICY_PATH = Path(__file__).resolve().parents[1] / "docs/simulation-policy.json"
COMBINATIONS = {
    "save_more": (True, False, False),
    "retire_earlier": (False, True, False),
    "lower_expenses": (False, False, True),
    "save_and_retire": (True, True, False),
    "save_and_spend": (True, False, True),
    "retire_and_spend": (False, True, True),
    "combined": (True, True, True),
}
CASE_NAMES = ("baseline", "unchanged", *COMBINATIONS, "retired", "pension_gap",
              "unconfirmed_pension", "missing_expenses")
STUDY_SEEDS = (7, 21, 42, 73, 109)
STUDY_PATHS = (1000, 5000)
STUDY_CASES = ("combined", "retired", "pension_gap")
# One-factor-at-a-time diagnostics only, not additional service presets.
SENSITIVITY = (
    ("base", None, None),
    ("mean_2pct", "annual_return_mean", 0.02),
    ("mean_6pct", "annual_return_mean", 0.06),
    ("volatility_5pct", "annual_return_volatility", 0.05),
    ("volatility_15pct", "annual_return_volatility", 0.15),
    ("inflation_1pct", "annual_inflation_rate", 0.01),
    ("inflation_3pct", "annual_inflation_rate", 0.03),
)
NOTICE = "Synthetic handoff only; not market calibration, a customer API, or BE deployment validation."


def load_policy() -> dict:
    """Load the committed handoff artifact, never a runtime fallback."""
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    map_simulation_assumptions(policy["assumption_values"])
    return policy


def build_request(name: str = "combined") -> dict:
    """Fresh synthetic input with exactly the selected MVP policy's five fields."""
    if not isinstance(name, str) or name not in CASE_NAMES:
        raise ValueError("select a built-in handoff case")
    source_name = {"retired": "saved_retired_no_pension", "pension_gap": "saved_pension_gap",
                   "unconfirmed_pension": "unconfirmed_pension"}.get(name, "saved_working")
    source = build_analysis_case(source_name)
    definitions = {row["scenario_id"]: row["changes"] for row in source.scenario_values}
    changes = {}
    if name in COMBINATIONS:
        savings, retirement, expenses = COMBINATIONS[name]
        if savings:
            changes["monthly_savings"] = definitions["save_more"]["monthly_savings"]
        if retirement:
            changes["retirement_age"] = definitions["retire_earlier"]["retirement_age"]
            changes["retirement_expenses"] = deepcopy(definitions["retire_earlier"]["retirement_expenses"])
        if expenses:
            changes["retirement_expenses"] = deepcopy(definitions["lower_expenses"]["retirement_expenses"])
            if retirement:
                # An EXPLICIT synthetic choice: lower the entire first phase,
                # including the newly retired years. The engine never fills it.
                changes["retirement_expenses"][0]["from_age"] = changes["retirement_age"]
    elif name == "retired":
        changes = definitions["lower_expenses"]
    elif name == "pension_gap":
        changes = definitions["retire_later"]
    request = {
        "saved_data": source.saved_data,
        "assumption_values": load_policy()["assumption_values"],
        "confirmed_empty_sections": list(source.confirmed_empty_sections),
        "scenarios": [] if name in ("baseline", "unconfirmed_pension", "missing_expenses") else [
            {"scenario_id": name, "changes": changes},
        ],
    }
    if name == "missing_expenses":
        request["saved_data"]["goal_periods"] = []
    return request


def execute(request: dict) -> tuple[int, dict]:
    """Only known calculation/output failures become an error JSON, not HTTP 200."""
    try:
        payload = analysis_to_dict(analyze_retirement(**request))
        status = 0
    except RetirementAnalysisError as error:
        payload = analysis_error_to_dict(error)
        status = 1
    # Complete validation before the CLI emits any output.
    if json.loads(json.dumps(payload, allow_nan=False)) != payload:
        raise AssertionError("strict JSON round-trip mismatch")
    return status, payload


def _measurement(request: dict) -> dict:
    original = deepcopy(request)
    started = perf_counter()
    status, payload = execute(request)
    elapsed = perf_counter() - started
    if status or request != original:
        raise AssertionError("study calculation failed or mutated its synthetic input")
    baseline = payload["baseline"]
    scenario = payload["scenarios"][0]
    return {
        "assumptions": dict(request["assumption_values"]),
        "num_years": payload["metadata"]["end_age"] - payload["metadata"]["current_age"] + 1,
        "baseline_probability": baseline["success_probability"],
        "changed_probability": scenario["result"]["success_probability"],
        "delta_pp": scenario["delta"]["success_probability_delta_pp"],
        "baseline_final_p50_krw": baseline["final_balance_percentiles"]["p50"],
        "changed_final_p50_krw": scenario["result"]["final_balance_percentiles"]["p50"],
        "elapsed_seconds": elapsed,
    }


def run_study() -> dict:
    """Fixed bounded diagnostic matrix; no quality/SLA claim or preset selection.

30 paired baseline/What-if runs for seeds/path counts, then 7 one-factor
sensitivity runs. Max call is 5000 x 51 x 2 path-years. Timings include the local
analysis/serialization check, not HTTP/DB/concurrency. No user-controlled limits.
"""
    rows = []
    for name in STUDY_CASES:
        for count in STUDY_PATHS:
            for seed in STUDY_SEEDS:
                request = build_request(name)
                request["assumption_values"].update(num_paths=count, seed=seed)
                rows.append({"case": name, **_measurement(request)})
    sensitivity = []
    for name, field, value in SENSITIVITY:
        request = build_request("combined")
        if field is not None:
            request["assumption_values"][field] = value
        sensitivity.append({"case": name, **_measurement(request)})
    summaries = []
    for name in STUDY_CASES:
        for count in STUDY_PATHS:
            selected = [row for row in rows if row["case"] == name and row["assumptions"]["num_paths"] == count]
            summaries.append({"case": name, "num_paths": count, **{
                key + "_range": [min(row[key] for row in selected), max(row[key] for row in selected)]
                for key in ("baseline_probability", "changed_probability", "delta_pp")
            }})
    return {
        "kind": "synthetic_handoff_diagnostics_v1",
        "policy_version": load_policy()["policy_version"],
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "seeds": list(STUDY_SEEDS), "path_counts": list(STUDY_PATHS),
        "paired_runs": rows, "seed_ranges": summaries, "sensitivity_runs": sensitivity,
        "limitations": ["synthetic_only", "not_market_calibration", "not_a_confidence_interval",
                        "not_be_performance_or_sla", "sensitivity_values_are_not_service_presets"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--case", choices=CASE_NAMES, default="combined")
    group.add_argument("--study", action="store_true", help="run the fixed 37-call diagnostic matrix")
    parser.add_argument("--request", action="store_true", help="show the synthetic Python arguments, without calculation")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)
    if args.study and args.request:
        parser.error("--request cannot be combined with --study")
    print(NOTICE, file=sys.stderr)
    if args.study:
        status, payload = 0, run_study()
    elif args.request:
        status, payload = 0, build_request(args.case)
    else:
        status, payload = execute(build_request(args.case))
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2 if args.pretty else None)
    print(encoded)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
