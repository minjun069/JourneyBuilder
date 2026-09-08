"""FR-08 시연용 시드 데이터 — 가상 고객 + 상담 세션 + 전 섹션 데이터.

세그먼트 6축(S1 연령·S2 니즈·S3 성향·S4 자산규모·S5 건전성·S6 진행)이
고르게 분포하도록 순환 배정한다. 금액 항목의 type/name 은
reports/calculators.py 의 키워드 분류기가 인식하는 값만 사용한다.

이메일이 @retirewise.demo 로 끝나는 계정만 만들고, 재실행 시 그 계정들만
지우고 다시 만든다 (실사용 데이터는 건드리지 않음).
"""
import random
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from consulting.models import (
    BasicInfo,
    ConsultingSession,
    Expense,
    FinancialAsset,
    FinancialGoalMilestone,
    FinancialGoalPeriod,
    FinancialGoalPeriodItem,
    FinancialMindset,
    FinancialSatisfaction,
    Income,
    Liability,
    Message,
    PensionAsset,
    RealEstateAsset,
)

DEMO_DOMAIN = 'retirewise.demo'
DEMO_PASSWORD = 'demo-pass-2026!'

LAST_NAMES = '김이박최정강조윤장임한오서신권황안송류전홍'
FIRST_NAMES = (
    '민준', '서연', '도윤', '지우', '하준', '서준', '지호', '수아', '지민', '예은',
    '현우', '유진', '준서', '채원', '건우', '다은', '지훈', '소율', '태윤', '가은',
)

# S1: 연령 브래킷 (지표 스키마와 동일 경계)
AGE_BANDS = ((20, 29), (30, 39), (40, 49), (50, 64), (65, 75))
# S2: 니즈
NEEDS = [choice[0] for choice in BasicInfo.Need.choices]
# S3: 투자 성향 태그 → 안정형/균형형/공격형 (요구사항 7절 매핑)
TENDENCY_GROUPS = (
    ['안전 최우선', '안정 추구', '유동성 중시'],
    ['균형 추구', '장기 지향'],
    ['성장 추구', '위험 감수', '수익 중시', '단기 지향'],
)
# S4: 순자산 목표 구간 (원)
NET_WORTH_TARGETS = (
    (5_000_000, 45_000_000),
    (55_000_000, 190_000_000),
    (210_000_000, 480_000_000),
    (520_000_000, 950_000_000),
    (1_050_000_000, 3_000_000_000),
)
# S5: 건전성 프로필 — (저축여력, 부채성향) 을 조절해 건전/주의/위험이 섞이게 함
HEALTH_PROFILES = ('healthy', 'caution', 'risky')
# S6: 상담 진행 상태 분포
STATUS_POOL = (
    ['completed'] * 7 + ['in_progress'] * 1 + ['onboarding'] * 1 + ['abandoned'] * 1
)

JOBS_BY_BAND = {
    0: ['직장인', '직장인', '프리랜서', '학생', '기타'],
    1: ['직장인', '직장인', '공무원', '자영업자', '프리랜서'],
    2: ['직장인', '공무원', '자영업자', '자영업자', '전업주부'],
    3: ['직장인', '자영업자', '공무원', '전업주부', '기타'],
    4: ['퇴직자', '퇴직자', '퇴직자', '자영업자', '기타'],
}

MINDSET_TEMPLATES = {
    'values': '가족의 안정적인 생활을 최우선으로 생각합니다.',
    'past_experience': '과거 {exp}을(를) 경험하면서 재무 관리의 중요성을 느꼈습니다.',
    'current_situation': '현재 소득은 안정적이지만 지출 관리가 과제입니다.',
    'current_challenge': '{need} 준비 자금을 어떻게 마련할지 고민입니다.',
    'invest_philosophy': '{tendency} 성향으로 자산을 운용하고 싶습니다.',
    'future_goals': '{retirement_age}세에 은퇴해 여유로운 노후를 보내는 것이 목표입니다.',
}
EXPERIENCES = ('주식 손실', '전세 사기 걱정', '적금 만기 수령', '코인 투자', '부동산 매매')


class Command(BaseCommand):
    help = 'PB 대시보드 시연용 가상 고객 데이터를 생성한다 (FR-08).'

    def add_arguments(self, parser):
        parser.add_argument('--count', type=int, default=40, help='생성할 고객 수 (기본 40)')
        parser.add_argument('--seed', type=int, default=42, help='난수 시드 (기본 42, 재현 가능)')

    @transaction.atomic
    def handle(self, *args, **options):
        count = options['count']
        rng = random.Random(options['seed'])
        User = get_user_model()

        deleted, _ = User.objects.filter(email__endswith=f'@{DEMO_DOMAIN}').delete()
        if deleted:
            self.stdout.write(f'기존 데모 데이터 {deleted}행 삭제')

        now = timezone.now()
        status_counts = {}
        # 연령·자산규모 배정을 셔플 — 순환(index 나머지)끼리 겹치면 특정 축이
        # 특정 상태에만 몰리는 aliasing 이 생긴다
        age_pool = [i % len(AGE_BANDS) for i in range(count)]
        band_pool = [i % len(NET_WORTH_TARGETS) for i in range(count)]
        rng.shuffle(age_pool)
        rng.shuffle(band_pool)
        for index in range(count):
            profile = self._make_profile(index, age_pool[index], band_pool[index], rng, now)
            self._create_customer(User, profile, rng, now)
            status_counts[profile['status']] = status_counts.get(profile['status'], 0) + 1

        self.stdout.write(self.style.SUCCESS(
            f'가상 고객 {count}명 생성 완료 (비밀번호 공통: {DEMO_PASSWORD})'
        ))
        self.stdout.write(f'상태 분포: {status_counts}')

    # ── 프로필 설계: 세그먼트 축별 균등 배정 (연령·자산은 셔플 풀) ─────────
    def _make_profile(self, index, band_index, net_band_index, rng, now):
        age = rng.randint(*AGE_BANDS[band_index])
        health = HEALTH_PROFILES[(index // len(AGE_BANDS)) % len(HEALTH_PROFILES)]
        tendency_group = TENDENCY_GROUPS[index % len(TENDENCY_GROUPS)]
        net_lo, net_hi = NET_WORTH_TARGETS[net_band_index]
        status = STATUS_POOL[index % len(STATUS_POOL)]
        # 5% 는 세션 없는 고객 (S6 '세션 없음' 버킷)
        if index % 20 == 19:
            status = 'no_session'

        return {
            'index': index,
            'age': age,
            'band_index': band_index,
            'health': health,
            'tendency': rng.sample(tendency_group, k=min(2, len(tendency_group))),
            'need': NEEDS[index % len(NEEDS)],
            'job': rng.choice(JOBS_BY_BAND[band_index]),
            'net_worth_target': rng.randint(net_lo, net_hi),
            'status': status,
            'name': rng.choice(LAST_NAMES) + rng.choice(FIRST_NAMES),
            'created_days_ago': rng.randint(1, 45),
        }

    def _create_customer(self, User, profile, rng, now):
        user = User.objects.create_user(
            email=f"demo{profile['index'] + 1:03d}@{DEMO_DOMAIN}",
            password=DEMO_PASSWORD,
            name=profile['name'],
            birth_year=now.year - profile['age'],
            phone=f"010-{rng.randint(1000, 9999)}-{rng.randint(1000, 9999)}",
        )
        if profile['status'] == 'no_session':
            return

        session = ConsultingSession.objects.create(user=user, status=ConsultingSession.Status.ONBOARDING)
        created_at = now - timedelta(days=profile['created_days_ago'], hours=rng.randint(0, 12))
        ConsultingSession.objects.filter(pk=session.pk).update(created_at=created_at)

        monthly_income = self._monthly_income(profile, rng)
        self._create_basic_info(session, profile, monthly_income, rng)
        self._create_messages(session, created_at, rng)

        if profile['status'] == 'onboarding':
            return
        if profile['status'] == 'abandoned':
            ConsultingSession.objects.filter(pk=session.pk).update(
                status=ConsultingSession.Status.ABANDONED)
            return

        self._create_finances(session, profile, monthly_income, rng)
        self._create_goals_mindset_satisfaction(session, profile, monthly_income, rng)

        if profile['status'] == 'in_progress':
            # 일부 섹션(만족도)을 비워 진행 중 상태를 유지한다
            FinancialSatisfaction.objects.filter(session=session).delete()
            ConsultingSession.objects.filter(pk=session.pk).update(
                status=ConsultingSession.Status.IN_PROGRESS)
            return

        ConsultingSession.objects.filter(pk=session.pk).update(
            status=ConsultingSession.Status.COMPLETED,
            completed_at=created_at + timedelta(days=rng.randint(0, 3)),
        )

    def _monthly_income(self, profile, rng):
        base_by_band = (3_200_000, 4_800_000, 6_500_000, 7_500_000, 3_000_000)
        base = base_by_band[profile['band_index']]
        wealth_boost = 1 + (profile['net_worth_target'] / 3_000_000_000)
        return int(base * rng.uniform(0.75, 1.35) * wealth_boost)

    def _create_basic_info(self, session, profile, monthly_income, rng):
        dependents = {}
        if profile['age'] >= 35 and rng.random() < 0.6:
            dependents = {'children': [{'age': max(1, profile['age'] - rng.randint(28, 34))}]}
        BasicInfo.objects.create(
            session=session,
            age=profile['age'],
            job=profile['job'],
            marital_status='기혼' if profile['age'] >= 33 and rng.random() < 0.7 else '미혼',
            dependents=dependents,
            tendency=profile['tendency'],
            interest=profile['need'] + ' 설계',
            need=profile['need'],
            monthly_income=monthly_income,
            retirement_age=rng.choice([60, 62, 65]),
            end_age=rng.choice([85, 88, 90]),
        )

    # ── 재무 데이터: 순자산 목표·건전성 프로필에 맞게 생성 ──────────────
    def _monthly_budget(self, profile, rng):
        """월 소득 대비 비율 예산. healthy 는 가계수지·총저축성향 지표의
        연령별 양호 기준을 동시에 충족하도록 예산식으로 계산한다
        (단, 20대는 기준 특성상 가계수지 충족 불가 — 주의/위험 1개 허용)."""
        health = profile['health']
        if health == 'healthy':
            tax_rate = 0.10
            good_from = (50, 30, 20, 10, 5)[profile['band_index']]
            savings_total = min(0.45, (good_from + 3) / 100 * (1 - tax_rate))
            surplus = (good_from + 3) / 100
            insurance = rng.uniform(0.08, 0.10)
            living = max(0.05, 1 - tax_rate - insurance - savings_total - surplus)
            pension = min(savings_total * 0.45, 0.16)
            return {
                'tax': tax_rate, 'insurance': insurance, 'living': living,
                'deposit_saving': savings_total - pension, 'pension_saving': pension,
                'dsr': 0.0,
            }
        if health == 'caution':
            return {
                'tax': rng.uniform(0.09, 0.13), 'insurance': rng.uniform(0.05, 0.075),
                'living': rng.uniform(0.4, 0.55), 'deposit_saving': rng.uniform(0.05, 0.1),
                'pension_saving': rng.uniform(0.03, 0.07), 'dsr': rng.uniform(0.2, 0.35),
            }
        return {
            'tax': rng.uniform(0.09, 0.13), 'insurance': rng.uniform(0.01, 0.04),
            'living': rng.uniform(0.55, 0.8), 'deposit_saving': rng.uniform(0.0, 0.04),
            'pension_saving': rng.uniform(0.0, 0.02), 'dsr': rng.uniform(0.42, 0.6),
        }

    def _create_finances(self, session, profile, monthly_income, rng):
        health = profile['health']
        budget = self._monthly_budget(profile, rng)
        target_net = profile['net_worth_target']
        debt_ratio = {'healthy': rng.uniform(0.0, 0.15),
                      'caution': rng.uniform(0.25, 0.45),
                      'risky': rng.uniform(0.55, 0.75)}[health]
        total_assets = int(target_net / (1 - debt_ratio)) if debt_ratio < 1 else target_net
        total_debt = total_assets - target_net

        # 자산 배분 — 건전 프로필은 금융자산비중지표(40%+)를 충족하도록 상향
        aggressive = any(tag in TENDENCY_GROUPS[2] for tag in profile['tendency'])
        if health == 'healthy':
            cash_share = rng.uniform(0.25, 0.4)
            invest_share = rng.uniform(0.2, 0.3)
        else:
            cash_share = rng.uniform(0.03, 0.12)
            invest_share = rng.uniform(0.25, 0.45) if aggressive else rng.uniform(0.05, 0.2)
        pension_share = rng.uniform(0.05, 0.15)
        estate_share = max(0.0, 1 - invest_share - cash_share - pension_share)

        cash_total = int(total_assets * cash_share)
        FinancialAsset.objects.create(
            session=session, asset_type='예금/적금', name='주거래 예금',
            principal=int(cash_total * 0.9), current_value=cash_total,
            monthly_savings=int(monthly_income * budget['deposit_saving']),
            purpose='비상자금',
        )
        invest_total = int(total_assets * invest_share)
        if invest_total:
            FinancialAsset.objects.create(
                session=session, asset_type='주식', name='국내외 주식·ETF',
                principal=int(invest_total * rng.uniform(0.8, 1.1)),
                current_value=invest_total,
                monthly_savings=0, purpose='자산증식',
            )

        # 연금 — 국민연금(공적) + 연금저축(사적, 노후대비저축지표 원천)
        pension_total = int(total_assets * pension_share)
        PensionAsset.objects.create(
            session=session, type='국민연금', name='국민연금',
            current_value=0, monthly_savings=0,
            receive_method='연금형', pension_start_age=65,
            expected_amount=(rng.randint(100, 160) if health == 'healthy'
                             else rng.randint(60, 110)) * 10_000,
        )
        PensionAsset.objects.create(
            session=session, type='연금저축', name='연금저축펀드',
            current_value=pension_total,
            monthly_savings=int(monthly_income * budget['pension_saving']),
            receive_method='연금형', pension_start_age=65, pension_period=20,
        )

        # 부동산 — 고액 자산가는 아파트, 소액은 보증금 (완료 요건 충족용으로 전원 1건)
        estate_total = int(total_assets * estate_share)
        if estate_total >= 150_000_000:
            RealEstateAsset.objects.create(
                session=session, type='아파트', name='거주용 아파트',
                current_value=estate_total,
                acquisition_price=int(estate_total * rng.uniform(0.7, 0.95)),
            )
        else:
            RealEstateAsset.objects.create(
                session=session, type='전세보증금', name='거주지 보증금',
                current_value=max(estate_total, 20_000_000),
            )

        # 부채 — 위험 프로필일수록 단기부채 비중 증가
        if total_debt > 0:
            short_share = {'healthy': 0.0, 'caution': 0.2, 'risky': 0.4}[health]
            long_amount = int(total_debt * (1 - short_share))
            if long_amount:
                Liability.objects.create(
                    session=session, type='주택담보대출', name='주담대',
                    balance=long_amount, interest_rate=rng.uniform(3.2, 4.8),
                    rate_type='고정', repayment_method='원리금균등',
                )
            short_amount = total_debt - long_amount
            if short_amount:
                Liability.objects.create(
                    session=session, type='신용카드론', name='카드론',
                    balance=short_amount, interest_rate=rng.uniform(7.0, 14.0),
                    rate_type='변동', repayment_method='만기일시',
                )

        # 소득
        income_type = '사업소득' if profile['job'] == '자영업자' else (
            '연금소득' if profile['job'] == '퇴직자' else '근로소득')
        Income.objects.create(session=session, type=income_type, name='주 소득',
                              amount=monthly_income, is_fixed=True)

        # 지출 — 분류기 키워드에 맞춘 항목 구성 (비율은 _monthly_budget)
        Expense.objects.create(session=session, name='세금·4대보험',
                               amount=int(monthly_income * budget['tax']), is_fixed=True)
        Expense.objects.create(session=session, name='실비·암보험 보험료',
                               amount=int(monthly_income * budget['insurance']), is_fixed=True)
        if total_debt > 0 and budget['dsr'] > 0:
            Expense.objects.create(session=session, name='대출 원리금 상환',
                                   amount=int(monthly_income * budget['dsr']), is_fixed=True)
        Expense.objects.create(session=session, name='주거·통신비',
                               amount=int(monthly_income * budget['living'] * 0.4), is_fixed=True)
        Expense.objects.create(session=session, name='식비·생활비',
                               amount=int(monthly_income * budget['living'] * 0.6), is_fixed=False)

    def _create_goals_mindset_satisfaction(self, session, profile, monthly_income, rng):
        basic = session.basic_info
        retirement_age = basic.retirement_age or 62
        end_age = basic.end_age or 88

        before = FinancialGoalPeriod.objects.create(
            session=session, phase='before',
            from_age=profile['age'], to_age=retirement_age - 1,
        )
        FinancialGoalPeriodItem.objects.create(
            period=before, name='생활비', amount=int(monthly_income * 0.6), is_fixed=True)
        after = FinancialGoalPeriod.objects.create(
            session=session, phase='after',
            from_age=retirement_age, to_age=end_age,
        )
        retire_expense = {'healthy': rng.randint(180, 230),
                          'caution': rng.randint(220, 300),
                          'risky': rng.randint(250, 350)}[profile['health']]
        FinancialGoalPeriodItem.objects.create(
            period=after, name='은퇴 후 생활비',
            amount=retire_expense * 10_000, is_fixed=True)

        if rng.random() < 0.7:
            FinancialGoalMilestone.objects.create(
                session=session, phase='before',
                age=min(retirement_age - 1, profile['age'] + rng.randint(3, 10)),
                category='지출', content=rng.choice(['자녀 대학 등록금', '주택 구입', '자녀 결혼 자금']),
                amount=rng.randint(3000, 20000) * 10_000,
            )

        FinancialMindset.objects.create(
            session=session,
            values=MINDSET_TEMPLATES['values'],
            past_experience=MINDSET_TEMPLATES['past_experience'].format(exp=rng.choice(EXPERIENCES)),
            current_situation=MINDSET_TEMPLATES['current_situation'],
            current_challenge=MINDSET_TEMPLATES['current_challenge'].format(need=profile['need']),
            invest_philosophy=MINDSET_TEMPLATES['invest_philosophy'].format(
                tendency=', '.join(profile['tendency'])),
            future_goals=MINDSET_TEMPLATES['future_goals'].format(
                retirement_age=basic.retirement_age),
        )
        base_score = {'healthy': 4, 'caution': 3, 'risky': 2}[profile['health']]
        FinancialSatisfaction.objects.create(
            session=session,
            cashflow_score=self._score(base_score, rng),
            risk_score=self._score(base_score, rng),
            investment_score=self._score(base_score, rng),
            management_score=self._score(base_score, rng),
            inheritance_score=self._score(base_score - 1, rng),
        )

    @staticmethod
    def _score(base, rng):
        return max(1, min(5, base + rng.randint(-1, 1)))

    def _create_messages(self, session, created_at, rng):
        for offset in range(rng.randint(2, 5)):
            message = Message.objects.create(
                session=session,
                role='user' if offset % 2 == 0 else 'assistant',
                content='시연용 상담 메시지입니다.',
            )
            Message.objects.filter(pk=message.pk).update(
                created_at=created_at + timedelta(hours=offset * 2))
