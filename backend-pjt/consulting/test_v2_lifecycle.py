from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from .ai_lifecycle import (
    ACTIVE_STATE_TTL_SECONDS,
    CONTRACT_VERSION,
    TERMINAL_STATE_TTL_SECONDS,
    LifecycleError,
    RedisLifecycleStore,
    _validate_error_review_fields,
    bind_terminal_callback,
    build_v2_ai_payload,
    create_v2_assistant_message,
    decide_change,
    dispatch_v2_ai_request,
    expire_if_due,
    make_processing_state,
    validate_browser_message,
    validate_terminal_callback,
    verify_service_bearer,
)
from .v2_views import V2MessageCreateView, V2PollingView, serialize_v2_message


UTC = timezone.utc
NOW = datetime(2026, 9, 2, 6, 0, tzinfo=UTC)


def completed_callback(**overrides):
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

    def test_error_review_and_elicit_controls_are_explicitly_bound(self):
        review = validate_browser_message({
            **self.valid_payload(),
            'request_mode': 'error_review',
            'review_trigger': 'save',
        })
        self.assertEqual(review['review_trigger'], 'save')

        with self.assertRaises(LifecycleError):
            validate_browser_message({**self.valid_payload(), 'request_mode': 'error_review'})
        with self.assertRaises(LifecycleError):
            validate_browser_message({**self.valid_payload(), 'elicit_action': 'start'})

        elicit = validate_browser_message({
            **self.valid_payload(),
            'step_id': 'financial-mindset',
            'elicit_action': 'start',
        })
        self.assertEqual(elicit['elicit_action'], 'start')


class ServiceAuthenticationTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()

    @override_settings(AI_SERVICE_BEARER_TOKEN='expected-token')
    def test_requires_exact_bearer_token(self):
        valid = self.factory.post(
            '/api/v2/internal/ai/callback/',
            {},
            format='json',
            HTTP_AUTHORIZATION='Bearer expected-token',
        )
        verify_service_bearer(valid)

        invalid = self.factory.post(
            '/api/v2/internal/ai/callback/',
            {},
            format='json',
            HTTP_AUTHORIZATION='Bearer wrong-token',
        )
        with self.assertRaises(LifecycleError) as captured:
            verify_service_bearer(invalid)
        self.assertEqual(captured.exception.code, 'SERVICE_UNAUTHORIZED')


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

    def test_optional_namespace_prefixes_every_lifecycle_key(self):
        client = FakeRedis()
        store = RedisLifecycleStore(client, namespace='p07:run42:')
        state = {'request_id': 'ai_req_test', 'status': 'processing'}

        store.save_active(state)
        store.save_idempotency(3, 12, 'idem-key', {'body': {'ok': True}})

        self.assertIn('p07:run42:ai:v2:request:ai_req_test', client.values)
        self.assertIn('p07:run42:ai:v2:idempotency:3:12:idem-key', client.values)


class AIRequestDispatchTests(SimpleTestCase):
    @patch('consulting.ai_lifecycle.get_step_field_registry')
    @patch('consulting.ai_lifecycle.build_tool_context')
    @patch('consulting.ai_lifecycle.build_customer_info')
    def test_builds_frozen_payload_without_callback_url(
        self,
        customer_info,
        tool_context,
        registry,
    ):
        customer_info.return_value = {'saved_data': {}, 'current_form': {}}
        registry.return_value = [{'path': 'monthly_income'}]
        tool_context.return_value = {
            'schema_version': '1.0',
            'session_id': 12,
            'step_id': 'basic-info',
        }
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
        self.assertEqual(payload['field_registry'], [{'path': 'monthly_income'}])
        self.assertEqual(payload['tool_context']['session_id'], 12)
        self.assertNotIn('callback_url', payload)

    @override_settings(
        AI_SERVICE_URL='http://ai-pjt:8001/chat',
        AI_SERVICE_BEARER_TOKEN='service-token',
    )
    def test_retries_retryable_response_once_with_identical_request(self):
        first = Mock(status_code=503)
        second = Mock(status_code=202)
        second.json.return_value = {
            'contract_version': CONTRACT_VERSION,
            'request_id': 'ai_req_test',
            'status': 'accepted',
        }
        post = Mock(side_effect=[first, second])
        payload = {'request_id': 'ai_req_test'}

        result = dispatch_v2_ai_request(payload, post=post)

        self.assertEqual(result['status'], 'accepted')
        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args_list[0], post.call_args_list[1])
        self.assertEqual(post.call_args.kwargs['timeout'], 5)
        self.assertEqual(
            post.call_args.kwargs['headers']['Authorization'],
            'Bearer service-token',
        )

    @override_settings(
        AI_SERVICE_URL='http://ai-pjt:8001/chat',
        AI_SERVICE_BEARER_TOKEN='service-token',
    )
    def test_does_not_retry_non_retryable_http_error(self):
        response = Mock(status_code=401)
        response.raise_for_status.side_effect = ValueError('unauthorized')
        post = Mock(return_value=response)

        with self.assertRaises(ValueError):
            dispatch_v2_ai_request({'request_id': 'ai_req_test'}, post=post)

        self.assertEqual(post.call_count, 1)


class RequestStateTransitionTests(SimpleTestCase):
    def processing_state(self):
        return make_processing_state(
            request_id='ai_req_test',
            session=SimpleNamespace(id=12),
            user=SimpleNamespace(id=3),
            user_message_id=20,
            thread_id='user-3-session-12',
            step_id='basic-info',
            now=NOW,
        )

    def test_processing_deadline_expires_once(self):
        state = self.processing_state()
        self.assertEqual(state['processing_deadline_at'], '2026-09-02T06:01:30Z')

        active, changed = expire_if_due(state, now=NOW + timedelta(seconds=89))
        self.assertIs(active, state)
        self.assertFalse(changed)

        expired, changed = expire_if_due(state, now=NOW + timedelta(seconds=90))
        self.assertTrue(changed)
        self.assertEqual(expired['status'], 'expired')
        self.assertEqual(expired['error']['code'], 'AI_REQUEST_EXPIRED')
        replay, changed = expire_if_due(expired, now=NOW + timedelta(seconds=180))
        self.assertIs(replay, expired)
        self.assertFalse(changed)

    def test_callback_binding_and_terminal_digest_are_immutable(self):
        state = self.processing_state()
        payload = completed_callback()
        bound, replay = bind_terminal_callback(state, payload)
        self.assertFalse(replay)
        terminal = {**bound, 'status': 'completed', 'assistant_message_id': 21}

        replayed, replay = bind_terminal_callback(terminal, payload)
        self.assertTrue(replay)
        self.assertIs(replayed, terminal)

        with self.assertRaises(LifecycleError) as captured:
            bind_terminal_callback(
                terminal,
                completed_callback(completed_at='2026-09-02T06:00:43Z'),
            )
        self.assertEqual(captured.exception.code, 'CALLBACK_CONFLICT')

    def test_callback_rejects_binding_mismatch_and_expired_state(self):
        state = self.processing_state()
        with self.assertRaises(LifecycleError) as captured:
            bind_terminal_callback(state, completed_callback(session_id=99))
        self.assertEqual(captured.exception.code, 'CALLBACK_BINDING_MISMATCH')

        with self.assertRaises(LifecycleError) as captured:
            bind_terminal_callback({**state, 'status': 'expired'}, completed_callback())
        self.assertEqual(captured.exception.code, 'CALLBACK_CONFLICT')

        with self.assertRaises(LifecycleError) as captured:
            bind_terminal_callback({**state, 'status': 'unknown'}, completed_callback())
        self.assertEqual(captured.exception.code, 'CALLBACK_CONFLICT')


class CallbackValidationTests(SimpleTestCase):
    def test_completed_and_failed_terminal_callbacks_are_valid(self):
        self.assertEqual(validate_terminal_callback(completed_callback())['status'], 'completed')
        failed = completed_callback(
            status='failed',
            error={
                'code': 'LLM_TIMEOUT',
                'message': 'AI 응답 생성 시간이 초과되었습니다.',
                'retryable': True,
            },
        )
        failed.pop('output')
        self.assertEqual(validate_terminal_callback(failed)['status'], 'failed')

    def test_missing_status_is_not_inferred(self):
        payload = completed_callback()
        payload.pop('status')
        with self.assertRaises(LifecycleError) as captured:
            validate_terminal_callback(payload)
        self.assertEqual(captured.exception.message, 'status는 필수입니다.')

    def test_rejects_naive_or_malformed_completed_at(self):
        for value in ('not-a-date', '2026-09-02T06:00:42'):
            with self.subTest(value=value), self.assertRaises(LifecycleError):
                validate_terminal_callback(completed_callback(completed_at=value))

    def test_structured_error_review_output_is_validated(self):
        review = {
            'type': 'error_review',
            'trigger': 'save',
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
        payload = completed_callback(output={'response': '확인이 필요합니다.', 'review': review})
        self.assertEqual(validate_terminal_callback(payload), payload)

        invalid = completed_callback(output={
            'response': '확인이 필요합니다.',
            'review': {**review, 'trigger': 'blocking_save'},
        })
        with self.assertRaises(LifecycleError):
            validate_terminal_callback(invalid)

        _validate_error_review_fields({'review': review}, 'basic-info')
        with self.assertRaises(LifecycleError):
            _validate_error_review_fields({
                'review': {
                    **review,
                    'items': [{**review['items'][0], 'path': 'unknown_field'}],
                },
            }, 'basic-info')

        low_confidence = completed_callback(output={
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
            validate_terminal_callback(low_confidence)


class FormChangeDecisionTests(SimpleTestCase):
    def action_message(self):
        return SimpleNamespace(
            extracted_data={
                'action': {
                    'action_id': 'act_test',
                    'status': 'pending',
                    'changes': [
                        {
                            'change_id': 'chg_income',
                            'decision_status': 'pending',
                            'section': 'basic_info',
                            'path': 'monthly_income',
                            'operation': 'set',
                            'value': 3_000_000,
                            'label': '세후 월 소득',
                            'reason': '사용자 발화에서 추출',
                        },
                        {
                            'change_id': 'chg_job',
                            'decision_status': 'pending',
                            'section': 'basic_info',
                            'path': 'job',
                            'operation': 'set',
                            'value': '직장인',
                        },
                    ],
                },
            },
            save=Mock(),
        )

    def test_each_change_is_approved_or_rejected_independently(self):
        message = self.action_message()

        approved = decide_change(
            message,
            change_id='chg_income',
            decision='approved',
        )
        self.assertEqual(approved['action_status'], 'partially_resolved')
        self.assertEqual(approved['change'], {
            'section': 'basic_info',
            'path': 'monthly_income',
            'operation': 'set',
            'value': 3_000_000,
        })

        rejected = decide_change(
            message,
            change_id='chg_job',
            decision='rejected',
        )
        self.assertEqual(rejected['action_status'], 'resolved')
        self.assertNotIn('change', rejected)

    def test_opposite_decision_conflicts(self):
        message = self.action_message()
        decide_change(message, change_id='chg_income', decision='approved')

        with self.assertRaises(LifecycleError) as captured:
            decide_change(message, change_id='chg_income', decision='rejected')

        self.assertEqual(captured.exception.code, 'ACTION_DECISION_CONFLICT')

    @patch('consulting.ai_lifecycle.create_assistant_message_from_callback')
    def test_callback_message_exposes_only_v2_action_shape(self, create_message):
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
            completed_callback(),
            'basic-info',
        )

        self.assertEqual(set(result.extracted_data), {'action'})
        action = result.extracted_data['action']
        self.assertTrue(action['action_id'].startswith('act_'))
        self.assertTrue(action['changes'][0]['change_id'].startswith('chg_'))
        self.assertEqual(action['changes'][0]['decision_status'], 'pending')
        message.save.assert_called_once_with(update_fields=['extracted_data'])


class V2LifecycleViewTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.user = SimpleNamespace(id=3, is_authenticated=True)
        self.session = SimpleNamespace(id=12, status='in_progress')

    def authenticated_post(self, path, body, **headers):
        request = self.factory.post(path, body, format='json', **headers)
        force_authenticate(request, user=self.user)
        return request

    def test_polling_message_uses_the_frozen_public_shape(self):
        message = SimpleNamespace(
            id=21,
            role='assistant',
            content='확인해주세요.',
            extracted_data={'action': {}},
            created_at=NOW,
        )

        self.assertEqual(serialize_v2_message(message), {
            'id': 21,
            'role': 'assistant',
            'content': '확인해주세요.',
            'extracted_data': {'action': {}},
        })

    @patch('consulting.v2_views.dispatch_v2_ai_request')
    @patch('consulting.v2_views.build_v2_ai_payload')
    @patch('consulting.v2_views.new_request_id', return_value='ai_req_test')
    @patch('consulting.v2_views.Message.objects.create')
    @patch('consulting.v2_views.RedisLifecycleStore')
    @patch('consulting.v2_views.get_owned_session')
    def test_message_endpoint_persists_processing_before_dispatch(
        self,
        get_session,
        store_class,
        create_message,
        _new_request_id,
        build_payload,
        dispatch,
    ):
        get_session.return_value = self.session
        store = store_class.return_value
        store.get_idempotency.return_value = None
        create_message.return_value = SimpleNamespace(id=20)
        build_payload.return_value = {
            'request_id': 'ai_req_test',
            'thread_id': 'user-3-session-12',
        }
        events = []
        store.save_active.side_effect = lambda _state: events.append('save_active')
        dispatch.side_effect = lambda _payload: events.append('dispatch')
        request = self.authenticated_post(
            '/api/v2/consultations/12/messages/',
            {
                'contract_version': CONTRACT_VERSION,
                'content': '월급은 세후 300만 원이에요.',
                'step_id': 'basic-info',
                'form_data': {},
            },
            HTTP_IDEMPOTENCY_KEY='idem-message',
        )

        response = V2MessageCreateView.as_view()(request, session_id=12)

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data['data']['request_id'], 'ai_req_test')
        store.save_active.assert_called_once()
        dispatch.assert_called_once_with(build_payload.return_value)
        self.assertEqual(events, ['save_active', 'dispatch'])
        store.save_idempotency.assert_called_once()

    @patch('consulting.v2_views.RedisLifecycleStore')
    @patch('consulting.v2_views.get_owned_session')
    def test_identical_message_replay_returns_saved_response(
        self,
        get_session,
        store_class,
    ):
        get_session.return_value = self.session
        body = {
            'contract_version': CONTRACT_VERSION,
            'content': '월급은 세후 300만 원이에요.',
            'step_id': 'basic-info',
            'form_data': {},
        }
        from .ai_lifecycle import canonical_digest

        store_class.return_value.get_idempotency.return_value = {
            'payload_digest': canonical_digest(body),
            'status_code': 202,
            'body': {
                'contract_version': CONTRACT_VERSION,
                'success': True,
                'data': {'request_id': 'ai_req_test', 'status': 'processing'},
            },
        }
        request = self.authenticated_post(
            '/api/v2/consultations/12/messages/',
            body,
            HTTP_IDEMPOTENCY_KEY='idem-message',
        )

        response = V2MessageCreateView.as_view()(request, session_id=12)

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data['data']['request_id'], 'ai_req_test')

    @patch(
        'consulting.v2_views.recover_terminal_state',
        side_effect=lambda _request_id, **kwargs: kwargs['state'],
    )
    @patch(
        'consulting.v2_views.finalize_expired_request',
        side_effect=lambda state: state,
    )
    @patch('consulting.v2_views.RedisLifecycleStore')
    @patch('consulting.v2_views.get_owned_session')
    def test_polling_turns_overdue_processing_into_terminal_expired(
        self,
        get_session,
        store_class,
        _finalize_expired,
        _recover_terminal,
    ):
        get_session.return_value = self.session
        store = store_class.return_value
        store.get_request.return_value = {
            'request_id': 'ai_req_test',
            'status': 'processing',
            'session_id': 12,
            'user_id': 3,
            'processing_deadline_at': '2020-01-01T00:00:00Z',
        }
        request = self.factory.get(
            '/api/v2/consultations/12/ai-responses/ai_req_test/'
        )
        force_authenticate(request, user=self.user)

        response = V2PollingView.as_view()(
            request,
            session_id=12,
            request_id='ai_req_test',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['data']['status'], 'expired')
        self.assertEqual(response.data['data']['error']['code'], 'AI_REQUEST_EXPIRED')
        store.save_terminal.assert_called_once()
