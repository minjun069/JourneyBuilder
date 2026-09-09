"""가계재무지표 7종의 양호/주의/위험 등급 판정.

front-pjt/src/components/charts/financialIndicatorSchema.js 의 존(zone) 정의를
그대로 포팅한 것 — 고객 대시보드 게이지와 PB 화면 집계가 같은 기준을 쓰도록
두 파일을 함께 유지보수한다. 값 방향(가계수지 = 100 - 지출률)도 프론트와 동일.
"""

from reports.policy import load_financial_policy


GOOD = '양호'
WARN = '주의'
RISK = '위험'
UNAVAILABLE = '판정 불가'

POLICY = load_financial_policy()


def _thresholds(reference_id):
    result = {}
    for item in POLICY['reference_values']:
        if item['reference_id'] != reference_id:
            continue
        age_min = item['age_min']
        age_max = item['age_max']
        if age_min == 20 and age_max == 29:
            bracket = '20s'
        elif age_min == 20 and age_max == 49:
            result.update({'20s': item['value'], '30s': item['value'], '40s': item['value']})
            continue
        elif age_min == 30 and age_max == 39:
            bracket = '30s'
        elif age_min == 40 and age_max == 49:
            bracket = '40s'
        elif age_min == 50 and age_max == 59:
            bracket = '50s'
        elif age_min == 60 and age_max == 64:
            bracket = '60-64'
        elif age_min == 60 and age_max is None:
            result.update({'60-64': item['value'], '65+': item['value']})
            continue
        elif age_min == 65 and age_max is None:
            bracket = '65+'
        else:
            continue
        result[bracket] = item['value']
    return result


SAVING_GOOD_FROM = _thresholds('total_savings_propensity_min')
DSR_GOOD_UNDER = _thresholds('debt_service_ratio_max')
EMERGENCY_GOOD_FROM = _thresholds('emergency_reserve_min')


def age_bracket(age):
    if not age or age < 30:
        return '20s'
    if age < 40:
        return '30s'
    if age < 50:
        return '40s'
    if age < 60:
        return '50s'
    if age < 65:
        return '60-64'
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
    if threshold == 0:
        return GOOD if value == 0 else RISK
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
        raw_value = raw_indicators.get(source_key)
        if raw_value is None:
            graded.append({
                'key': key,
                'value': None,
                'grade': UNAVAILABLE,
                'group': group,
            })
            continue
        value = float(raw_value)
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
