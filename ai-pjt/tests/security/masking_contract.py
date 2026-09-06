"""P13 synthetic masking contract shared by future production-bound tests.

This module deliberately does not import production code. It owns only
synthetic cases and assertions that F11 can bind to real serialization
checkpoints after the production masking interface is published.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable


class DataClass(str, Enum):
    """Project data classes relevant to the P13 masking boundary."""

    DIRECT_IDENTIFIER = "direct_identifier"
    QUASI_IDENTIFIER = "quasi_identifier"
    PROJECT_HIGH_RISK_FINANCIAL = "project_high_risk_financial"
    OPERATIONAL_INFORMATION = "operational_information"


class Sink(str, Enum):
    """Serialization checkpoints at which raw sentinel leakage is inspected."""

    MODEL_INPUT = "model_input"
    MODEL_OUTPUT = "model_output"
    APPLICATION_LOG = "application_log"
    TRACE_EXPORT = "trace_export"


ALL_SINKS = frozenset(Sink)
OBSERVABILITY_SINKS = frozenset({Sink.APPLICATION_LOG, Sink.TRACE_EXPORT})


@dataclass(frozen=True)
class MaskingSentinel:
    """One synthetic value and the sinks where its raw form must not appear."""

    case_id: str
    field_name: str
    data_class: DataClass
    raw_value: str
    forbidden_fragments: tuple[str, ...]
    forbidden_sinks: frozenset[Sink]
    safe_example: str


MASKING_SENTINELS = (
    MaskingSentinel(
        case_id="direct-email",
        field_name="email",
        data_class=DataClass.DIRECT_IDENTIFIER,
        raw_value="p13-user@example.invalid",
        forbidden_fragments=("p13-user@example.invalid",),
        forbidden_sinks=ALL_SINKS,
        safe_example="[DIRECT_IDENTIFIER]",
    ),
    MaskingSentinel(
        case_id="direct-phone",
        field_name="phone_number",
        data_class=DataClass.DIRECT_IDENTIFIER,
        raw_value="010-0000-0000",
        forbidden_fragments=("010-0000-0000", "01000000000"),
        forbidden_sinks=ALL_SINKS,
        safe_example="[DIRECT_IDENTIFIER]",
    ),
    MaskingSentinel(
        case_id="direct-account",
        field_name="account_identifier",
        data_class=DataClass.DIRECT_IDENTIFIER,
        raw_value="P13-ACCOUNT-0001",
        forbidden_fragments=("P13-ACCOUNT-0001",),
        forbidden_sinks=ALL_SINKS,
        safe_example="[DIRECT_IDENTIFIER]",
    ),
    MaskingSentinel(
        case_id="quasi-profile",
        field_name="age_job_family_profile",
        data_class=DataClass.QUASI_IDENTIFIER,
        raw_value="P13-QI-AGE47-TEACHER-FAMILY3",
        forbidden_fragments=("P13-QI-AGE47-TEACHER-FAMILY3",),
        forbidden_sinks=OBSERVABILITY_SINKS,
        safe_example="[QUASI_IDENTIFIER_BUCKETED]",
    ),
    MaskingSentinel(
        case_id="financial-income",
        field_name="monthly_income",
        data_class=DataClass.PROJECT_HIGH_RISK_FINANCIAL,
        raw_value="P13-FIN-INCOME-7310000",
        forbidden_fragments=(
            "P13-FIN-INCOME-7310000",
            "7310000",
            "7,310,000",
        ),
        forbidden_sinks=OBSERVABILITY_SINKS,
        safe_example="[FINANCIAL_VALUE_MASKED]",
    ),
    MaskingSentinel(
        case_id="financial-assets",
        field_name="total_assets",
        data_class=DataClass.PROJECT_HIGH_RISK_FINANCIAL,
        raw_value="P13-FIN-ASSETS-248600000",
        forbidden_fragments=(
            "P13-FIN-ASSETS-248600000",
            "248600000",
            "248,600,000",
        ),
        forbidden_sinks=OBSERVABILITY_SINKS,
        safe_example="[FINANCIAL_VALUE_BUCKETED]",
    ),
    MaskingSentinel(
        case_id="financial-debt",
        field_name="total_debt",
        data_class=DataClass.PROJECT_HIGH_RISK_FINANCIAL,
        raw_value="P13-FIN-DEBT-73400000",
        forbidden_fragments=(
            "P13-FIN-DEBT-73400000",
            "73400000",
            "73,400,000",
        ),
        forbidden_sinks=OBSERVABILITY_SINKS,
        safe_example="[FINANCIAL_VALUE_BUCKETED]",
    ),
    MaskingSentinel(
        case_id="operational-request",
        field_name="request_id",
        data_class=DataClass.OPERATIONAL_INFORMATION,
        raw_value="P13-REQUEST-RAW-0001",
        forbidden_fragments=("P13-REQUEST-RAW-0001",),
        forbidden_sinks=ALL_SINKS,
        safe_example="[PSEUDONYMIZED_REQUEST]",
    ),
)


def sentinels_for_sink(
    sink: Sink,
    sentinels: Iterable[MaskingSentinel] = MASKING_SENTINELS,
) -> tuple[MaskingSentinel, ...]:
    """Return sentinels whose raw values are prohibited at ``sink``."""

    return tuple(case for case in sentinels if sink in case.forbidden_sinks)


def find_raw_sentinel_leaks(
    rendered: str | bytes,
    sink: Sink,
    sentinels: Iterable[MaskingSentinel] = MASKING_SENTINELS,
) -> tuple[str, ...]:
    """Return stable leak identifiers found in a serialized checkpoint value."""

    text = (
        rendered.decode("utf-8", errors="replace")
        if isinstance(rendered, bytes)
        else rendered
    )
    leaks: list[str] = []
    for case in sentinels_for_sink(sink, sentinels):
        for fragment in case.forbidden_fragments:
            if fragment in text:
                leaks.append(f"{case.case_id}:{fragment}")
    return tuple(leaks)


def assert_no_raw_sentinels(
    rendered: str | bytes,
    sink: Sink,
    sentinels: Iterable[MaskingSentinel] = MASKING_SENTINELS,
) -> None:
    """Fail when a raw synthetic value reaches a sink where it is prohibited."""

    leaks = find_raw_sentinel_leaks(rendered, sink, sentinels)
    if leaks:
        joined = ", ".join(leaks)
        raise AssertionError(f"{sink.value} contains raw masking sentinels: {joined}")
