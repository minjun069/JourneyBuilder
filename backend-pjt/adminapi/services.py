"""PB 대시보드 집계 계층 (요구사항 정의서 4절).

L1 대표 세션 선정 → L2 정규화(기존 분류기·방어적 형변환은 calculators 가 담당)
→ L3 고객별 파생 스냅샷 → L4 집계(요약 KPI). 전부 요청 시 계산(on-the-fly)이라
60초 폴링(FR-09)만으로 항상 현재 DB 상태가 반영된다.
"""
from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Max, Prefetch
from django.utils import timezone

from consulting.models import ConsultingSession
from reports.calculators import (
    calculate_asset_statement,
    calculate_cash_flow,
    calculate_financial_indicators,
)

from .grading import age_bracket, build_graded_indicators, count_risky

User = get_user_model()

SESSION_NONE = 'none'
ACTIVE_STATUSES = (
    ConsultingSession.Status.IN_PROGRESS,
    ConsultingSession.Status.ONBOARDING,
)

# 자산 규모(S4) 버킷 — (키, 상한 원). 상한 None = 무제한
NET_WORTH_BANDS = (
    ('under_50m', 50_000_000),
    ('50m_200m', 200_000_000),
    ('200m_500m', 500_000_000),
    ('500m_1b', 1_000_000_000),
    ('over_1b', None),
)

# S3 투자 성향 태그 → 3그룹 (요구사항 7절)
TENDENCY_GROUPS = {
    'stable': ('안전 최우선', '안정 추구', '유동성 중시'),
    'balanced': ('균형 추구', '장기 지향'),
    'aggressive': ('성장 추구', '위험 감수', '수익 중시', '단기 지향'),
}


def classify_tendency(tags):
    """복수 태그는 다수결, 동률이면 균형형. 태그 없으면 None (S3)."""
    if not tags:
        return None
    counts = {
        group: sum(1 for tag in tags if tag in group_tags)
        for group, group_tags in TENDENCY_GROUPS.items()
    }
    best = max(counts.values())
    if best == 0:
        return None
    winners = [group for group, count in counts.items() if count == best]
    return winners[0] if len(winners) == 1 else 'balanced'


def health_bucket(risk_count):
    """S5 재무 건전성: 위험 지표 0개=건전 / 1~2개=주의 / 3개+=위험."""
    if risk_count is None:
        return None
    if risk_count == 0:
        return 'healthy'
    if risk_count <= 2:
        return 'caution'
    return 'risky'


def customers_queryset():
    """고객(일반 유저) 목록 — 계산기가 순회하는 모든 하위 테이블을 일괄 로드 (NFR-03)."""
    session_qs = (
        ConsultingSession.objects
        .select_related('basic_info', 'financial_mindset', 'financial_satisfaction')
        .prefetch_related(
            'financial_assets', 'pension_assets', 'real_estate_assets',
            'liabilities', 'incomes', 'expenses',
            'goal_periods__items', 'goal_milestones',
        )
    )
    return (
        User.objects
        .filter(is_staff=False, is_active=True, deleted_at__isnull=True)
        .prefetch_related(Prefetch('consulting_sessions', queryset=session_qs))
        .annotate(
            last_message_at=Max('consulting_sessions__messages__created_at'),
            last_session_at=Max('consulting_sessions__created_at'),
        )
        .order_by('id')
    )


def representative_session(user):
    """L1: 최신 completed → 없으면 최신 진행 세션. abandoned 제외 (Q-05)."""
    sessions = list(user.consulting_sessions.all())  # Meta.ordering = -created_at
    for session in sessions:
        if session.status == ConsultingSession.Status.COMPLETED:
            return session
    for session in sessions:
        if session.status in ACTIVE_STATUSES:
            return session
    return None


def _rel(instance, name):
    """select_related 된 reverse OneToOne 을 없으면 None 으로 안전 접근."""
    try:
        return getattr(instance, name)
    except ObjectDoesNotExist:
        return None


def customer_age(user, basic_info):
    if basic_info and basic_info.age:
        return basic_info.age
    if user.birth_year:
        return timezone.localdate().year - user.birth_year
    return None


def net_worth_band(net_worth):
    if net_worth is None:
        return None
    for key, upper in NET_WORTH_BANDS:
        if upper is None or net_worth < upper:
            return key
    return NET_WORTH_BANDS[-1][0]


def build_snapshot(session, age):
    """L3: 대표 세션에 기존 계산기를 적용한 고객별 파생 스냅샷."""
    asset_data = calculate_asset_statement(session)
    cash_flow_data = calculate_cash_flow(session)
    raw_indicators = calculate_financial_indicators(session, asset_data, cash_flow_data)
    indicators = build_graded_indicators(raw_indicators, age)
    return {
        'asset_statement': asset_data,
        'cash_flow': cash_flow_data,
        'indicators': indicators,
        'risk_count': count_risky(indicators),
    }


def _session_status(user, session):
    if session:
        return str(session.status)
    if user.consulting_sessions.all():
        return str(ConsultingSession.Status.ABANDONED)  # 이탈 세션만 있는 고객 (S6 '이탈')
    return SESSION_NONE


def has_financial_input(snapshot):
    """자산·부채·현금흐름이 전부 비어 있으면 미입력으로 본다.

    미입력 고객을 '순자산 0원'으로 표시하면 오해를 부르므로 공란 처리 (NFR-04).
    """
    return bool(
        snapshot['asset_statement']['total_assets']
        or snapshot['asset_statement']['total_liabilities']
        or snapshot['cash_flow']['total_cash_inflow']
    )


def _last_activity(user):
    candidates = [
        getattr(user, 'last_message_at', None),
        getattr(user, 'last_session_at', None),
    ]
    candidates = [value for value in candidates if value]
    return max(candidates) if candidates else None


def build_customer_row(user, with_indicators=False):
    """FR-03 고객 리스트 한 행. 세션 없는 고객은 지표를 공란(None) 처리 (NFR-04).

    with_indicators=True 면 세그먼트 집계용으로 지표 등급 목록을 '_indicators' 에 담는다.
    """
    session = representative_session(user)
    basic = _rel(session, 'basic_info') if session else None
    age = customer_age(user, basic)

    row = {
        'user_id': user.id,
        'name': user.name,
        'email': user.email,
        'age': age,
        'age_band': age_bracket(age) if age else None,
        'job': basic.job if basic else None,
        'need': basic.need if basic else None,
        'tendency_group': classify_tendency(basic.tendency if basic else None),
        'session_status': _session_status(user, session),
        'progress_pct': session.get_progress_pct() if session else 0,
        'net_worth': None,
        'net_worth_band': None,
        'monthly_surplus': None,
        'risk_count': None,
        'health': None,
        'last_activity': _last_activity(user),
    }

    if session:
        snapshot = build_snapshot(session, age)
        if has_financial_input(snapshot):
            row['net_worth'] = snapshot['asset_statement']['net_worth']
            row['net_worth_band'] = net_worth_band(row['net_worth'])
            row['monthly_surplus'] = snapshot['cash_flow']['net_cash_flow']
            row['risk_count'] = snapshot['risk_count']
            row['health'] = health_bucket(row['risk_count'])
            if with_indicators:
                row['_indicators'] = snapshot['indicators']
    return row


def build_customer_detail(user):
    """FR-04·05 고객 상세 — 대표 세션 전 섹션 + L3 파생."""
    session = representative_session(user)
    basic = _rel(session, 'basic_info') if session else None
    mindset = _rel(session, 'financial_mindset') if session else None
    satisfaction = _rel(session, 'financial_satisfaction') if session else None
    age = customer_age(user, basic)

    detail = {
        'profile': {
            'user_id': user.id,
            'name': user.name,
            'email': user.email,
            'phone': user.phone,  # NFR-02: 전화번호는 상세에서만
            'joined_at': user.created_at,
            'last_activity': _last_activity(user),
        },
        'basic_info': _serialize_basic_info(basic),
        'session': _serialize_session(session, with_progress=True),
        'sessions': [
            _serialize_session(item) for item in user.consulting_sessions.all()
        ],
        'derived': None,
        'asset_statement': None,
        'cash_flow': None,
        'goals': _serialize_goals(session),
        'mindset': _serialize_mindset(mindset),
        'satisfaction': _serialize_satisfaction(satisfaction),
    }

    if session:
        snapshot = build_snapshot(session, age)
        asset_data = snapshot['asset_statement']
        cash_flow_data = snapshot['cash_flow']
        detail['asset_statement'] = asset_data
        detail['cash_flow'] = cash_flow_data
        detail['derived'] = {
            'has_financial_input': has_financial_input(snapshot),
            'age': age,
            'total_assets': asset_data['total_assets'],
            'total_liabilities': asset_data['total_liabilities'],
            'net_worth': asset_data['net_worth'],
            'debt_to_asset_ratio': asset_data['debt_to_asset_ratio'],
            'monthly_income': cash_flow_data['total_cash_inflow'],
            'monthly_expense': cash_flow_data['total_cash_outflow'],
            'monthly_surplus': cash_flow_data['net_cash_flow'],
            'savings_rate': cash_flow_data['savings_investment_rate'],
            'indicators': snapshot['indicators'],
            'risk_count': snapshot['risk_count'],
        }
    return detail


def build_summary():
    """FR-06 개요 KPI + 최근 상담 활동."""
    rows = [build_customer_row(user) for user in customers_queryset()]
    customer_count = len(rows)
    completed_count = sum(
        1 for row in rows if row['session_status'] == ConsultingSession.Status.COMPLETED
    )
    risk_customer_count = sum(
        1 for row in rows if row['risk_count'] is not None and row['risk_count'] >= 3
    )
    recent_sessions = (
        ConsultingSession.objects
        .filter(user__is_staff=False, user__is_active=True)
        .select_related('user')
        .order_by('-created_at')[:10]
    )
    return {
        'kpi': {
            'customer_count': customer_count,
            'completed_rate': round(completed_count / customer_count * 100, 1) if customer_count else 0,
            'total_net_worth': sum(row['net_worth'] or 0 for row in rows),
            'risk_customer_count': risk_customer_count,
        },
        'recent_activities': [
            {
                'user_id': session.user_id,
                'name': session.user.name,
                'session_id': session.id,
                'status': session.status,
                'created_at': session.created_at,
                'completed_at': session.completed_at,
            }
            for session in recent_sessions
        ],
    }


# 세그먼트 축별 버킷 순서 (없는 버킷은 count 0 으로 채운다). 'unknown' = 미입력
SEGMENT_AXES = {
    'age_band': ('20s', '30s', '40s', '50s', '65+', 'unknown'),
    'need': ('은퇴', '투자', '주택자금', '교육자금', '보장', '기타', 'unknown'),
    'tendency_group': ('stable', 'balanced', 'aggressive', 'unknown'),
    'net_worth_band': tuple(key for key, _ in NET_WORTH_BANDS) + ('unknown',),
    'health': ('healthy', 'caution', 'risky', 'unknown'),
    'session_status': ('onboarding', 'in_progress', 'completed', 'abandoned', SESSION_NONE),
}


def build_segments():
    """FR-07: 6개 축 분포 + 지표별 등급 스택 + 연령×순자산 산점도."""
    rows = []
    indicator_stack = {}
    scatter = []

    for user in customers_queryset():
        row = build_customer_row(user, with_indicators=True)
        for indicator in row.pop('_indicators', []):
            stack = indicator_stack.setdefault(
                indicator['key'], {'양호': 0, '주의': 0, '위험': 0})
            stack[indicator['grade']] += 1
        rows.append(row)
        if row['age'] is not None and row['net_worth'] is not None:
            scatter.append({
                'user_id': row['user_id'],
                'name': row['name'],
                'age': row['age'],
                'net_worth': row['net_worth'],
                'need': row['need'],
                'monthly_surplus': row['monthly_surplus'],
            })

    axes = {}
    for axis, buckets in SEGMENT_AXES.items():
        counts = {bucket: 0 for bucket in buckets}
        for row in rows:
            bucket = row[axis] if row[axis] is not None else 'unknown'
            counts[bucket] = counts.get(bucket, 0) + 1
        axes[axis] = [
            {'bucket': bucket, 'count': count} for bucket, count in counts.items()
        ]

    return {
        'axes': axes,
        'indicator_risk': [
            {'key': key, **stack} for key, stack in indicator_stack.items()
        ],
        'scatter': scatter,
    }


def _serialize_session(session, with_progress=False):
    if not session:
        return None
    data = {
        'id': session.id,
        'status': session.status,
        'created_at': session.created_at,
        'completed_at': session.completed_at,
    }
    if with_progress:
        data['progress_pct'] = session.get_progress_pct()
        data['section_completion'] = session.get_section_completion()
    return data


def _serialize_basic_info(basic):
    if not basic:
        return None
    return {
        'age': basic.age,
        'job': basic.job,
        'marital_status': basic.marital_status,
        'dependents': basic.dependents,
        'tendency': basic.tendency,
        'interest': basic.interest,
        'need': basic.need,
        'monthly_income': basic.monthly_income,
        'retirement_age': basic.retirement_age,
        'end_age': basic.end_age,
    }


def _serialize_goals(session):
    if not session:
        return {'periods': [], 'milestones': []}
    return {
        'periods': [
            {
                'id': period.id,
                'phase': period.phase,
                'from_age': period.from_age,
                'to_age': period.to_age,
                'items': [
                    {'name': item.name, 'amount': item.amount, 'is_fixed': item.is_fixed}
                    for item in period.items.all()
                ],
            }
            for period in session.goal_periods.all()
        ],
        'milestones': [
            {
                'id': milestone.id,
                'phase': milestone.phase,
                'age': milestone.age,
                'category': milestone.category,
                'content': milestone.content,
                'amount': milestone.amount,
                'note': milestone.note,
            }
            for milestone in session.goal_milestones.all()
        ],
    }


def _serialize_mindset(mindset):
    if not mindset:
        return None
    return {
        'values': mindset.values,
        'past_experience': mindset.past_experience,
        'current_situation': mindset.current_situation,
        'current_challenge': mindset.current_challenge,
        'invest_philosophy': mindset.invest_philosophy,
        'future_goals': mindset.future_goals,
    }


def _serialize_satisfaction(satisfaction):
    if not satisfaction:
        return None
    return {
        'cashflow_score': satisfaction.cashflow_score,
        'risk_score': satisfaction.risk_score,
        'investment_score': satisfaction.investment_score,
        'management_score': satisfaction.management_score,
        'inheritance_score': satisfaction.inheritance_score,
    }
