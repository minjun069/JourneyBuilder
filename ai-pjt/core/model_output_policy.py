"""Deterministic inspection and redaction of model outputs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import re
from typing import Any

from core.masking import is_reference_token
from core.model_contracts import ModelOutputBlockedError, ModelPurpose


DIRECT_TEXT_PATTERNS = (
    re.compile(
        r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+"
        r"@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"
    ),
    re.compile(r"(?<!\d)(?:\+?82[- .]?)?0?1[016789][- .]?\d{3,4}[- .]?\d{4}(?!\d)"),
    re.compile(r"(?<!\d)\d{6}[- ]?[1-4]\d{6}(?!\d)"),
)
SECRET_TEXT_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(
        r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\."
        r"[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])"
    ),
)
FORBIDDEN_OUTPUT_FIELDS = frozenset({
    "account_identifier", "account_id", "account_number", "address",
    "authorization", "bearer", "cookie", "customer_id", "customer_name",
    "date_of_birth", "email", "email_address", "full_name", "name",
    "password", "phone", "phone_number", "resident_registration_number",
    "rrn", "secret", "token", "user_id", "user_name",
})


def block_forbidden_output(
    result: Any,
    *,
    purpose: ModelPurpose,
    operation_name: str | None = None,
) -> None:
    payload = serializable_output(result)
    if contains_forbidden_text(
        payload,
        purpose=purpose,
        operation_name=operation_name,
    ) or contains_forbidden_field(
        payload,
        purpose=purpose,
        operation_name=operation_name,
    ):
        raise ModelOutputBlockedError(
            "model output contained a direct identifier or secret"
        )
    from guardrails.boundary_policy import evaluate_financial_output

    decision = evaluate_financial_output(payload)
    if not decision.allowed:
        raise ModelOutputBlockedError(
            "model output contained unsupported financial certainty",
            reason_code=decision.reason_code,
        )


def serializable_output(result: Any) -> Any:
    dumper = getattr(result, "model_dump", None)
    if callable(dumper):
        return dumper(mode="json")
    if isinstance(result, (str, bytes, int, float, bool)) or result is None:
        return result
    if isinstance(result, Mapping):
        return dict(result)
    if isinstance(result, Sequence):
        return list(result)
    raise ModelOutputBlockedError("model output could not be inspected safely")


def redact_direct_text(value: Any) -> Any:
    if isinstance(value, str):
        for pattern in DIRECT_TEXT_PATTERNS:
            value = pattern.sub("[DIRECT_IDENTIFIER]", value)
        for pattern in SECRET_TEXT_PATTERNS:
            value = pattern.sub("[SECRET]", value)
        return value
    if isinstance(value, Mapping):
        return {key: redact_direct_text(child) for key, child in value.items()}
    if isinstance(value, list):
        return [redact_direct_text(child) for child in value]
    return value


def contains_forbidden_text(
    value: Any,
    *,
    purpose: ModelPurpose | None = None,
    operation_name: str | None = None,
) -> bool:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        if any(pattern.search(value) for pattern in (*DIRECT_TEXT_PATTERNS, *SECRET_TEXT_PATTERNS)):
            return True
        stripped = value.strip()
        if stripped.startswith(("{", "[")):
            try:
                decoded = json.loads(stripped)
            except json.JSONDecodeError:
                return False
            return contains_forbidden_field(
                decoded,
                purpose=purpose,
                operation_name=operation_name,
            )
        return False
    if isinstance(value, Mapping):
        return any(
            contains_forbidden_text(
                child,
                purpose=purpose,
                operation_name=operation_name,
            )
            for child in value.values()
        )
    if isinstance(value, Sequence):
        return any(
            contains_forbidden_text(
                child,
                purpose=purpose,
                operation_name=operation_name,
            )
            for child in value
        )
    return False


def contains_forbidden_field(
    value: Any,
    *,
    purpose: ModelPurpose | None = None,
    operation_name: str | None = None,
    path: tuple[str | int, ...] = (),
) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "_")
            child_path = (*path, str(key))
            forbidden = (
                (
                    normalized in FORBIDDEN_OUTPUT_FIELDS
                    and not _is_allowed_business_name_path(
                        child_path,
                        purpose=purpose,
                        operation_name=operation_name,
                    )
                )
                or normalized.endswith("_email")
                or normalized.endswith("_phone")
                or normalized.endswith("_account_number")
                or normalized.endswith("_api_key")
                or normalized.endswith("_password")
                or normalized.endswith("_secret")
                or normalized.endswith("_token")
            )
            if forbidden and child not in (None, "") and not is_reference_token(child):
                return True
            if contains_forbidden_field(
                child,
                purpose=purpose,
                operation_name=operation_name,
                path=child_path,
            ):
                return True
        return False
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(
            contains_forbidden_field(
                child,
                purpose=purpose,
                operation_name=operation_name,
                path=(*path, index),
            )
            for index, child in enumerate(value)
        )
    return False


def _is_allowed_business_name_path(
    path: tuple[str | int, ...],
    *,
    purpose: ModelPurpose | None,
    operation_name: str | None,
) -> bool:
    """Allow form-row labels that the field-registry validator checks next."""

    if (
        purpose is not ModelPurpose.INPUT_ASSIST
        or operation_name != "input_assist.extract"
        or not path
        or path[-1] != "name"
    ):
        return False
    for index, part in enumerate(path[:-3]):
        if (
            part == "changes"
            and isinstance(path[index + 1], int)
            and path[index + 2] == "value"
        ):
            return True
    return False
