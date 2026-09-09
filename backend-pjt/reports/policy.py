"""Fail-closed loader for the approved shared financial calculation policy."""

from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
from typing import Any


POLICY_RELATIVE_PATH = Path(
    "contract-fixtures/financial-calculation/v1/policy.json"
)


class FinancialPolicyError(RuntimeError):
    pass


def _policy_path() -> Path:
    candidates = (
        Path(__file__).resolve().parents[2] / POLICY_RELATIVE_PATH,
        Path(__file__).resolve().parents[1] / POLICY_RELATIVE_PATH,
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FinancialPolicyError("approved financial calculation policy is missing")


@lru_cache(maxsize=1)
def load_financial_policy() -> dict[str, Any]:
    try:
        payload = json.loads(_policy_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FinancialPolicyError(
            "approved financial calculation policy cannot be loaded"
        ) from exc
    required = {
        "schema_version",
        "policy_version",
        "status",
        "approved_at",
        "valid_from",
        "valid_until",
        "country",
        "sources",
        "assumptions",
        "national_pension",
        "indicators",
        "reference_values",
    }
    if set(payload) != required:
        raise FinancialPolicyError("financial calculation policy schema mismatch")
    if payload["schema_version"] != "1.0" or payload["status"] != "approved":
        raise FinancialPolicyError("financial calculation policy is not approved")
    if not isinstance(payload["policy_version"], str) or not payload["policy_version"]:
        raise FinancialPolicyError("financial calculation policy version is missing")
    if not isinstance(payload["assumptions"], dict):
        raise FinancialPolicyError("financial calculation assumptions are invalid")
    if not isinstance(payload["national_pension"], dict):
        raise FinancialPolicyError("national pension policy is invalid")
    if not isinstance(payload["reference_values"], list):
        raise FinancialPolicyError("financial reference values are invalid")
    return payload


def active_policy_version() -> str:
    return str(load_financial_policy()["policy_version"])
