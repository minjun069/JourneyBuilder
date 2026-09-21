"""DB 가 필요한 시뮬레이션 테스트만 둔다.

CI(.gitlab-ci.yml test:backend)는 backend-pjt/tests 와 contract_tests 를 **맨
unittest** 로 discover 한다 — Postgres 가 없어서 DB 를 건드리는 순간 실패한다.
그래서 순수 로직 검사는 전부 backend-pjt/tests/ 와 backend-pjt/contract_tests/ 에
있고, 여기에는 세션 → 스냅샷 → 어댑터가 실제 ORM 을 거쳐 이어지는지 확인하는
검사만 남긴다 (`python manage.py test simulations` 로 돈다).

**주의**: 이 파일은 CI 게이트에 걸리지 않는다. 맨 unittest 로 돌릴 수 있는 검사는
여기 두지 말고 backend-pjt/tests/ 에 둔다.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from consulting.models import ConsultingSession, FinancialAsset
from reports.snapshots import build_calculation_input_snapshot

from .services import confirmed_empty_sections, prepare_input

User = get_user_model()


def issue_codes(snapshot, sections):
    from .adapter import build_simulation_input  # noqa: PLC0415

    return {
        issue.code
        for issue in build_simulation_input(
            snapshot, confirmed_empty_sections=sections
        ).issues
    }


class ConfirmedEmptySectionsTests(TestCase):
    """'금융자산 없음' 확인이 어댑터까지 전달되는지 (S15P21B109).

    ConsultingSession.financial_assets_confirmed_empty 는 세션을 100% 완료로
    만들어 뷰의 준비 상태 검사를 통과시킨다. 그 사실을 어댑터에 넘기지 않으면
    같은 세션이 반드시 422 INPUT_NOT_READY 가 된다 — 통과시켜 놓고 막는 셈이다.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email='confirm-empty@test.com', password='pass-1234-ok', name='확인'
        )
        self.session = ConsultingSession.objects.create(user=self.user)

    def _snapshot(self):
        return build_calculation_input_snapshot(self.session)

    def _set_flag(self, value=True):
        self.session.financial_assets_confirmed_empty = value
        self.session.save(update_fields=['financial_assets_confirmed_empty'])

    def test_flag_with_no_rows_confirms_the_section(self):
        self._set_flag()
        snapshot = self._snapshot()
        sections = confirmed_empty_sections(self.session, snapshot)
        self.assertEqual(sections, ('financial_assets',))
        self.assertNotIn(
            'financial_assets_empty', issue_codes(snapshot, sections))

    def test_no_flag_still_reports_the_empty_section(self):
        snapshot = self._snapshot()
        sections = confirmed_empty_sections(self.session, snapshot)
        self.assertEqual(sections, ())
        self.assertIn('financial_assets_empty', issue_codes(snapshot, sections))

    def test_flag_with_rows_present_is_not_confirmed(self):
        """모듈 _confirm_empty() 는 이름만 보고 행이 남아 있으면 거절한다.

        플래그만 믿고 넘기면 InputMappingError('invalid_confirmation') 가 나면서
        고칠 수 있는 422 가 ANALYSIS_REJECTED 로 둔갑한다.
        """
        self._set_flag()
        FinancialAsset.objects.create(
            session=self.session, asset_type='예금/적금', name='주거래 예금',
            current_value=10_000_000, monthly_savings=0,
        )
        snapshot = self._snapshot()
        self.assertEqual(confirmed_empty_sections(self.session, snapshot), ())

    def test_flag_is_ignored_on_objects_without_it(self):
        """계약 테스트의 가짜 세션처럼 플래그가 없는 객체는 '확인 안 함' 이다."""

        class Bare:
            pass

        self.assertEqual(
            confirmed_empty_sections(Bare(), {'financial_assets': []}), ())


class PrepareInputForwardsConfirmationTests(TestCase):
    """prepare_input() 이 확인 목록을 어댑터로 그대로 넘기는지."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='prepare-empty@test.com', password='pass-1234-ok', name='준비'
        )
        self.session = ConsultingSession.objects.create(
            user=self.user, financial_assets_confirmed_empty=True
        )

    def test_confirmation_removes_the_empty_section_issue(self):
        from .errors import SimulationError  # noqa: PLC0415

        snapshot = build_calculation_input_snapshot(self.session)
        sections = confirmed_empty_sections(self.session, snapshot)

        with self.assertRaises(SimulationError) as without:
            prepare_input(snapshot)
        with self.assertRaises(SimulationError) as with_confirmation:
            prepare_input(snapshot, confirmed_empty_sections=sections)

        def codes(context):
            return {
                issue['code']
                for issue in context.exception.details['issues']
            }

        # 세션이 비어 있어 다른 issue 는 남는다. 사라져야 하는 것은 이 하나뿐이다.
        self.assertIn('financial_assets_empty', codes(without))
        self.assertNotIn('financial_assets_empty', codes(with_confirmation))
