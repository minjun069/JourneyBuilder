import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from .ai_tokenization import (
    REFERENCE_TOKEN_PATTERN,
    ReferenceTokenValidationError,
    ReferenceTokenVault,
)
from .v2_sse import proxy_v2_ai_stream


def vault():
    return ReferenceTokenVault(user_id=3, session_id=12, request_id='money-test', namespace='a1b2c3d4e5f6')


def terminal(value, *, step='basic-info', section='basic_info', path='monthly_income', operation='set'):
    return {
        'contract_version': '2.0', 'request_id': 'money-test', 'session_id': 12,
        'thread_id': 'user-3-session-12', 'status': 'completed',
        'completed_at': '2026-09-21T00:00:00Z',
        'output': {
            'response': '변경 내용을 확인해 주세요.', 'step_id': step,
            'action': {
                'type': 'form_change', 'target': 'draft_form', 'user_requested': True,
                'changes': [{'section': section, 'path': path, 'operation': operation,
                             'value': value, 'reason': '사용자 요청'}],
            },
        },
    }


def restored_value(token_vault, value, **kwargs):
    result = token_vault.restore_terminal(terminal(value, **kwargs), step_id=kwargs.get('step', 'basic-info'))
    return result['output']['action']['changes'][0]['value']


class FinancialReferenceValueTests(SimpleTestCase):
    def test_complete_money_expression_restores_to_canonical_integer(self):
        cases = {
            '500만 원': 5_000_000, '5000000원': 5_000_000, '1.5만원': 15_000,
            '1억 5000만원': 150_000_000, '1억 5천만원': 150_000_000,
            '1만 5000원': 15_000, '1,500,000원': 1_500_000,
            '1500000000원': 1_500_000_000, '1000001000000원': 1_000_001_000_000,
            '01012345678원': 1_012_345_678, '+500만원': 5_000_000, '0원': 0,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                token_vault = vault()
                token = token_vault.tokenize(text)
                self.assertEqual(REFERENCE_TOKEN_PATTERN.fullmatch(token).group('kind'), 'FINANCIAL_VALUE')
                self.assertEqual(token_vault.restore(token), text)
                self.assertEqual(restored_value(token_vault, token), expected)

    def test_unsupported_or_negative_amounts_fail_without_partial_conversion(self):
        for text in ('-500만원', '−500만원', '- +500만원', '500.5원', '1e6원', '500달러원', '500%원',
                     '500개월원', '1억 -500만원', '1만1억원', '1천백만원', '500 500원', '2*500만원'):
            with self.subTest(text=text):
                token_vault = vault()
                token = token_vault.tokenize(text)
                self.assertEqual(token_vault.restore(token), text)
                with self.assertRaises(ReferenceTokenValidationError):
                    restored_value(token_vault, token)

    def test_only_exact_financial_token_can_fill_numeric_krw(self):
        token_vault = vault()
        token = token_vault.tokenize('500만원')
        direct = token_vault.tokenize({'account_number': '5000000'})['account_number']
        for value in (direct, token + '원', token + '+1', token + token, '500만원', '5000000'):
            with self.subTest(value=value):
                with self.assertRaises(ReferenceTokenValidationError):
                    restored_value(token_vault, value)
        for path in ('age', 'job'):
            with self.assertRaises(ReferenceTokenValidationError):
                restored_value(token_vault, token, path=path)
        with self.assertRaises(ReferenceTokenValidationError):
            restored_value(token_vault, token, step='pension-assets', section='pension_assets', path='expected_return_rate')

    def test_identifier_context_cannot_be_reinterpreted_as_a_money_reference(self):
        for text in ('계좌번호 1500000000원', '전화번호 01012345678원', '주민번호 1000001000000원'):
            with self.subTest(text=text):
                token_vault = vault()
                tokenized = token_vault.tokenize(text)
                self.assertNotIn('[[FINANCIAL_VALUE_', tokenized)
                token = REFERENCE_TOKEN_PATTERN.search(tokenized).group(0)
                with self.assertRaises(ReferenceTokenValidationError):
                    restored_value(token_vault, token)

    def test_forged_and_other_request_tokens_and_literal_tokens_are_rejected(self):
        token_vault = vault()
        for token in ('[[FINANCIAL_VALUE_a1b2c3d4e5f6_1]]', '[[FINANCIAL_VALUE_deadbeefcafe_1]]'):
            with self.assertRaises(ReferenceTokenValidationError):
                restored_value(token_vault, token)
        literal = token_vault.tokenize('[[FINANCIAL_VALUE_deadbeefcafe_1]]')
        with self.assertRaises(ReferenceTokenValidationError):
            restored_value(token_vault, literal)
        token = token_vault.tokenize('500만원')
        for key, wrong in (('request_id', 'other'), ('session_id', 999)):
            payload = terminal(token)
            payload[key] = wrong
            with self.assertRaises(ReferenceTokenValidationError):
                token_vault.restore_terminal(payload, step_id='basic-info')
        with self.assertRaises(ReferenceTokenValidationError):
            token_vault.restore_terminal(terminal(token), step_id='general')

    def test_add_and_nested_array_validate_each_actual_canonical_leaf(self):
        token_vault = vault()
        token = token_vault.tokenize('500만원')
        row = restored_value(token_vault, {'name': '예금', 'principal': token},
                             step='financial-assets', section='financial_assets', path='name', operation='add')
        self.assertEqual(row, {'name': '예금', 'principal': 5_000_000})
        row = {'name': '생활비', 'amount': token, 'is_fixed': False}
        expected = {**row, 'amount': 5_000_000}
        self.assertEqual(restored_value(token_vault, row, step='financial-goals',
                                       section='goal_periods', path='items', operation='add'), expected)
        self.assertEqual(restored_value(token_vault, [row], step='financial-goals',
                                       section='goal_periods', path='items', operation='update'), [expected])
        for bad in ({'name': token}, {'unknown': token}, {'amount': {'value': token}}):
            with self.assertRaises(ReferenceTokenValidationError):
                restored_value(token_vault, [bad], step='financial-goals', section='goal_periods', path='items')
        with self.assertRaises(ReferenceTokenValidationError):
            restored_value(token_vault, {'unit': '월', 'count': token},
                           step='expenses', section='expenses', path='frequency')
        with self.assertRaises(ReferenceTokenValidationError):
            restored_value(token_vault, {'children': [{'age': token}]}, path='dependents')

    def test_canonical_types_constraints_and_finite_values_are_checked_after_restore(self):
        token_vault = vault()
        for value in (True, -1, 1.5, float('nan'), float('inf'), float('-inf')):
            with self.subTest(value=value):
                with self.assertRaises(ReferenceTokenValidationError):
                    restored_value(token_vault, value)
        self.assertEqual(restored_value(token_vault, 5_000_000), 5_000_000)
        self.assertIsNone(restored_value(token_vault, None))
        for value in (17, 101):
            with self.assertRaises(ReferenceTokenValidationError):
                restored_value(token_vault, value, path='age')
        with self.assertRaises(ReferenceTokenValidationError):
            restored_value(token_vault, '알 수 없는 직업', path='job')
        with self.assertRaises(ReferenceTokenValidationError):
            restored_value(token_vault, None, path='dependents')

    def test_text_and_approval_flags_keep_original_values(self):
        token_vault = vault()
        token = token_vault.tokenize('500만 원')
        payload = terminal(token)
        payload['output']['response'] = f'{token}으로 바꾸려면 확인해 주세요.'
        payload['output']['action']['changes'][0]['reason'] = f'{token} 입력 요청'
        original = deepcopy(payload)
        result = token_vault.restore_terminal(payload, step_id='basic-info')
        self.assertEqual(payload, original)
        self.assertEqual(result['output']['response'], '500만 원으로 바꾸려면 확인해 주세요.')
        self.assertEqual(result['output']['action']['changes'][0]['reason'], '500만 원 입력 요청')
        self.assertIs(result['output']['action']['user_requested'], True)
        self.assertEqual(result['output']['action']['target'], 'draft_form')

    @patch('consulting.v2_sse.finalize_terminal_result')
    @patch('consulting.v2_sse.stream_v2_ai_response')
    def test_proxy_restores_and_validates_before_finalizing(self, upstream, finalize):
        async def events(payload):
            for line in ('event: done', f'data: {json.dumps(payload, ensure_ascii=False)}', ''):
                yield line

        async def collect(stream):
            return [frame async for frame in stream]

        for amount, success in (('500만 원', True), ('-500만원', False), ('1e6원', False)):
            with self.subTest(amount=amount):
                token_vault = vault()
                token = token_vault.tokenize(amount)
                upstream.return_value = events(terminal(token))
                state = {'request_id': 'money-test', 'session_id': 12, 'user_id': 3,
                         'thread_id': 'user-3-session-12', 'step_id': 'basic-info', 'user_message_id': 41}
                message = SimpleNamespace(id=99, role='assistant', content='확인해 주세요.', extracted_data={'action': None})
                finalize.return_value = SimpleNamespace(state={**state, 'status': 'completed' if success else 'failed'},
                                                         message=message if success else None, replay=False)
                frames = asyncio.run(collect(proxy_v2_ai_stream(SimpleNamespace(id=12), state, state,
                                                                MagicMock(), token_vault=token_vault)))
                finalized = finalize.call_args.args[1]
                if success:
                    self.assertEqual(finalized['output']['action']['changes'][0]['value'], 5_000_000)
                    self.assertIn('event: done', frames[-1])
                else:
                    self.assertEqual(finalized['error']['code'], 'POLICY_BLOCKED')
                    self.assertIn('event: error', frames[-1])
