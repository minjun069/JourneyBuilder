from collections import Counter

from django.test import SimpleTestCase

from .field_registry import ALLOWED_OPERATIONS, FIELDS, SECTION_META
from .form_actions import normalize_form_action
from .models import BasicInfo
from .serializers import (
    BasicInfoWriteSerializer,
    ExpenseSerializer,
    FinancialAssetSerializer,
    FinancialSatisfactionSerializer,
    parse_korean_money,
)


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
