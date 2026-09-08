"""가계재무지표 7종의 양호/주의/위험 등급 판정.

front-pjt/src/components/charts/financialIndicatorSchema.js 의 존(zone) 정의를
그대로 포팅한 것 — 고객 대시보드 게이지와 PB 화면 집계가 같은 기준을 쓰도록
두 파일을 함께 유지보수한다. 값 방향(가계수지 = 100 - 지출률)도 프론트와 동일.
"""

GOOD = '양호'
WARN = '주의'
RISK = '위험'

# 연령 브래킷별 양호 기준값 (프론트 getZones 와 동일)
SAVING_GOOD_FROM = {'20s': 50, '30s': 30, '40s': 20, '50s': 10, '65+': 5}
DSR_GOOD_UNDER = {'20s': 25, '30s': 25, '40s': 25, '50s': 15, '65+': 5}
EMERGENCY_GOOD_FROM = {'20s': 2, '30s': 3, '40s': 4, '50s': 5, '65+': 6}


def age_bracket(age):
    if not age or age < 30:
        return '20s'
    if age < 40:
        return '30s'
    if age < 50:
        return '40s'
    if age < 65:
        return '50s'
    return '65+'


def _higher_is_better(value, good_from):
    # 프론트 zone: [_, t/2)=위험, [t/2, t)=주의, [t, max]=양호
    if value >= good_from:
        return GOOD
    if value >= good_from / 2:
        return WARN
    return RISK


def _grade_household_balance(value, bracket):
    return _higher_is_better(value, SAVING_GOOD_FROM[bracket])


def _grade_savings_propensity(value, bracket):
    return _higher_is_better(value, SAVING_GOOD_FROM[bracket])


def _grade_financial_asset_ratio(value, bracket):
    if value >= 40:
        return GOOD
    if value >= 20:
        return WARN
    return RISK


def _grade_debt_service_ratio(value, bracket):
    # 낮을수록 좋음. 프론트 zone: [0, t)=양호, [t, 1.6t)=주의, 이상=위험
    threshold = DSR_GOOD_UNDER[bracket]
    if value < threshold:
        return GOOD
    if value < threshold * 1.6:
        return WARN
    return RISK


def _grade_retirement_savings(value, bracket):
    if value >= 50:
        return GOOD
    if value >= 25:
        return WARN
    return RISK


def _grade_emergency_reserve(value, bracket):
    return _higher_is_better(value, EMERGENCY_GOOD_FROM[bracket])


def _grade_insurance_preparation(value, bracket):
    if value >= 8:
        return GOOD
    if value >= 5:
        return WARN
    return RISK


# (한글 키, 백엔드 계산기 키, 값 변환, 등급 함수, 게이지 그룹)
INDICATOR_DEFS = (
    ('가계수지지표', 'household_balance', lambda v: round(100 - v, 1), _grade_household_balance, 'cashflow'),
    ('총저축성향지표', 'total_savings_propensity', None, _grade_savings_propensity, 'cashflow'),
    ('금융자산비중지표', 'financial_asset_ratio', None, _grade_financial_asset_ratio, 'assets'),
    ('총부채상환지표', 'debt_service_ratio', None, _grade_debt_service_ratio, 'assets'),
    ('노후대비저축지표', 'retirement_savings', None, _grade_retirement_savings, 'assets'),
    ('비상예비금지표', 'emergency_reserve', None, _grade_emergency_reserve, 'risk'),
    ('보장성보험준비지표', 'insurance_preparation', None, _grade_insurance_preparation, 'risk'),
)


def build_graded_indicators(raw_indicators, age):
    """calculate_financial_indicators 결과 → [{key, value, grade, group}] 7종.

    value 는 프론트 게이지(makeIndicator)에 그대로 넣을 수 있는 표시값이다.
    """
    bracket = age_bracket(age)
    graded = []
    for key, source_key, transform, grade_fn, group in INDICATOR_DEFS:
        value = float(raw_indicators.get(source_key) or 0)
        if transform:
            value = transform(value)
        graded.append({
            'key': key,
            'value': value,
            'grade': grade_fn(value, bracket),
            'group': group,
        })
    return graded


def count_risky(graded_indicators):
    return sum(1 for item in graded_indicators if item['grade'] == RISK)
