"""Explicit v1 JSON-compatible mappings; no simulation, I/O, or HTTP policy.

Only the package's concrete immutable models are supported. Validation on
shallow copies reuses model invariants without mutating or repairing sources.
See docs/analysis-output.md for numeric limits and diagnostic precedence.
"""

from __future__ import annotations

from copy import copy
from math import isfinite
from typing import NoReturn

from .analysis import RetirementAnalysisResult
from .analysis_errors import RetirementAnalysisError
from .input_adapter import InputMappingNotice, ProfileMappingResult
from .models import (
    ModelValidationError, PensionIncome, RetirementExpensePeriod, RetirementProfile, SimulationAssumptions,
)
from .monte_carlo import (
    AmountPercentiles, AssetPercentiles, FirstDepletionCount, MonteCarloMetadata, MonteCarloResult,
)
from .what_if import WhatIfChanges
from .what_if_engine import (
    MAX_WHAT_IF_SCENARIOS, AmountPercentileDelta, AssetPercentileDelta, FirstDepletionDelta,
    WhatIfCalculationError, WhatIfResult, WhatIfScenario, WhatIfScenarioResult,
)


_MAX_JSON_NUMBER = 9_007_199_254_740_991
_CONTRACT_VERSION = "retirement-analysis-v1"
_PROFILE_NUMBERS = ("current_age", "retirement_age", "end_age", "liquid_assets", "monthly_savings")
_ASSUMPTION_FIELDS = ("annual_return_mean", "annual_return_volatility", "annual_inflation_rate", "num_paths", "seed")
_COUNT_FIELDS = ("num_paths", "success_count", "depletion_count", "never_depleted_count")
_DELTA_COUNTS = ("success_count_delta", "depletion_count_delta", "never_depleted_count_delta")
_SHARED_METADATA = ("current_age", "end_age", "assumptions", "model_version", "python_version", "python_implementation")


def _invalid(path: str) -> NoReturn:
    raise RetirementAnalysisError("invalid_analysis_result", path) from None


def _read(value: object, name: str, path: str) -> object:
    try:
        return getattr(value, name)
    except AttributeError:
        _invalid(path)


def _model(value: object, kind: type, path: str) -> None:
    # Subclasses could override properties/copy behavior and are not v1 models.
    if type(value) is not kind:
        _invalid(path)


def _rows(value: object, path: str, *, limit: int | None = None) -> tuple:
    if type(value) is not tuple or (limit is not None and len(value) > limit):
        _invalid(path)
    return value


def _number(value: object, path: str, *, integer: bool = False) -> int | float:
    if type(value) not in ((int,) if integer else (int, float)):
        _invalid(path)
    if (type(value) is float and not isfinite(value)) or abs(value) > _MAX_JSON_NUMBER:
        raise RetirementAnalysisError("unsafe_json_number", path) from None
    return value


def _text(value: object, path: str, *, expected: str | None = None) -> str:
    if type(value) is not str or not value or (expected is not None and value != expected):
        _invalid(path)
    return value


def _validate(value: object, kind: type, path: str) -> None:
    _model(value, kind, path)
    try:
        checked = copy(value)
        kind.__post_init__(checked)
        if checked != value:
            _invalid(path)
    except (ModelValidationError, WhatIfCalculationError, AttributeError):
        _invalid(path)


def _number_fields(value: object, names: tuple[str, ...], path: str, *, integer: bool = False) -> dict:
    return {name: _number(_read(value, name, f"{path}.{name}"), f"{path}.{name}", integer=integer)
            for name in names}


def _periods(value: object, path: str, *, pension: bool = False) -> list:
    kind = PensionIncome if pension else RetirementExpensePeriod
    names = ("start_age", "end_age", "monthly_amount") if pension else ("from_age", "to_age", "monthly_amount")
    output = []
    for index, item in enumerate(_rows(value, path)):
        location = f"{path}[{index}]"
        _model(item, kind, location)
        output.append(_number_fields(item, names, location, integer=True))
        _validate(item, kind, location)
    return output


def _profile(value: object, path: str) -> dict:
    _model(value, RetirementProfile, path)
    output = _number_fields(value, _PROFILE_NUMBERS, path, integer=True)
    for name in ("retirement_expenses", "pension_incomes"):
        location = f"{path}.{name}"
        output[name] = _periods(_read(value, name, location), location, pension=name == "pension_incomes")
    _validate(value, RetirementProfile, path)
    return output


def _changes(value: object, path: str) -> dict:
    _model(value, WhatIfChanges, path)
    output = {}
    for name in ("monthly_savings", "retirement_age", "retirement_expenses"):
        location = f"{path}.{name}"
        item = _read(value, name, location)
        output[name] = (None if item is None else _periods(item, location)
                        if name == "retirement_expenses" else _number(item, location, integer=True))
    if output["retirement_expenses"] == []:
        _invalid(f"{path}.retirement_expenses")
    _validate(value, WhatIfChanges, path)
    return output


def _amounts(value: object, path: str, *, delta: bool = False) -> dict:
    kind = AmountPercentileDelta if delta else AmountPercentiles
    _model(value, kind, path)
    output = _number_fields(value, ("p10", "p50", "p90"), path)
    _validate(value, kind, path)
    return output


def _age_rows(value: object, path: str, *, delta: bool = False, counts: bool = False) -> list:
    kind = (FirstDepletionDelta if delta else FirstDepletionCount) if counts else (
        AssetPercentileDelta if delta else AssetPercentiles
    )
    output = []
    for index, item in enumerate(_rows(value, path)):
        location = f"{path}[{index}]"
        _model(item, kind, location)
        row = _number_fields(item, ("age",), location, integer=True)
        name = "count" if counts else "balance"
        field_path = f"{location}.{name}"
        field = _read(item, name, field_path)
        row[name] = _number(field, field_path, integer=True) if counts else _amounts(field, field_path, delta=delta)
        _validate(item, kind, location)
        output.append(row)
    return output


def _metadata(value: object, *, what_if: bool) -> dict:
    _model(value, MonteCarloMetadata, "metadata")
    assumptions = _read(value, "assumptions", "metadata.assumptions")
    _model(assumptions, SimulationAssumptions, "metadata.assumptions")
    config = {name: _number(_read(assumptions, name, f"metadata.assumptions.{name}"),
                           f"metadata.assumptions.{name}", integer=name in ("num_paths", "seed"))
              for name in _ASSUMPTION_FIELDS}
    _validate(assumptions, SimulationAssumptions, "metadata.assumptions")
    output = _number_fields(value, ("current_age", "end_age", "num_paths"), "metadata", integer=True)
    output["assumptions"] = config
    output["path_source"] = _text(_read(value, "path_source", "metadata.path_source"),
                                  "metadata.path_source", expected="generated")
    output["mc_model_version"] = _text(_read(value, "model_version", "metadata.mc_model_version"),
                                       "metadata.mc_model_version", expected="retirement-mc-v1")
    for name in ("python_version", "python_implementation"):
        output[name] = _text(_read(value, name, f"metadata.{name}"), f"metadata.{name}")
    _validate(value, MonteCarloMetadata, "metadata")
    output["effective_seed"] = None if value.seed is None else _number(value.seed, "metadata.effective_seed", integer=True)
    output["return_model"] = value.return_model
    output["rng_algorithm"] = value.rng_algorithm
    output["what_if_model_version"] = "retirement-what-if-v1" if what_if else None
    return output


def _execution(value: object, market: MonteCarloMetadata, path: str, *, source: str) -> dict:
    _model(value, MonteCarloMetadata, path)
    _validate(_read(value, "assumptions", path), SimulationAssumptions, path)
    _validate(value, MonteCarloMetadata, path)
    for name in _SHARED_METADATA:
        if _read(value, name, path) != getattr(market, name):
            _invalid(path)
    path_source = _text(_read(value, "path_source", f"{path}.path_source"), f"{path}.path_source", expected=source)
    return {"path_source": path_source,
            "effective_seed": None if value.seed is None else _number(value.seed, f"{path}.effective_seed", integer=True)}


def _summary(value: object, path: str, market: MonteCarloMetadata, *, source: str) -> dict:
    _model(value, MonteCarloResult, path)
    execution = _execution(_read(value, "metadata", f"{path}.execution"), market, f"{path}.execution", source=source)
    output = {}
    for name, counts in (("asset_percentiles", False), ("first_depletion_counts", True)):
        location = f"{path}.{name}"
        output[name] = _age_rows(_read(value, name, location), location, counts=counts)
    location = f"{path}.final_shortfall_percentiles"
    output["final_shortfall_percentiles"] = _amounts(_read(value, "final_shortfall_percentiles", location), location)
    _validate(value, MonteCarloResult, path)  # Validate horizon/count before accessing derived properties.
    output.update(_number_fields(value, _COUNT_FIELDS, path, integer=True))
    output.update(_number_fields(value, ("success_probability", "depletion_probability"), path))
    output["final_balance_percentiles"] = _amounts(value.final_balance_percentiles, f"{path}.final_balance_percentiles")
    output["execution"] = execution
    return output


def _delta(value: WhatIfScenarioResult, path: str) -> dict:
    output = {}
    for name, counts in (("asset_deltas", False), ("first_depletion_deltas", True)):
        location = f"{path}.{name}"
        output[name] = _age_rows(_read(value, name, location), location, delta=True, counts=counts)
    location = f"{path}.final_shortfall_delta"
    output["final_shortfall_delta"] = _amounts(_read(value, "final_shortfall_delta", location), location, delta=True)
    # Recheck stored deltas using the existing model's pure consistency rules.
    # Never replace an inconsistent stored result with the recomputed copy.
    _validate(value, WhatIfScenarioResult, path)
    output.update(_number_fields(value, _DELTA_COUNTS, path, integer=True))
    output.update(_number_fields(value, ("success_probability_delta_pp", "depletion_probability_delta_pp"), path))
    output["final_balance_delta"] = _amounts(value.final_balance_delta, f"{path}.final_balance_delta", delta=True)
    return output


def analysis_to_dict(analysis: RetirementAnalysisResult) -> dict[str, object]:
    """Return a fresh, complete v1 dict or a validated output error; no JSON text.

    Only concrete package models/immutable tuples and supported scalar types are
    accepted. Numbers keep their values; nonfinite/unsafe magnitudes fail rather
    than being rounded, coerced, clipped, or replaced. Engines are never called.
    """
    _model(analysis, RetirementAnalysisResult, "analysis")
    version = _text(_read(analysis, "contract_version", "contract_version"), "contract_version", expected=_CONTRACT_VERSION)
    mapped = _read(analysis, "mapped_input", "analysis")
    _model(mapped, ProfileMappingResult, "analysis")
    mapping_version = _text(_read(mapped, "mapping_version", "mapping_version"), "mapping_version", expected="retirement-input-v1")
    notices = []
    for index, item in enumerate(_rows(_read(mapped, "notices", "notices"), "notices")):
        path = f"notices[{index}]"
        _model(item, InputMappingNotice, path)
        notice = {name: _text(_read(item, name, f"{path}.{name}"), f"{path}.{name}") for name in ("code", "field_name")}
        _validate(item, InputMappingNotice, path)
        notices.append(notice)
    baseline_profile = _profile(_read(mapped, "profile", "baseline_profile"), "baseline_profile")
    _validate(mapped, ProfileMappingResult, "analysis")

    definitions = _rows(_read(analysis, "scenario_definitions", "scenarios"), "scenarios", limit=MAX_WHAT_IF_SCENARIOS)
    profiles = _rows(_read(analysis, "scenario_profiles", "scenarios"), "scenarios", limit=MAX_WHAT_IF_SCENARIOS)
    if len(definitions) != len(profiles):
        _invalid("scenarios")
    what_if = bool(definitions)
    calculation = _read(analysis, "calculation", "analysis")
    _model(calculation, WhatIfResult if what_if else MonteCarloResult, "analysis")
    market = _read(calculation, "metadata", "metadata")
    metadata = _metadata(market, what_if=what_if)
    baseline = _read(calculation, "baseline", "baseline") if what_if else calculation
    baseline_output = _summary(baseline, "baseline", market, source="provided" if what_if else "generated")
    scenarios = []
    if what_if:
        _text(_read(calculation, "model_version", "metadata.what_if_model_version"),
              "metadata.what_if_model_version", expected="retirement-what-if-v1")
        comparisons = _rows(_read(calculation, "scenarios", "scenarios"), "scenarios", limit=MAX_WHAT_IF_SCENARIOS)
        if len(comparisons) != len(definitions):
            _invalid("scenarios")
        for index, (definition, profile, pair) in enumerate(zip(definitions, profiles, comparisons)):
            path = f"scenarios[{index}]"
            _model(definition, WhatIfScenario, path)
            identifier = _text(_read(definition, "scenario_id", f"{path}.scenario_id"), f"{path}.scenario_id")
            changes = _changes(_read(definition, "changes", f"{path}.changes"), f"{path}.changes")
            _validate(definition, WhatIfScenario, path)
            profile_output = _profile(profile, f"{path}.profile")
            _model(pair, WhatIfScenarioResult, path)
            if _text(_read(pair, "scenario_id", f"{path}.scenario_id"), f"{path}.scenario_id") != identifier:
                _invalid(f"{path}.scenario_id")
            pair_baseline = _read(pair, "baseline", "baseline")
            if pair_baseline is not baseline:
                _summary(pair_baseline, "baseline", market, source="provided")
                if pair_baseline != baseline:
                    _invalid("baseline")
            result_output = _summary(_read(pair, "result", f"{path}.result"), f"{path}.result", market, source="provided")
            scenarios.append({"scenario_id": identifier, "changes": changes, "profile": profile_output,
                              "result": result_output, "delta": _delta(pair, f"{path}.delta")})
        _validate(calculation, WhatIfResult, "analysis")
    _validate(analysis, RetirementAnalysisResult, "analysis")
    return {
        "contract_version": version, "mode": analysis.mode, "mapping_version": mapping_version,
        "units": {"amount": "KRW", "monthly_amount": "KRW/month", "probability": "fraction",
                  "probability_delta": "pp", "annual_rate": "fraction", "age": "years"},
        "notices": notices, "baseline_profile": baseline_profile, "metadata": metadata,
        "baseline": baseline_output, "scenarios": scenarios,
    }


def analysis_error_to_dict(error: RetirementAnalysisError) -> dict[str, object]:
    """Serialize only validated diagnostics, not arbitrary exceptions or args.

    Revalidate all public diagnostic fields even when an error was manually
    constructed or altered. Invalid inputs raise ModelValidationError, not a
    recursive error payload. Traceback/context, unknown attributes and args are
    deliberately not read into the output.
    """
    if type(error) is not RetirementAnalysisError:
        raise ModelValidationError("error", "must be a RetirementAnalysisError") from None
    try:
        checked = RetirementAnalysisError(error.code, error.field_name, reason_code=error.reason_code)
        if error.stage != checked.stage or error.message != checked.message:
            raise ModelValidationError("error", "must have consistent diagnostic fields") from None
    except AttributeError:
        raise ModelValidationError("error", "must have complete diagnostic fields") from None
    return {"contract_version": _CONTRACT_VERSION, "error": {
        "code": checked.code, "stage": checked.stage, "field_name": checked.field_name,
        "reason_code": checked.reason_code, "message": checked.message,
    }}
