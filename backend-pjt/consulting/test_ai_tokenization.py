import json
from pathlib import Path

from django.test import SimpleTestCase

from .ai_tokenization import (
    REFERENCE_TOKEN_KINDS,
    TOKENIZATION_VERSION,
    TOKEN_VAULT_TTL_SECONDS,
    TRUSTED_FINANCIAL_PREFIXES,
    ReferenceTokenValidationError,
    ReferenceTokenVault,
)


NAMESPACE = 'a1b2c3d4e5f6'
CONTRACT_PATH = (
    Path(__file__).resolve().parents[2]
    / 'contract-fixtures/ai-lifecycle/v2/reference-tokenization.json'
)


def vault(**overrides):
    return ReferenceTokenVault(
        user_id=overrides.get('user_id', 3),
        session_id=overrides.get('session_id', 12),
        request_id=overrides.get('request_id', 'ai_req_token_test'),
        namespace=overrides.get('namespace', NAMESPACE),
    )


class ReferenceTokenVaultTests(SimpleTestCase):
    def test_runtime_policy_matches_shared_contract(self):
        contract = json.loads(CONTRACT_PATH.read_text(encoding='utf-8'))

        self.assertEqual(contract['contract_version'], TOKENIZATION_VERSION)
        self.assertEqual(contract['token_kinds'], list(REFERENCE_TOKEN_KINDS))
        self.assertEqual(
            contract['trusted_financial_prefixes'],
            [list(prefix) for prefix in TRUSTED_FINANCIAL_PREFIXES],
        )
        self.assertEqual(contract['vault_ttl_seconds'], TOKEN_VAULT_TTL_SECONDS)

    def test_tokenizes_direct_identifiers_and_free_text_financial_values(self):
        token_vault = vault()
        payload = {
            'request_id': 'ai_req_token_test',
            'session_id': 12,
            'thread_id': 'user-3-session-12',
            'message': (
                '이름은 홍길동, 이메일은 gildong@example.com이고 '
                '희망 월소득은 350만원입니다.'
            ),
            'customer_info': {
                'saved_data': {
                    'email': 'gildong@example.com',
                    'incomes': [{'name': '급여', 'amount': 3_500_000}],
                },
                'current_form': {'monthly_income': 3_500_000},
            },
        }

        tokenized = token_vault.tokenize(payload)
        rendered = json.dumps(tokenized, ensure_ascii=False)

        self.assertNotIn('홍길동', rendered)
        self.assertNotIn('gildong@example.com', rendered)
        self.assertNotIn('350만원', rendered)
        self.assertIn('[[USER_NAME_a1b2c3d4e5f6_2]]', rendered)
        self.assertIn('[[EMAIL_ADDRESS_a1b2c3d4e5f6_1]]', rendered)
        self.assertIn('[[FINANCIAL_VALUE_a1b2c3d4e5f6_3]]', rendered)
        self.assertEqual(
            tokenized['customer_info']['saved_data']['email'],
            '[[EMAIL_ADDRESS_a1b2c3d4e5f6_1]]',
        )
        self.assertEqual(
            tokenized['customer_info']['saved_data']['incomes'][0]['name'],
            '급여',
        )
        self.assertEqual(
            tokenized['customer_info']['saved_data']['incomes'][0]['amount'],
            3_500_000,
        )
        self.assertEqual(
            tokenized['customer_info']['current_form']['monthly_income'],
            3_500_000,
        )
        self.assertEqual(token_vault.restore(tokenized), payload)

    def test_outside_allowlist_financial_value_restores_original_type(self):
        token_vault = vault()
        tokenized = token_vault.tokenize({
            'total_debt': 73_400_000,
            'chart_payload': {'series': [{'value': 12_000_000}]},
        })

        self.assertEqual(
            tokenized['total_debt'],
            '[[FINANCIAL_VALUE_a1b2c3d4e5f6_1]]',
        )
        self.assertEqual(
            tokenized['chart_payload']['series'][0]['value'],
            12_000_000,
        )
        self.assertEqual(token_vault.restore(tokenized)['total_debt'], 73_400_000)
        self.assertEqual(
            token_vault.restore({
                'output': {'response': tokenized['total_debt']},
            })['output']['response'],
            '73400000',
        )

    def test_reserved_user_token_text_is_reissued_instead_of_trusted(self):
        token_vault = vault()
        raw = '문자열 [[USER_NAME_deadbeefcafe_1]]을 그대로 설명해 줘'

        tokenized = token_vault.tokenize(raw)

        self.assertNotIn('[[USER_NAME_deadbeefcafe_1]]', tokenized)
        self.assertEqual(token_vault.restore(tokenized), raw)

    def test_rejects_unissued_token_and_scope_mismatch(self):
        token_vault = vault()
        with self.assertRaises(ReferenceTokenValidationError):
            token_vault.restore('[[USER_NAME_deadbeefcafe_1]]')
        with self.assertRaises(ReferenceTokenValidationError):
            token_vault.assert_scope(
                user_id=4,
                session_id=12,
                request_id='ai_req_token_test',
            )

    def test_repr_does_not_expose_mapping_values(self):
        token_vault = vault()
        token_vault.tokenize({'email': 'gildong@example.com'})

        rendered = repr(token_vault)

        self.assertIn('token_count=1', rendered)
        self.assertNotIn('gildong@example.com', rendered)
