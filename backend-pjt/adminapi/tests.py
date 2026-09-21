from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from consulting.models import (
    BasicInfo,
    ConsultingSession,
    Expense,
    FinancialAsset,
    Income,
    Liability,
    PensionAsset,
    RealEstateAsset,
)
from reports.snapshots import build_calculation_input_snapshot
from simulations.adapter import build_simulation_input

from .grading import GOOD, RISK, WARN, build_graded_indicators
from .management.commands.seed_demo import (
    PENSION_PERIOD_LIFETIME,
    PENSION_PERIOD_SAVINGS,
)
from .services import representative_session

User = get_user_model()

ADMIN_PING = '/api/admin/v1/ping/'
CUSTOMERS = '/api/admin/v1/customers/'
SUMMARY = '/api/admin/v1/analytics/summary/'


def bearer_client(user):
    client = APIClient()
    token = RefreshToken.for_user(user).access_token
    client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
    return client


class AdminAccessControlTests(TestCase):
    """NFR-01: 관리자 API는 서버에서 IsAdminUser로 이중 방어한다."""

    @classmethod
    def setUpTestData(cls):
        cls.pb = User.objects.create_user(email='pb@test.com', password='pb-pass-1234', name='PB')
        cls.pb.is_staff = True
        cls.pb.save(update_fields=['is_staff'])
        cls.customer = User.objects.create_user(
            email='customer@test.com', password='cust-pass-1234', name='고객'
        )

    def test_pb_token_gets_200(self):
        response = bearer_client(self.pb).get(ADMIN_PING)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['success'])

    def test_customer_token_gets_403(self):
        response = bearer_client(self.customer).get(ADMIN_PING)
        self.assertEqual(response.status_code, 403)

    def test_anonymous_gets_401(self):
        response = APIClient().get(ADMIN_PING)
        self.assertEqual(response.status_code, 401)


def make_full_customer(email='cust-a@test.com', name='고객A'):
    """결정적 수치의 완료 고객 — 순자산 1.5억, 월 잉여 60만, 위험지표 2개.

    지표 기대값 (age 35, 30s 브래킷):
    가계수지 12.0(위험) · 총저축성향 33.3(양호) · 금융자산비중 20.0(주의)
    · DSR 0(양호) · 노후대비 0(위험) · 비상예비금 20.0(양호) · 보장성보험 8.0(양호)
    """
    user = User.objects.create_user(email=email, password='cust-pass-1234', name=name)
    session = ConsultingSession.objects.create(
        user=user, status=ConsultingSession.Status.COMPLETED)
    BasicInfo.objects.create(session=session, age=35, job='직장인', need='은퇴')
    FinancialAsset.objects.create(
        session=session, asset_type='예금/적금', name='예금',
        current_value=50_000_000, monthly_savings=1_500_000)
    RealEstateAsset.objects.create(
        session=session, type='아파트', name='아파트', current_value=200_000_000)
    Liability.objects.create(session=session, type='주택담보대출', name='주담대', balance=100_000_000)
    Income.objects.create(session=session, type='근로소득', name='급여', amount=5_000_000, is_fixed=True)
    Expense.objects.create(session=session, name='세금·4대보험', amount=500_000, is_fixed=True)
    Expense.objects.create(session=session, name='실비 보험료', amount=400_000, is_fixed=True)
    Expense.objects.create(session=session, name='식비', amount=2_000_000, is_fixed=False)
    return user


class GradingTests(TestCase):
    def test_grades_match_frontend_zone_boundaries(self):
        raw = {
            'household_balance': 88.0,        # 표시값 12.0 — 30대 기준 t=30 → 위험
            'total_savings_propensity': 30.0,  # 경계값 t=30 → 양호 (front zone from:t 포함)
            'financial_asset_ratio': 20.0,     # 경계값 → 주의
            'debt_service_ratio': 25.0,        # 경계값 t=25 → 주의 (front 양호 zone 은 [0,t) 미포함)
            'retirement_savings': 49.9,        # 주의
            'emergency_reserve': 1.4,          # t=3, t/2=1.5 미만 → 위험
            'insurance_preparation': 8.0,      # 양호
        }
        graded = {item['key']: item for item in build_graded_indicators(raw, age=35)}
        self.assertEqual(graded['가계수지지표']['value'], 12.0)
        self.assertEqual(graded['가계수지지표']['grade'], RISK)
        self.assertEqual(graded['총저축성향지표']['grade'], GOOD)
        self.assertEqual(graded['금융자산비중지표']['grade'], WARN)
        self.assertEqual(graded['총부채상환지표']['grade'], WARN)
        self.assertEqual(graded['노후대비저축지표']['grade'], WARN)
        self.assertEqual(graded['비상예비금지표']['grade'], RISK)
        self.assertEqual(graded['보장성보험준비지표']['grade'], GOOD)

    def test_age_changes_bracket(self):
        raw = {'household_balance': 60.0}  # 표시값 40
        as_20s = build_graded_indicators(raw, age=25)[0]   # t=50 → 주의 (40 >= 25)
        as_50s = build_graded_indicators(raw, age=55)[0]   # t=10 → 양호
        self.assertEqual(as_20s['grade'], WARN)
        self.assertEqual(as_50s['grade'], GOOD)


class RepresentativeSessionTests(TestCase):
    def test_prefers_latest_completed_over_newer_active(self):
        user = User.objects.create_user(email='rep@test.com', password='pass-1234-ok', name='rep')
        completed = ConsultingSession.objects.create(
            user=user, status=ConsultingSession.Status.COMPLETED)
        ConsultingSession.objects.create(
            user=user, status=ConsultingSession.Status.IN_PROGRESS)
        self.assertEqual(representative_session(user).id, completed.id)

    def test_excludes_abandoned(self):
        user = User.objects.create_user(email='aband@test.com', password='pass-1234-ok', name='이탈')
        ConsultingSession.objects.create(user=user, status=ConsultingSession.Status.ABANDONED)
        self.assertIsNone(representative_session(user))


class CustomerListApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.pb = User.objects.create_user(email='pb@test.com', password='pb-pass-1234', name='PB')
        cls.pb.is_staff = True
        cls.pb.save(update_fields=['is_staff'])
        cls.full = make_full_customer()
        cls.empty = User.objects.create_user(
            email='empty@test.com', password='pass-1234-ok', name='무세션')
        cls.abandoned = User.objects.create_user(
            email='aband@test.com', password='pass-1234-ok', name='이탈고객')
        ConsultingSession.objects.create(
            user=cls.abandoned, status=ConsultingSession.Status.ABANDONED)

    def setUp(self):
        self.client = bearer_client(self.pb)

    def test_row_fields_and_derived_values(self):
        rows = self.client.get(CUSTOMERS).json()['data']
        row = next(r for r in rows if r['user_id'] == self.full.id)
        self.assertEqual(row['name'], '고객A')
        self.assertEqual(row['age'], 35)
        self.assertEqual(row['job'], '직장인')
        self.assertEqual(row['need'], '은퇴')
        self.assertEqual(row['session_status'], 'completed')
        self.assertEqual(row['net_worth'], 150_000_000)
        self.assertEqual(row['net_worth_band'], '50m_200m')
        self.assertEqual(row['monthly_surplus'], 600_000)
        self.assertEqual(row['risk_count'], 2)

    def test_staff_accounts_are_not_listed(self):
        rows = self.client.get(CUSTOMERS).json()['data']
        self.assertNotIn(self.pb.id, [r['user_id'] for r in rows])

    def test_customer_without_session_renders_blank(self):
        rows = self.client.get(CUSTOMERS).json()['data']
        row = next(r for r in rows if r['user_id'] == self.empty.id)
        self.assertEqual(row['session_status'], 'none')
        self.assertIsNone(row['net_worth'])
        self.assertIsNone(row['risk_count'])
        row = next(r for r in rows if r['user_id'] == self.abandoned.id)
        self.assertEqual(row['session_status'], 'abandoned')

    def test_session_without_financial_input_renders_blank(self):
        # 자산·현금흐름 미입력 세션을 '순자산 0원'으로 표시하지 않는다 (NFR-04)
        user = User.objects.create_user(
            email='onboard@test.com', password='pass-1234-ok', name='온보딩')
        session = ConsultingSession.objects.create(
            user=user, status=ConsultingSession.Status.ONBOARDING)
        BasicInfo.objects.create(session=session, age=28, need='투자')
        rows = self.client.get(CUSTOMERS).json()['data']
        row = next(r for r in rows if r['user_id'] == user.id)
        self.assertEqual(row['session_status'], 'onboarding')
        self.assertEqual(row['need'], '투자')
        self.assertIsNone(row['net_worth'])
        self.assertIsNone(row['monthly_surplus'])
        self.assertIsNone(row['risk_count'])

    def test_status_filter_and_search(self):
        rows = self.client.get(CUSTOMERS, {'status': 'completed'}).json()['data']
        self.assertEqual([r['user_id'] for r in rows], [self.full.id])
        rows = self.client.get(CUSTOMERS, {'search': '무세션'}).json()['data']
        self.assertEqual([r['user_id'] for r in rows], [self.empty.id])

    def test_ordering_puts_missing_values_last(self):
        rows = self.client.get(CUSTOMERS, {'ordering': '-net_worth'}).json()['data']
        self.assertEqual(rows[0]['user_id'], self.full.id)
        self.assertIsNone(rows[-1]['net_worth'])

    def test_pagination_meta(self):
        body = self.client.get(CUSTOMERS, {'page_size': 1, 'page': 2}).json()
        self.assertEqual(len(body['data']), 1)
        self.assertEqual(body['meta']['total'], 3)
        self.assertEqual(body['meta']['page'], 2)
        self.assertEqual(body['meta']['total_pages'], 3)
        self.assertIn('generated_at', body['meta'])

    def test_query_budget(self):
        # NFR-03: 고객 수와 무관하게 쿼리 수 상한 준수 (N+1 금지)
        with CaptureQueriesContext(connection) as ctx:
            self.client.get(CUSTOMERS)
        self.assertLessEqual(len(ctx.captured_queries), 15, [q['sql'][:80] for q in ctx.captured_queries])


class CustomerDetailApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.pb = User.objects.create_user(email='pb@test.com', password='pb-pass-1234', name='PB')
        cls.pb.is_staff = True
        cls.pb.save(update_fields=['is_staff'])
        cls.full = make_full_customer()

    def setUp(self):
        self.client = bearer_client(self.pb)

    def test_detail_payload(self):
        body = self.client.get(f'{CUSTOMERS}{self.full.id}/').json()
        data = body['data']
        self.assertEqual(data['profile']['name'], '고객A')
        self.assertEqual(data['derived']['net_worth'], 150_000_000)
        self.assertEqual(data['derived']['risk_count'], 2)
        self.assertEqual(len(data['derived']['indicators']), 7)
        self.assertIsNotNone(data['asset_statement'])
        self.assertIsNotNone(data['cash_flow'])
        self.assertEqual(data['basic_info']['need'], '은퇴')
        self.assertEqual(len(data['sessions']), 1)

    def test_unknown_customer_returns_404(self):
        response = self.client.get(f'{CUSTOMERS}999999/')
        self.assertEqual(response.status_code, 404)

    def test_staff_user_id_returns_404(self):
        response = self.client.get(f'{CUSTOMERS}{self.pb.id}/')
        self.assertEqual(response.status_code, 404)


class SummaryApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.pb = User.objects.create_user(email='pb@test.com', password='pb-pass-1234', name='PB')
        cls.pb.is_staff = True
        cls.pb.save(update_fields=['is_staff'])
        make_full_customer()
        User.objects.create_user(email='empty@test.com', password='pass-1234-ok', name='무세션')

    def test_summary_kpi(self):
        body = bearer_client(self.pb).get(SUMMARY).json()
        kpi = body['data']['kpi']
        self.assertEqual(kpi['customer_count'], 2)
        self.assertEqual(kpi['completed_rate'], 50.0)
        self.assertEqual(kpi['total_net_worth'], 150_000_000)
        self.assertEqual(kpi['risk_customer_count'], 0)
        self.assertEqual(len(body['data']['recent_activities']), 1)


class TendencyClassificationTests(TestCase):
    def test_majority_and_tie_rules(self):
        from .services import classify_tendency
        self.assertEqual(classify_tendency(['안전 최우선', '안정 추구', '단기 지향']), 'stable')
        self.assertEqual(classify_tendency(['균형 추구']), 'balanced')
        self.assertEqual(classify_tendency(['성장 추구', '위험 감수']), 'aggressive')
        # 동률(1:1) → 균형형
        self.assertEqual(classify_tendency(['안전 최우선', '수익 중시']), 'balanced')
        self.assertIsNone(classify_tendency([]))
        self.assertIsNone(classify_tendency(['알 수 없는 태그']))


class SegmentsApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.pb = User.objects.create_user(email='pb@test.com', password='pb-pass-1234', name='PB')
        cls.pb.is_staff = True
        cls.pb.save(update_fields=['is_staff'])
        cls.full = make_full_customer()
        User.objects.create_user(email='empty@test.com', password='pass-1234-ok', name='무세션')

    def setUp(self):
        self.client = bearer_client(self.pb)

    def test_axes_and_scatter(self):
        body = self.client.get('/api/admin/v1/analytics/segments/').json()
        data = body['data']
        self.assertEqual(set(data['axes'].keys()), {
            'age_band', 'need', 'tendency_group', 'net_worth_band', 'health', 'session_status'})
        age = {b['bucket']: b['count'] for b in data['axes']['age_band']}
        self.assertEqual(age['30s'], 1)       # 고객A (35세)
        self.assertEqual(age['unknown'], 1)   # 무세션 고객
        health = {b['bucket']: b['count'] for b in data['axes']['health']}
        self.assertEqual(health['caution'], 1)  # 위험지표 2개 → 주의
        self.assertEqual(len(data['indicator_risk']), 7)
        self.assertEqual(len(data['scatter']), 1)
        self.assertEqual(data['scatter'][0]['net_worth'], 150_000_000)

    def test_health_filter_on_list(self):
        rows = self.client.get(CUSTOMERS, {'health': 'caution'}).json()['data']
        self.assertEqual([r['user_id'] for r in rows], [self.full.id])
        rows = self.client.get(CUSTOMERS, {'need': 'unknown'}).json()['data']
        self.assertTrue(all(r['need'] is None for r in rows))


class SeedDemoCommandTests(TestCase):
    def test_seed_creates_consistent_demo_data(self):
        call_command('seed_demo', '--count', '10', verbosity=0)
        demo_users = User.objects.filter(email__endswith='@retirewise.demo')
        self.assertEqual(demo_users.count(), 10)
        self.assertFalse(demo_users.filter(is_staff=True).exists())

        pb = User.objects.create_user(email='pb2@test.com', password='pb-pass-1234', name='PB')
        pb.is_staff = True
        pb.save(update_fields=['is_staff'])
        rows = bearer_client(pb).get(CUSTOMERS, {'page_size': 100}).json()['data']
        demo_rows = [r for r in rows if r['email'].endswith('@retirewise.demo')]
        self.assertEqual(len(demo_rows), 10)

        completed = [r for r in demo_rows if r['session_status'] == 'completed']
        self.assertTrue(completed, '완료 고객이 최소 1명 있어야 한다')
        for row in completed:
            self.assertEqual(row['progress_pct'], 100)
            self.assertIsNotNone(row['net_worth'])
            self.assertIsNotNone(row['risk_count'])

    def test_seed_is_rerunnable(self):
        call_command('seed_demo', '--count', '6', verbosity=0)
        call_command('seed_demo', '--count', '6', verbosity=0)
        self.assertEqual(
            User.objects.filter(email__endswith='@retirewise.demo').count(), 6)


class SeedDemoPensionPeriodTests(TestCase):
    """시드 연금 행이 시뮬레이션 어댑터의 수령 기간 규격을 만족해야 한다.

    pension_period 는 CharField 다. 비워 두면 pension_period_missing, 정수를 넣으면
    '20' 으로 저장돼 pension_period_unsupported 가 난다. 둘 다 시연 고객을
    시뮬레이션 불가 상태로 만든다.
    """

    @classmethod
    def setUpTestData(cls):
        call_command('seed_demo', '--count', '10', verbosity=0)

    def test_no_pension_row_has_an_empty_period(self):
        self.assertTrue(PensionAsset.objects.exists())
        self.assertFalse(
            PensionAsset.objects.filter(pension_period__isnull=True).exists())
        self.assertFalse(PensionAsset.objects.filter(pension_period='').exists())

    def test_seeded_periods_use_the_frontend_select_values(self):
        self.assertEqual(
            set(PensionAsset.objects.values_list('pension_period', flat=True)),
            {PENSION_PERIOD_LIFETIME, PENSION_PERIOD_SAVINGS},
        )

    def test_adapter_reports_no_pension_period_issue_for_seeded_sessions(self):
        """패턴을 여기 복사하지 않고 어댑터 자신의 판정을 돌린다.

        복사하면 어댑터가 규칙을 바꿔도 이 테스트가 눈치채지 못한다.
        """
        sessions = ConsultingSession.objects.filter(
            pension_assets__isnull=False).distinct()
        self.assertTrue(sessions.exists())
        for session in sessions:
            with self.subTest(session=session.pk):
                codes = {
                    issue.code
                    for issue in build_simulation_input(
                        build_calculation_input_snapshot(session)).issues
                }
                self.assertNotIn('pension_period_missing', codes)
                self.assertNotIn('pension_period_unsupported', codes)


class BootstrapDemoCommandTests(TestCase):
    def test_creates_pb_and_seed_in_one_shot(self):
        call_command('bootstrap_demo', '--count', '6', verbosity=0)
        pb = User.objects.get(email='admin1234@naver.com')
        self.assertTrue(pb.is_staff)
        self.assertTrue(pb.check_password('admin1234'))
        self.assertEqual(pb.name, 'pb')
        self.assertEqual(
            User.objects.filter(email__endswith='@retirewise.demo').count(), 6)

    def test_rerun_is_idempotent_and_resets_pb_password(self):
        call_command('bootstrap_demo', '--count', '4', verbosity=0)
        pb = User.objects.get(email='admin1234@naver.com')
        pb.set_password('changed-by-someone-1')
        pb.save(update_fields=['password'])
        call_command('bootstrap_demo', '--count', '4', verbosity=0)
        pb.refresh_from_db()
        self.assertTrue(pb.check_password('admin1234'))
        self.assertEqual(User.objects.filter(email='admin1234@naver.com').count(), 1)
        self.assertEqual(
            User.objects.filter(email__endswith='@retirewise.demo').count(), 4)

    def test_guard_blocks_db_with_real_customers(self):
        User.objects.create_user(email='real@user.com', password='real-pass-1234', name='실사용자')
        with self.assertRaises(CommandError):
            call_command('bootstrap_demo', '--count', '4', verbosity=0)
        # 가드 발동 시 아무것도 만들지 않는다
        self.assertFalse(User.objects.filter(email='admin1234@naver.com').exists())
        # --yes 로 명시하면 실행되고, 실사용자 데이터는 건드리지 않는다
        call_command('bootstrap_demo', '--count', '4', '--yes', verbosity=0)
        self.assertTrue(User.objects.filter(email='real@user.com').exists())
        self.assertEqual(
            User.objects.filter(email__endswith='@retirewise.demo').count(), 4)

    def test_staff_only_db_needs_no_flag(self):
        pb = User.objects.create_user(email='other-pb@test.com', password='pb-pass-1234', name='PB')
        pb.is_staff = True
        pb.save(update_fields=['is_staff'])
        call_command('bootstrap_demo', '--count', '4', verbosity=0)  # 가드 미발동
        self.assertTrue(User.objects.filter(email='admin1234@naver.com').exists())


class CreatePbCommandTests(TestCase):
    def test_creates_new_pb_account(self):
        call_command('create_pb', 'new-pb@test.com', '--name', '김PB', '--password', 'pb-pass-1234')
        user = User.objects.get(email='new-pb@test.com')
        self.assertTrue(user.is_staff)
        self.assertTrue(user.check_password('pb-pass-1234'))
        self.assertEqual(user.name, '김PB')

    def test_promotes_existing_customer(self):
        User.objects.create_user(email='cust@test.com', password='cust-pass-1234', name='고객')
        call_command('create_pb', 'cust@test.com')
        self.assertTrue(User.objects.get(email='cust@test.com').is_staff)

    def test_new_account_requires_password(self):
        with self.assertRaises(CommandError):
            call_command('create_pb', 'no-pass@test.com')

    def test_rejects_weak_password(self):
        with self.assertRaises(CommandError):
            call_command('create_pb', 'weak@test.com', '--password', '1234')
