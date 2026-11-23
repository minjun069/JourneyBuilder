"""Editable synthetic user inputs. Amounts are KRW, rates are percentages."""

from copy import deepcopy

AS_OF = "2026-09-21T00:00:00+00:00"

BASE_INPUT = {
    "basic_info": {
        "age": 40, "retirement_age": 60, "end_age": 90,
        "monthly_income": 5_000_000, "job": "직장인", "dependents": {},
    },
    "financial_assets": [{
        "asset_type": "예금/적금", "name": "노후 준비 예금",
        "principal": 100_000_000, "current_value": 120_000_000,
        "monthly_savings": 700_000, "purpose": "노후준비",
    }],
    "pension_assets": [{
        "type": "국민연금", "name": "국민연금", "current_value": 0,
        "monthly_savings": 0, "expected_return_rate": 0,
        "subscription_date": "2010-01-01", "payment_period_years": 35,
        "receive_method": "연금형", "pension_start_age": 65,
        "pension_period": "종신", "expected_amount": 1_000_000,
    }],
    "real_estate_assets": [{
        "type": "주택/아파트", "name": "거주 아파트", "current_value": 350_000_000,
        "acquisition_price": 300_000_000, "acquisition_date": "2020-01-01",
        "monthly_rental_income": 0, "rental_deposit": 0, "secured_loan": 80_000_000,
    }],
    "liabilities": [{
        "type": "주택담보대출", "name": "주택담보대출", "balance": 80_000_000,
        "interest_rate": 4, "repayment_method": "원리금균등",
        "loan_date": "2020-01-01", "maturity_date": "2050-01-01",
    }],
    "incomes": [{"type": "근로소득", "name": "급여", "amount": 5_000_000, "is_fixed": True}],
    "expenses": [
        {"name": "생활비", "amount": 2_500_000, "is_fixed": True, "frequency": "매월"},
        {"name": "대출 원리금 상환", "amount": 500_000, "is_fixed": True, "frequency": "매월"},
    ],
    "goal_periods": [
        {"phase": "before", "from_age": 40, "to_age": 59,
         "items": [{"name": "생활비", "amount": 2_500_000, "is_fixed": True}]},
        {"phase": "after", "from_age": 60, "to_age": 69,
         "items": [{"name": "생활비", "amount": 2_500_000, "is_fixed": True}]},
        {"phase": "after", "from_age": 70, "to_age": 79,
         "items": [{"name": "생활비", "amount": 2_200_000, "is_fixed": True}]},
        {"phase": "after", "from_age": 80, "to_age": 90,
         "items": [{"name": "생활비", "amount": 2_000_000, "is_fixed": True}]},
    ],
    "goal_milestones": [{
        "phase": "before", "age": 50, "category": "지출",
        "content": "자녀 교육비", "amount": 30_000_000,
    }],
}


def scenarios():
    """Return independent copies so edits never leak between scenarios."""
    normal = deepcopy(BASE_INPUT)
    deficit = deepcopy(BASE_INPUT)
    deficit["basic_info"]["monthly_income"] = 2_500_000
    deficit["incomes"][0]["amount"] = 2_500_000
    deficit["expenses"][0]["amount"] = 3_500_000
    empty = deepcopy(BASE_INPUT)
    for section in ("financial_assets", "pension_assets", "real_estate_assets", "liabilities"):
        empty[section] = []
    debt = deepcopy(BASE_INPUT)
    debt["liabilities"][0]["balance"] = 650_000_000
    debt["real_estate_assets"][0]["secured_loan"] = 650_000_000
    debt["expenses"][1]["amount"] = 3_000_000
    return [
        {"id": "normal", "title": "기본 가계", "description": "소득·저축·주택·연금이 있는 가계", "input": normal},
        {"id": "deficit", "title": "월 적자", "description": "월 지출이 수입을 초과하는 가계", "input": deficit},
        {"id": "no-assets", "title": "보유 자산 없음", "description": "자산·연금·부채가 모두 없는 가계", "input": empty},
        {"id": "high-debt", "title": "부채 과다", "description": "부채가 자산을 초과해 순자산이 음수인 가계", "input": debt},
    ]
