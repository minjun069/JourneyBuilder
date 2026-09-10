"""Frozen v2 request, callback, and error contracts."""

from __future__ import annotations

from datetime import datetime
import secrets
from typing import Any, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    model_validator,
)


CONTRACT_VERSION = "2.0"


class CustomerInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    saved_data: dict[str, Any]
    current_form: dict[str, Any]


class ToolFieldSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: StrictStr
    section: StrictStr
    path: StrictStr
    value_type: StrictStr
    unit: StrictStr
    constraints: dict[str, Any]
    cardinality: StrictStr
    sensitivity: StrictStr
    writable_by_ai: StrictBool
    nullable: StrictBool
    label: StrictStr
    operations: list[StrictStr]


class ReadyDashboardMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    availability: Literal["ready"]
    metric_id: Literal["retirement_gap"]
    value: StrictInt = Field(ge=0)
    unit: Literal["KRW"]
    report_id: StrictInt = Field(gt=0)
    report_version: StrictInt = Field(gt=0)
    calculation_policy_version: StrictStr = Field(min_length=1)
    generated_at: AwareDatetime


class UnavailableDashboardMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    availability: Literal["unavailable"]
    reason: Literal[
        "report_not_found",
        "report_processing",
        "report_failed",
        "report_stale",
        "policy_version_mismatch",
        "report_schema_invalid",
    ]


class RetirementScenarioSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: StrictStr
    status: Literal["ready"]
    version: StrictInt = Field(gt=0)
    generated_at: AwareDatetime
    assumptions: dict[str, Any]
    metrics: dict[str, Any]


class AvailableReportSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_id: StrictInt = Field(gt=0)
    report_type: StrictStr
    version: StrictInt = Field(gt=0)
    status: Literal["processing", "ready", "failed"]
    generated_at: AwareDatetime


class ToolContextSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"]
    session_id: StrictInt = Field(gt=0)
    step_id: StrictStr = Field(min_length=1)
    generated_at: AwareDatetime
    field_specs: list[ToolFieldSpec]
    dashboard_metrics: dict[
        StrictStr,
        ReadyDashboardMetric | UnavailableDashboardMetric,
    ]
    retirement_scenarios: dict[StrictStr, RetirementScenarioSnapshot]
    available_reports: list[AvailableReportSnapshot]
    section_completion: dict[StrictStr, StrictBool]

    @model_validator(mode="after")
    def validate_allowlists(self):
        if set(self.dashboard_metrics) - {"retirement_gap"}:
            raise ValueError("unsupported dashboard metric")
        for metric_id, metric in self.dashboard_metrics.items():
            if isinstance(metric, ReadyDashboardMetric) and metric.metric_id != metric_id:
                raise ValueError("dashboard metric key mismatch")
        allowed_sections = {
            "basic_info",
            "financial_mindset",
            "financial_satisfaction",
            "financial_assets",
            "pension_assets",
            "real_estate_assets",
            "liabilities",
            "incomes",
            "expenses",
            "financial_goals",
        }
        if set(self.section_completion) - allowed_sections:
            raise ValueError("unsupported section completion key")
        if any(field.step_id != self.step_id for field in self.field_specs):
            raise ValueError("field spec step mismatch")
        return self


class ChatV2Request(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["2.0"]
    request_id: StrictStr = Field(min_length=1, max_length=128)
    session_id: StrictInt = Field(gt=0)
    thread_id: StrictStr = Field(min_length=1, max_length=256)
    step_id: StrictStr = Field(min_length=1, max_length=128)
    message: StrictStr = Field(min_length=1, max_length=2000)
    deadline_at: AwareDatetime
    customer_info: CustomerInfo
    field_registry: list[dict[str, Any]]
    tool_context: ToolContextSnapshot | None = None
    chart_payload: dict[str, Any] | None = None
    request_mode: Literal["chat", "error_review"] = "chat"
    review_trigger: Literal["save", "user_request"] | None = None
    elicit_action: Literal["start", "resume"] | None = None

    @model_validator(mode="after")
    def validate_tool_context_binding(self):
        if self.tool_context is not None and (
            self.tool_context.session_id != self.session_id
            or self.tool_context.step_id != self.step_id
        ):
            raise ValueError("tool_context request binding mismatch")
        if self.request_mode == "error_review" and self.review_trigger is None:
            raise ValueError("error review requires review_trigger")
        if self.request_mode == "chat" and self.review_trigger is not None:
            raise ValueError("review_trigger is only valid for error review")
        if self.elicit_action is not None and (
            self.request_mode != "chat" or self.step_id != "financial-mindset"
        ):
            raise ValueError("elicit_action request binding mismatch")
        return self


class ChatAccepted(BaseModel):
    contract_version: Literal["2.0"] = CONTRACT_VERSION
    request_id: str
    status: Literal["accepted"] = "accepted"


class CallbackError(BaseModel):
    code: Literal[
        "LLM_TIMEOUT",
        "UPSTREAM_UNAVAILABLE",
        "OUTPUT_SCHEMA_INVALID",
        "POLICY_BLOCKED",
        "INTERNAL_ERROR",
    ]
    message: str
    retryable: bool


class CompletedCallback(BaseModel):
    contract_version: Literal["2.0"] = CONTRACT_VERSION
    request_id: str
    session_id: int
    thread_id: str
    status: Literal["completed"] = "completed"
    completed_at: datetime
    output: dict[str, Any]


class FailedCallback(BaseModel):
    contract_version: Literal["2.0"] = CONTRACT_VERSION
    request_id: str
    session_id: int
    thread_id: str
    status: Literal["failed"] = "failed"
    completed_at: datetime
    error: CallbackError


class ServiceContractError(Exception):
    """Transport error that maps to the frozen service error envelope."""

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retryable = retryable

    def envelope(self) -> dict[str, Any]:
        return {
            "success": False,
            "error": {
                "code": self.code,
                "message": self.message,
                "retryable": self.retryable,
            },
        }


def verify_service_authorization(value: str | None, expected_token: str) -> None:
    scheme, separator, supplied = (value or "").partition(" ")
    valid = (
        separator == " "
        and scheme == "Bearer"
        and bool(expected_token)
        and secrets.compare_digest(supplied, expected_token)
    )
    if not valid:
        raise ServiceContractError(
            status_code=401,
            code="SERVICE_UNAUTHORIZED",
            message="서비스 인증에 실패했습니다.",
        )
