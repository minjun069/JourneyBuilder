"""Read-only lookup for values in the approved shared calculation policy."""

from __future__ import annotations

from datetime import date
from functools import lru_cache
import json
from pathlib import Path
from typing import Any


POLICY_RELATIVE_PATH = Path(
    "contract-fixtures/financial-calculation/v1/policy.json"
)
POLICY_KEYS = {
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
SOURCE_KEYS = {
    "source_id",
    "publisher",
    "source_version",
    "as_of",
    "license",
}
REFERENCE_KEYS = {
    "reference_id",
    "age_min",
    "age_max",
    "value",
    "unit",
    "period",
    "population",
    "comparator",
    "source_id",
    "as_of",
}


class ApprovedFinancialPolicyError(RuntimeError):
    pass


class ApprovedReferenceValueNotFound(LookupError):
    pass


def _policy_path() -> Path:
    candidates = (
        Path(__file__).resolve().parents[2] / POLICY_RELATIVE_PATH,
        Path(__file__).resolve().parents[1] / POLICY_RELATIVE_PATH,
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise ApprovedFinancialPolicyError(
        "approved financial calculation policy is missing"
    )


@lru_cache(maxsize=1)
def load_approved_financial_policy() -> dict[str, Any]:
    try:
        payload = json.loads(_policy_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ApprovedFinancialPolicyError(
            "approved financial calculation policy cannot be loaded"
        ) from exc
    _validate_policy_schema(payload)
    return payload


def lookup_approved_reference_value(
    *,
    reference_id: str,
    age: int,
    period: str,
    population: str,
    effective_on: date | None = None,
) -> dict[str, Any]:
    """Return one exact approved value; never interpolate or estimate."""

    policy = load_approved_financial_policy()
    _validate_policy_window(policy, effective_on or date.today())
    sources = {source["source_id"]: source for source in policy["sources"]}
    matches = [
        item
        for item in policy["reference_values"]
        if item["reference_id"] == reference_id
        and item["period"] == period
        and item["population"] == population
        and item["age_min"] <= age
        and (item["age_max"] is None or age <= item["age_max"])
    ]
    if len(matches) != 1:
        raise ApprovedReferenceValueNotFound(
            "no unique approved reference value matches the requested criteria"
        )
    match = matches[0]
    source = sources.get(match["source_id"])
    if source is None or source["as_of"] != match["as_of"]:
        raise ApprovedFinancialPolicyError(
            "approved reference value provenance is invalid"
        )
    return {
        "reference_id": match["reference_id"],
        "age": age,
        "value": match["value"],
        "unit": match["unit"],
        "period": match["period"],
        "population": match["population"],
        "comparator": match["comparator"],
        "source_id": source["source_id"],
        "source_version": source["source_version"],
        "as_of": match["as_of"],
        "calculation_policy_version": policy["policy_version"],
    }


def _validate_policy_schema(payload: Any) -> None:
    if not isinstance(payload, dict) or set(payload) != POLICY_KEYS:
        raise ApprovedFinancialPolicyError(
            "financial calculation policy schema mismatch"
        )
    if payload["schema_version"] != "1.0" or payload["status"] != "approved":
        raise ApprovedFinancialPolicyError(
            "financial calculation policy is not approved"
        )
    if not isinstance(payload["policy_version"], str) or not payload["policy_version"]:
        raise ApprovedFinancialPolicyError(
            "financial calculation policy version is missing"
        )
    _parse_date(payload["approved_at"], "policy approved_at")
    _parse_date(payload["valid_from"], "policy valid_from")
    if payload["valid_until"] is not None:
        _parse_date(payload["valid_until"], "policy valid_until")
    if not isinstance(payload["sources"], list) or not payload["sources"]:
        raise ApprovedFinancialPolicyError("financial policy sources are invalid")
    source_ids: set[str] = set()
    for source in payload["sources"]:
        if not isinstance(source, dict) or set(source) != SOURCE_KEYS:
            raise ApprovedFinancialPolicyError("financial policy source schema mismatch")
        if any(not isinstance(source[key], str) or not source[key] for key in SOURCE_KEYS):
            raise ApprovedFinancialPolicyError("financial policy source is incomplete")
        if source["source_id"] in source_ids:
            raise ApprovedFinancialPolicyError("financial policy source ID is duplicated")
        _parse_date(source["as_of"], "source as_of")
        source_ids.add(source["source_id"])
    if not isinstance(payload["reference_values"], list):
        raise ApprovedFinancialPolicyError("financial reference values are invalid")
    for item in payload["reference_values"]:
        _validate_reference(item, source_ids)


def _validate_reference(item: Any, source_ids: set[str]) -> None:
    if not isinstance(item, dict) or set(item) != REFERENCE_KEYS:
        raise ApprovedFinancialPolicyError("financial reference value schema mismatch")
    string_fields = (
        "reference_id",
        "unit",
        "period",
        "population",
        "comparator",
        "source_id",
        "as_of",
    )
    if any(not isinstance(item[key], str) or not item[key] for key in string_fields):
        raise ApprovedFinancialPolicyError("financial reference value is incomplete")
    if item["source_id"] not in source_ids:
        raise ApprovedFinancialPolicyError("financial reference source is not approved")
    if isinstance(item["age_min"], bool) or not isinstance(item["age_min"], int):
        raise ApprovedFinancialPolicyError("financial reference age_min is invalid")
    if item["age_min"] < 0:
        raise ApprovedFinancialPolicyError("financial reference age_min is invalid")
    age_max = item["age_max"]
    if age_max is not None and (
        isinstance(age_max, bool)
        or not isinstance(age_max, int)
        or age_max < item["age_min"]
    ):
        raise ApprovedFinancialPolicyError("financial reference age_max is invalid")
    value = item["value"]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ApprovedFinancialPolicyError("financial reference value is not numeric")
    if item["comparator"] not in {"lt", "lte", "eq", "gte", "gt"}:
        raise ApprovedFinancialPolicyError("financial reference comparator is invalid")
    _parse_date(item["as_of"], "reference as_of")


def _validate_policy_window(policy: dict[str, Any], effective_on: date) -> None:
    valid_from = _parse_date(policy["valid_from"], "policy valid_from")
    valid_until_raw = policy["valid_until"]
    if valid_until_raw is not None and not isinstance(valid_until_raw, str):
        raise ApprovedFinancialPolicyError("policy valid_until is invalid")
    valid_until = (
        _parse_date(valid_until_raw, "policy valid_until")
        if valid_until_raw is not None
        else None
    )
    if effective_on < valid_from or (
        valid_until is not None and effective_on > valid_until
    ):
        raise ApprovedFinancialPolicyError(
            "financial calculation policy is outside its validity window"
        )


def _parse_date(value: Any, field_name: str) -> date:
    if not isinstance(value, str):
        raise ApprovedFinancialPolicyError(f"{field_name} is invalid")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ApprovedFinancialPolicyError(f"{field_name} is invalid") from exc
