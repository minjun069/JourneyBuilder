"""Canonical calculation-input snapshot used to detect stale reports."""

from __future__ import annotations

from hashlib import sha256
import json

from django.core.serializers.json import DjangoJSONEncoder


RELATION_FIELDS = {
    "financial_assets": (
        "asset_type", "name", "principal", "current_value", "monthly_savings",
        "purpose",
    ),
    "pension_assets": (
        "type", "name", "current_value", "monthly_savings",
        "expected_return_rate", "subscription_date", "payment_period_years",
        "receive_method", "pension_start_age", "pension_period", "expected_amount",
    ),
    "real_estate_assets": (
        "type", "name", "current_value", "acquisition_price", "acquisition_date",
        "monthly_rental_income", "rental_deposit", "secured_loan",
    ),
    "liabilities": (
        "type", "name", "balance", "interest_rate", "rate_type", "base_rate",
        "repayment_method", "loan_date", "maturity_date", "grace_period_years",
    ),
    "incomes": ("type", "name", "amount", "is_fixed"),
    "expenses": ("name", "amount", "is_fixed", "frequency"),
    "goal_milestones": ("phase", "age", "category", "content", "amount", "note"),
}


def build_calculation_input_snapshot(session) -> dict:
    basic_info = getattr(session, "basic_info", None)
    basic = None
    if basic_info is not None:
        basic = {
            field: getattr(basic_info, field)
            for field in (
                "age", "monthly_income", "retirement_age", "end_age",
                "dependents",
            )
        }

    snapshot = {"basic_info": basic}
    for relation_name, fields in RELATION_FIELDS.items():
        relation = getattr(session, relation_name)
        snapshot[relation_name] = list(
            relation.order_by("id").values(*fields)
        )

    periods = []
    for period in session.goal_periods.order_by("id").prefetch_related("items"):
        periods.append({
            "phase": period.phase,
            "from_age": period.from_age,
            "to_age": period.to_age,
            "items": list(
                period.items.order_by("id").values("name", "amount", "is_fixed")
            ),
        })
    snapshot["goal_periods"] = periods
    return snapshot


def digest_snapshot(snapshot: dict) -> str:
    """이미 확보한 스냅샷의 digest.

    calculation_input_digest(session) 는 DB 를 다시 읽는다. 계산과 기준 검증에
    같은 시점을 써야 하는 호출부는 스냅샷을 한 번만 뜬 뒤 이 함수로 해시한다
    (S15P21B109-136). 해시 구현은 여기 한 곳에만 둔다.
    """
    encoded = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        cls=DjangoJSONEncoder,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def calculation_input_digest(session) -> str:
    return digest_snapshot(build_calculation_input_snapshot(session))
