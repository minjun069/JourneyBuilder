from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from .ai_lifecycle import (
    ACTIVE_STATE_TTL_SECONDS,
    CONTRACT_VERSION,
    TERMINAL_STATE_TTL_SECONDS,
    LifecycleError,
    RedisLifecycleStore,
    _validate_error_review_fields,
    bind_terminal_result,
    build_v2_ai_payload,
    create_v2_assistant_message,
    make_processing_state,
    validate_browser_message,
    validate_terminal_payload,
)
from .ai_tokenization import ReferenceTokenVault, TOKEN_VAULT_TTL_SECONDS


UTC = timezone.utc
NOW = datetime(2026, 9, 2, 6, 0, tzinfo=UTC)


def completed_terminal(**overrides):
    payload = {
        'contract_version': CONTRACT_VERSION,
        'request_id': 'ai_req_test',
        'session_id': 12,
        'thread_id': 'user-3-session-12',
        'status': 'completed',
        'completed_at': '2026-09-02T06:00:42Z',
        'output': {'response': '두 항목을 각각 확인해주세요.'},
    }
    payload.update(overrides)
    return payload


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.ttls = {}

    def get(self, key):
        return self.values.get(key)

    def setex(self, key, ttl, value):
        self.values[key] = value
        self.ttls[key] = ttl

    def delete(self, key):
        return int(self.values.pop(key, None) is not None)


class BrowserMessageValidationTests(SimpleTestCase):
    def valid_payload(self):
        return {
            'contract_version': CONTRACT_VERSION,
            'content': '월급은 세후 300만 원이에요.',
            'step_id': 'basic-info',
            'form_data': {},
        }

    def test_accepts_only_the_frozen_v2_shape(self):
        self.assertEqual(validate_browser_message(self.valid_payload()), self.valid_payload())

        with self.assertRaises(LifecycleError) as captured:
            validate_browser_message({**self.valid_payload(), 'form': {}})

        self.assertEqual(captured.exception.code, 'REQUEST_INVALID')

    def test_missing_and_invalid_content_have_stable_errors(self):
        missing = self.valid_payload()
        missing.pop('content')
        with self.assertRaises(LifecycleError) as captured:
            validate_browser_message(missing)
        self.assertEqual(captured.exception.message, 'content는 필수입니다.')

        invalid = {**self.valid_payload(), 'content': 3_000_000}
        with self.assertRaises(LifecycleError) as captured:
            validate_browser_message(invalid)
        self.assertEqual(captured.exception.message, 'content는 문자열이어야 합니다.')

    def test_rejects_unsupported_contract_version(self):
        with self.assertRaises(LifecycleError) as captured:
            validate_browser_message({**self.valid_payload(), 'contract_version': '1.0'})

        self.assertEqual(captured.exception.status_code, 422)
        self.assertEqual(captured.exception.code, 'CONTRACT_VERSION_UNSUPPORTED')

    def test_error_review_is_save_bound_and_legacy_controls_are_rejected(self):
        review = validate_browser_message({
            **self.valid_payload(),
            'request_mode': 'error_review',
        })
        self.assertEqual(review['request_mode'], 'error_review')

        with self.assertRaises(LifecycleError):
            validate_browser_message({
                **self.valid_payload(),
                'request_mode': 'error_review',
                'review_trigger': 'save',
            })
        with self.assertRaises(LifecycleError):
            validate_browser_message({**self.valid_payload(), 'elicit_action': 'start'})

    def test_presentation_entry_is_a_bound_mode(self):
        payload = validate_browser_message({
            **self.valid_payload(),
            'step_id': 'lifetime-cashflow',
            'request_mode': 'presentation_entry',
        })

        self.assertEqual(payload['request_mode'], 'presentation_entry')
        self.assertNotIn('review_trigger', payload)


class RedisLifecycleStoreTests(SimpleTestCase):
    def test_active_terminal_and_idempotency_ttls_are_distinct(self):
        client = FakeRedis()
        store = RedisLifecycleStore(client)
        state = {'request_id': 'ai_req_test', 'status': 'processing'}

        store.save_active(state)
        self.assertEqual(
            client.ttls[store.request_key('ai_req_test')],
            ACTIVE_STATE_TTL_SECONDS,
        )
        self.assertEqual(store.get_request('ai_req_test'), state)

        state['status'] = 'completed'
        store.save_terminal(state)
        self.assertEqual(
            client.ttls[store.request_key('ai_req_test')],
            TERMINAL_STATE_TTL_SECONDS,
        )

        store.save_idempotency(3, 12, 'idem-key', {'body': {'ok': True}})
        idempotency_key = store.idempotency_key(3, 12, 'idem-key')
        self.assertEqual(client.ttls[idempotency_key], TERMINAL_STATE_TTL_SECONDS)
        self.assertEqual(
            store.get_idempotency(3, 12, 'idem-key'),
            {'body': {'ok': True}},
        )

        vault = ReferenceTokenVault(
            user_id=3,
            session_id=12,
            request_id='ai_req_test',
            namespace='a1b2c3d4e5f6',
        )
        vault.tokenize({'email': 'gildong@example.com'})
        store.save_token_vault(vault)
        token_key = store.token_vault_key('ai_req_test')
        self.assertEqual(client.ttls[token_key], TOKEN_VAULT_TTL_SECONDS)
        self.assertIn('gildong@example.com', client.values[token_key])
        store.delete_token_vault('ai_req_test')
        self.assertNotIn(token_key, client.values)

    def test_optional_namespace_prefixes_every_lifecycle_key(self):
        client = FakeRedis()
        store = RedisLifecycleStore(client, namespace='p07:run42:')
        state = {'request_id': 'ai_req_test', 'status': 'processing'}

        store.save_active(state)
        store.save_idempotency(3, 12, 'idem-key', {'body': {'ok': True}})
        store.save_token_vault(ReferenceTokenVault(
            user_id=3,
            session_id=12,
            request_id='ai_req_test',
            namespace='a1b2c3d4e5f6',
        ))

        self.assertIn('p07:run42:ai:v2:request:ai_req_test', client.values)
        self.assertIn('p07:run42:ai:v2:idempotency:3:12:idem-key', client.values)
        self.assertIn('p07:run42:ai:v2:token-vault:ai_req_test', client.values)


class AIStreamRequestTests(SimpleTestCase):
    @patch('consulting.ai_lifecycle.build_customer_info')
    def test_builds_frozen_payload_without_callback_url(
        self,
        customer_info,
    ):
        customer_info.return_value = {'saved_data': {}, 'current_form': {}}
        payload = build_v2_ai_payload(
            request_id='ai_req_test',
            session=SimpleNamespace(id=12),
            user=SimpleNamespace(id=3),
            message='은퇴 준비 상태를 알려줘',
            step_id='basic-info',
            form_data={},
            now=NOW,
        )

        self.assertEqual(payload['thread_id'], 'user-3-session-12')
        self.assertEqual(payload['deadline_at'], '2026-09-02T06:01:00Z')
        self.assertNotIn('field_registry', payload)
        self.assertNotIn('tool_context', payload)
        self.assertNotIn('callback_url', payload)

class RequestStateTransitionTests(SimpleTestCase):
    def processing_state(self):
        return make_processing_state(
            request_id='ai_req_test',
            session=SimpleNamespace(id=12),
            user=SimpleNamespace(id=3),
            user_message_id=20,
            thread_id='user-3-session-12',
            step_id='basic-info',
            payload_digest='a' * 64,
            now=NOW,
        )

    def test_terminal_binding_and_digest_are_immutable(self):
        state = self.processing_state()
        payload = completed_terminal()
        bound, replay = bind_terminal_result(state, payload)
        self.assertFalse(replay)
        terminal = {**bound, 'status': 'completed', 'assistant_message_id': 21}

        replayed, replay = bind_terminal_result(terminal, payload)
        self.assertTrue(replay)
        self.assertIs(replayed, terminal)

        with self.assertRaises(LifecycleError) as captured:
            bind_terminal_result(
                terminal,
                completed_terminal(completed_at='2026-09-02T06:00:43Z'),
            )
        self.assertEqual(captured.exception.code, 'TERMINAL_CONFLICT')

    def test_terminal_rejects_binding_mismatch_and_expired_state(self):
        state = self.processing_state()
        with self.assertRaises(LifecycleError) as captured:
            bind_terminal_result(state, completed_terminal(session_id=99))
        self.assertEqual(captured.exception.code, 'TERMINAL_BINDING_MISMATCH')

        with self.assertRaises(LifecycleError) as captured:
            bind_terminal_result({**state, 'status': 'expired'}, completed_terminal())
        self.assertEqual(captured.exception.code, 'TERMINAL_CONFLICT')

        with self.assertRaises(LifecycleError) as captured:
            bind_terminal_result({**state, 'status': 'unknown'}, completed_terminal())
        self.assertEqual(captured.exception.code, 'TERMINAL_CONFLICT')


class TerminalPayloadValidationTests(SimpleTestCase):
    def test_completed_and_failed_terminal_payloads_are_valid(self):
        self.assertEqual(validate_terminal_payload(completed_terminal())['status'], 'completed')
        failed = completed_terminal(
            status='failed',
            error={
                'code': 'LLM_TIMEOUT',
                'message': 'AI 응답 생성 시간이 초과되었습니다.',
                'retryable': True,
            },
        )
        failed.pop('output')
        self.assertEqual(validate_terminal_payload(failed)['status'], 'failed')

    def test_legacy_elicit_ui_action_is_rejected(self):
        payload = completed_terminal()
        payload['output']['ui_action'] = {
            'type': 'resume_elicit',
            'label': '이어서 계속',
        }
        with self.assertRaises(LifecycleError) as captured:
            validate_terminal_payload(payload)
        self.assertEqual(captured.exception.code, 'REQUEST_SCHEMA_INVALID')

    def test_missing_status_is_not_inferred(self):
        payload = completed_terminal()
        payload.pop('status')
        with self.assertRaises(LifecycleError) as captured:
            validate_terminal_payload(payload)
        self.assertEqual(captured.exception.message, 'status는 필수입니다.')

    def test_rejects_naive_or_malformed_completed_at(self):
        for value in ('not-a-date', '2026-09-02T06:00:42'):
            with self.subTest(value=value), self.assertRaises(LifecycleError):
                validate_terminal_payload(completed_terminal(completed_at=value))

    def test_structured_error_review_output_is_validated(self):
        review = {
            'type': 'error_review',
            'manual': {'document_id': 'interface-basic-info', 'version': '1.0.0'},
            'items': [{
                'section': 'basic_info',
                'path': 'monthly_income',
                'current_value': 20_000,
                'reason': '다른 소득 정보와 비교해 단위 착오가 의심됩니다.',
                'suggested_value': 2_000_000,
                'confidence': 0.91,
                'confidence_level': 'high',
            }],
        }
        payload = completed_terminal(output={'response': '확인이 필요합니다.', 'review': review})
        self.assertEqual(validate_terminal_payload(payload), payload)

        invalid = completed_terminal(output={
            'response': '확인이 필요합니다.',
            'review': {**review, 'trigger': 'save'},
        })
        with self.assertRaises(LifecycleError):
            validate_terminal_payload(invalid)

        _validate_error_review_fields({'review': review}, 'basic-info')
        with self.assertRaises(LifecycleError):
            _validate_error_review_fields({
                'review': {
                    **review,
                    'items': [{**review['items'][0], 'path': 'unknown_field'}],
                },
            }, 'basic-info')

        low_confidence = completed_terminal(output={
            'response': '확인이 필요합니다.',
            'review': {
                **review,
                'items': [{
                    **review['items'][0],
                    'confidence': 0.59,
                    'confidence_level': 'low',
                }],
            },
        })
        with self.assertRaises(LifecycleError):
            validate_terminal_payload(low_confidence)


class FormChangeProposalTests(SimpleTestCase):
    @patch('consulting.ai_lifecycle.create_assistant_message_from_terminal')
    def test_terminal_message_exposes_only_v2_action_shape(self, create_message):
        message = SimpleNamespace(
            extracted_data={
                'intent': 'input_assist',
                'requires_approval': True,
                'pending_changes': [],
                'action': {
                    'type': 'form_change',
                    'step_id': 'basic-info',
                    'target': 'draft_form',
                    'mode': 'proposal',
                    'changes': [{
                        'section': 'basic_info',
                        'path': 'job',
                        'operation': 'set',
                        'value': '직장인',
                    }],
                },
            },
            save=Mock(),
        )
        create_message.return_value = message

        result = create_v2_assistant_message(
            SimpleNamespace(id=12),
            completed_terminal(),
            'basic-info',
        )

        self.assertEqual(set(result.extracted_data), {'action'})
        action = result.extracted_data['action']
        self.assertTrue(action['action_id'].startswith('act_'))
        self.assertTrue(action['changes'][0]['change_id'].startswith('chg_'))
        self.assertEqual(action['changes'][0]['decision_status'], 'pending')
        message.save.assert_called_once_with(update_fields=['extracted_data'])
