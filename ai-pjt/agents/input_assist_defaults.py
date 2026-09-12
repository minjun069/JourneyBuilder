"""Deterministic Input Assist defaults from the approved domain policy."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any, Mapping

from tools.financial_policy import (
    ApprovedFinancialPolicyError,
    ApprovedReferenceValueNotFound,
    lookup_approved_reference_value,
)


FIXTURE_RELATIVE_PATH = Path(
    "contract-fixtures/ai-lifecycle/v2/input-assist-default-values.json"
)
POLICY_RELATIVE_PATH = Path("contract-fixtures/financial-calculation/v1/policy.json")


class InputAssistDefaultPolicyError(ValueError):
    pass


class ReferenceTargetNotAllowed(ValueError):
    """A sealed reference artifact cannot target the approved existing row."""


REFERENCE_BINDING_FIELDS = frozenset({
    "reference_id",
    "age",
    "value",
    "unit",
    "period",
    "population",
    "comparator",
    "source_id",
    "source_version",
    "as_of",
    "calculation_policy_version",
})


def _repo_file(relative_path: Path) -> Path:
    module_path = Path(__file__).resolve()
    candidates = (
        module_path.parents[2] / relative_path,
        module_path.parents[1] / relative_path,
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def load_default_policy(path: Path | None = None) -> dict[str, Any]:
    fixture_path = path or _repo_file(FIXTURE_RELATIVE_PATH)
    try:
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InputAssistDefaultPolicyError("default policy could not be loaded") from exc
    if payload.get("fixture_kind") != "input-assist-default-values":
        raise InputAssistDefaultPolicyError("unsupported default policy")
    if payload.get("schema_version") != "1.1":
        raise InputAssistDefaultPolicyError("unsupported default policy schema")
    return payload


def _plan_end_age() -> int:
    try:
        payload = json.loads(_repo_file(POLICY_RELATIVE_PATH).read_text(encoding="utf-8"))
        value = payload["assumptions"]["plan_end_age"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise InputAssistDefaultPolicyError("financial policy plan_end_age unavailable") from exc
    if isinstance(value, bool) or not isinstance(value, int):
        raise InputAssistDefaultPolicyError("financial policy plan_end_age invalid")
    return value


def _present(value: Any) -> bool:
    return value is not None and value != ""


def _has_identity(value: Mapping[str, Any]) -> bool:
    return value.get("id") is not None or bool(value.get("client_item_id"))


def _explicit_household_benchmark(message: str) -> bool:
    compact = message.replace(" ", "")
    return any(token in compact for token in ("가구평균", "가계평균", "전국가구", "가구벤치마크"))


def _retirement_household(message: str) -> str | None:
    compact = message.replace(" ", "")
    if any(token in compact for token in ("1인노후", "1인은퇴", "혼자노후", "개인노후")):
        return "single"
    if any(token in compact for token in ("부부노후", "부부은퇴", "2인노후")):
        return "couple"
    return None


def _explicit_workplace_retirement(message: str) -> bool:
    compact = message.replace(" ", "")
    return any(token in compact for token in ("사업장정년", "법정정년", "직장정년"))


def _birth_year(message: str) -> int | None:
    match = re.search(r"(?<!\d)((?:19|20)\d{2})\s*년(?:생|출생)", message)
    return int(match.group(1)) if match else None


def _pension_start_age(year: int) -> int | None:
    if 1953 <= year <= 1956:
        return 61
    if 1957 <= year <= 1960:
        return 62
    if 1961 <= year <= 1964:
        return 63
    if 1965 <= year <= 1968:
        return 64
    if year >= 1969:
        return 65
    return None


def _pension_eligibility_confirmed(message: str) -> bool:
    compact = message.replace(" ", "")
    return any(token in compact for token in ("노령연금수급요건확인", "노령연금수급요건충족"))


def _metadata(
    policy: Mapping[str, Any],
    *,
    source_id: str,
    assumption_level: str,
    transform: str,
    today: date,
) -> dict[str, Any] | None:
    source = policy.get("sources", {}).get(source_id)
    if not isinstance(source, Mapping):
        return None
    try:
        expires_on = date.fromisoformat(str(source["expires_on"]))
    except (KeyError, ValueError):
        return None
    if today > expires_on:
        return None
    return {
        "policy_version": policy["policy_version"],
        "source": {
            "source_id": source_id,
            "title": source["title"],
            "reference_period": source["reference_period"],
        },
        "retrieved_at": policy["retrieved_at"],
        "transform": transform,
        "assumption_level": assumption_level,
    }


def _proposal(
    *,
    section: str,
    path: str,
    value: Any,
    item: Mapping[str, Any] | None = None,
    parent: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    result = {
        "section": section,
        "path": path,
        "operation": "update" if item is not None else "set",
        "excerpt": "승인된 기본값 정책",
        "policy_value": deepcopy(value),
        "policy_metadata": deepcopy(dict(metadata)),
    }
    if item is not None:
        if item.get("id") is not None:
            result["item_id"] = item["id"]
        elif item.get("client_item_id"):
            result["client_item_id"] = item["client_item_id"]
    if parent is not None:
        if parent.get("id") is not None:
            result["parent_item_id"] = parent["id"]
        elif parent.get("client_item_id"):
            result["parent_client_item_id"] = parent["client_item_id"]
    return result


def _reference_metadata(
    reference: Mapping[str, Any],
    *,
    title: str,
) -> dict[str, Any]:
    return {
        "policy_version": reference["calculation_policy_version"],
        "source": {
            "source_id": reference["source_id"],
            "title": title,
            "reference_period": reference["period"],
        },
        "retrieved_at": reference["as_of"],
        "transform": "승인 기준값을 원 단위 월 금액으로 직접 매핑",
        "assumption_level": "household_benchmark",
        "reference": {
            field: reference[field]
            for field in REFERENCE_BINDING_FIELDS
            if field != "value"
        },
    }


def _household_references(
    policy: Mapping[str, Any],
    *,
    effective_on: date,
) -> dict[str, dict[str, Any]]:
    mapping = policy.get("household_expense_reference_ids")
    if not isinstance(mapping, Mapping):
        raise InputAssistDefaultPolicyError("household reference mapping unavailable")
    references: dict[str, dict[str, Any]] = {}
    try:
        for name, reference_id in mapping.items():
            if not isinstance(name, str) or not isinstance(reference_id, str):
                raise InputAssistDefaultPolicyError("household reference mapping invalid")
            references[name] = lookup_approved_reference_value(
                reference_id=reference_id,
                age=0,
                period="monthly",
                population="kr_general_households_1plus",
                effective_on=effective_on,
            )
    except (ApprovedFinancialPolicyError, ApprovedReferenceValueNotFound) as exc:
        raise InputAssistDefaultPolicyError("household reference policy unavailable") from exc
    return references


def build_bound_reference_proposals(
    *,
    step_id: str,
    message: str,
    current_form: Mapping[str, Any],
    bound_inputs: Mapping[str, Any],
    user_requested_change: bool,
    today: date | None = None,
    policy: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Map one sealed household reference only to its approved existing target."""

    if step_id != "financial-goals" or not user_requested_change:
        raise ReferenceTargetNotAllowed("reference_target_not_allowed")
    if not REFERENCE_BINDING_FIELDS.issubset(bound_inputs):
        raise ReferenceTargetNotAllowed("reference_target_not_allowed")

    active_policy = dict(policy or load_default_policy())
    mapping = active_policy.get("household_expense_reference_ids")
    reference_id = bound_inputs.get("reference_id")
    if not isinstance(mapping, Mapping) or reference_id not in mapping.values():
        raise ReferenceTargetNotAllowed("reference_target_not_allowed")
    expected_name = next(
        name for name, mapped_id in mapping.items() if mapped_id == reference_id
    )

    try:
        approved = lookup_approved_reference_value(
            reference_id=str(reference_id),
            age=bound_inputs["age"],
            period=bound_inputs["period"],
            population=bound_inputs["population"],
            effective_on=today or date.today(),
        )
    except (ApprovedFinancialPolicyError, ApprovedReferenceValueNotFound, TypeError):
        raise ReferenceTargetNotAllowed("reference_target_not_allowed") from None
    if any(bound_inputs.get(field) != approved.get(field) for field in REFERENCE_BINDING_FIELDS):
        raise ReferenceTargetNotAllowed("reference_target_not_allowed")
    if (
        approved["unit"] != "KRW"
        or approved["period"] != "monthly"
        or approved["population"] != "kr_general_households_1plus"
        or approved["comparator"] != "eq"
    ):
        raise ReferenceTargetNotAllowed("reference_target_not_allowed")

    candidates: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    periods = current_form.get("goal_periods")
    for period in periods if isinstance(periods, list) else []:
        if (
            not isinstance(period, Mapping)
            or not _has_identity(period)
            or period.get("phase") != "before"
        ):
            continue
        items = period.get("items")
        for item in items if isinstance(items, list) else []:
            if (
                isinstance(item, Mapping)
                and _has_identity(item)
                and item.get("name") == expected_name
                and not _present(item.get("amount"))
            ):
                candidates.append((period, item))
    if len(candidates) != 1:
        raise ReferenceTargetNotAllowed("reference_target_not_allowed")

    period, item = candidates[0]
    source = active_policy.get("sources", {}).get("household_expenses_2025", {})
    title = source.get("title") if isinstance(source, Mapping) else None
    if not isinstance(title, str) or not title:
        raise ReferenceTargetNotAllowed("reference_target_not_allowed")
    return [_proposal(
        section="goal_periods",
        path="items[].amount",
        value=approved["value"],
        item=item,
        parent=period,
        metadata=_reference_metadata(approved, title=title),
    )]


def build_default_proposals(
    *,
    step_id: str,
    message: str,
    current_form: Mapping[str, Any],
    saved_data: Mapping[str, Any] | None = None,
    today: date | None = None,
    policy: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return policy-backed proposals only; never create rows or overwrite values."""

    active_policy = dict(policy or load_default_policy())
    current_date = today or date.today()
    saved = saved_data if isinstance(saved_data, Mapping) else {}
    proposals: list[dict[str, Any]] = []

    if step_id == "basic-info":
        if not _present(current_form.get("end_age")):
            metadata = {
                "policy_version": active_policy["policy_version"],
                "source": {
                    "source_id": "financial-calculation-policy",
                    "title": "RetireWise financial calculation policy",
                    "reference_period": "active policy",
                },
                "retrieved_at": active_policy["retrieved_at"],
                "transform": "plan_end_age 직접 참조",
                "assumption_level": "planning_assumption",
            }
            proposals.append(_proposal(
                section="basic_info", path="end_age", value=_plan_end_age(), metadata=metadata
            ))
        age = current_form.get("age")
        if (
            not _present(current_form.get("retirement_age"))
            and current_form.get("job") == "직장인"
            and isinstance(age, int)
            and not isinstance(age, bool)
            and age < 60
            and _explicit_workplace_retirement(message)
        ):
            metadata = _metadata(
                active_policy,
                source_id="statutory_retirement_age",
                assumption_level="planning_assumption",
                transform="사업장 정년 계획가정 60세",
                today=current_date,
            )
            if metadata:
                proposals.append(_proposal(
                    section="basic_info", path="retirement_age", value=60, metadata=metadata
                ))
        return proposals

    if step_id == "pension-assets":
        year = _birth_year(message)
        metadata = _metadata(
            active_policy,
            source_id="national_pension_age",
            assumption_level="official_rule",
            transform="국민연금 노령연금 규칙 직접 적용",
            today=current_date,
        )
        rows = current_form.get("assets", [])
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, Mapping) or not _has_identity(row) or row.get("type") != "국민연금":
                continue
            if metadata and not _present(row.get("receive_method")):
                proposals.append(_proposal(
                    section="pension_assets", path="receive_method", value="연금형",
                    item=row, metadata=metadata,
                ))
            if (
                metadata
                and _pension_eligibility_confirmed(message)
                and not _present(row.get("pension_period"))
            ):
                proposals.append(_proposal(
                    section="pension_assets", path="pension_period", value="종신",
                    item=row, metadata=metadata,
                ))
            start_age = _pension_start_age(year) if year is not None else None
            if metadata and start_age is not None and not _present(row.get("pension_start_age")):
                proposals.append(_proposal(
                    section="pension_assets", path="pension_start_age", value=start_age,
                    item=row, metadata=metadata,
                ))
        return proposals

    if step_id == "expenses" and _explicit_household_benchmark(message):
        fallback_metadata = _metadata(
            active_policy,
            source_id="household_expenses_2025",
            assumption_level="household_benchmark",
            transform="전국 가구 월평균 원 단위 직접 매핑",
            today=current_date,
        )
        if not fallback_metadata:
            return []
        try:
            references = _household_references(active_policy, effective_on=current_date)
        except InputAssistDefaultPolicyError:
            return []
        rows = current_form.get("assets", [])
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, Mapping) or not _has_identity(row) or row.get("name") not in references:
                continue
            reference = references[row["name"]]
            source = active_policy["sources"]["household_expenses_2025"]
            metadata = _reference_metadata(reference, title=source["title"])
            if not _present(row.get("amount")):
                proposals.append(_proposal(
                    section="expenses", path="amount", value=reference["value"],
                    item=row, metadata=metadata,
                ))
            if not _present(row.get("frequency")):
                proposals.append(_proposal(
                    section="expenses", path="frequency",
                    value={"none": False, "unit": "월", "count": 1},
                    item=row, metadata={
                        **fallback_metadata,
                        "transform": "월평균을 월 1회로 정규화",
                    },
                ))
        return proposals

    if step_id != "financial-goals":
        return []

    basic_info = saved.get("basic_info", {})
    if not isinstance(basic_info, Mapping):
        basic_info = {}
    age = basic_info.get("age") or saved.get("basic_info.age") or saved.get("age")
    retirement_age = basic_info.get("retirement_age") or saved.get("basic_info.retirement_age") or saved.get("retirement_age")
    end_age = basic_info.get("end_age") or saved.get("basic_info.end_age") or saved.get("end_age")
    range_values = {
        "before": (age, retirement_age),
        "after": (retirement_age, end_age),
    }
    valid_ranges = (
        all(
            isinstance(value, int) and not isinstance(value, bool)
            for value in (age, retirement_age, end_age)
        )
        and age <= retirement_age <= end_age
    )
    household = _retirement_household(message)
    periods = current_form.get("goal_periods")
    if not isinstance(periods, list):
        periods = []
    retirement_meta = _metadata(
        active_policy,
        source_id="retirement_living_cost_2024",
        assumption_level="planning_assumption",
        transform="기존 목표 항목명에 월 생활비 배분액 직접 매핑",
        today=current_date,
    ) if household else None
    household_meta = _metadata(
        active_policy,
        source_id="household_expenses_2025",
        assumption_level="household_benchmark",
        transform="기존 목표 항목명에 월 생활비 배분액 직접 매핑",
        today=current_date,
    ) if _explicit_household_benchmark(message) else None
    household_references: dict[str, dict[str, Any]] = {}
    if household_meta:
        try:
            household_references = _household_references(
                active_policy,
                effective_on=current_date,
            )
        except InputAssistDefaultPolicyError:
            household_meta = None
    for period in periods:
        if not isinstance(period, Mapping) or not _has_identity(period) or period.get("phase") not in range_values:
            continue
        phase = period["phase"]
        start, end = range_values[phase]
        if valid_ranges:
            range_meta = {
                "policy_version": active_policy["policy_version"],
                "source": {"source_id": "confirmed-basic-info", "title": "확인된 기본 정보", "reference_period": "current"},
                "retrieved_at": active_policy["retrieved_at"],
                "transform": "확인된 현재·은퇴·계획종료 연령 연결",
                "assumption_level": "deterministic",
            }
            if not _present(period.get("from_age")):
                proposals.append(_proposal(
                    section="goal_periods", path="from_age", value=start,
                    item=period, metadata=range_meta,
                ))
            if not _present(period.get("to_age")):
                proposals.append(_proposal(
                    section="goal_periods", path="to_age", value=end,
                    item=period, metadata=range_meta,
                ))
        benchmark = (
            active_policy["retirement_monthly_expenses"][household]
            if phase == "after" and household and retirement_meta
            else household_references
            if phase == "before" and household_meta
            else {}
        )
        for item in period.get("items", []) if isinstance(period.get("items"), list) else []:
            if not isinstance(item, Mapping) or not _has_identity(item) or item.get("name") not in benchmark or _present(item.get("amount")):
                continue
            if phase == "before":
                reference = benchmark[item["name"]]
                source = active_policy["sources"]["household_expenses_2025"]
                value = reference["value"]
                metadata = _reference_metadata(reference, title=source["title"])
            else:
                value = benchmark[item["name"]]
                metadata = retirement_meta
            proposals.append(_proposal(
                section="goal_periods", path="items[].amount", value=value,
                item=item, parent=period, metadata=metadata,
            ))
    return proposals
