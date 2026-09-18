"""AI-local canonical field contract keyed by ``step_id``."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


ALLOWED_OPERATIONS = ("set", "add", "update", "remove")


@dataclass(frozen=True)
class FieldSpec:
    step_id: str
    section: str
    path: str
    value_type: str
    unit: str
    constraints: dict[str, Any]
    cardinality: str
    sensitivity: str
    writable_by_ai: bool
    nullable: bool
    label: str
    operations: tuple[str, ...] = ALLOWED_OPERATIONS

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["operations"] = list(self.operations)
        return value


SECTION_META = {
    "basic_info": ("basic-info", "single"),
    "financial_mindset": ("financial-mindset", "single"),
    "financial_satisfaction": ("financial-satisfaction", "single"),
    "financial_assets": ("financial-assets", "list"),
    "pension_assets": ("pension-assets", "list"),
    "real_estate_assets": ("real-estate", "list"),
    "liabilities": ("liabilities", "list"),
    "incomes": ("income", "list"),
    "expenses": ("expenses", "list"),
    "goal_periods": ("financial-goals", "nested_list"),
    "goal_milestones": ("financial-goals", "list"),
}


def _field(
    section: str,
    path: str,
    value_type: str,
    unit: str = "none",
    *,
    constraints: dict[str, Any] | None = None,
    sensitivity: str = "financial_sensitive",
    writable: bool = True,
    nullable: bool = True,
    label: str,
) -> FieldSpec:
    step_id, cardinality = SECTION_META[section]
    return FieldSpec(
        step_id=step_id,
        section=section,
        path=path,
        value_type=value_type,
        unit=unit,
        constraints=constraints or {},
        cardinality=cardinality,
        sensitivity=sensitivity,
        writable_by_ai=writable,
        nullable=nullable,
        label=label,
        operations=ALLOWED_OPERATIONS if writable else (),
    )


FIELDS = [
    _field("basic_info", "age", "integer", "age", constraints={"min": 18, "max": 100}, sensitivity="quasi_identifier", label="나이"),
    _field("basic_info", "job", "string", constraints={"choices": ["직장인", "공무원", "자영업자", "프리랜서", "전업주부", "학생", "퇴직자", "기타"]}, sensitivity="quasi_identifier", label="직업"),
    _field("basic_info", "marital_status", "string", constraints={"choices": ["미혼", "기혼"]}, sensitivity="quasi_identifier", label="혼인 상태"),
    _field("basic_info", "dependents", "object", sensitivity="quasi_identifier", nullable=False, label="부양가족"),
    _field("basic_info", "dependents.children[].age", "integer", "age", constraints={"min": 0, "max": 40}, sensitivity="quasi_identifier", writable=False, label="자녀 나이"),
    _field("basic_info", "dependents.parents[].age", "integer", "age", constraints={"min": 0, "max": 120}, sensitivity="quasi_identifier", writable=False, label="부모 나이"),
    _field("basic_info", "dependents.others[].name", "string", sensitivity="quasi_identifier", writable=False, label="기타 부양가족 관계"),
    _field("basic_info", "dependents.others[].age", "integer", "age", constraints={"min": 0, "max": 120}, sensitivity="quasi_identifier", writable=False, label="기타 부양가족 나이"),
    _field("basic_info", "tendency", "array", nullable=False, label="재무 성향"),
    _field("basic_info", "interest", "string", constraints={"max_length": 200}, label="주요 관심사"),
    _field("basic_info", "need", "string", constraints={"choices": ["은퇴", "투자", "주택자금", "교육자금", "보장", "기타"]}, label="주요 니즈"),
    _field("basic_info", "monthly_income", "integer", "KRW", constraints={"min": 0, "period": "month"}, label="세후 월 소득"),
    _field("basic_info", "retirement_age", "integer", "age", constraints={"min": 18, "max": 100}, sensitivity="quasi_identifier", label="은퇴 예정 나이"),
    _field("basic_info", "end_age", "integer", "age", constraints={"min": 18, "max": 120}, sensitivity="quasi_identifier", label="계획 종료 나이"),
    *[
        _field("financial_mindset", path, "string", label=label)
        for path, label in (
            ("values", "가치관"),
            ("past_experience", "과거 경험"),
            ("current_situation", "현재 재무 상황"),
            ("current_challenge", "당면 과제"),
            ("invest_philosophy", "투자 철학"),
            ("future_goals", "미래 인생 목표"),
        )
    ],
    *[
        _field("financial_satisfaction", path, "integer", "score", constraints={"min": 1, "max": 5}, label=label)
        for path, label in (
            ("cashflow_score", "현금흐름 관리 만족도"),
            ("risk_score", "위험 관리 만족도"),
            ("investment_score", "투자 만족도"),
            ("management_score", "재무관리 만족도"),
            ("inheritance_score", "상속 만족도"),
        )
    ],
    _field("financial_assets", "asset_type", "string", constraints={"max_length": 50}, label="자산 유형"),
    _field("financial_assets", "name", "string", constraints={"max_length": 100}, label="상품명"),
    _field("financial_assets", "principal", "integer", "KRW", constraints={"min": 0}, label="원금"),
    _field("financial_assets", "current_value", "integer", "KRW", constraints={"min": 0}, label="현재 평가금액"),
    _field("financial_assets", "monthly_savings", "integer", "KRW", constraints={"min": 0, "period": "month"}, label="월 적립액"),
    _field("financial_assets", "purpose", "string", constraints={"max_length": 50}, label="운용 목적"),
    _field("pension_assets", "type", "string", constraints={"max_length": 50}, label="연금 유형"),
    _field("pension_assets", "name", "string", constraints={"max_length": 100}, label="상품명"),
    _field("pension_assets", "current_value", "integer", "KRW", constraints={"min": 0}, label="현재 평가금액"),
    _field("pension_assets", "monthly_savings", "integer", "KRW", constraints={"min": 0, "period": "month"}, label="월 적립액"),
    _field("pension_assets", "expected_return_rate", "decimal", "percent", constraints={"min": 0, "max": 20}, label="기대수익률"),
    _field("pension_assets", "subscription_date", "date", "date", label="가입일"),
    _field("pension_assets", "payment_period_years", "integer", "year", constraints={"min": 1, "max": 50}, label="납입기간"),
    _field("pension_assets", "receive_method", "string", constraints={"choices": ["연금형", "일시금"]}, label="수령방식"),
    _field("pension_assets", "pension_start_age", "integer", "age", constraints={"min": 55, "max": 80}, label="연금 개시 연령"),
    _field("pension_assets", "pension_period", "string", constraints={"max_length": 50}, label="연금 기간"),
    _field("pension_assets", "expected_amount", "integer", "KRW", constraints={"min": 0, "period": "month"}, label="예상 수령액"),
    _field("real_estate_assets", "type", "string", constraints={"max_length": 50}, label="부동산 유형"),
    _field("real_estate_assets", "name", "string", constraints={"max_length": 200}, label="부동산 이름"),
    _field("real_estate_assets", "current_value", "integer", "KRW", constraints={"min": 0}, label="현재 평가금액"),
    _field("real_estate_assets", "acquisition_price", "integer", "KRW", constraints={"min": 0}, label="취득가액"),
    _field("real_estate_assets", "acquisition_date", "date", "date", label="취득일"),
    _field("real_estate_assets", "monthly_rental_income", "integer", "KRW", constraints={"min": 0, "period": "month"}, label="월 임대소득"),
    _field("real_estate_assets", "rental_deposit", "integer", "KRW", constraints={"min": 0}, label="임대보증금"),
    _field("real_estate_assets", "secured_loan", "integer", "KRW", constraints={"min": 0}, label="담보대출금"),
    _field("liabilities", "type", "string", constraints={"max_length": 50}, label="부채 유형"),
    _field("liabilities", "name", "string", constraints={"max_length": 200}, label="대출 상품명"),
    _field("liabilities", "balance", "integer", "KRW", constraints={"min": 0}, label="대출잔액"),
    _field("liabilities", "interest_rate", "decimal", "percent", constraints={"min": 0, "max": 30}, label="대출금리"),
    _field("liabilities", "rate_type", "string", constraints={"choices": ["고정", "변동"]}, label="금리유형"),
    _field("liabilities", "base_rate", "string", constraints={"max_length": 20}, label="기준금리"),
    _field("liabilities", "repayment_method", "string", constraints={"max_length": 50}, label="상환방식"),
    _field("liabilities", "loan_date", "date", "date", label="대출일"),
    _field("liabilities", "maturity_date", "date", "date", label="만기일"),
    _field("liabilities", "grace_period_years", "integer", "year", constraints={"min": 0, "max": 10}, label="거치기간"),
    _field("incomes", "type", "string", constraints={"max_length": 50}, label="소득 유형"),
    _field("incomes", "name", "string", constraints={"max_length": 100}, label="소득 내용"),
    _field("incomes", "amount", "integer", "KRW", constraints={"min": 0, "period": "month"}, label="소득 금액"),
    _field("incomes", "is_fixed", "boolean", label="고정 여부"),
    _field("expenses", "name", "string", constraints={"max_length": 100}, label="지출 항목"),
    _field("expenses", "amount", "integer", "KRW", constraints={"min": 0}, label="지출 금액"),
    _field("expenses", "is_fixed", "boolean", label="고정 여부"),
    _field("expenses", "frequency", "object", label="지출 주기"),
    _field("expenses", "frequency.none", "boolean", nullable=False, writable=False, label="주기 해당 없음"),
    _field("expenses", "frequency.unit", "string", constraints={"choices": ["연", "월", "주"]}, writable=False, label="주기 단위"),
    _field("expenses", "frequency.count", "integer", constraints={"min": 1}, writable=False, label="주기 횟수"),
    _field("goal_periods", "phase", "string", constraints={"choices": ["before", "after"]}, nullable=False, label="은퇴 전후 구분"),
    _field("goal_periods", "from_age", "integer", "age", constraints={"min": 1, "max": 120}, nullable=False, label="시작 나이"),
    _field("goal_periods", "to_age", "integer", "age", constraints={"min": 1, "max": 120}, nullable=False, label="종료 나이"),
    _field("goal_periods", "items", "array", nullable=False, label="기간별 항목"),
    _field("goal_periods", "items[].name", "string", constraints={"max_length": 100}, label="기간별 항목명"),
    _field("goal_periods", "items[].amount", "integer", "KRW", constraints={"min": 0, "period": "month"}, label="기간별 금액"),
    _field("goal_periods", "items[].is_fixed", "boolean", label="기간별 고정 여부"),
    _field("goal_milestones", "phase", "string", constraints={"choices": ["before", "after"]}, nullable=False, label="은퇴 전후 구분"),
    _field("goal_milestones", "age", "integer", "age", constraints={"min": 1, "max": 120}, label="마일스톤 나이"),
    _field("goal_milestones", "category", "string", constraints={"choices": ["소득", "지출"]}, label="마일스톤 분류"),
    _field("goal_milestones", "content", "string", constraints={"max_length": 200}, label="마일스톤 내용"),
    _field("goal_milestones", "amount", "integer", "KRW", constraints={"min": 0}, label="마일스톤 금액"),
    _field("goal_milestones", "note", "string", constraints={"max_length": 200}, label="마일스톤 비고"),
]

for section, (_step_id, cardinality) in SECTION_META.items():
    timestamp_path = "updated_at" if cardinality == "single" else "created_at"
    FIELDS.append(_field(
        section,
        "id",
        "integer",
        sensitivity="operational",
        writable=False,
        nullable=False,
        label="항목 ID",
    ))
    FIELDS.append(_field(
        section,
        timestamp_path,
        "datetime",
        "datetime",
        sensitivity="operational",
        writable=False,
        nullable=False,
        label="변경 시각" if timestamp_path == "updated_at" else "생성 시각",
    ))

FIELDS.append(_field(
    "goal_periods",
    "items[].id",
    "integer",
    sensitivity="operational",
    writable=False,
    nullable=False,
    label="기간별 항목 ID",
))


def load_field_registry(step_id: str) -> list[dict[str, Any]]:
    """Return a fresh projection of the AI-local contract for one step."""

    if not isinstance(step_id, str) or not step_id.strip():
        raise ValueError("step_id must be a non-empty string")
    return [field.to_dict() for field in FIELDS if field.step_id == step_id]
