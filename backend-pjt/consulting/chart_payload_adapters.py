"""Deterministic report-to-chart projections for Presentation explanations.

Only values already present in a validated Backend report are projected here.
The helpers never estimate a missing value or repair an inconsistent formula.
"""

from __future__ import annotations

from collections.abc import Callable
from math import isfinite
from typing import Any


class ProjectionError(ValueError):
    pass


def _number(value: Any, path: str, *, integer: bool = False) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProjectionError(f"schema_invalid:{path}")
    if isinstance(value, float) and not isfinite(value):
        raise ProjectionError(f"schema_invalid:{path}")
    if integer and not isinstance(value, int):
        raise ProjectionError(f"schema_invalid:{path}")
    return value


def _object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProjectionError(f"schema_invalid:{path}")
    return value


def _rows(value: Any, path: str, *, allow_empty: bool = False) -> list[Any]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise ProjectionError(f"schema_invalid:{path}")
    return value


def _display(value: int | float, unit: str) -> str:
    if unit in {"KRW", "KRW/month", "KRW/year"}:
        suffix = {"KRW": "원", "KRW/month": "원/월", "KRW/year": "원/년"}[unit]
        return f"{int(value):,}{suffix}"
    if unit == "percent":
        return f"{value:g}%"
    if unit == "fraction":
        return f"{value * 100:.1f}%"
    if unit == "age":
        return f"{int(value)}세"
    if unit == "year":
        return f"{int(value)}년"
    if unit == "month":
        return f"{int(value)}개월"
    if unit == "count":
        return f"{int(value)}개"
    return str(value)


def _fact(
    fact_id: str,
    label: str,
    value: int | float,
    unit: str,
    path: str,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "id": fact_id,
        "label": label,
        "value": value,
        "unit": unit,
        "display": _display(value, unit),
        "provenance": {"source": "verified_payload", "path": path},
        **extra,
    }


def _event(
    event_id: str,
    event_type: str,
    label: str,
    *,
    age: int | None = None,
    facts: list[dict[str, Any]] | None = None,
    reason_code: str,
    path: str,
) -> dict[str, Any]:
    return {
        "id": event_id,
        "type": event_type,
        "label": label,
        "age": age,
        "facts": facts or [],
        "reason_code": reason_code,
        "provenance": {"source": "verified_payload", "path": path},
    }


def _category_totals(raw: Any, path: str) -> list[dict[str, Any]]:
    categories = _object(raw, path)
    output = []
    for key, category in categories.items():
        category = _object(category, f"{path}.{key}")
        label = category.get("label")
        if not isinstance(label, str) or not label.strip():
            raise ProjectionError(f"schema_invalid:{path}.{key}.label")
        total = _number(category.get("total"), f"{path}.{key}.total", integer=True)
        ratio = _number(category.get("ratio"), f"{path}.{key}.ratio")
        output.append({"key": key, "label": label, "total": total, "ratio": ratio})
    return output


def asset_statement(content: dict[str, Any]) -> dict[str, Any]:
    assets = _number(content.get("total_assets"), "total_assets", integer=True)
    liabilities = _number(content.get("total_liabilities"), "total_liabilities", integer=True)
    net_worth = _number(content.get("net_worth"), "net_worth", integer=True)
    if net_worth != assets - liabilities:
        raise ProjectionError("formula_invalid:net_worth")
    asset_categories = _category_totals(content.get("asset_categories"), "asset_categories")
    liability_categories = _category_totals(content.get("liability_categories"), "liability_categories")
    if sum(item["total"] for item in asset_categories) != assets:
        raise ProjectionError("formula_invalid:asset_categories")
    if sum(item["total"] for item in liability_categories) != liabilities:
        raise ProjectionError("formula_invalid:liability_categories")
    return {
        "period": {"dimension": "snapshot", "unit": "current"},
        "units": {"amount": "KRW", "ratio": "percent"},
        "summary": {"total_assets": assets, "total_liabilities": liabilities, "net_worth": net_worth},
        "series": {"asset_categories": asset_categories, "liability_categories": liability_categories},
        "highlights": [
            _fact("total-assets", "총자산", assets, "KRW", "total_assets"),
            _fact("total-liabilities", "총부채", liabilities, "KRW", "total_liabilities"),
            _fact("net-worth", "순자산", net_worth, "KRW", "net_worth"),
        ],
        "events": [],
        "warnings": ([{"code": "negative_net_worth", "message": "순자산이 음수입니다."}] if net_worth < 0 else []),
        "details": {"components": {"assets": asset_categories, "liabilities": liability_categories}},
    }


def cash_flow(content: dict[str, Any]) -> dict[str, Any]:
    inflow = _number(content.get("total_cash_inflow"), "total_cash_inflow", integer=True)
    outflow = _number(content.get("total_cash_outflow"), "total_cash_outflow", integer=True)
    net = _number(content.get("net_cash_flow"), "net_cash_flow", integer=True)
    if net != inflow - outflow:
        raise ProjectionError("formula_invalid:net_cash_flow")
    inflow_categories = _category_totals(content.get("inflow_categories"), "inflow_categories")
    outflow_categories = _category_totals(content.get("outflow_categories"), "outflow_categories")
    if sum(item["total"] for item in inflow_categories) != inflow:
        raise ProjectionError("formula_invalid:inflow_categories")
    if sum(item["total"] for item in outflow_categories) != outflow:
        raise ProjectionError("formula_invalid:outflow_categories")
    warnings = []
    if net < 0:
        warnings.append({"code": "monthly_deficit", "message": "월 현금흐름이 적자입니다."})
    elif inflow == 0:
        warnings.append({"code": "zero_inflow", "message": "확인된 월 현금유입이 없습니다."})
    return {
        "period": {"dimension": "month", "unit": "month"},
        "units": {"amount": "KRW/month", "ratio": "percent"},
        "summary": {"total_cash_inflow": inflow, "total_cash_outflow": outflow, "net_cash_flow": net},
        "series": {"inflow_categories": inflow_categories, "outflow_categories": outflow_categories},
        "highlights": [
            _fact("monthly-inflow", "월 총유입", inflow, "KRW/month", "total_cash_inflow"),
            _fact("monthly-outflow", "월 총유출", outflow, "KRW/month", "total_cash_outflow"),
            _fact("monthly-net", "월 순현금흐름", net, "KRW/month", "net_cash_flow"),
        ],
        "events": [],
        "warnings": warnings,
        "details": {"components": {"inflows": inflow_categories, "outflows": outflow_categories}},
    }


_INDICATORS = (
    ("household_balance", "가계수지지표", "percent"),
    ("total_savings_propensity", "총저축성향지표", "percent"),
    ("emergency_reserve", "비상예비금지표", "month"),
    ("insurance_preparation", "보장성보험준비지표", "percent"),
    ("debt_service_ratio", "총부채상환지표", "percent"),
    ("financial_asset_ratio", "금융자산비중지표", "percent"),
    ("retirement_savings", "노후대비저축지표", "percent"),
)


def financial_indicators(content: dict[str, Any]) -> dict[str, Any]:
    metrics = []
    for key, label, unit in _INDICATORS:
        value = content.get(key)
        if value is None:
            metrics.append({"key": key, "label": label, "value": None, "unit": unit, "display": None})
            continue
        value = _number(value, key)
        metrics.append({"key": key, "label": label, "value": value, "unit": unit, "display": _display(value, unit)})
    available = [item for item in metrics if item["value"] is not None]
    highlights = [
        _fact(item["key"], item["label"], item["value"], item["unit"], item["key"])
        for item in available[:3]
    ]
    warnings = []
    if not available:
        warnings.append({"code": "indicator_denominator_unavailable", "message": "지표 계산에 필요한 분모가 없습니다."})
    return {
        "period": {"dimension": "snapshot", "unit": "current"},
        "units": {"ratio": "percent", "reserve": "month"},
        "summary": {"available_indicator_count": len(available)},
        "series": metrics,
        "highlights": highlights,
        "events": [],
        "warnings": warnings,
        "details": {"indicators": metrics},
    }


def financial_goal_result(content: dict[str, Any]) -> dict[str, Any]:
    pre = _object(content.get("pre_retirement"), "pre_retirement")
    post = _rows(content.get("post_retirement_phases"), "post_retirement_phases", allow_empty=True)
    events: list[dict[str, Any]] = []
    ages: list[int] = []

    def add_goal(row: Any, path: str, event_type: str, label: str) -> None:
        row = _object(row, path)
        age = _number(row.get("age"), f"{path}.age", integer=True)
        current = _number(row.get("current_amount"), f"{path}.current_amount", integer=True)
        future = _number(row.get("future_amount"), f"{path}.future_amount", integer=True)
        ages.append(age)
        events.append(_event(
            f"{event_type}-{len(events) + 1}", event_type, label, age=age,
            facts=[
                _fact("current-amount", "현재가치", current, "KRW", f"{path}.current_amount"),
                _fact("future-amount", "목표시점 금액", future, "KRW", f"{path}.future_amount"),
            ], reason_code="reported_goal_timing", path=path,
        ))

    def add_period(row: Any, path: str, label: str) -> None:
        row = _object(row, path)
        start = _number(row.get("from_age"), f"{path}.from_age", integer=True)
        end = _number(row.get("to_age"), f"{path}.to_age", integer=True)
        current = _number(row.get("current_total"), f"{path}.current_total", integer=True)
        future = _number(row.get("future_total"), f"{path}.future_total", integer=True)
        if end < start:
            raise ProjectionError(f"period_invalid:{path}")
        ages.extend((start, end))
        events.append(_event(
            f"goal-period-{len(events) + 1}", "goal_period", label, age=start,
            facts=[
                _fact("current-amount", "현재가치", current, "KRW", f"{path}.current_total"),
                _fact("future-amount", "구간 시작시점 금액", future, "KRW", f"{path}.future_total"),
                _fact("period-end", "구간 종료 연령", end, "age", f"{path}.to_age"),
            ], reason_code="reported_goal_period", path=path,
        ))

    for index, row in enumerate(_rows(pre.get("desired_expenses"), "pre_retirement.desired_expenses", allow_empty=True)):
        add_period(row, f"pre_retirement.desired_expenses[{index}]", "은퇴 전 희망지출 구간")
    for bucket in ("expected_expenses", "achieved_funds"):
        for index, row in enumerate(_rows(pre.get(bucket), f"pre_retirement.{bucket}", allow_empty=True)):
            add_goal(row, f"pre_retirement.{bucket}[{index}]", "goal", "은퇴 전 재무목표")
    for phase_index, phase in enumerate(post):
        phase = _object(phase, f"post_retirement_phases[{phase_index}]")
        start = _number(phase.get("start_age"), f"post_retirement_phases[{phase_index}].start_age", integer=True)
        end = _number(phase.get("end_age"), f"post_retirement_phases[{phase_index}].end_age", integer=True)
        ages.extend((start, end))
        for index, row in enumerate(_rows(phase.get("desired_expenses"), f"post_retirement_phases[{phase_index}].desired_expenses", allow_empty=True)):
            add_period(row, f"post_retirement_phases[{phase_index}].desired_expenses[{index}]", "은퇴 후 희망지출 구간")
        for bucket in ("expected_expenses", "achieved_funds"):
            for index, row in enumerate(_rows(phase.get(bucket), f"post_retirement_phases[{phase_index}].{bucket}", allow_empty=True)):
                add_goal(row, f"post_retirement_phases[{phase_index}].{bucket}[{index}]", "goal", "은퇴 후 재무목표")
    total = len(events)
    highlights = [_fact("goal-count", "시점형 재무목표", total, "count", "pre_retirement/post_retirement_phases")]
    return {
        "period": ({"dimension": "age", "from": min(ages), "to": max(ages), "unit": "year"} if ages else {"dimension": "age", "unit": "year"}),
        "units": {"amount": "KRW", "age": "age"},
        "summary": {"event_count": total},
        "series": events,
        "highlights": highlights,
        "events": events,
        "warnings": ([{"code": "no_timed_goals", "message": "시점형 재무목표가 없습니다."}] if total == 0 else []),
        "details": {"goal_events": events},
    }


def retirement_fund(content: dict[str, Any]) -> dict[str, Any]:
    required = _number(content.get("total_required_fund"), "total_required_fund", integer=True)
    prepared = _number(content.get("total_prepared_fund"), "total_prepared_fund", integer=True)
    shortfall = _number(content.get("shortfall"), "shortfall", integer=True)
    if shortfall != max(0, required - prepared):
        raise ProjectionError("formula_invalid:shortfall")
    phases = _rows(content.get("phase_details"), "phase_details", allow_empty=True)
    normalized = []
    for index, raw in enumerate(phases):
        raw = _object(raw, f"phase_details[{index}]")
        normalized.append({
            "phase_name": str(raw.get("phase_name", "")),
            "duration_years": _number(raw.get("duration_years"), f"phase_details[{index}].duration_years", integer=True),
            "monthly_required": _number(raw.get("monthly_required"), f"phase_details[{index}].monthly_required", integer=True),
            "annual_required": _number(raw.get("annual_required"), f"phase_details[{index}].annual_required", integer=True),
            "total_required": _number(raw.get("total_required"), f"phase_details[{index}].total_required", integer=True),
        })
    if sum(item["total_required"] for item in normalized) != required:
        raise ProjectionError("formula_invalid:phase_details")
    warnings = ([{"code": "retirement_shortfall", "message": "은퇴 필요자금 대비 부족액이 있습니다."}] if shortfall > 0 else [])
    return {
        "period": {"dimension": "retirement_phase", "unit": "year"},
        "units": {"amount": "KRW", "duration": "year"},
        "summary": {"total_required_fund": required, "total_prepared_fund": prepared, "shortfall": shortfall},
        "series": normalized,
        "highlights": [
            _fact("required-fund", "총 필요자금", required, "KRW", "total_required_fund"),
            _fact("prepared-fund", "확인된 준비자금", prepared, "KRW", "total_prepared_fund"),
            _fact("shortfall", "부족액", shortfall, "KRW", "shortfall"),
        ],
        "events": [],
        "warnings": warnings,
        "details": {"phases": normalized},
    }


def retirement_readiness(content: dict[str, Any]) -> dict[str, Any]:
    current_age = _number(content.get("current_age"), "current_age", integer=True)
    retirement_age = _number(content.get("retirement_age"), "retirement_age", integer=True)
    total = _number(content.get("total_retirement_assets"), "total_retirement_assets", integer=True)
    projections = _rows(content.get("projections_by_age"), "projections_by_age")
    normalized = []
    previous_age = None
    for index, raw in enumerate(projections):
        raw = _object(raw, f"projections_by_age[{index}]")
        row = {key: _number(raw.get(key), f"projections_by_age[{index}].{key}", integer=True) for key in ("age", "financial", "real_estate", "pension", "total", "yearly_growth")}
        if row["total"] != row["financial"] + row["real_estate"] + row["pension"]:
            raise ProjectionError("formula_invalid:projections_by_age.total")
        if previous_age is not None and row["age"] != previous_age + 1:
            raise ProjectionError("period_invalid:projections_by_age.age")
        previous_age = row["age"]
        normalized.append(row)
    if normalized[0]["age"] != current_age or normalized[-1]["age"] != retirement_age:
        raise ProjectionError("period_invalid:projections_by_age")
    income_sources = _object(content.get("retirement_income_sources"), "retirement_income_sources")
    severance = _number(income_sources.get("severance_pay_estimated"), "retirement_income_sources.severance_pay_estimated", integer=True)
    if total != normalized[-1]["total"] + severance:
        raise ProjectionError("formula_invalid:total_retirement_assets")
    events = [_event("retirement-start", "retirement_start", "은퇴 시작", age=retirement_age, reason_code="reported_retirement_age", path="retirement_age")]
    return {
        "period": {"dimension": "age", "from": current_age, "to": retirement_age, "interval": 1, "unit": "year"},
        "units": {"amount": "KRW", "age": "age"},
        "summary": {"current_age": current_age, "retirement_age": retirement_age, "total_retirement_assets": total},
        "series": normalized,
        "highlights": [
            _fact("retirement-age", "은퇴 시작 연령", retirement_age, "age", "retirement_age"),
            _fact("retirement-assets", "은퇴 시점 예상자산", total, "KRW", "total_retirement_assets"),
            _fact("asset-growth", "현재 대비 자산 변화", normalized[-1]["total"] - normalized[0]["total"], "KRW", "projections_by_age"),
        ],
        "events": events,
        "warnings": [],
        "details": {"retirement_income_sources": income_sources},
    }


def pension_schedule(content: dict[str, Any]) -> dict[str, Any]:
    results = _object(content.get("results"), "results")
    start_age = _number(results.get("pension_start_age"), "results.pension_start_age", integer=True)
    monthly = _number(results.get("final_monthly_est"), "results.final_monthly_est", integer=True)
    schedule = _rows(content.get("yearly_schedule"), "yearly_schedule", allow_empty=True)
    normalized = []
    previous_age = None
    for index, raw in enumerate(schedule):
        raw = _object(raw, f"yearly_schedule[{index}]")
        row = {key: _number(raw.get(key), f"yearly_schedule[{index}].{key}", integer=True) for key in ("age", "year", "monthly_amount", "annual_amount")}
        if previous_age is not None and row["age"] != previous_age + 1:
            raise ProjectionError("period_invalid:yearly_schedule.age")
        previous_age = row["age"]
        normalized.append(row)
    if normalized and normalized[0]["age"] != start_age:
        raise ProjectionError("period_invalid:pension_start_age")
    events = [_event(
        "pension-start", "pension_start", "연금 수령 개시", age=start_age,
        facts=[_fact("monthly-pension", "월 예상 연금", monthly, "KRW/month", "results.final_monthly_est")],
        reason_code="reported_pension_start", path="results.pension_start_age",
    )]
    return {
        "period": ({"dimension": "age", "from": normalized[0]["age"], "to": normalized[-1]["age"], "interval": 1, "unit": "year"} if normalized else {"dimension": "age", "from": start_age, "to": start_age, "unit": "year"}),
        "units": {"amount": "KRW", "monthly_amount": "KRW/month", "annual_amount": "KRW/year", "age": "age"},
        "summary": {"pension_start_age": start_age, "final_monthly_est": monthly, "estimate_method": results.get("estimate_method")},
        "series": normalized,
        "highlights": [
            _fact("pension-start-age", "연금 개시 연령", start_age, "age", "results.pension_start_age"),
            _fact("monthly-pension", "월 예상 연금", monthly, "KRW/month", "results.final_monthly_est"),
        ],
        "events": events,
        "warnings": ([{"code": "pension_estimate_unavailable", "message": "확인된 연금 예상액이 없습니다."}] if monthly == 0 else []),
        "details": {"estimate_method": results.get("estimate_method")},
    }


_LIFETIME_FIELDS = (
    "age", "cash_inflow", "cash_outflow", "net_flow", "liquid_assets",
    "pension_assets", "real_estate_assets", "liabilities", "total_net_worth",
)


def lifetime_cashflow(content: dict[str, Any]) -> dict[str, Any]:
    rows = _rows(content.get("chart_data"), "chart_data")
    normalized = []
    previous_age = None
    for index, raw in enumerate(rows):
        raw = _object(raw, f"chart_data[{index}]")
        if set(raw) != set(_LIFETIME_FIELDS):
            raise ProjectionError(f"schema_invalid:chart_data[{index}]")
        row = {field: _number(raw.get(field), f"chart_data[{index}].{field}", integer=True) for field in _LIFETIME_FIELDS}
        if previous_age is not None and row["age"] != previous_age + 1:
            raise ProjectionError("period_invalid:chart_data.age")
        previous_age = row["age"]
        if row["cash_outflow"] > 0 or row["net_flow"] != row["cash_inflow"] + row["cash_outflow"]:
            raise ProjectionError("formula_invalid:chart_data.net_flow")
        if row["total_net_worth"] != row["liquid_assets"] + row["pension_assets"] + row["real_estate_assets"] - row["liabilities"]:
            raise ProjectionError("formula_invalid:chart_data.total_net_worth")
        normalized.append(row)

    depletion = _object(content.get("depletion_point"), "depletion_point")
    depleted = depletion.get("is_depleted")
    if not isinstance(depleted, bool):
        raise ProjectionError("schema_invalid:depletion_point.is_depleted")
    first_depletion = next((row["age"] for row in normalized if row["liquid_assets"] < 0), None)
    if depleted != (first_depletion is not None) or depletion.get("depletion_age") != first_depletion:
        raise ProjectionError("formula_invalid:depletion_point")
    final = _object(content.get("final_status"), "final_status")
    expected_final = {
        "end_age": normalized[-1]["age"],
        "final_deficit": max(0, -normalized[-1]["liquid_assets"]),
        "final_net_worth": normalized[-1]["total_net_worth"],
    }
    if any(final.get(key) != value for key, value in expected_final.items()):
        raise ProjectionError("formula_invalid:final_status")

    explanation = _object(content.get("explanation_data"), "explanation_data")
    reported_events = _rows(explanation.get("events"), "explanation_data.events", allow_empty=True)
    components = _rows(explanation.get("components_by_age"), "explanation_data.components_by_age")
    component_by_age = {}
    row_by_age = {row["age"]: row for row in normalized}
    for index, raw in enumerate(components):
        raw = _object(raw, f"explanation_data.components_by_age[{index}]")
        age = _number(raw.get("age"), f"explanation_data.components_by_age[{index}].age", integer=True)
        inflows = _object(raw.get("cash_inflow"), f"explanation_data.components_by_age[{index}].cash_inflow")
        outflows = _object(raw.get("cash_outflow"), f"explanation_data.components_by_age[{index}].cash_outflow")
        inflows = {key: _number(value, f"explanation_data.components_by_age[{index}].cash_inflow.{key}", integer=True) for key, value in inflows.items()}
        outflows = {key: _number(value, f"explanation_data.components_by_age[{index}].cash_outflow.{key}", integer=True) for key, value in outflows.items()}
        row = row_by_age.get(age)
        if row is None or sum(inflows.values()) != row["cash_inflow"] or sum(outflows.values()) != -row["cash_outflow"]:
            raise ProjectionError("formula_invalid:explanation_data.components_by_age")
        component_by_age[age] = {"age": age, "cash_inflow": inflows, "cash_outflow": outflows}
    if set(component_by_age) != set(row_by_age):
        raise ProjectionError("period_invalid:explanation_data.components_by_age")

    events: list[dict[str, Any]] = []
    for index, raw in enumerate(reported_events):
        raw = _object(raw, f"explanation_data.events[{index}]")
        event_type = raw.get("type")
        age = _number(raw.get("age"), f"explanation_data.events[{index}].age", integer=True)
        if event_type not in {"retirement_start", "pension_start", "expense_phase_change"} or age not in row_by_age:
            raise ProjectionError("schema_invalid:explanation_data.events")
        facts = []
        if event_type == "pension_start":
            annual_amount = _number(raw.get("annual_amount"), f"explanation_data.events[{index}].annual_amount", integer=True)
            facts.append(_fact("annual-pension", "연간 연금유입", annual_amount, "KRW/year", f"explanation_data.events[{index}].annual_amount"))
        elif event_type == "expense_phase_change":
            annual_expense = _number(raw.get("annual_expense"), f"explanation_data.events[{index}].annual_expense", integer=True)
            facts.append(_fact("annual-expense", "현재가치 기준 연간 생활비", annual_expense, "KRW/year", f"explanation_data.events[{index}].annual_expense"))
        events.append(_event(
            f"reported-{event_type}-{index + 1}", event_type,
            {"retirement_start": "은퇴 시작", "pension_start": "연금 개시", "expense_phase_change": "생활비 단계 변경"}[event_type],
            age=age, facts=facts, reason_code="reported_calculation_event", path=f"explanation_data.events[{index}]",
        ))
    deficit_ages = []
    prior_deficit = False
    for row in normalized:
        deficit = row["net_flow"] < 0
        if deficit and not prior_deficit:
            deficit_ages.append(row["age"])
            events.append(_event(
                f"cashflow-deficit-{row['age']}", "cashflow_deficit_start", "현금흐름 적자 전환", age=row["age"],
                facts=[_fact("net-flow", "순현금흐름", row["net_flow"], "KRW/year", f"chart_data.age={row['age']}.net_flow")],
                reason_code="verified_sign_transition", path="chart_data",
            ))
        prior_deficit = deficit
    if first_depletion is not None:
        row = row_by_age[first_depletion]
        events.append(_event(
            "asset-depletion", "asset_depletion", "유동자산 고갈", age=first_depletion,
            facts=[_fact("liquid-assets", "유동자산", row["liquid_assets"], "KRW", f"chart_data.age={first_depletion}.liquid_assets")],
            reason_code="verified_negative_liquid_assets", path="depletion_point",
        ))
    warnings = []
    if deficit_ages:
        warnings.append({"code": "cashflow_deficit", "message": "현금흐름 적자 구간이 있습니다."})
    if first_depletion is not None:
        warnings.append({"code": "asset_depletion", "message": "유동자산 고갈 시점이 있습니다."})
    if normalized[-1]["age"] - normalized[0]["age"] >= 30:
        warnings.append({"code": "long_horizon", "message": "장기 결과는 적용 가정에 민감합니다."})
    return {
        "period": {"dimension": "age", "from": normalized[0]["age"], "to": normalized[-1]["age"], "interval": 1, "unit": "year"},
        "units": {
            "age": "age", "cash_inflow": "KRW/year", "cash_outflow": "KRW/year",
            "net_flow": "KRW/year", "liquid_assets": "KRW", "pension_assets": "KRW",
            "real_estate_assets": "KRW", "liabilities": "KRW", "total_net_worth": "KRW",
        },
        "summary": {"depletion_point": depletion, "final_status": final},
        "series": normalized,
        "highlights": [
            _fact("final-net-worth", "마지막 연령의 순자산", normalized[-1]["total_net_worth"], "KRW", "final_status.final_net_worth"),
            _fact("final-liquid-assets", "마지막 연령의 유동자산", normalized[-1]["liquid_assets"], "KRW", f"chart_data[{len(normalized)-1}].liquid_assets"),
            *([_fact("first-deficit-age", "첫 적자 전환 연령", deficit_ages[0], "age", "chart_data.net_flow")] if deficit_ages else []),
        ][:3],
        "events": sorted(events, key=lambda item: (item["age"], item["id"])),
        "warnings": warnings,
        "details": {"cashflow_components_by_age": list(component_by_age.values())},
    }


def required_savings(content: dict[str, Any]) -> dict[str, Any]:
    current = _number(content.get("current_monthly_savings"), "current_monthly_savings", integer=True)
    target = _number(content.get("target_monthly_savings"), "target_monthly_savings", integer=True)
    additional = _number(content.get("additional_required_savings"), "additional_required_savings", integer=True)
    gap = _number(content.get("retirement_gap"), "retirement_gap", integer=True)
    if target != current + additional:
        raise ProjectionError("formula_invalid:target_monthly_savings")
    base = _object(content.get("simulation_base_data"), "simulation_base_data")
    if _number(base.get("original_shortfall"), "simulation_base_data.original_shortfall", integer=True) != gap:
        raise ProjectionError("formula_invalid:retirement_gap")
    current_age = _number(base.get("current_age"), "simulation_base_data.current_age", integer=True)
    retirement_age = _number(base.get("base_retirement_age"), "simulation_base_data.base_retirement_age", integer=True)
    return {
        "period": {"dimension": "age", "from": current_age, "to": retirement_age, "unit": "year"},
        "units": {"amount": "KRW", "monthly_amount": "KRW/month", "age": "age"},
        "summary": {"current_monthly_savings": current, "target_monthly_savings": target, "additional_required_savings": additional, "retirement_gap": gap},
        "series": [],
        "highlights": [
            _fact("current-savings", "현재 월 저축액", current, "KRW/month", "current_monthly_savings"),
            _fact("target-savings", "목표 월 저축액", target, "KRW/month", "target_monthly_savings"),
            _fact("additional-savings", "추가 필요 월 저축액", additional, "KRW/month", "additional_required_savings"),
        ],
        "events": [_event("retirement-start", "retirement_start", "은퇴 시작", age=retirement_age, reason_code="reported_retirement_age", path="simulation_base_data.base_retirement_age")],
        "warnings": ([{"code": "additional_savings_required", "message": "현재 저축액 외 추가 저축액이 필요합니다."}] if additional > 0 else []),
        "details": {"retirement_gap": gap},
    }


PROJECTORS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "balance-sheet": asset_statement,
    "cash-flow": cash_flow,
    "financial-indicators": financial_indicators,
    "financial-goal-result": financial_goal_result,
    "retirement-fund": retirement_fund,
    "retirement-readiness": retirement_readiness,
    "pension-schedule": pension_schedule,
    "lifetime-cashflow": lifetime_cashflow,
    "required-savings": required_savings,
}
