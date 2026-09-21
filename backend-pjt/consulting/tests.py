from collections import Counter
from types import SimpleNamespace

from django.test import SimpleTestCase, TestCase
from django.urls import Resolver404, resolve, reverse
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate

from .field_registry import ALLOWED_OPERATIONS, FIELDS, SECTION_META
from .form_actions import normalize_form_action
from accounts.models import User

from .models import BasicInfo, ConsultingSession, FinancialAsset
from .serializers import (
    BasicInfoWriteSerializer,
    ExpenseSerializer,
    FinancialAssetSerializer,
    FinancialSatisfactionSerializer,
    parse_korean_money,
)
from .views import FinancialAssetsView, MessageListView


class CanonicalFieldRegistryTests(SimpleTestCase):
    def test_registry_covers_every_frozen_section_with_unique_paths(self):
        self.assertEqual({field.section for field in FIELDS}, set(SECTION_META))
        keys = [(field.section, field.path) for field in FIELDS]
        self.assertEqual(len(keys), len(set(keys)))
        expected_counts = {
            'basic_info': 16,
            'financial_mindset': 8,
            'financial_satisfaction': 7,
            'financial_assets': 8,
            'pension_assets': 13,
            'real_estate_assets': 10,
            'liabilities': 12,
            'incomes': 6,
            'expenses': 9,
            'goal_periods': 10,
            'goal_milestones': 8,
        }
        self.assertEqual(dict(Counter(field.section for field in FIELDS)), expected_counts)


    def test_writable_fields_use_cardinality_independent_operations(self):
        for field in FIELDS:
            expected = ALLOWED_OPERATIONS if field.writable_by_ai else ()
            self.assertEqual(field.operations, expected)


class ZeroNullContractTests(SimpleTestCase):
    def test_money_zero_is_preserved_and_unitless_number_is_canonical(self):
        self.assertEqual(parse_korean_money(0), 0)
        self.assertEqual(parse_korean_money('0'), 0)
        self.assertEqual(parse_korean_money('0만'), 0)
        self.assertEqual(parse_korean_money('3000000'), 3000000)
        self.assertEqual(parse_korean_money('300만'), 3000000)

    def test_zero_amount_is_valid_but_negative_amount_is_rejected(self):
        zero = FinancialAssetSerializer(data={'current_value': 0})
        self.assertTrue(zero.is_valid(), zero.errors)

        negative = FinancialAssetSerializer(data={'current_value': -1})
        self.assertFalse(negative.is_valid())

    def test_zero_age_and_zero_satisfaction_are_rejected(self):
        age = BasicInfoWriteSerializer(data={'age': 0})
        self.assertFalse(age.is_valid())

        satisfaction = FinancialSatisfactionSerializer(data={'cashflow_score': 0})
        self.assertFalse(satisfaction.is_valid())

    def test_partial_update_checks_existing_cross_field_values(self):
        instance = BasicInfo(age=50, retirement_age=60, end_age=90)
        serializer = BasicInfoWriteSerializer(
            instance,
            data={'retirement_age': 40},
            partial=True,
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn('retirement_age', serializer.errors)

    def test_false_and_explicit_frequency_none_are_not_null(self):
        serializer = ExpenseSerializer(data={
            'amount': 0,
            'is_fixed': False,
            'frequency': {'none': True, 'unit': '월', 'count': None},
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertIs(serializer.validated_data['is_fixed'], False)
        self.assertEqual(
            serializer.validated_data['frequency'],
            {'none': True, 'unit': '월', 'count': None},
        )


class FormActionPolicyTests(SimpleTestCase):
    def action(self, *, requested):
        return normalize_form_action({
            'action': {
                'type': 'form_change',
                'target': 'draft_form',
                'user_requested': requested,
                'changes': [{
                    'section': 'basic_info',
                    'path': 'monthly_income',
                    'operation': 'set',
                    'value': 0,
                }],
            },
        }, 'basic-info')

    def test_explicit_request_still_requires_approval(self):
        action = self.action(requested=True)
        self.assertEqual(action['mode'], 'proposal')
        self.assertEqual(action['changes'][0]['value'], 0)

    def test_every_ai_generated_change_is_proposal(self):
        self.assertEqual(
            self.action(requested=True)['mode'],
            'proposal',
        )
        self.assertEqual(
            self.action(requested=False)['mode'],
            'proposal',
        )

    def test_malformed_metadata_does_not_bypass_approval(self):
        self.assertEqual(
            self.action(requested=True)['mode'],
            'proposal',
        )
        self.assertEqual(
            self.action(requested='true')['mode'],
            'proposal',
        )

    def test_unknown_field_or_operation_is_rejected(self):
        with self.assertRaises(ValueError):
            normalize_form_action({
                'action': {
                    'type': 'form_change',
                    'user_requested': True,
                    'changes': [{
                        'section': 'basic_info',
                        'path': 'unknown',
                        'operation': 'replace',
                    }],
                },
            }, 'basic-info')


class AITransportAndDecisionRemovalTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()

    def test_v1_message_list_rejects_non_streaming_post(self):
        request = self.factory.post('/messages/', {'content': 'test'}, format='json')
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True))

        response = MessageListView.as_view()(request, session_id=12)

        self.assertEqual(response.status_code, 405)

    def test_polling_callback_and_legacy_stream_routes_are_absent(self):
        for path in (
            '/api/v1/consultations/12/ai-responses/request-1/',
            '/api/v2/consultations/12/ai-responses/request-1/',
            '/api/ai/callback/',
            '/api/v2/internal/ai/callback/',
            '/api/v1/consultations/12/messages/stream/',
            '/api/v1/consultations/12/ai-actions/approve/',
            '/api/v1/consultations/12/ai-actions/reject/',
            '/api/v2/consultations/12/ai-actions/action-1/changes/change-1/approve/',
            '/api/v2/consultations/12/ai-actions/action-1/changes/change-1/reject/',
        ):
            with self.subTest(path=path), self.assertRaises(Resolver404):
                resolve(path)


class FinancialAssetsEmptyConfirmationTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.client = APIClient()
        self.user = User.objects.create_user(
            email='no-assets@example.invalid',
            password='test-only-password',
            name='금융자산 없음 테스트',
        )
        self.session = ConsultingSession.objects.create(
            user=self.user,
            status=ConsultingSession.Status.IN_PROGRESS,
        )
        self.client.force_authenticate(user=self.user)

    def test_confirm_empty_removes_assets_and_completes_the_section(self):
        FinancialAsset.objects.create(
            session=self.session,
            name='기존 예금',
            current_value=1_000_000,
        )
        response = self.client.put(
            reverse('financial-assets-confirm-empty', kwargs={'session_id': self.session.id}),
            {},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.session.refresh_from_db()
        self.assertFalse(self.session.financial_assets.exists())
        self.assertTrue(self.session.financial_assets_confirmed_empty)
        self.assertTrue(self.session.get_section_completion()['financial_assets'])
        self.assertTrue(response.data['data']['financial_assets_confirmed_empty'])

    def test_regular_asset_save_clears_empty_confirmation(self):
        self.session.financial_assets_confirmed_empty = True
        self.session.save(update_fields=['financial_assets_confirmed_empty'])
        request = self.factory.put(
            '/financial-assets/',
            [{'name': '새 예금', 'current_value': 2_000_000}],
            format='json',
        )
        force_authenticate(request, user=self.user)

        response = FinancialAssetsView.as_view()(request, session_id=self.session.id)

        self.assertEqual(response.status_code, 200)
        self.session.refresh_from_db()
        self.assertFalse(self.session.financial_assets_confirmed_empty)
        self.assertEqual(self.session.financial_assets.count(), 1)

    def test_regular_empty_save_clears_confirmation_and_completion(self):
        self.session.financial_assets_confirmed_empty = True
        self.session.save(update_fields=['financial_assets_confirmed_empty'])
        request = self.factory.put('/financial-assets/', [], format='json')
        force_authenticate(request, user=self.user)

        response = FinancialAssetsView.as_view()(request, session_id=self.session.id)

        self.assertEqual(response.status_code, 200)
        self.session.refresh_from_db()
        self.assertFalse(self.session.financial_assets_confirmed_empty)
        self.assertFalse(self.session.get_section_completion()['financial_assets'])

    def test_confirm_empty_hides_another_users_session(self):
        other_user = User.objects.create_user(
            email='other-no-assets@example.invalid',
            password='test-only-password',
            name='다른 사용자',
        )
        self.client.force_authenticate(user=other_user)

        response = self.client.put(
            reverse('financial-assets-confirm-empty', kwargs={'session_id': self.session.id}),
            {},
            format='json',
        )

        self.assertEqual(response.status_code, 404)
        self.session.refresh_from_db()
        self.assertFalse(self.session.financial_assets_confirmed_empty)
