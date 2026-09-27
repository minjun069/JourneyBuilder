from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User
from .models import (
    BasicInfo, ConsultingSession, FinancialGoalMilestone, FinancialGoalPeriod,
    FinancialMindset, FinancialSatisfaction,
)


class EmptySectionConfirmationTests(TestCase):
    cases = {
        'pension-assets': ('pension_assets', 'pension-assets'),
        'real-estate': ('real_estate_assets', 'real-estate-assets'),
        'liabilities': ('liabilities', 'liabilities'),
        'income': ('incomes', 'incomes'),
        'expenses': ('expenses', 'expenses'),
        'financial-goals': ('financial_goals', 'goal-periods'),
    }

    def setUp(self):
        self.user = User.objects.create_user(email='empty@example.invalid', password='test-password', name='테스트')
        self.session = ConsultingSession.objects.create(user=self.user)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def confirm(self, step):
        return self.client.put(reverse('section-confirm-empty', kwargs={
            'session_id': self.session.pk, 'step_id': step,
        }), {}, format='json')

    def test_confirmation_is_persistent_idempotent_and_regular_save_resets_it(self):
        for step, (section, route) in self.cases.items():
            with self.subTest(step=step):
                self.assertEqual(self.confirm(step).status_code, 200)
                response = self.confirm(step)
                self.assertEqual(response.status_code, 200)
                self.session.refresh_from_db()
                self.assertEqual(self.session.confirmed_empty_sections.count(section), 1)
                if section != 'liabilities':
                    self.assertTrue(response.data['data']['section_completion'][section])
                    detail = self.client.get(reverse('consultation-detail', kwargs={'session_id': self.session.pk}))
                    self.assertTrue(detail.data['data']['section_completion'][section])
                response = self.client.put(reverse(route, kwargs={'session_id': self.session.pk}), [], format='json')
                self.assertEqual(response.status_code, 200)
                self.session.refresh_from_db()
                self.assertNotIn(section, self.session.confirmed_empty_sections)
                if section != 'liabilities':
                    self.assertFalse(self.session.get_section_completion()[section])

    def test_confirm_deletes_only_selected_section_and_new_data_clears_confirmation(self):
        self.client.put(reverse('incomes', kwargs={'session_id': self.session.pk}),
                        [{'type': '근로소득', 'name': '급여', 'amount': 1000000}], format='json')
        self.assertEqual(self.session.incomes.count(), 1)
        self.assertEqual(self.confirm('expenses').status_code, 200)
        self.assertEqual(self.session.incomes.count(), 1)
        self.assertEqual(self.confirm('income').status_code, 200)
        self.assertFalse(self.session.incomes.exists())
        response = self.client.put(reverse('incomes', kwargs={'session_id': self.session.pk}),
                                   [{'type': '근로소득', 'name': '급여', 'amount': 1000000}], format='json')
        self.assertEqual(response.status_code, 200)
        self.session.refresh_from_db()
        self.assertNotIn('incomes', self.session.confirmed_empty_sections)
        self.assertIn('expenses', self.session.confirmed_empty_sections)
        self.assertTrue(self.session.get_section_completion()['incomes'])

    def test_goals_confirmation_deletes_both_kinds_and_milestone_save_resets_it(self):
        FinancialGoalPeriod.objects.create(session=self.session, phase='before', from_age=30, to_age=40)
        FinancialGoalMilestone.objects.create(session=self.session, phase='after', age=65, amount=100)
        self.assertEqual(self.confirm('financial-goals').status_code, 200)
        self.assertFalse(self.session.goal_periods.exists())
        self.assertFalse(self.session.goal_milestones.exists())
        response = self.client.put(reverse('goal-milestones', kwargs={'session_id': self.session.pk}), [], format='json')
        self.assertEqual(response.status_code, 200)
        self.session.refresh_from_db()
        self.assertNotIn('financial_goals', self.session.confirmed_empty_sections)

    def test_unsupported_required_section_is_rejected(self):
        self.assertEqual(self.confirm('basic-info').status_code, 400)
        self.session.refresh_from_db()
        self.assertEqual(self.session.confirmed_empty_sections, [])

    def test_all_confirmed_empty_sections_allow_report_readiness(self):
        BasicInfo.objects.create(session=self.session)
        FinancialMindset.objects.create(session=self.session)
        FinancialSatisfaction.objects.create(session=self.session)
        self.client.put(reverse('financial-assets-confirm-empty', kwargs={'session_id': self.session.pk}), {}, format='json')
        for step in self.cases:
            self.assertEqual(self.confirm(step).status_code, 200)
        self.session.refresh_from_db()
        self.assertTrue(self.session.is_ready_for_reports())
        self.assertEqual(self.session.get_progress_pct(), 100)
        self.assertEqual(self.session.status, ConsultingSession.Status.COMPLETED)

    def test_another_user_or_anonymous_cannot_confirm(self):
        other = User.objects.create_user(email='other@example.invalid', password='test-password', name='다른 사용자')
        self.client.force_authenticate(other)
        self.assertEqual(self.confirm('income').status_code, 404)
        self.client.force_authenticate(None)
        self.assertEqual(self.confirm('income').status_code, 401)
        self.session.refresh_from_db()
        self.assertEqual(self.session.confirmed_empty_sections, [])
