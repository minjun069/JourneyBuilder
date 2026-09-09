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


def calculation_input_digest(session) -> str:
    encoded = json.dumps(
        build_calculation_input_snapshot(session),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        cls=DjangoJSONEncoder,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()
