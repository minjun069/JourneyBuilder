from collections import Counter
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from .field_registry import ALLOWED_OPERATIONS, FIELDS, SECTION_META
from .form_actions import normalize_form_action
from .ai_services import build_ai_request_payload, normalize_change_value
from .models import BasicInfo
from .serializers import (
    BasicInfoWriteSerializer,
    ExpenseSerializer,
    FinancialAssetSerializer,
    FinancialSatisfactionSerializer,
    parse_korean_money,
)
from .views import AICallbackView, MessageStreamView


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
    def action(self, *, confidence, requested):
        return normalize_form_action({
            'action': {
                'type': 'form_change',
                'target': 'draft_form',
                'confidence': confidence,
                'user_requested': requested,
                'changes': [{
                    'section': 'basic_info',
                    'path': 'monthly_income',
                    'operation': 'set',
                    'value': 0,
                }],
            },
        }, 'basic-info')

    def test_explicit_high_confidence_request_still_requires_approval(self):
        action = self.action(confidence=0.90, requested=True)
        self.assertEqual(action['mode'], 'proposal')
        self.assertEqual(action['changes'][0]['value'], 0)

    def test_every_ai_generated_change_is_proposal(self):
        self.assertEqual(
            self.action(confidence=0.89, requested=True)['mode'],
            'proposal',
        )
        self.assertEqual(
            self.action(confidence=1.0, requested=False)['mode'],
            'proposal',
        )

    def test_malformed_metadata_does_not_bypass_approval(self):
        self.assertEqual(
            self.action(confidence='nan', requested=True)['mode'],
            'proposal',
        )
        self.assertEqual(
            self.action(confidence=1.0, requested='true')['mode'],
            'proposal',
        )

    def test_unknown_field_or_operation_is_rejected(self):
        with self.assertRaises(ValueError):
            normalize_form_action({
                'action': {
                    'type': 'form_change',
                    'confidence': 1,
                    'user_requested': True,
                    'changes': [{
                        'section': 'basic_info',
                        'path': 'unknown',
                        'operation': 'replace',
                    }],
                },
            }, 'basic-info')


class AIRequestContractTests(SimpleTestCase):
    def test_monthly_income_suggestion_is_normalized_to_won(self):
        self.assertEqual(
            normalize_change_value('basic_info', 'monthly_income', '300'),
            3_000_000,
        )

    @override_settings(AI_CALLBACK_URL='http://backend:8000/api/ai/callback/')
    @patch('consulting.ai_services.build_customer_info')
    def test_ai_payload_contains_routing_and_callback_fields(self, build_customer_info):
        build_customer_info.return_value = {
            'saved_data': {},
            'current_form': {'monthly_income': 3_000_000},
        }
        session = SimpleNamespace(id=12)
        user = SimpleNamespace(id=3)

        payload = build_ai_request_payload(
            request_id='ai_req_test',
            session=session,
            user=user,
            message='은퇴 준비 상태를 알려줘',
            step_id='basic-info',
            form_data={'monthly_income': 3_000_000},
        )

        self.assertEqual(payload['request_id'], 'ai_req_test')
        self.assertEqual(payload['session_id'], 12)
        self.assertEqual(payload['thread_id'], 'user-3-session-12')
        self.assertEqual(payload['callback_url'], 'http://backend:8000/api/ai/callback/')
        self.assertEqual(
            payload['customer_info']['current_form']['monthly_income'],
            3_000_000,
        )


class AICallbackContractTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()

    @override_settings(AI_CALLBACK_SECRET='expected-secret')
    def test_callback_rejects_invalid_internal_secret(self):
        request = self.factory.post(
            '/api/ai/callback/',
            {'request_id': 'ai_req_test'},
            format='json',
            HTTP_X_INTERNAL_SECRET='wrong-secret',
        )

        response = AICallbackView.as_view()(request)

        self.assertEqual(response.status_code, 403)

    @override_settings(AI_CALLBACK_SECRET='')
    @patch('consulting.views.save_ai_request_state')
    @patch('consulting.views.get_ai_request_state')
    @patch('consulting.views.ConsultingSession.objects.filter')
    def test_failed_callback_stores_safe_public_error(
        self,
        session_filter,
        get_request_state,
        save_request_state,
    ):
        session_filter.return_value.first.return_value = SimpleNamespace(id=12, user_id=3)
        get_request_state.return_value = {
            'request_id': 'ai_req_test',
            'session_id': 12,
            'user_id': 3,
            'status': 'processing',
        }
        request = self.factory.post(
            '/api/ai/callback/',
            {
                'request_id': 'ai_req_test',
                'session_id': 12,
                'status': 'failed',
                'error_code': 'AI_PROCESSING_FAILED',
            },
            format='json',
        )

        response = AICallbackView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        saved_state = save_request_state.call_args.args[1]
        self.assertEqual(saved_state['status'], 'failed')
        self.assertEqual(saved_state['error_code'], 'AI_PROCESSING_FAILED')
        self.assertNotIn('traceback', saved_state['error'].lower())


class DisabledSSEEndpointTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()

    @patch('consulting.views.get_session_or_404')
    def test_temporary_sse_endpoint_does_not_store_dummy_message(self, get_session):
        get_session.return_value = SimpleNamespace(status='in_progress')
        request = self.factory.get('/messages/stream/')
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True))

        response = MessageStreamView.as_view()(request, session_id=12)

        self.assertEqual(response.status_code, 501)
