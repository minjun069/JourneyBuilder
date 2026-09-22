"""커뮤니티 작성자 밴드 산출 — 상담 데이터 기반 스냅샷.

글 작성 시점에 작성자의 대표 상담 세션에서 나이/자산/소득 밴드를 계산해
Post 에 문자열로 저장한다. 밴드 라벨은 프론트 필터 옵션 문자열과 동일.
어떤 경우에도 밴드 산출 실패가 글 작성을 실패시키지 않는다 (실패 밴드는 '').

응답 직렬화에 쓰는 작성자 이름 가림(mask_display_name)도 여기에 둔다.
"""
from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone

from consulting.models import ConsultingSession
from reports.calculators import calculate_asset_statement

ACTIVE_STATUSES = (
    ConsultingSession.Status.IN_PROGRESS,
    ConsultingSession.Status.ONBOARDING,
)


def mask_display_name(name):
    """작성자 실명 → 가림 이름. 3자 이상 '김민수'→'김○○', 2자 '김철'→'김○'.

    커뮤니티 응답은 비인증 GET 으로도 열리는데 밴드(자산/소득/연령)가 함께
    나가므로, 실명을 그대로 노출하면 상담 데이터가 특정 개인에 결합된다.
    1자 이름은 가릴 곳이 없어 그대로 두고, 이름이 없으면 '익명'.
    """
    name = (name or '').strip()
    if not name:
        return '익명'
    if len(name) == 1:
        return name
    return name[0] + '○' * (len(name) - 1)


def age_band_label(age):
    """나이 → 연령 밴드. None 이면 '' (미산출)."""
    if age is None:
        return ''
    if age < 30:
        return '20대'
    if age < 40:
        return '30대'
    if age < 50:
        return '40대'
    if age < 60:
        return '50대'
    return '60대+'


def asset_band_label(net_worth):
    """순자산(원) → 자산 밴드. None 이면 '' (미산출)."""
    if net_worth is None:
        return ''
    if net_worth < 100_000_000:
        return '1억 미만'
    if net_worth < 300_000_000:
        return '1~3억'
    if net_worth < 500_000_000:
        return '3~5억'
    if net_worth < 1_000_000_000:
        return '5~10억'
    return '10억+'


def income_band_label(annual_income):
    """연소득(원) → 소득 밴드. None 이면 '' (미산출)."""
    if annual_income is None:
        return ''
    if annual_income < 30_000_000:
        return '3천 미만'
    if annual_income < 50_000_000:
        return '3~5천'
    if annual_income < 70_000_000:
        return '5~7천'
    if annual_income < 100_000_000:
        return '7천~1억'
    return '1억+'


def _representative_session(user):
    """최신 completed → 없으면 최신 진행 세션. abandoned 제외.

    adminapi/services.py 의 대표 세션 선정 패턴과 동일 (adminapi 미의존).
    """
    sessions = list(user.consulting_sessions.all())  # Meta.ordering = -created_at
    for session in sessions:
        if session.status == ConsultingSession.Status.COMPLETED:
            return session
    for session in sessions:
        if session.status in ACTIVE_STATUSES:
            return session
    return None


def _basic_info(session):
    """reverse OneToOne 을 없으면 None 으로 안전 접근."""
    try:
        return session.basic_info
    except ObjectDoesNotExist:
        return None


def derive_author_bands(user):
    """작성자의 (age_band, asset_band, income_band) 튜플을 계산한다.

    밴드별로 독립 try/except — 한 밴드 계산 실패가 다른 밴드를 지우지 않고,
    전체 실패 시에도 ('', '', '') 로 글 작성 자체는 절대 실패시키지 않는다.
    """
    try:
        session = _representative_session(user)
        if session is None:
            return '', '', ''
        basic = _basic_info(session)

        age_band = ''
        try:
            if basic and basic.age:
                age = basic.age
            elif user.birth_year:
                age = timezone.localdate().year - user.birth_year
            else:
                age = None
            age_band = age_band_label(age)
        except Exception:
            age_band = ''

        asset_band = ''
        try:
            stmt = calculate_asset_statement(session)
            # 자산·부채 모두 0 이면 재무 미입력으로 본다 (순자산 0 → '1억 미만' 오분류 방지)
            if stmt['total_assets'] == 0 and stmt['total_liabilities'] == 0:
                net_worth = None
            else:
                net_worth = stmt['net_worth']
            asset_band = asset_band_label(net_worth)
        except Exception:
            asset_band = ''

        income_band = ''
        try:
            monthly_income = basic.monthly_income if basic else None
            annual_income = monthly_income * 12 if monthly_income is not None else None
            income_band = income_band_label(annual_income)
        except Exception:
            income_band = ''

        return age_band, asset_band, income_band
    except Exception:
        return '', '', ''
