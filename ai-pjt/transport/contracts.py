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
    StrictInt,
    StrictStr,
)


CONTRACT_VERSION = "2.0"


class CustomerInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    saved_data: dict[str, Any]
    current_form: dict[str, Any]


class ChatV2Request(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["2.0"]
    request_id: StrictStr = Field(min_length=1, max_length=128)
    payload_digest: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    session_id: StrictInt = Field(gt=0)
    thread_id: StrictStr = Field(min_length=1, max_length=256)
    step_id: StrictStr = Field(min_length=1, max_length=128)
    message: StrictStr = Field(min_length=1, max_length=2000)
    deadline_at: AwareDatetime
    customer_info: CustomerInfo
    chart_payload: dict[str, Any] | None = None
    request_mode: Literal["chat", "error_review", "presentation_entry"] = "chat"

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
