from datetime import timedelta
from django.utils import timezone
import re

from .policy import active_policy_version, load_financial_policy


CALCULATION_POLICY = load_financial_policy()

ASSUMPTION_SOURCES = {
    'inflation_rate': {
        'label': '한국은행 물가안정목표',
        'url': 'https://www.bok.or.kr/portal/main/contents.do?menuNo=200291',
    },
    'pension_formula': {
        'label': '국민연금 간이 추정',
        'url': None,
    },
}

DEFAULT_ASSUMPTIONS = dict(CALCULATION_POLICY['assumptions'])

PENSION_ASSUMPTIONS = {
    'a_value': CALCULATION_POLICY['national_pension']['a_value_monthly_krw'],
    'formula_constant': CALCULATION_POLICY['national_pension']['formula_constant'],
    'premium_rate': CALCULATION_POLICY['national_pension']['premium_rate'],
    'min_income': CALCULATION_POLICY['national_pension']['standard_monthly_income_min_krw'],
    'max_income': CALCULATION_POLICY['national_pension']['standard_monthly_income_max_krw'],
}

# 자산현황표
CASH_SAVINGS_KEYWORDS = (
    '현금', '입출금', '예금', '적금', '저축', '청약', 'cma', 'mmf', '파킹',
)
TANGIBLE_ASSET_KEYWORDS = (
    '실물', '자동차', '차량', '금', '귀금속', '미술품', '예술품', '명품',
)
INVESTMENT_ASSET_KEYWORDS = (
    '주식', '펀드', 'etf', '채권', '가상자산', '코인', 'crypto', '투자',
    'els', 'dls', '리츠', 'reit',
)
SHORT_TERM_LIABILITY_KEYWORDS = (
    '카드', '신용카드', '마이너스통장', '현금서비스', '단기', '카드론',
)

# 현금흐름표
EARNED_INCOME_KEYWORDS = ('근로', '급여', '월급', '연봉', '상여', '보너스')
BUSINESS_INCOME_KEYWORDS = ('사업', '자영업', '프리랜서')
CAPITAL_GAIN_INCOME_KEYWORDS = ('양도', '매각', '처분')
FINANCIAL_INCOME_KEYWORDS = ('금융', '이자', '배당')
RENTAL_INCOME_KEYWORDS = ('임대', '월세')
PENSION_INCOME_KEYWORDS = ('연금',)
SAVING_OUTFLOW_KEYWORDS = (
    '저축', '예금', '적금', '청약', 'cma', 'mmf', '파킹',
)
INVESTMENT_OUTFLOW_KEYWORDS = (
    '투자', '주식', '펀드', 'etf', '채권', '가상자산', '코인', '연금',
    'irp', 'els', 'dls', '리츠', 'reit',
)
DEBT_REPAYMENT_KEYWORDS = (
    '부채', '대출', '상환', '원리금', '원금', '이자', '카드론',
)

# 가계재무지표
NON_CONSUMPTION_KEYWORDS = (
    '세금', '국민연금', '건강보험', '고용보험', '산재보험', '소득세', '주민세', '원천징수'
)
PROTECTION_INSURANCE_KEYWORDS = (
    '보험', '실비', '암보험', '종신', '운전자', '화재보험', '치아'
)

# 자산 현황 계산
def calculate_asset_statement(session):
    asset_categories = {
        'cash_savings': _empty_category('현금/저축성 자산'),
        'tangible_assets': _empty_category('실물자산'),
        'investment_assets': _empty_category('투자자산'),
        'real_estate': _empty_category('부동산'),
        'pension_assets': _empty_category('연금자산'),
    }
    liability_categories = {
        'short_term': _empty_category('단기부채'),
        'long_term': _empty_category('중장기부채'),
    }

    for asset in session.financial_assets.all():
        category_key = _classify_financial_asset(asset.asset_type)
        amount = _to_int(asset.current_value)
        asset_categories[category_key]['items'].append({
            'id': asset.id,
            'asset_type': asset.asset_type,
            'name': asset.name,
            'principal': _to_int(asset.principal),
            'current_value': amount,
            'monthly_savings': _to_int(asset.monthly_savings),
            'purpose': asset.purpose,
        })
        asset_categories[category_key]['total'] += amount

    for asset in session.real_estate_assets.all():
        amount = _to_int(asset.current_value)
        asset_categories['real_estate']['items'].append({
            'id': asset.id,
            'type': asset.type,
            'name': asset.name,
            'current_value': amount,
            'acquisition_price': _to_int(asset.acquisition_price),
            'acquisition_date': _date_to_string(asset.acquisition_date),
            'monthly_rental_income': _to_int(asset.monthly_rental_income),
            'rental_deposit': _to_int(asset.rental_deposit),
            'secured_loan': _to_int(asset.secured_loan),
        })
        asset_categories['real_estate']['total'] += amount

    for asset in session.pension_assets.all():
        amount = _to_int(asset.current_value)
        asset_categories['pension_assets']['items'].append({
            'id': asset.id,
            'type': asset.type,
            'name': asset.name,
            'current_value': amount,
            'monthly_savings': _to_int(asset.monthly_savings),
            'expected_return_rate': _decimal_to_float(asset.expected_return_rate),
            'subscription_date': _date_to_string(asset.subscription_date),
            'payment_period_years': asset.payment_period_years,
            'receive_method': asset.receive_method,
            'pension_start_age': asset.pension_start_age,
            'pension_period': asset.pension_period,
            'expected_amount': _to_int(asset.expected_amount),
        })
        asset_categories['pension_assets']['total'] += amount

    for liability in session.liabilities.all():
        category_key = _classify_liability(liability)
        amount = _to_int(liability.balance)
        liability_categories[category_key]['items'].append({
            'id': liability.id,
            'type': liability.type,
            'name': liability.name,
            'balance': amount,
            'interest_rate': _decimal_to_float(liability.interest_rate),
            'rate_type': liability.rate_type,
            'base_rate': liability.base_rate,
            'repayment_method': liability.repayment_method,
            'loan_date': _date_to_string(liability.loan_date),
            'maturity_date': _date_to_string(liability.maturity_date),
            'grace_period_years': liability.grace_period_years,
        })
        liability_categories[category_key]['total'] += amount

    total_assets = sum(category['total'] for category in asset_categories.values())
    total_liabilities = sum(category['total'] for category in liability_categories.values())
    net_worth = total_assets - total_liabilities

    _attach_ratios(asset_categories, total_assets)
    _attach_ratios(liability_categories, total_liabilities)

    return {
        'assumptions': _assumptions_payload(),
        'total_assets': total_assets,
        'total_liabilities': total_liabilities,
        'total_debts': total_liabilities,
        'net_worth': net_worth,
        'asset_categories': asset_categories,
        'liability_categories': liability_categories,
        'debt_to_asset_ratio': _ratio(total_liabilities, total_assets),
        # 기존 프론트 호환용 필드
        'financial': (
            asset_categories['cash_savings']['total']
            + asset_categories['tangible_assets']['total']
            + asset_categories['investment_assets']['total']
        ),
        'real_estate': asset_categories['real_estate']['total'],
        'pension': asset_categories['pension_assets']['total'],
        'liabilities': total_liabilities,
        'real_estate_ratio': asset_categories['real_estate']['ratio'],
    }


# 현금 흐름 계산
def calculate_cash_flow(session):
    inflow_categories = {
        'earned_income': _empty_category('근로소득'),
        'business_income': _empty_category('사업소득'),
        'capital_gain_income': _empty_category('양도소득'),
        'financial_income': _empty_category('금융소득'),
        'rental_income': _empty_category('임대소득'),
        'pension_income': _empty_category('연금소득'),
        'other_income': _empty_category('기타소득'),
    }
    outflow_categories = {
        'savings': _empty_category('저축'),
        'investments': _empty_category('투자'),
        'debt_repayment': _empty_category('부채 상환'),
        'fixed_consumption': _empty_category('소비-고정지출'),
        'variable_consumption': _empty_category('소비-변동지출'),
    }

    for income in session.incomes.all():
        category_key = _classify_income(income.type)
        amount = _to_int(income.amount)
        inflow_categories[category_key]['items'].append({
            'id': income.id,
            'type': income.type,
            'amount': amount,
            'is_fixed': income.is_fixed,
        })
        inflow_categories[category_key]['total'] += amount

    for expense in session.expenses.all():
        category_key = _classify_expense(expense)
        amount = _to_int(expense.amount)
        outflow_categories[category_key]['items'].append({
            'id': expense.id,
            'name': expense.name,
            'amount': amount,
            'is_fixed': expense.is_fixed,
            'frequency': expense.frequency,
            'source': 'expense',
        })
        outflow_categories[category_key]['total'] += amount

    _add_asset_monthly_savings_to_outflow(session, outflow_categories)

    total_cash_inflow = sum(category['total'] for category in inflow_categories.values())
    total_cash_outflow = sum(category['total'] for category in outflow_categories.values())
    net_cash_flow = total_cash_inflow - total_cash_outflow
    savings_total = outflow_categories['savings']['total']
    investments_total = outflow_categories['investments']['total']

    _attach_ratios(inflow_categories, total_cash_inflow)
    _attach_ratios(outflow_categories, total_cash_outflow)

    return {
        'assumptions': _assumptions_payload(),
        'total_cash_inflow': total_cash_inflow,
        'total_cash_outflow': total_cash_outflow,
        'net_cash_flow': net_cash_flow,
        'savings_rate': _ratio(savings_total, total_cash_inflow),
        'investment_rate': _ratio(investments_total, total_cash_inflow),
        'savings_investment_rate': _ratio(
            savings_total + investments_total,
            total_cash_inflow,
        ),
        'inflow_categories': inflow_categories,
        'outflow_categories': outflow_categories,
        # 기존 프론트 호환용 필드
        'monthly_income': total_cash_inflow,
        'monthly_expense': total_cash_outflow,
        'monthly_surplus': net_cash_flow,
        'income_detail': list(session.incomes.values('type', 'amount', 'is_fixed')),
        'expense_detail': list(session.expenses.values('name', 'amount', 'is_fixed')),
    }


def _empty_category(label):
    return {
        'label': label,
        'total': 0,
        'ratio': 0,
        'items': [],
    }


# 자산 현황-자산 카테고리 분류
def _classify_financial_asset(asset_type):
    value = (asset_type or '').lower()

    if _contains_any(value, CASH_SAVINGS_KEYWORDS):
        return 'cash_savings'
    if _contains_any(value, TANGIBLE_ASSET_KEYWORDS):
        return 'tangible_assets'
    if _contains_any(value, INVESTMENT_ASSET_KEYWORDS):
        return 'investment_assets'
    return 'investment_assets'

# 자산 현황-부채 카테고리(단기, 중장기) 분류
def _classify_liability(liability):
    liability_type = (liability.type or '').lower()
    if _contains_any(liability_type, SHORT_TERM_LIABILITY_KEYWORDS):
        return 'short_term'

    if liability.maturity_date:
        one_year_later = timezone.localdate() + timedelta(days=365)
        if liability.maturity_date <= one_year_later:
            return 'short_term'

    return 'long_term'

# 현금흐름-유입 카테고리 분류
def _classify_income(income_type):
    value = (income_type or '').lower()

    if _contains_any(value, EARNED_INCOME_KEYWORDS):
        return 'earned_income'
    if _contains_any(value, BUSINESS_INCOME_KEYWORDS):
        return 'business_income'
    if _contains_any(value, CAPITAL_GAIN_INCOME_KEYWORDS):
        return 'capital_gain_income'
    if _contains_any(value, FINANCIAL_INCOME_KEYWORDS):
        return 'financial_income'
    if _contains_any(value, RENTAL_INCOME_KEYWORDS):
        return 'rental_income'
    if _contains_any(value, PENSION_INCOME_KEYWORDS):
        return 'pension_income'
    return 'other_income'

# 현금흐름-유출 카테고리 분류
def _classify_expense(expense):
    value = (expense.name or '').lower()

    if _contains_any(value, SAVING_OUTFLOW_KEYWORDS):
        return 'savings'
    if _contains_any(value, INVESTMENT_OUTFLOW_KEYWORDS):
        return 'investments'
    if _contains_any(value, DEBT_REPAYMENT_KEYWORDS):
        return 'debt_repayment'
    if expense.is_fixed is True:
        return 'fixed_consumption'
    return 'variable_consumption'


def _add_asset_monthly_savings_to_outflow(session, outflow_categories):
    for asset in session.financial_assets.all():
        amount = _to_int(asset.monthly_savings)
        if not amount:
            continue

        category_key = (
            'savings'
            if _classify_financial_asset(asset.asset_type) == 'cash_savings'
            else 'investments'
        )
        outflow_categories[category_key]['items'].append({
            'id': asset.id,
            'name': asset.name,
            'asset_type': asset.asset_type,
            'amount': amount,
            'source': 'financial_asset_monthly_savings',
        })
        outflow_categories[category_key]['total'] += amount

    for asset in session.pension_assets.all():
        amount = _to_int(asset.monthly_savings)
        if not amount:
            continue

        outflow_categories['investments']['items'].append({
            'id': asset.id,
            'name': asset.name,
            'type': asset.type,
            'amount': amount,
            'source': 'pension_asset_monthly_savings',
        })
        outflow_categories['investments']['total'] += amount


# 가계재무지표 계산
def calculate_financial_indicators(session, asset_data, cash_flow_data):
    total_income = cash_flow_data['total_cash_inflow']
    total_expense = cash_flow_data['total_cash_outflow']
    
    outflow = cash_flow_data['outflow_categories']
    asset_cats = asset_data['asset_categories']

    # 1. 가계수지지표 = (총지출 ÷ 총소득) × 100
    household_balance_indicator = _ratio_or_none(total_expense, total_income)

    # 비소비지출은 상세 표시에만 유지한다. 승인 산식의 분모는 총소득이다.
    non_consumption_expense = sum(
        _to_int(expense.amount)
        for expense in session.expenses.all()
        if _contains_any((expense.name or '').lower(), NON_CONSUMPTION_KEYWORDS)
    )
    disposable_income = max(0, total_income - non_consumption_expense)

    total_savings = outflow['savings']['total'] + outflow['investments']['total']
    total_savings_propensity = _ratio_or_none(total_savings, total_income)

    # 3. 비상예비금지표 = 유동성 자산 / 월평균 지출
    # 유동성 자산은 현금/저축성 자산으로 산정
    liquid_assets = asset_cats['cash_savings']['total']
    
    # 승인 산식은 현금흐름표의 총지출을 분모로 사용한다.
    emergency_reserve_indicator = (
        round(liquid_assets / total_expense, 2)
        if total_expense > 0 else None
    )

    # 4. 보장성보험준비지표 = (월 보장성 보험료 합계 / 월 총소득) * 100
    # 의무보험(건강보험 등)과 저축성 보험을 제외한 순수 보장성 보험 추출
    protection_insurance_expense = sum(
        _to_int(expense.amount)
        for expense in session.expenses.all()
        if _contains_any((expense.name or '').lower(), PROTECTION_INSURANCE_KEYWORDS)
        and not _contains_any((expense.name or '').lower(), NON_CONSUMPTION_KEYWORDS)
    )
    insurance_preparation_indicator = _ratio_or_none(
        protection_insurance_expense,
        total_income,
    )

    # 5. 총부채상환지표 (DSR) = 1년간 갚아야 하는 대출 원금&이자 / 연간 소득
    debt_repayment = outflow['debt_repayment']['total']
    dsr_indicator = _ratio_or_none(debt_repayment, total_income)

    # 6. 금융자산비중지표 = (금융자산 / 총자산) * 100
    # 투자 및 유동성 자산 합계 (실물자산, 부동산 제외)
    pure_financial_assets = asset_cats['cash_savings']['total'] + asset_cats['investment_assets']['total']
    financial_asset_ratio = _ratio_or_none(
        pure_financial_assets,
        asset_data['total_assets'],
    )

    # 7. 노후대비저축지표 = 노후대비저축 ÷ 총저축
    required_retirement_living_expense = _average_monthly_retirement_expense(session)
    expected_public_pension = _public_pension_monthly(session)
    monthly_shortfall = max(0, required_retirement_living_expense - expected_public_pension)
    
    # 현재 노후를 위해 저축 중인 연금자산 월 납입액
    pension_savings = sum(
        _to_int(asset.monthly_savings)
        for asset in session.pension_assets.all()
    )
    
    retirement_savings_indicator = _ratio_or_none(pension_savings, total_savings)

    return {
        'assumptions': _assumptions_payload(),
        'household_balance': household_balance_indicator,          # 가계수지지표 (%)
        'total_savings_propensity': total_savings_propensity,      # 총저축성향지표 (%)
        'emergency_reserve': emergency_reserve_indicator,          # 비상예비금지표 (배수, 개월수)
        'insurance_preparation': insurance_preparation_indicator,  # 보장성보험준비지표 (%)
        'debt_service_ratio': dsr_indicator,                       # 총부채상환지표 (%)
        'financial_asset_ratio': financial_asset_ratio,            # 금융자산비중지표 (%)
        'retirement_savings': retirement_savings_indicator,        # 노후대비저축지표 (%)
        
        # 중간 계산 값들 (프론트엔드에서 상세 내역을 보여줄 때 유용함)
        '_details': {
            'disposable_income': disposable_income,
            'liquid_assets': liquid_assets,
            'total_expense': total_expense,
            'total_savings': total_savings,
            'protection_insurance_expense': protection_insurance_expense,
            'pure_financial_assets': pure_financial_assets,
            'required_retirement_living_expense': required_retirement_living_expense,
            'expected_public_pension': expected_public_pension,
            'monthly_shortfall': monthly_shortfall,
            'pension_savings': pension_savings,
        }
    }


# 재무목표 계산
# inflation_rate: 기본 물가상승률 (기본값 2%)
def calculate_financial_goal_result(session, inflation_rate=None):
    # 1. 기본 정보 로드 (나이 정보 추출)
    basic_info = getattr(session, 'basic_info', None)
    if not basic_info:
        return {}

    inflation_rate = _resolve_rate(inflation_rate, 'inflation_rate')
    current_age = basic_info.age or 30
    retirement_age = basic_info.retirement_age or 60
    end_age = basic_info.end_age or DEFAULT_ASSUMPTIONS['plan_end_age']

    # 물가상승률이 반영된 미래가치(FV) 계산 헬퍼 함수
    def _get_fv(amount, target_age):
        amount = _to_int(amount)
        years_to_goal = max(0, _to_int(target_age) - current_age)
        return int(amount * ((1 + inflation_rate) ** years_to_goal))

    # [은퇴 전] 재무목표 세팅
    pre_retirement = {
        'desired_expenses': [],  # 희망 지출 규모 (Period)
        'expected_expenses': [], # 예상 지출 (Milestone - 지출)
        'achieved_funds': [],    # 달성 자금 (Milestone - 소득)
    }

    # 은퇴 전 희망 지출 규모 (FinancialGoalPeriod)
    for period in session.goal_periods.filter(phase='before').prefetch_related('items'):
        period_total = sum(_to_int(item.amount) for item in period.items.all())
        items = [{'name': item.name, 'amount': _to_int(item.amount), 'is_fixed': item.is_fixed} for item in period.items.all()]
        
        pre_retirement['desired_expenses'].append({
            'from_age': period.from_age,
            'to_age': period.to_age,
            'items': items,
            'current_total': period_total,
            'future_total': _get_fv(period_total, period.from_age) # 시작 나이 기준으로 미래가치 산출
        })

    # 은퇴 전 마일스톤 (FinancialGoalMilestone)
    for ms in session.goal_milestones.filter(phase='before'):
        data = {
            'age': ms.age,
            'content': ms.content,
            'note': ms.note,
            'current_amount': _to_int(ms.amount),
            'future_amount': _get_fv(ms.amount, ms.age)
        }
        if ms.category == '지출':
            pre_retirement['expected_expenses'].append(data)
        elif ms.category == '소득':
            pre_retirement['achieved_funds'].append(data)

    # [은퇴 후] 재무목표 세팅 (활동기, 회상기, 간병기 분리)
    phases = _build_retirement_phases(retirement_age, end_age)

    post_retirement_phases = []
    for phase in phases:
        post_retirement_phases.append({
            'phase_name': phase['name'],
            'start_age': phase['start_age'],
            'end_age': phase['end_age'],
            'desired_expenses': [],
            'expected_expenses': [],
            'achieved_funds': []
        })

    # 나이에 따라 어느 구간(Index)에 속하는지 판별하는 헬퍼 함수
    # 은퇴 후 희망 지출 규모 (FinancialGoalPeriod)
    for period in session.goal_periods.filter(phase='after').prefetch_related('items'):
        phase_idx = _retirement_phase_index(phases, period.from_age) # 시작 나이 기준으로 속할 구간 판별
        period_total = sum(_to_int(item.amount) for item in period.items.all())
        items = [{'name': item.name, 'amount': _to_int(item.amount), 'is_fixed': item.is_fixed} for item in period.items.all()]
        
        post_retirement_phases[phase_idx]['desired_expenses'].append({
            'from_age': period.from_age,
            'to_age': period.to_age,
            'items': items,
            'current_total': period_total,
            'future_total': _get_fv(period_total, period.from_age)
        })

    # 은퇴 후 마일스톤 (FinancialGoalMilestone)
    for ms in session.goal_milestones.filter(phase='after'):
        phase_idx = _retirement_phase_index(phases, ms.age)
        data = {
            'age': ms.age,
            'content': ms.content,
            'note': ms.note,
            'current_amount': _to_int(ms.amount),
            'future_amount': _get_fv(ms.amount, ms.age)
        }
        if ms.category == '지출':
            post_retirement_phases[phase_idx]['expected_expenses'].append(data)
        elif ms.category == '소득':
            post_retirement_phases[phase_idx]['achieved_funds'].append(data)

    return {
        'assumptions': _assumptions_payload(
            inflation_rate=inflation_rate,
        ),
        'inflation_rate_applied': inflation_rate,
        'pre_retirement': pre_retirement,
        'post_retirement_phases': post_retirement_phases,
    }


# 은퇴 필요자금 계산
def calculate_retirement_fund(session, inflation_rate=None):

    basic_info = getattr(session, 'basic_info', None)
    if not basic_info:
        return {}

    inflation_rate = _resolve_rate(inflation_rate, 'inflation_rate')
    current_age = basic_info.age or 30
    retirement_age = basic_info.retirement_age or 60
    end_age = basic_info.end_age or DEFAULT_ASSUMPTIONS['plan_end_age']

    # 1. 은퇴 준비기간 및 은퇴 기간
    prep_period_years = max(0, retirement_age - current_age)
    retirement_period_years = max(0, end_age - retirement_age + 1)

    # 2. 은퇴 3단계 구간 설정
    phases = _build_retirement_phases(retirement_age, end_age)

    # 각 단계별 현재가치 기준 월 필요자금 합산 (FinancialGoalPeriod 활용)
    for p in phases:
        p['current_monthly_expense'] = 0

    for period in session.goal_periods.filter(phase='after').prefetch_related('items'):
        phase_idx = _retirement_phase_index(phases, period.from_age)
        period_total = sum(_to_int(item.amount) for item in period.items.all())
        phases[phase_idx]['current_monthly_expense'] += period_total

    total_required_fund = 0
    phase_details = []

    # 3. 단계별 월/연/총 필요 자금 계산
    for p in phases:
        duration = max(0, p['end_age'] - p['start_age'] + 1)
        current_monthly = p['current_monthly_expense']

        # 해당 구간의 총 필요자금: 나이별로 매년 물가상승률을 누적 적용하여 합산
        phase_total = 0
        for age in range(p['start_age'], p['end_age'] + 1):
            years_from_now = max(0, age - current_age)
            # age 시점의 월 생활비
            fv_monthly = current_monthly * ((1 + inflation_rate) ** years_from_now)
            # age 시점의 연 생활비
            fv_annual = fv_monthly * 12
            phase_total += fv_annual

        # 보고서 표기용 (구간 시작 첫 해의 필요 자금을 대표값으로 사용)
        start_years_from_now = max(0, p['start_age'] - current_age)
        representative_monthly = int(current_monthly * ((1 + inflation_rate) ** start_years_from_now))
        representative_annual = representative_monthly * 12

        total_required_fund += phase_total

        phase_details.append({
            'phase_name': p['name'],
            'duration_years': duration,
            'monthly_required': representative_monthly,
            'annual_required': representative_annual,
            'total_required': int(phase_total)
        })

    # 4. 노후 준비자금 계산 (PensionAsset 활용)
    total_prepared_fund = 0
    for pension in session.pension_assets.all():
        expected_monthly = _to_int(pension.expected_amount)
        if not expected_monthly:
            continue
            
        pension_period_str = pension.pension_period or ''
        period_years = 0
        
        # 수령 기간 텍스트 처리 ('종신' 또는 '10년' 등)
        if '종신' in pension_period_str:
            start_age = pension.pension_start_age or retirement_age
            period_years = max(0, end_age - start_age + 1)
        else:
            # 문자열에서 숫자만 추출
            match = re.search(r'\d+', pension_period_str)
            if match:
                period_years = int(match.group())
            else:
                # 숫자가 없으면 (예: 기간 미입력) 은퇴 시점부터 기대수명까지로 기본 할당
                start_age = pension.pension_start_age or retirement_age
                period_years = max(0, end_age - start_age + 1)

        total_prepared_fund += expected_monthly * 12 * period_years

    # 5. 노후 부족자금
    shortfall = max(0, int(total_required_fund) - int(total_prepared_fund))

    return {
        'assumptions': _assumptions_payload(
            inflation_rate=inflation_rate,
        ),
        'prep_period_years': prep_period_years,
        'retirement_period_years': retirement_period_years,
        'total_required_fund': int(total_required_fund),
        'total_prepared_fund': int(total_prepared_fund),
        'shortfall': shortfall,
        'phase_details': phase_details,
    }


# 은퇴 준비자금 계산
def calculate_retirement_readiness(
    session,
    fin_return_rate=None,
    re_appreciation_rate=None,
    income_growth_rate=None,
    rent_increase_rate=None,
    vacancy_rate=None
):
    
    basic_info = getattr(session, 'basic_info', None)
    if not basic_info:
        return {}

    fin_return_rate = _resolve_rate(fin_return_rate, 'aggressive_financial_return_rate')
    re_appreciation_rate = _resolve_rate(re_appreciation_rate, 'real_estate_appreciation_rate')
    income_growth_rate = _resolve_rate(income_growth_rate, 'wage_growth_rate')
    rent_increase_rate = _resolve_rate(rent_increase_rate, 'rent_growth_rate')
    vacancy_rate = _resolve_rate(vacancy_rate, 'vacancy_rate')
    pension_default_return_rate = _resolve_rate(None, 'financial_return_rate')
    current_age = basic_info.age or 30
    retirement_age = basic_info.retirement_age or 60
    years_to_retire = max(0, retirement_age - current_age)

    # 1. 미래가치(FV) 산출 헬퍼 함수 (거치금 + 적립금)
    def _fv(pv, pmt_monthly, rate, years):
        pv = _to_int(pv)
        pmt_monthly = _to_int(pmt_monthly)
        pmt_annual = pmt_monthly * 12
        if rate == 0:
            return pv + (pmt_annual * years)
        # 현가의 미래가치 + 연납입금의 미래가치
        fv_pv = pv * ((1 + rate) ** years)
        fv_pmt = pmt_annual * (((1 + rate) ** years - 1) / rate)
        return int(fv_pv + fv_pmt)

    # 나이별 자산 구성 변화 추이 (그래프용 데이터)
    yearly_projections = []
    
    for age in range(current_age, retirement_age + 1):
        t = age - current_age
        
        # [금융자산] 현재가치 및 납입액의 t년 후 가치
        fin_val = sum(
            _fv(asset.current_value, asset.monthly_savings, fin_return_rate, t)
            for asset in session.financial_assets.all()
        )
        
        # [부동산] 현재가치의 t년 후 가치 (임대보증금/대출금은 변동 없다고 가정)
        re_val = sum(
            int(_to_int(asset.current_value) * ((1 + re_appreciation_rate) ** t))
            for asset in session.real_estate_assets.all()
        )
        
        # [연금자산] 현재가치 및 납입액의 t년 후 가치
        pen_val = sum(
            _fv(
                asset.current_value,
                asset.monthly_savings,
                _normalize_rate(asset.expected_return_rate, pension_default_return_rate),
                t,
            )
            for asset in session.pension_assets.all()
            if '국민' not in (asset.type or '') # 국민연금은 자산 축적분에서 일시 제외 (수령액으로 별도 계산)
        )
        
        # 총 자산
        total_val = fin_val + re_val + pen_val
        
        yearly_projections.append({
            'age': age,
            'financial': fin_val,
            'real_estate': re_val,
            'pension': pen_val,
            'total': total_val,
            'yearly_growth': 0 # 다음 루프에서 계산
        })

    # 연도별 자산 증가분 계산
    for i in range(1, len(yearly_projections)):
        yearly_projections[i]['yearly_growth'] = yearly_projections[i]['total'] - yearly_projections[i-1]['total']

    # 은퇴 시점 자산 구성 (Total Readiness)
    
    # 은퇴 시 예상 평가액
    retirement_assets = yearly_projections[-1] if yearly_projections else {'financial': 0, 'real_estate': 0, 'pension': 0}
    
    # [공적연금] (월 수령액 기준 보정)
    public_pension_monthly = sum(
        _to_int(asset.expected_amount)
        for asset in session.pension_assets.all()
        if '국민' in (asset.type or '') or '공무원' in (asset.type or '')
    )
    
    # [퇴직금] (DB/DC 추정)
    severance_pay_total = 0
    current_salary = _to_int(basic_info.monthly_income)
    
    for asset in session.pension_assets.all():
        asset_type = (asset.type or '').upper()
        if '퇴직' in asset_type or 'IRP' in asset_type:
            # DB형 가정: 은퇴 직전 예상 월급 * 근속연수 (간이로 남은 연수를 근속연수에 추가)
            if 'DB' in asset_type or not asset.monthly_savings:
                expected_final_salary = current_salary * ((1 + income_growth_rate) ** years_to_retire)
                # DB형 근속연수 추정 (현재가치 / 현재월급 + 남은연수) -> 데이터가 없으므로 남은 연수만 더함
                estimated_years_worked = (_to_int(asset.current_value) / current_salary) if current_salary > 0 else 0
                severance_pay_total += int(expected_final_salary * (estimated_years_worked + years_to_retire))
            # DC형 가정: (현재잔액 * 수익률) + (추가납입액 * 수익률)
            else:
                severance_pay_total += _fv(
                    asset.current_value, 
                    asset.monthly_savings, 
                    _normalize_rate(asset.expected_return_rate, fin_return_rate),
                    years_to_retire
                )

    # [부동산 임대수익] (은퇴 시점 연 임대수익)
    rental_income_annual = sum(
        int(_to_int(asset.monthly_rental_income) * ((1 + rent_increase_rate) ** years_to_retire) * 12 * (1 - vacancy_rate))
        for asset in session.real_estate_assets.all()
        if _to_int(asset.monthly_rental_income) > 0
    )

    # [주택연금] (데이터 모델에 명시적 필드가 없으므로, 향후 확장을 위해 0으로 세팅하거나 특정 조건 시 추가)
    reverse_mortgage_annual = 0

    return {
        'current_age': current_age,
        'retirement_age': retirement_age,
        'assumptions': _assumptions_payload(
            financial_return_rate=fin_return_rate,
            pension_default_return_rate=pension_default_return_rate,
            real_estate_appreciation_rate=re_appreciation_rate,
            wage_growth_rate=income_growth_rate,
            rent_growth_rate=rent_increase_rate,
            vacancy_rate=vacancy_rate,
        ),
        'rates_applied': {
            'financial_return': fin_return_rate,
            'pension_default_return': pension_default_return_rate,
            'real_estate_appreciation': re_appreciation_rate,
        },
        'current_composition': {
            'financial': yearly_projections[0]['financial'] if yearly_projections else 0,
            'real_estate': yearly_projections[0]['real_estate'] if yearly_projections else 0,
            'pension': yearly_projections[0]['pension'] if yearly_projections else 0,
        },
        'retirement_composition': {
            'financial': retirement_assets['financial'],
            'real_estate': retirement_assets['real_estate'],
            'pension_accumulation': retirement_assets['pension'], 
        },
        'retirement_income_sources': {
            'public_pension_monthly': int(public_pension_monthly),
            'severance_pay_estimated': int(severance_pay_total),
            'rental_income_annual': int(rental_income_annual),
            'reverse_mortgage_annual': reverse_mortgage_annual,
        },
        'total_retirement_assets': int(
            retirement_assets['financial'] + 
            retirement_assets['real_estate'] + 
            retirement_assets['pension'] + 
            severance_pay_total
        ),
        'projections_by_age': yearly_projections
    }


# 연금 스케줄 계산
def calculate_pension_schedule(session, inflation_rate=None):
    basic_info = getattr(session, 'basic_info', None)
    if not basic_info:
        return {}

    inflation_rate = _resolve_rate(inflation_rate, 'inflation_rate')
    current_age = basic_info.age or 30
    retirement_age = basic_info.retirement_age or 60
    income = _to_int(basic_info.monthly_income)


    # 1. 국민연금 산식 기본 가정
    A_VALUE = PENSION_ASSUMPTIONS['a_value']
    CONSTANT = PENSION_ASSUMPTIONS['formula_constant']
    PREMIUM_RATE = PENSION_ASSUMPTIONS['premium_rate']
    MIN_INCOME = PENSION_ASSUMPTIONS['min_income']
    MAX_INCOME = PENSION_ASSUMPTIONS['max_income']

    # B값: 사용자의 현재 소득을 상/하한선 내로 보정
    B_VALUE = max(MIN_INCOME, min(income, MAX_INCOME)) if income > 0 else 0

    # 가입 기간은 사용자 입력만 사용하며 임의 가입 시작 연령을 만들지 않는다.
    public_pension = next(
        (asset for asset in session.pension_assets.all() if _is_public_pension(asset)),
        None,
    )
    total_months = (
        max(0, _to_int(public_pension.payment_period_years) * 12)
        if public_pension is not None and public_pension.payment_period_years
        else 0
    )
    
    # 20년(240개월) 초과 가입월수 (n)
    n = max(0, total_months - 240)

    # 지급률 = 가입 10년(120개월) 50%부터 시작, 1년마다 5% 가산
    if total_months < 120:
        payment_rate = 0.0
    else:
        # 사진의 산식을 코드로 번역 (10년일때 0.5, 20년일때 1.0)
        payment_rate = 0.50 + ((total_months - 120) / 12) * 0.05

    # 3. 기본연금액 및 조기/연기 조정
    # 기본연금액 추정 (연간 금액) = 1.29 * (A + B) * (1 + 0.05n/12)
    base_pension_annual = CONSTANT * (A_VALUE + B_VALUE) * (1 + 0.05 * (n / 12))

    normal_start_age = _normal_public_pension_start_age(current_age)
    expected_start_age = _public_pension_start_age(session) or normal_start_age

    # 조기/연기 수급에 따른 가감률 계산
    # 조기: 1개월당 -0.5% / 연기: 1개월당 +0.6%
    diff_months = (expected_start_age - normal_start_age) * 12
    adjustment_rate = 0.0

    if diff_months < 0:
        adjustment_rate = diff_months * abs(
            CALCULATION_POLICY['national_pension']['early_adjustment_per_month']
        )
    elif diff_months > 0:
        adjustment_rate = diff_months * CALCULATION_POLICY[
            'national_pension'
        ]['delayed_adjustment_per_month']

    # 조기/연기 반영 후 금액. base_pension_annual 산식에 가입기간 보정이 이미 반영되어
    # payment_rate를 다시 곱하지 않는다.
    adjusted_annual = base_pension_annual * (1 + adjustment_rate) if total_months >= 120 else 0

    # 부양가족연금액 (일단 0원 처리, 필요시 dependents JSON에서 가족 수 파싱하여 가산)
    dependent_pension_annual = 0

    # 최종 추정액 산출 (연간 & 월간)
    user_entered_monthly = _public_pension_monthly(session)
    if user_entered_monthly:
        final_monthly_est = user_entered_monthly
        final_annual_est = user_entered_monthly * 12
        estimate_method = 'user_input'
    else:
        final_annual_est = adjusted_annual + dependent_pension_annual
        final_monthly_est = final_annual_est / 12 if final_annual_est > 0 else 0
        estimate_method = 'simple_formula' if total_months else 'unavailable'

    # 4. 연도별 국민연금 스케줄 (물가 연동 그래프용)
    end_age = basic_info.end_age or DEFAULT_ASSUMPTIONS['plan_end_age']
    schedule = []
    
    current_year = timezone.now().year if timezone.now() else 2024
    start_year = current_year + max(0, expected_start_age - current_age)

    if final_annual_est > 0:
        for age in range(expected_start_age, end_age + 1):
            t = age - expected_start_age # 수급 개시 후 경과 년수
            fv_annual = final_annual_est * ((1 + inflation_rate) ** t)
            
            schedule.append({
                'age': age,
                'year': start_year + t,
                'monthly_amount': int(fv_annual / 12),
                'annual_amount': int(fv_annual)
            })

    return {
        'assumptions': _assumptions_payload(
            inflation_rate=inflation_rate,
            pension_formula={
                **PENSION_ASSUMPTIONS,
                'current_a_value': A_VALUE,
                'income_range_min': MIN_INCOME,
                'income_range_max': MAX_INCOME,
                'estimate_method_priority': 'user_input_then_simple_formula',
            },
        ),
        'results': {
            'estimate_method': estimate_method,
            'pension_start_age': expected_start_age,
            'early_pension_age': normal_start_age - 5,
            'total_months': total_months,
            'exceed_20yr_months': n,
            'basic_pension_annual_est': int(base_pension_annual),  # 사진 속 7,989,629원에 해당하는 연간 금액
            'payment_rate': round(payment_rate, 4),
            'adjusted_annual_est': int(adjusted_annual),
            'dependent_pension': int(dependent_pension_annual),
            'final_monthly_est': int(final_monthly_est)            # 실제 매월 통장에 꽂히는 금액
        },
        'yearly_schedule': schedule
    }


# 생애현금흐름 및 생애순자산 계산
def calculate_lifetime_cashflow(
    session, 
    inflation_rate=None,
    investment_rate=None,
    wage_growth_rate=None,
    re_growth_rate=None
):

    basic_info = getattr(session, 'basic_info', None)
    if not basic_info:
        return {}

    inflation_rate = _resolve_rate(inflation_rate, 'conservative_inflation_rate')
    investment_rate = _resolve_rate(investment_rate, 'financial_return_rate')
    wage_growth_rate = _resolve_rate(wage_growth_rate, 'wage_growth_rate')
    re_growth_rate = _resolve_rate(re_growth_rate, 'real_estate_growth_rate')
    current_age = basic_info.age or 30
    retirement_age = basic_info.retirement_age or 60
    end_age = basic_info.end_age or 90

    # 1. 초기 자산 및 현재 현금흐름 세팅
    # 유동자산(금융자산) 및 부동산 자산 초기값
    liquid_assets = sum(_to_int(a.current_value) for a in session.financial_assets.all())
    pension_assets = sum(_to_int(a.current_value) for a in session.pension_assets.all())
    real_estate_assets = sum(_to_int(a.current_value) for a in session.real_estate_assets.all())
    liability_balance = sum(_to_int(liability.balance) for liability in session.liabilities.all())
    
    # 현재 연간 소득 및 지출 (은퇴 전 베이스라인)
    base_annual_income = sum(_to_int(i.amount) for i in session.incomes.all()) * 12
    base_annual_expense = sum(_to_int(e.amount) for e in session.expenses.all()) * 12

    # 은퇴 후 부동산 연 임대소득 베이스라인
    base_annual_rental = sum(_to_int(a.monthly_rental_income) for a in session.real_estate_assets.all()) * 12

    # 2. 은퇴 후 단계별 필요 지출액 파싱
    retirement_phases = []
    for period in session.goal_periods.filter(phase='after').prefetch_related('items'):
        period_total = sum(_to_int(item.amount) for item in period.items.all())
        retirement_phases.append({
            'from_age': period.from_age,
            'to_age': period.to_age,
            'annual_expense': period_total * 12
        })

    def _get_retirement_expense(age):
        for phase in retirement_phases:
            if phase['from_age'] <= age <= phase['to_age']:
                return phase['annual_expense']
        # 매칭되는 구간이 없으면 마지막 구간 혹은 0 반환
        return retirement_phases[-1]['annual_expense'] if retirement_phases else 0

    # 3. 은퇴 후 연금 소득 파싱
    pensions = []
    for p in session.pension_assets.all():
        if p.expected_amount:
            pensions.append({
                'start_age': p.pension_start_age or retirement_age,
                'annual_amount': _to_int(p.expected_amount) * 12
            })

    def _get_pension_income(age):
        return sum(p['annual_amount'] for p in pensions if p['start_age'] <= age)

    # 4. 연도별 시뮬레이션 루프 (Age-by-Age)
    yearly_data = []
    explanation_components = []
    depletion_age = None

    explanation_events = ([{
        'type': 'retirement_start',
        'age': retirement_age,
    }] if current_age <= retirement_age <= end_age else [])
    explanation_events.extend({
        'type': 'pension_start',
        'age': pension['start_age'],
        'annual_amount': pension['annual_amount'],
    } for pension in pensions if current_age <= pension['start_age'] <= end_age)
    explanation_events.extend({
        'type': 'expense_phase_change',
        'age': phase['from_age'],
        'annual_expense': phase['annual_expense'],
    } for phase in retirement_phases if current_age <= phase['from_age'] <= end_age)

    for age in range(current_age, end_age + 1):
        t_from_now = age - current_age
        
        # --- (1) 현금 유입/유출 산출 ---
        if age < retirement_age:
            # 은퇴 전: 근로/사업 소득 + 현재 생활비 (각각 상승률/물가 반영)
            inflow = int(base_annual_income * ((1 + wage_growth_rate) ** t_from_now))
            outflow = int(base_annual_expense * ((1 + inflation_rate) ** t_from_now))
            inflow_components = {'earned_and_other_income': inflow}
            outflow_components = {'living_expenses': outflow}
        else:
            # 은퇴 후: 연금 + 임대소득 / 은퇴기별 생활비
            t_from_retire = age - retirement_age
            pension_in = _get_pension_income(age) # 연금은 이미 물가 반영된 스케줄로 가정하거나 고정
            rental_in = int(base_annual_rental * ((1 + inflation_rate) ** t_from_retire))
            inflow = pension_in + rental_in
            inflow_components = {
                'pension_income': pension_in,
                'rental_income': rental_in,
            }
            
            # 은퇴 후 생활비는 현재가치로 입력되었으므로 물가상승률 반영
            base_expense = _get_retirement_expense(age)
            outflow = int(base_expense * ((1 + inflation_rate) ** t_from_now))
            outflow_components = {'retirement_living_expenses': outflow}
            
        net_flow = inflow - outflow

        # --- (2) 자산 평가액 업데이트 ---
        # 유동자산이 남아있을 때만 투자 수익이 발생한다고 가정
        if liquid_assets > 0:
            liquid_assets = int(liquid_assets * (1 + investment_rate))
            
        # 순현금흐름(Net Flow)을 유동자산에 가감
        liquid_assets += net_flow
        
        # 부동산 자산 가치 상승 반영
        real_estate_assets = int(real_estate_assets * (1 + re_growth_rate))
        pension_assets = int(pension_assets * (1 + investment_rate))
        
        # 생애 순자산 = 금융자산 + 연금자산 + 부동산 - 부채
        total_net_worth = liquid_assets + pension_assets + real_estate_assets - liability_balance

        # --- (3) 금융자산 고갈 시점 체크 ---
        if liquid_assets < 0 and depletion_age is None:
            depletion_age = age

        # --- (4) 연도별 데이터 적재 ---
        yearly_data.append({
            'age': age,
            'cash_inflow': inflow,           # Green Bar (image_6f731f)
            'cash_outflow': -outflow,        # Blue Bar (음수 표현용)
            'net_flow': net_flow,            # Red Bar (음수면 적자)
            'liquid_assets': liquid_assets,  # Blue Line (image_6f75e4, image_6f7683의 A 곡선)
            'pension_assets': pension_assets,
            'real_estate_assets': real_estate_assets,
            'liabilities': liability_balance,
            'total_net_worth': total_net_worth # Orange Line
        })
        explanation_components.append({
            'age': age,
            'cash_inflow': inflow_components,
            'cash_outflow': outflow_components,
        })

    # 90세(혹은 end_age) 시점의 최종 부족 자금 (음수일 경우 양수로 변환하여 저장)
    if not yearly_data:
        return {}

    final_liquid = yearly_data[-1]['liquid_assets']
    final_deficit = abs(final_liquid) if final_liquid < 0 else 0

    return {
        'assumptions': _assumptions_payload(
            inflation_rate=inflation_rate,
            investment_rate=investment_rate,
            wage_growth_rate=wage_growth_rate,
            real_estate_growth_rate=re_growth_rate,
        ),
        'depletion_point': {
            'is_depleted': depletion_age is not None,
            'depletion_age': depletion_age
        },
        'final_status': {
            'end_age': end_age,
            'final_deficit': final_deficit,   # Age 90 Deficit (image_6f75e4)
            'final_net_worth': yearly_data[-1]['total_net_worth']
        },
        'chart_data': yearly_data,
        'explanation_data': {
            'events': explanation_events,
            'components_by_age': explanation_components,
        },
    }


# 필요 저축액 계산
def calculate_required_savings(session, total_shortfall, expected_return_rate=None):
    basic_info = getattr(session, 'basic_info', None)
    if not basic_info:
        return {}

    expected_return_rate = _resolve_rate(expected_return_rate, 'financial_return_rate')
    current_age = basic_info.age or 30
    retirement_age = basic_info.retirement_age or 60

    # 1. 현재 월 저축액 산출 (금융자산 + 연금자산의 월 납입액 합산)
    current_monthly_savings = sum(
        _to_int(asset.monthly_savings) 
        for asset in session.financial_assets.all()
    ) + sum(
        _to_int(asset.monthly_savings) 
        for asset in session.pension_assets.all()
    )

    # 2. 추가 필요 월 저축액 계산 (PMT 공식 적용)
    # PMT = FV * r / ((1+r)^n - 1)
    months_to_retire = max(1, (retirement_age - current_age) * 12)
    monthly_rate = expected_return_rate / 12

    if total_shortfall > 0:
        if monthly_rate > 0:
            additional_required_savings = int(
                total_shortfall * monthly_rate / (((1 + monthly_rate) ** months_to_retire) - 1)
            )
        else:
            # 수익률이 0%로 세팅된 경우 (단순 나누기)
            additional_required_savings = int(total_shortfall / months_to_retire)
    else:
        # 부족 자금이 없으면 추가 저축도 필요 없음
        additional_required_savings = 0

    # 3. 목표 월 저축액 = 현재 저축액 + 추가 필요 저축액
    target_monthly_savings = current_monthly_savings + additional_required_savings

    return {
        'assumptions': _assumptions_payload(
            expected_return_rate=expected_return_rate,
        ),
        'current_monthly_savings': current_monthly_savings,
        'target_monthly_savings': target_monthly_savings,
        'additional_required_savings': additional_required_savings,
        
        # 프론트엔드 슬라이더 시뮬레이션을 위한 기초 데이터 제공
        'simulation_base_data': {
            'current_age': current_age,
            'base_retirement_age': retirement_age,
            'expected_return_rate': expected_return_rate,
            'original_shortfall': total_shortfall,
            # 아래 값들은 은퇴 생활비 조정 및 주택연금 슬라이더 적용을 위해 
            # 세션에서 추정된 '현재가치 기준 월 생활비'와 '부동산 총액'을 넘겨줍니다.
            'base_monthly_expense': _average_monthly_retirement_expense(session),
            'total_real_estate_value': sum(_to_int(a.current_value) for a in session.real_estate_assets.all())
        }
    }


def _build_retirement_phases(retirement_age, end_age):
    retirement_age = _to_int(retirement_age)
    end_age = max(_to_int(end_age), retirement_age)
    total_years = end_age - retirement_age + 1
    base_length, remainder = divmod(total_years, 3)
    lengths = [
        base_length + (1 if remainder > 0 else 0),
        base_length + (1 if remainder > 1 else 0),
        base_length,
    ]

    phases = []
    start_age = retirement_age
    for name, length in zip(('활동기', '회상기', '간병기'), lengths):
        end = start_age + length - 1
        phases.append({
            'name': name,
            'start_age': start_age,
            'end_age': end,
        })
        start_age = end + 1

    return phases


def _retirement_phase_index(phases, age):
    age = _to_int(age)
    for idx, phase in enumerate(phases):
        if phase['start_age'] <= age <= phase['end_age']:
            return idx

    if age < phases[0]['start_age']:
        return 0

    for idx in range(len(phases) - 1, -1, -1):
        if phases[idx]['end_age'] >= phases[idx]['start_age']:
            return idx
    return 0


def _average_monthly_retirement_expense(session):
    weighted_total = 0
    total_years = 0

    # .all() 순회로 prefetch 캐시를 활용한다 (PB 리스트처럼 세션 다건을 다룰 때 N+1 방지)
    after_periods = [period for period in session.goal_periods.all() if period.phase == 'after']
    for period in after_periods:
        years = max(1, _to_int(period.to_age) - _to_int(period.from_age) + 1)
        monthly_total = sum(_to_int(item.amount) for item in period.items.all())
        weighted_total += monthly_total * years
        total_years += years

    if total_years:
        return int(weighted_total / total_years)

    return sum(_to_int(expense.amount) for expense in session.expenses.all())


def _public_pension_monthly(session):
    return sum(
        _to_int(asset.expected_amount)
        for asset in session.pension_assets.all()
        if _is_public_pension(asset)
    )


def _public_pension_start_age(session):
    public_pension = next(
        (asset for asset in session.pension_assets.all() if _is_public_pension(asset)),
        None,
    )
    return public_pension.pension_start_age if public_pension else None


def _normal_public_pension_start_age(current_age):
    birth_year = timezone.now().year - _to_int(current_age)
    for bracket in CALCULATION_POLICY['national_pension']['normal_start_age_by_birth_year']:
        minimum = bracket.get('birth_year_min')
        maximum = bracket.get('birth_year_max')
        if minimum is not None and birth_year < minimum:
            continue
        if maximum is not None and birth_year > maximum:
            continue
        return bracket['age']
    raise ValueError('국민연금 지급개시연령 정책 구간을 찾지 못했습니다.')


def _is_public_pension(asset):
    pension_type = (asset.type or '')
    return '국민' in pension_type or '공무원' in pension_type


def _normalize_rate(value, default):
    if value is None:
        return default

    rate = float(value)
    return rate / 100 if rate > 1 else rate


def _resolve_rate(value, key):
    if value is None:
        return DEFAULT_ASSUMPTIONS[key]
    return _normalize_rate(value, DEFAULT_ASSUMPTIONS[key])


def _assumptions_payload(**overrides):
    return {
        **DEFAULT_ASSUMPTIONS,
        **overrides,
        'calculation_policy_version': active_policy_version(),
        'sources': ASSUMPTION_SOURCES,
    }


def _contains_any(value, keywords):
    return any(keyword in value for keyword in keywords)


def _attach_ratios(categories, total):
    for category in categories.values():
        category['ratio'] = _ratio(category['total'], total)


def _ratio(value, total):
    return round(value / total * 100, 1) if total else 0


def _ratio_or_none(value, total):
    return round(value / total * 100, 1) if total else None


def _to_int(value):
    return int(value or 0)


def _decimal_to_float(value):
    return float(value) if value is not None else None


def _date_to_string(value):
    return value.isoformat() if value else None
