"""Validated execution/output diagnostics, without raw values or HTTP policy."""

from __future__ import annotations

from re import fullmatch

from .input_adapter import _ERROR_MESSAGES
from .models import ModelValidationError
from .monte_carlo import MonteCarloCalculationError
from .what_if_engine import MAX_WHAT_IF_SCENARIOS, WhatIfCalculationError


_INDEX = r"\[(?:0|[1-9][0-9]*)\]"
_SCENARIO = r"scenarios\[[0-9]\]"
_EXPENSE = rf"retirement_expenses(?:{_INDEX}(?:\.(?:from_age|to_age|monthly_amount))?)?"
_INPUT_PATHS = (
    r"saved_data",
    r"basic_info(?:\.(?:age|retirement_age|end_age))?",
    rf"confirmed_empty_sections(?:{_INDEX})?",
    rf"(?:financial_assets|pension_assets|goal_periods|real_estate_assets|liabilities|goal_milestones)"
    rf"(?:{_INDEX}(?:\.id)?)?",
    rf"financial_assets{_INDEX}\.(?:asset_type|current_value|monthly_savings)",
    rf"pension_assets{_INDEX}\.(?:receive_method|pension_start_age|expected_amount|pension_period)",
    rf"goal_periods{_INDEX}\.(?:phase|from_age|to_age|items(?:{_INDEX}(?:\.(?:id|amount))?)?)",
)
_ASSUMPTION_PATHS = (
    r"assumptions(?:\.(?:annual_return_mean|annual_return_volatility|annual_inflation_rate|num_paths|seed))?",
)
_SCENARIO_PATHS = (
    r"scenarios",
    rf"{_SCENARIO}(?:\.(?:scenario_id|changes(?:\.(?:monthly_savings|retirement_age|{_EXPENSE}))?))?",
)
_APPLICATION_PATHS = (
    rf"{_SCENARIO}\.changes(?:\.(?:monthly_savings|retirement_age|retirement_expenses))?",
)
_CALCULATION_FIELDS = {
    "return_generation": (
        "log_return_mean", "log_return_volatility", "standard_normal", "log_return", "annual_returns",
    ),
    "cashflow": (
        "liquid_assets", "investment_return", "savings", "pension_income", "annual_expenses",
        "inflation_factor", "expenses", "balance_after_return", "balance_after_savings",
        "balance_after_pension", "closing_balance",
    ),
    "aggregation": ("num_paths", "asset_percentiles", "final_shortfall_percentiles"),
    "comparison": tuple(f"{field}.{q}" for field in ("asset_deltas", "final_shortfall_delta")
                        for q in ("p10", "p50", "p90")),
}
_CALCULATION_PATHS = frozenset({"calculation"} | {
    f"calculation.market.return_generation.{field}"
    for field in _CALCULATION_FIELDS["return_generation"]
} | {
    f"calculation.{scope}.{stage}.{field}"
    for scope in ("baseline", *(f"scenarios[{i}]" for i in range(MAX_WHAT_IF_SCENARIOS)))
    for stage in ("cashflow", "aggregation", "comparison")
    if stage != "comparison" or scope != "baseline"
    for field in _CALCULATION_FIELDS[stage]
})

# Output errors accept only paths from the v1 payload. Numeric errors cannot
# claim a text field or an arbitrary object/key as their source.
_PROFILE_ROOT = rf"(?:baseline_profile|{_SCENARIO}\.profile)"
_SUMMARY_ROOT = rf"(?:baseline|{_SCENARIO}\.result)"
_DELTA_ROOT = rf"{_SCENARIO}\.delta"
_AMOUNT_LEAF = r"(?:p10|p50|p90)"
_ASSUMPTION_LEAF = r"(?:annual_return_mean|annual_return_volatility|annual_inflation_rate|num_paths|seed)"
_NUMERIC_OUTPUT_PATHS = (
    rf"{_PROFILE_ROOT}\.(?:current_age|retirement_age|end_age|liquid_assets|monthly_savings)",
    rf"{_PROFILE_ROOT}\.retirement_expenses{_INDEX}\.(?:from_age|to_age|monthly_amount)",
    rf"{_PROFILE_ROOT}\.pension_incomes{_INDEX}\.(?:start_age|end_age|monthly_amount)",
    rf"{_SCENARIO}\.changes\.(?:monthly_savings|retirement_age)",
    rf"{_SCENARIO}\.changes\.retirement_expenses{_INDEX}\.(?:from_age|to_age|monthly_amount)",
    r"metadata\.(?:current_age|end_age|num_paths|effective_seed)",
    rf"metadata\.assumptions\.{_ASSUMPTION_LEAF}",
    rf"{_SUMMARY_ROOT}\.(?:num_paths|success_count|depletion_count|never_depleted_count|success_probability|depletion_probability)",
    rf"{_SUMMARY_ROOT}\.asset_percentiles{_INDEX}\.(?:age|balance\.{_AMOUNT_LEAF})",
    rf"{_SUMMARY_ROOT}\.first_depletion_counts{_INDEX}\.(?:age|count)",
    rf"{_SUMMARY_ROOT}\.(?:final_balance_percentiles|final_shortfall_percentiles)\.{_AMOUNT_LEAF}",
    rf"{_SUMMARY_ROOT}\.execution\.effective_seed",
    rf"{_DELTA_ROOT}\.(?:success_count_delta|depletion_count_delta|never_depleted_count_delta|success_probability_delta_pp|depletion_probability_delta_pp)",
    rf"{_DELTA_ROOT}\.asset_deltas{_INDEX}\.(?:age|balance\.{_AMOUNT_LEAF})",
    rf"{_DELTA_ROOT}\.first_depletion_deltas{_INDEX}\.(?:age|count)",
    rf"{_DELTA_ROOT}\.(?:final_balance_delta|final_shortfall_delta)\.{_AMOUNT_LEAF}",
)
_OUTPUT_PATHS = _NUMERIC_OUTPUT_PATHS + (
    r"analysis|contract_version|mode|mapping_version|scenarios",
    r"units(?:\.(?:amount|monthly_amount|probability|probability_delta|annual_rate|age))?",
    rf"notices(?:{_INDEX}(?:\.(?:code|field_name))?)?",
    rf"{_PROFILE_ROOT}(?:\.(?:retirement_expenses|pension_incomes)(?:{_INDEX})?)?",
    rf"{_SCENARIO}(?:\.scenario_id|\.changes(?:\.retirement_expenses(?:{_INDEX})?)?)?",
    r"metadata(?:\.(?:assumptions|path_source|return_model|rng_algorithm|mc_model_version|what_if_model_version|python_version|python_implementation))?",
    rf"{_SUMMARY_ROOT}(?:\.(?:asset_percentiles(?:{_INDEX}(?:\.balance)?)?|first_depletion_counts(?:{_INDEX})?|final_balance_percentiles|final_shortfall_percentiles|execution(?:\.path_source)?))?",
    rf"{_DELTA_ROOT}(?:\.(?:asset_deltas(?:{_INDEX}(?:\.balance)?)?|first_depletion_deltas(?:{_INDEX})?|final_balance_delta|final_shortfall_delta))?",
)
_ERROR_SPECS = {
    "input_mapping_failed": (
        "input", "Retirement input mapping failed.", tuple(_ERROR_MESSAGES), _INPUT_PATHS,
    ),
    "assumption_mapping_failed": (
        "assumptions", "Simulation assumption mapping failed.",
        ("missing_field", "invalid_type", "invalid_value", "unknown_field"), _ASSUMPTION_PATHS,
    ),
    "scenario_mapping_failed": (
        "scenarios", "What-if scenario mapping failed.",
        ("missing_field", "missing_value", "invalid_type", "invalid_value", "duplicate_id", "unknown_field"),
        _SCENARIO_PATHS,
    ),
    "scenario_application_failed": (
        "scenarios", "What-if scenario application failed.", ("invalid_profile",), _APPLICATION_PATHS,
    ),
    "calculation_failed": ("calculation", "Retirement calculation failed.", (None,), ()),
    "invalid_analysis_result": ("output", "Analysis result is invalid.", (None,), _OUTPUT_PATHS),
    "unsafe_json_number": ("output", "Analysis contains an unsafe JSON number.", (None,), _NUMERIC_OUTPUT_PATHS),
}


class RetirementAnalysisError(Exception):
    """An execution/output error with validated, read-only diagnostic properties.

    Code determines stage/message. Paths contain known keys and indices only;
    reason codes must be appropriate for the stage. Rejected constructor values
    raise ModelValidationError without echoing them. This is not an HTTP error.
    """

    __slots__ = ("_code", "_field_name", "_reason_code")

    def __init__(self, code: str, field_name: str, *, reason_code: str | None = None) -> None:
        if not isinstance(code, str) or code not in _ERROR_SPECS:
            raise ModelValidationError("error.code", "must be a supported analysis error code") from None
        _, _, reasons, patterns = _ERROR_SPECS[code]
        if (reason_code is not None and not isinstance(reason_code, str)) or reason_code not in reasons:
            raise ModelValidationError("error.reason_code", "must match the analysis error code") from None
        valid_path = False
        if isinstance(field_name, str):
            valid_path = (field_name in _CALCULATION_PATHS if code == "calculation_failed"
                          else any(fullmatch(p, field_name) for p in patterns))
        if not valid_path:
            raise ModelValidationError("error.field_name", "must be an allowed path for the error code") from None
        self._code, self._field_name, self._reason_code = code, field_name, reason_code
        super().__init__(f"{field_name}: {self.message} ({code})")

    @property
    def code(self) -> str:
        return self._code

    @property
    def stage(self) -> str:
        return _ERROR_SPECS[self.code][0]

    @property
    def field_name(self) -> str:
        return self._field_name

    @property
    def reason_code(self) -> str | None:
        return self._reason_code

    @property
    def message(self) -> str:
        return _ERROR_SPECS[self.code][1]


def _calculation_path(error: MonteCarloCalculationError | WhatIfCalculationError) -> str:
    # Existing numeric errors are not validated constructors. Inspect only known
    # fields and never interpolate an untrusted stage, field, scope, or index.
    stage, field = error.stage, error.field_name
    if (not isinstance(stage, str) or stage not in _CALCULATION_FIELDS
            or not isinstance(field, str) or field not in _CALCULATION_FIELDS[stage]):
        return "calculation"
    if isinstance(error, WhatIfCalculationError):
        if error.scope == "market" and error.scenario_index is None:
            scope = "market"
        elif error.scope == "baseline" and error.scenario_index is None:
            scope = "baseline"
        elif (error.scope == "scenario" and isinstance(error.scenario_index, int)
              and not isinstance(error.scenario_index, bool)
              and 0 <= error.scenario_index < MAX_WHAT_IF_SCENARIOS):
            scope = f"scenarios[{error.scenario_index}]"
        else:
            return "calculation"
    else:
        scope = "market" if stage == "return_generation" else "baseline"
    path = f"calculation.{scope}.{stage}.{field}"
    return path if path in _CALCULATION_PATHS else "calculation"
