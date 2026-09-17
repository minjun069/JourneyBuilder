import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import SimpleTestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from .sse import SSEProtocolError
from .v2_sse import (
    _validate_transient_event,
    proxy_v2_ai_stream,
    stream_v2_ai_response,
)
from .v2_views import V2MessageStreamView
from .ai_tokenization import ReferenceTokenVault


async def async_items(items):
    for item in items:
        yield item


async def collect_async(iterable):
    return [item async for item in iterable]


def parse_frame(frame):
    lines = frame.rstrip('\n').splitlines()
    return lines[0].removeprefix('event: '), json.loads(lines[1].removeprefix('data: '))


def ai_payload():
    payload = {
        'contract_version': '2.0',
        'request_id': 'ai_req_v2_stream',
        'session_id': 12,
        'thread_id': 'user-3-session-12',
        'step_id': 'general',
        'message': '은퇴 준비를 알려줘',
        'deadline_at': '2026-09-08T00:01:00Z',
        'customer_info': {'saved_data': {}, 'current_form': {}},
        'field_registry': [],
        'chart_payload': None,
    }
    from .ai_lifecycle import canonical_digest

    return {**payload, 'payload_digest': canonical_digest(payload)}


def processing_state():
    return {
        'contract_version': '2.0',
        'request_id': 'ai_req_v2_stream',
        'status': 'processing',
        'session_id': 12,
        'user_id': 3,
        'user_message_id': 41,
        'thread_id': 'user-3-session-12',
        'step_id': 'general',
        'payload_digest': ai_payload()['payload_digest'],
    }


def token_vault():
    return ReferenceTokenVault(
        user_id=3,
        session_id=12,
        request_id='ai_req_v2_stream',
        namespace='a1b2c3d4e5f6',
    )


def browser_body():
    return {
        'contract_version': '2.0',
        'content': '은퇴 준비를 알려줘',
        'step_id': 'general',
        'form_data': {},
    }


class V2AIStreamClientTests(SimpleTestCase):
    @override_settings(
        AI_SERVICE_URL='http://ai-pjt:8001',
        AI_SERVICE_TIMEOUT_SECONDS=5,
        AI_STREAM_READ_TIMEOUT_SECONDS=60,
        AI_SERVICE_BEARER_TOKEN='service-token',
    )
    @patch('httpx.AsyncClient')
    def test_client_uses_v2_stream_url_and_service_bearer(self, async_client_class):
        client = MagicMock()
        response = MagicMock()
        response.aiter_lines.return_value = async_items(['event: done', 'data: {}', ''])
        stream_context = MagicMock()
        stream_context.__aenter__ = AsyncMock(return_value=response)
        stream_context.__aexit__ = AsyncMock(return_value=None)
        client.stream.return_value = stream_context
        async_client_class.return_value.__aenter__ = AsyncMock(return_value=client)
        async_client_class.return_value.__aexit__ = AsyncMock(return_value=None)

        asyncio.run(collect_async(stream_v2_ai_response(ai_payload())))

        self.assertEqual(
            client.stream.call_args.args[:2],
            ('POST', 'http://ai-pjt:8001/api/ai/v2/chat/stream'),
        )
        headers = client.stream.call_args.kwargs['headers']
        self.assertEqual(headers['Authorization'], 'Bearer service-token')
        self.assertEqual(headers['Accept'], 'text/event-stream')
        self.assertNotIn('X-AI-Progress-Events', headers)

        response.aiter_lines.return_value = async_items(['event: done', 'data: {}', ''])
        asyncio.run(collect_async(stream_v2_ai_response(
            ai_payload(),
            progress_events=True,
        )))
        self.assertEqual(
            client.stream.call_args.kwargs['headers']['X-AI-Progress-Events'],
            'v1',
        )


class V2SSEProxyTests(SimpleTestCase):
    def test_transient_event_validator_rejects_sequence_phase_and_content(self):
        valid_identity = {
            'contract_version': '2.0',
            'request_id': 'ai_req_v2_stream',
            'session_id': 12,
            'thread_id': 'user-3-session-12',
        }
        invalid_events = (
            ('heartbeat', {**valid_identity, 'event_seq': 0}),
            ('heartbeat', {**valid_identity, 'event_seq': 1, 'content': '금지'}),
            ('progress', {
                **valid_identity,
                'event_seq': 1,
                'phase': 'internal_agent',
                'status': 'active',
            }),
            ('progress', {
                **valid_identity,
                'event_seq': 1,
                'phase': 'planning',
                'status': 'active',
                'percent': 10,
            }),
        )
        for event_name, data in invalid_events:
            with self.subTest(event_name=event_name, data=data):
                with self.assertRaises(SSEProtocolError):
                    _validate_transient_event(event_name, data, 0)

    @patch('consulting.v2_sse.finalize_terminal_result')
    @patch('consulting.v2_sse.stream_v2_ai_response')
    def test_opt_in_progress_and_heartbeat_are_safely_projected(
        self,
        upstream,
        finalize,
    ):
        upstream.return_value = async_items([
            'event: start',
            'data: {"contract_version":"2.0","request_id":"ai_req_v2_stream","session_id":12,"thread_id":"user-3-session-12"}',
            '',
            'event: progress',
            'data: {"contract_version":"2.0","request_id":"ai_req_v2_stream","session_id":12,"thread_id":"user-3-session-12","event_seq":1,"phase":"planning","status":"active"}',
            '',
            'event: heartbeat',
            'data: {"contract_version":"2.0","request_id":"ai_req_v2_stream","session_id":12,"thread_id":"user-3-session-12","event_seq":2}',
            '',
            'event: done',
            'data: {"contract_version":"2.0","request_id":"ai_req_v2_stream","session_id":12,"thread_id":"user-3-session-12","status":"completed","completed_at":"2026-09-08T00:00:01Z","output":{"response":"안녕","step_id":"general","action":null}}',
            '',
        ])
        message = SimpleNamespace(
            id=99,
            role='assistant',
            content='안녕',
            extracted_data={'action': None},
        )
        finalize.return_value = SimpleNamespace(
            state={
                **processing_state(),
                'status': 'completed',
                'assistant_message_id': 99,
            },
            message=message,
            replay=False,
        )

        frames = [parse_frame(frame) for frame in asyncio.run(collect_async(
            proxy_v2_ai_stream(
                SimpleNamespace(id=12),
                ai_payload(),
                processing_state(),
                MagicMock(),
                token_vault=token_vault(),
                progress_events=True,
            )
        ))]

        self.assertEqual(
            [event for event, _ in frames],
            ['start', 'progress', 'heartbeat', 'done'],
        )
        self.assertEqual(frames[1][1], {
            'contract_version': '2.0',
            'request_id': 'ai_req_v2_stream',
            'event_seq': 1,
            'phase': 'planning',
            'status': 'active',
        })
        self.assertNotIn('percent', json.dumps(frames))
        self.assertTrue(upstream.call_args.kwargs['progress_events'])

    @patch('consulting.v2_sse.finalize_terminal_result')
    @patch('consulting.v2_sse.stream_v2_ai_response')
    def test_transient_events_require_negotiation(self, upstream, finalize):
        upstream.return_value = async_items([
            'event: progress',
            'data: {"contract_version":"2.0","request_id":"ai_req_v2_stream","event_seq":1,"phase":"planning","status":"active"}',
            '',
        ])
        finalize.return_value = SimpleNamespace(
            state={**processing_state(), 'status': 'failed'},
            message=None,
            replay=False,
        )
        store = MagicMock()

        frames = [parse_frame(frame) for frame in asyncio.run(collect_async(
            proxy_v2_ai_stream(
                SimpleNamespace(id=12),
                ai_payload(),
                processing_state(),
                store,
                token_vault=token_vault(),
            )
        ))]

        self.assertEqual([event for event, _ in frames], ['start', 'error'])
        store.save_terminal.assert_called_once()

    @patch('consulting.v2_sse.finalize_terminal_result')
    @patch('consulting.v2_sse.stream_v2_ai_response')
    def test_disconnect_preserves_request_for_exact_retry(self, upstream, finalize):
        async def cancelled_stream():
            raise asyncio.CancelledError
            yield ''

        upstream.return_value = cancelled_stream()
        store = MagicMock()

        async def consume():
            async for _frame in proxy_v2_ai_stream(
                SimpleNamespace(id=12),
                ai_payload(),
                processing_state(),
                store,
                token_vault=token_vault(),
            ):
                pass

        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(consume())

        finalize.assert_not_called()
        store.save_terminal.assert_not_called()
        store.delete_token_vault.assert_not_called()
        self.assertTrue(
            store.save_active.call_args.args[0]['retry_after_disconnect']
        )

    @patch('consulting.v2_sse.finalize_terminal_result')
    @patch('consulting.v2_sse.stream_v2_ai_response')
    def test_done_saves_one_v2_message_and_terminal_state(self, upstream, finalize):
        upstream.return_value = async_items([
            'event: start',
            'data: {"contract_version":"2.0","request_id":"ai_req_v2_stream","session_id":12,"thread_id":"user-3-session-12"}',
            '',
            'event: done',
            'data: {"contract_version":"2.0","request_id":"ai_req_v2_stream","session_id":12,"thread_id":"user-3-session-12","status":"completed","completed_at":"2026-09-08T00:00:01Z","output":{"response":"안녕","step_id":"general","action":null}}',
            '',
        ])
        message = SimpleNamespace(
            id=99,
            role='assistant',
            content='안녕',
            extracted_data={'action': None},
        )
        terminal_state = {
            **processing_state(),
            'status': 'completed',
            'assistant_message_id': 99,
        }
        finalize.return_value = SimpleNamespace(
            state=terminal_state,
            message=message,
            replay=False,
        )
        store = MagicMock()

        frames = [parse_frame(frame) for frame in asyncio.run(collect_async(
            proxy_v2_ai_stream(
                SimpleNamespace(id=12),
                ai_payload(),
                processing_state(),
                store,
                token_vault=token_vault(),
            )
        ))]

        self.assertEqual([event for event, _ in frames], ['start', 'done'])
        self.assertEqual(frames[-1][1]['message']['id'], 99)
        finalize.assert_called_once()
        store.save_terminal.assert_called_once()
        saved = store.save_terminal.call_args.args[0]
        self.assertEqual(saved['status'], 'completed')
        self.assertEqual(saved['assistant_message_id'], 99)
        store.delete_token_vault.assert_called_once_with('ai_req_v2_stream')

    @patch('consulting.v2_sse.finalize_terminal_result')
    @patch('consulting.v2_sse.stream_v2_ai_response')
    def test_done_without_response_is_rejected(self, upstream, finalize):
        upstream.return_value = async_items([
            'event: start',
            'data: {"contract_version":"2.0","request_id":"ai_req_v2_stream","session_id":12,"thread_id":"user-3-session-12"}',
            '',
            'event: done',
            'data: {"contract_version":"2.0","request_id":"ai_req_v2_stream","session_id":12,"thread_id":"user-3-session-12","status":"completed","completed_at":"2026-09-08T00:00:01Z","output":{"response":"","step_id":"general","action":null}}',
            '',
        ])
        finalize.return_value = SimpleNamespace(
            state={**processing_state(), 'status': 'failed'},
            message=None,
            replay=False,
        )
        store = MagicMock()

        frames = [parse_frame(frame) for frame in asyncio.run(collect_async(
            proxy_v2_ai_stream(
                SimpleNamespace(id=12),
                ai_payload(),
                processing_state(),
                store,
                token_vault=token_vault(),
            )
        ))]

        self.assertEqual([event for event, _ in frames], ['start', 'error'])
        self.assertEqual(store.save_terminal.call_args.args[0]['status'], 'failed')

    @patch('consulting.v2_sse.finalize_terminal_result')
    @patch('consulting.v2_sse.stream_v2_ai_response')
    def test_delta_event_is_rejected_without_saving_partial_message(self, upstream, finalize):
        upstream.return_value = async_items([
            'event: start',
            'data: {"contract_version":"2.0","request_id":"ai_req_v2_stream","session_id":12,"thread_id":"user-3-session-12"}',
            '',
            'event: delta',
            'data: {"contract_version":"2.0","request_id":"ai_req_v2_stream","content":"잘못된 응답"}',
            '',
        ])
        finalize.return_value = SimpleNamespace(
            state={**processing_state(), 'status': 'failed'},
            message=None,
            replay=False,
        )
        store = MagicMock()

        frames = [parse_frame(frame) for frame in asyncio.run(collect_async(
            proxy_v2_ai_stream(
                SimpleNamespace(id=12),
                ai_payload(),
                processing_state(),
                store,
                token_vault=token_vault(),
            )
        ))]

        self.assertEqual([event for event, _ in frames], ['start', 'error'])
        finalize.assert_called_once()
        self.assertEqual(store.save_terminal.call_args.args[0]['status'], 'failed')
        store.delete_token_vault.assert_called_once_with('ai_req_v2_stream')

    @patch('consulting.v2_sse.finalize_terminal_result')
    @patch('consulting.v2_sse.stream_v2_ai_response')
    def test_restores_only_issued_tokens_before_save_and_browser_delivery(
        self,
        upstream,
        finalize,
    ):
        vault = token_vault()
        tokenized = vault.tokenize({
            'message': '이름은 홍길동이고 희망 월소득은 350만원입니다.',
            'total_debt': 73_400_000,
        })
        response = f"네, {tokenized['message']}"
        debt_token = tokenized['total_debt']
        upstream.return_value = async_items([
            'event: done',
            f'data: {json.dumps({"contract_version": "2.0", "request_id": "ai_req_v2_stream", "session_id": 12, "thread_id": "user-3-session-12", "status": "completed", "completed_at": "2026-09-08T00:00:01Z", "output": {"response": response, "step_id": "basic-info", "action": {"type": "form_change", "target": "draft_form", "user_requested": True, "confidence": 1.0, "changes": [{"section": "liabilities", "path": "balance", "operation": "set", "value": debt_token, "reason": "사용자 요청"}]}}}, ensure_ascii=False)}',
            '',
        ])
        restored_response = '네, 이름은 홍길동이고 희망 월소득은 350만원입니다.'
        message = SimpleNamespace(
            id=99,
            role='assistant',
            content=restored_response,
            extracted_data={'action': None},
        )
        finalize.return_value = SimpleNamespace(
            state={**processing_state(), 'status': 'completed', 'assistant_message_id': 99},
            message=message,
            replay=False,
        )
        store = MagicMock()

        frames = [parse_frame(frame) for frame in asyncio.run(collect_async(
            proxy_v2_ai_stream(
                SimpleNamespace(id=12),
                ai_payload(),
                processing_state(),
                store,
                token_vault=vault,
            )
        ))]

        finalized_payload = finalize.call_args.args[1]
        self.assertEqual(finalized_payload['output']['response'], restored_response)
        self.assertEqual(
            finalized_payload['output']['action']['changes'][0]['value'],
            73_400_000,
        )
        self.assertEqual([event for event, _ in frames], ['start', 'done'])
        self.assertEqual(frames[1][1]['message']['content'], restored_response)
        self.assertNotIn('[[USER_NAME_', json.dumps(frames, ensure_ascii=False))
        self.assertNotIn('[[FINANCIAL_VALUE_', json.dumps(frames, ensure_ascii=False))

    @patch('consulting.v2_sse.finalize_terminal_result')
    @patch('consulting.v2_sse.stream_v2_ai_response')
    def test_unissued_token_fails_closed_without_partial_response(self, upstream, finalize):
        forged = '[[USER_NAME_deadbeefcafe_1]]'
        upstream.return_value = async_items([
            'event: done',
            f'data: {json.dumps({"contract_version": "2.0", "request_id": "ai_req_v2_stream", "session_id": 12, "thread_id": "user-3-session-12", "status": "completed", "completed_at": "2026-09-08T00:00:01Z", "output": {"response": forged}})}',
            '',
        ])
        finalize.return_value = SimpleNamespace(
            state={**processing_state(), 'status': 'failed'},
            message=None,
            replay=False,
        )
        store = MagicMock()

        frames = [parse_frame(frame) for frame in asyncio.run(collect_async(
            proxy_v2_ai_stream(
                SimpleNamespace(id=12),
                ai_payload(),
                processing_state(),
                store,
                token_vault=token_vault(),
            )
        ))]

        self.assertEqual([event for event, _ in frames], ['start', 'error'])
        failure = finalize.call_args.args[1]
        self.assertEqual(failure['error']['code'], 'POLICY_BLOCKED')
        self.assertNotIn(forged, json.dumps(frames, ensure_ascii=False))


class V2MessageStreamViewTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()

    def request(self, body=None, *, progress_events_version=None):
        extra = {}
        if progress_events_version is not None:
            extra['HTTP_X_AI_PROGRESS_EVENTS'] = progress_events_version
        request = self.factory.post(
            '/api/consulting/v2/consultations/12/messages/stream/',
            body or browser_body(),
            format='json',
            HTTP_ACCEPT='text/event-stream',
            HTTP_IDEMPOTENCY_KEY='idem-test',
            **extra,
        )
        force_authenticate(request, user=SimpleNamespace(id=3, is_authenticated=True))
        return request

    @patch('consulting.v2_views.proxy_v2_ai_stream')
    @patch('consulting.v2_views.make_processing_state', return_value=processing_state())
    @patch('consulting.v2_views.build_v2_ai_payload', return_value=ai_payload())
    @patch('consulting.v2_views.new_request_id', return_value='ai_req_v2_stream')
    @patch('consulting.v2_views.Message.objects.create')
    @patch('consulting.v2_views.RedisLifecycleStore')
    @patch('consulting.v2_views.get_owned_session')
    @patch('consulting.v2_views.transaction.atomic')
    @patch('consulting.v2_views.AIRequestExecution.objects')
    @patch('consulting.v2_views.ConsultingSession.objects')
    def test_completed_session_accepts_presentation_entry_and_marks_system_event(
        self,
        session_objects,
        execution_objects,
        _atomic,
        get_session,
        store_class,
        create_user_message,
        _new_request_id,
        build_payload,
        _make_state,
        proxy_stream,
    ):
        session = SimpleNamespace(id=12, status='completed')
        get_session.return_value = session
        session_objects.select_for_update.return_value.filter.return_value.first.return_value = session
        execution_objects.filter.return_value.exists.return_value = False
        create_user_message.return_value = SimpleNamespace(id=41, content='최초 안내')
        store_class.return_value.get_idempotency.return_value = None
        proxy_stream.return_value = async_items([
            'event: start\ndata: {"contract_version":"2.0","request_id":"ai_req_v2_stream"}\n\n'
        ])
        body = {
            **browser_body(),
            'content': '이 보고서의 핵심을 먼저 안내해 주세요.',
            'step_id': 'lifetime-cashflow',
            'request_mode': 'presentation_entry',
        }

        response = V2MessageStreamView.as_view()(
            self.request(body, progress_events_version='v1'),
            session_id=12,
        )
        b''.join(asyncio.run(collect_async(response.streaming_content)))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(build_payload.call_args.kwargs['request_mode'], 'presentation_entry')
        self.assertEqual(
            create_user_message.call_args.kwargs['extracted_data'],
            {'system_event': 'presentation_entry', 'step_id': 'lifetime-cashflow'},
        )
        self.assertTrue(proxy_stream.call_args.kwargs['progress_events'])

    @patch('consulting.v2_views.get_owned_session')
    def test_completed_session_rejects_non_presentation_step(self, get_session):
        get_session.return_value = SimpleNamespace(id=12, status='completed')

        response = V2MessageStreamView.as_view()(self.request(), session_id=12)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['error']['code'], 'SESSION_CLOSED')

    @patch('consulting.v2_views.proxy_v2_ai_stream')
    @patch('consulting.v2_views.make_processing_state', return_value=processing_state())
    @patch('consulting.v2_views.build_v2_ai_payload', return_value=ai_payload())
    @patch('consulting.v2_views.new_request_id', return_value='ai_req_v2_stream')
    @patch('consulting.v2_views.Message.objects.create')
    @patch('consulting.v2_views.RedisLifecycleStore')
    @patch('consulting.v2_views.get_owned_session')
    @patch('consulting.v2_views.transaction.atomic')
    @patch('consulting.v2_views.AIRequestExecution.objects')
    @patch('consulting.v2_views.ConsultingSession.objects')
    def test_new_request_saves_state_and_returns_async_sse(
        self,
        session_objects,
        execution_objects,
        _atomic,
        get_session,
        store_class,
        create_user_message,
        _new_request_id,
        _build_payload,
        _make_state,
        proxy_stream,
    ):
        session = SimpleNamespace(id=12, status='in_progress')
        get_session.return_value = session
        session_objects.select_for_update.return_value.filter.return_value.first.return_value = session
        execution_objects.filter.return_value.exists.return_value = False
        create_user_message.return_value = SimpleNamespace(id=41, content='은퇴 준비를 알려줘')
        store = store_class.return_value
        store.get_idempotency.return_value = None
        _build_payload.return_value = {
            **ai_payload(),
            'message': '이메일은 gildong@example.com이고 월소득은 350만원입니다.',
            'customer_info': {
                'saved_data': {'email': 'gildong@example.com'},
                'current_form': {'monthly_income': 3_500_000},
            },
        }
        proxy_stream.return_value = async_items([
            'event: start\ndata: {"contract_version":"2.0","request_id":"ai_req_v2_stream"}\n\n'
        ])

        response = V2MessageStreamView.as_view()(self.request(), session_id=12)
        body = b''.join(asyncio.run(collect_async(response.streaming_content))).decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn('text/event-stream', response['Content-Type'])
        self.assertEqual(response['X-Accel-Buffering'], 'no')
        self.assertIn('event: start', body)
        create_user_message.assert_called_once()
        store.save_active.assert_called_once_with(processing_state())
        store.save_token_vault.assert_called_once()
        store.save_idempotency.assert_called_once()
        self.assertIs(
            proxy_stream.call_args.kwargs['token_vault'],
            store.save_token_vault.call_args.args[0],
        )
        self.assertFalse(proxy_stream.call_args.kwargs['progress_events'])
        upstream_payload = proxy_stream.call_args.args[1]
        rendered = json.dumps(upstream_payload, ensure_ascii=False)
        self.assertNotIn('gildong@example.com', rendered)
        self.assertNotIn('350만원', rendered)
        self.assertEqual(
            upstream_payload['customer_info']['current_form']['monthly_income'],
            3_500_000,
        )
        unsigned = {
            key: value for key, value in upstream_payload.items()
            if key != 'payload_digest'
        }
        from .ai_lifecycle import canonical_digest

        self.assertEqual(upstream_payload['payload_digest'], canonical_digest(unsigned))
        execution_objects.create.assert_called_once()
        self.assertEqual(
            execution_objects.create.call_args.kwargs['payload_digest'],
            upstream_payload['payload_digest'],
        )

    @patch('consulting.v2_views.get_owned_session')
    def test_progress_capability_rejects_unknown_version(self, get_session):
        get_session.return_value = SimpleNamespace(id=12, status='in_progress')

        response = V2MessageStreamView.as_view()(
            self.request(progress_events_version='v2'),
            session_id=12,
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['error']['code'], 'REQUEST_INVALID')

    @patch('consulting.v2_views.proxy_v2_ai_stream')
    @patch('consulting.v2_views.make_processing_state', return_value=processing_state())
    @patch('consulting.v2_views.build_v2_ai_payload', return_value=ai_payload())
    @patch('consulting.v2_views.new_request_id', return_value='ai_req_v2_stream')
    @patch('consulting.v2_views.Message.objects.create')
    @patch('consulting.v2_views.RedisLifecycleStore')
    @patch('consulting.v2_views.get_owned_session')
    @patch('consulting.v2_views.transaction.atomic')
    @patch('consulting.v2_views.AIRequestExecution.objects')
    @patch('consulting.v2_views.ConsultingSession.objects')
    def test_error_review_uses_the_same_sse_endpoint_and_forwards_trigger(
        self,
        session_objects,
        execution_objects,
        _atomic,
        get_session,
        store_class,
        create_user_message,
        _new_request_id,
        build_payload,
        _make_state,
        proxy_stream,
    ):
        session = SimpleNamespace(id=12, status='in_progress')
        get_session.return_value = session
        session_objects.select_for_update.return_value.filter.return_value.first.return_value = session
        execution_objects.filter.return_value.exists.return_value = False
        create_user_message.return_value = SimpleNamespace(id=41, content='현재 입력값을 검토해 주세요.')
        store_class.return_value.get_idempotency.return_value = None
        proxy_stream.return_value = async_items([
            'event: start\ndata: {"contract_version":"2.0","request_id":"ai_req_v2_stream"}\n\n'
        ])
        body = {
            **browser_body(),
            'content': '현재 입력값을 검토해 주세요.',
            'request_mode': 'error_review',
            'review_trigger': 'save',
        }

        response = V2MessageStreamView.as_view()(self.request(body), session_id=12)
        b''.join(asyncio.run(collect_async(response.streaming_content)))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(build_payload.call_args.kwargs['request_mode'], 'error_review')
        self.assertEqual(build_payload.call_args.kwargs['review_trigger'], 'save')

    @patch(
        'consulting.v2_views.recover_terminal_state',
        side_effect=lambda _request_id, **kwargs: kwargs['state'],
    )
    @patch('consulting.v2_views.Message.objects.create')
    @patch('consulting.v2_views.RedisLifecycleStore')
    @patch('consulting.v2_views.get_owned_session')
    def test_processing_idempotency_replay_does_not_create_message(
        self,
        get_session,
        store_class,
        create_user_message,
        _recover_terminal,
    ):
        get_session.return_value = SimpleNamespace(id=12, status='in_progress')
        store = store_class.return_value
        store.get_idempotency.return_value = {
            'transport': 'sse',
            'payload_digest': patch_digest(),
            'request_id': 'ai_req_v2_stream',
        }
        store.get_request.return_value = processing_state()

        response = V2MessageStreamView.as_view()(self.request(), session_id=12)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['error']['code'], 'AI_STREAM_IN_PROGRESS')
        create_user_message.assert_not_called()

    @patch('consulting.v2_views.proxy_v2_ai_stream')
    @patch(
        'consulting.v2_views.recover_terminal_state',
        side_effect=lambda _request_id, **kwargs: kwargs['state'],
    )
    @patch('consulting.v2_views.AIRequestExecution.objects')
    @patch('consulting.v2_views.RedisLifecycleStore')
    @patch('consulting.v2_views.get_owned_session')
    def test_disconnected_request_reuses_same_request_id_and_payload(
        self,
        get_session,
        store_class,
        execution_objects,
        _recover_terminal,
        proxy_stream,
    ):
        session = SimpleNamespace(id=12, status='in_progress')
        state = {**processing_state(), 'retry_after_disconnect': True}
        stored_payload = ai_payload()
        vault = token_vault()
        get_session.return_value = session
        store = store_class.return_value
        store.get_idempotency.return_value = {
            'transport': 'sse',
            'payload_digest': patch_digest(),
            'request_id': 'ai_req_v2_stream',
        }
        store.get_request.return_value = state
        store.get_token_vault.return_value = vault
        execution_objects.filter.return_value.first.return_value = SimpleNamespace(
            request_payload=stored_payload,
        )
        proxy_stream.return_value = async_items([
            'event: start\ndata: {"request_id":"ai_req_v2_stream"}\n\n'
        ])

        response = V2MessageStreamView.as_view()(self.request(), session_id=12)
        b''.join(asyncio.run(collect_async(response.streaming_content)))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(proxy_stream.call_args.args[1], stored_payload)
        self.assertEqual(proxy_stream.call_args.args[2]['request_id'], 'ai_req_v2_stream')
        self.assertNotIn('retry_after_disconnect', proxy_stream.call_args.args[2])
        self.assertIs(proxy_stream.call_args.kwargs['token_vault'], vault)

    @patch('consulting.v2_views.RedisLifecycleStore')
    @patch('consulting.v2_views.get_owned_session')
    @patch('consulting.v2_views.transaction.atomic')
    @patch('consulting.v2_views.AIRequestExecution.objects')
    @patch('consulting.v2_views.ConsultingSession.objects')
    def test_distinct_request_is_rejected_while_session_stream_is_processing(
        self,
        session_objects,
        execution_objects,
        _atomic,
        get_session,
        store_class,
    ):
        session = SimpleNamespace(id=12, status='in_progress')
        get_session.return_value = session
        store_class.return_value.get_idempotency.return_value = None
        session_objects.select_for_update.return_value.filter.return_value.first.return_value = session
        execution_objects.filter.return_value.exists.return_value = True

        response = V2MessageStreamView.as_view()(self.request(), session_id=12)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['error']['code'], 'AI_STREAM_IN_PROGRESS')

    @patch(
        'consulting.v2_views.recover_terminal_state',
        return_value={
            **processing_state(),
            'status': 'completed',
            'assistant_message_id': 99,
            'completed_at': '2026-09-08T00:00:01Z',
            'callback_digest': 'terminal-digest',
        },
    )
    @patch('consulting.v2_views.Message.objects.filter')
    @patch('consulting.v2_views.Message.objects.create')
    @patch('consulting.v2_views.RedisLifecycleStore')
    @patch('consulting.v2_views.get_owned_session')
    def test_completed_idempotency_replays_receipt_after_redis_loss(
        self,
        get_session,
        store_class,
        create_user_message,
        message_filter,
        recover_terminal,
    ):
        session = SimpleNamespace(id=12, status='in_progress')
        get_session.return_value = session
        message_filter.return_value.first.return_value = SimpleNamespace(
            id=99,
            role='assistant',
            content='복구된 완료 응답',
            extracted_data={'action': None},
        )
        store = store_class.return_value
        store.get_idempotency.return_value = {
            'transport': 'sse',
            'payload_digest': patch_digest(),
            'request_id': 'ai_req_v2_stream',
        }
        store.get_request.return_value = None

        response = V2MessageStreamView.as_view()(self.request(), session_id=12)
        frames = [parse_frame(frame.decode()) for frame in asyncio.run(collect_async(
            response.streaming_content
        ))]

        self.assertEqual(response.status_code, 200)
        self.assertEqual([event for event, _ in frames], ['start', 'done'])
        self.assertEqual(frames[-1][1]['message']['id'], 99)
        create_user_message.assert_not_called()
        recover_terminal.assert_called_once()
        store.save_terminal.assert_called_once()


def patch_digest():
    from .ai_lifecycle import canonical_digest, validate_browser_message

    return canonical_digest(validate_browser_message(browser_body()))
