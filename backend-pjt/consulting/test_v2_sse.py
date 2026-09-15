import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import SimpleTestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from .v2_sse import proxy_v2_ai_stream, stream_v2_ai_response
from .v2_views import V2MessageStreamView


async def async_items(items):
    for item in items:
        yield item


async def collect_async(iterable):
    return [item async for item in iterable]


def parse_frame(frame):
    lines = frame.rstrip('\n').splitlines()
    return lines[0].removeprefix('event: '), json.loads(lines[1].removeprefix('data: '))


def ai_payload():
    return {
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
    }


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


class V2SSEProxyTests(SimpleTestCase):
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
            )
        ))]

        self.assertEqual([event for event, _ in frames], ['start', 'done'])
        self.assertEqual(frames[-1][1]['message']['id'], 99)
        finalize.assert_called_once()
        store.save_terminal.assert_called_once()
        saved = store.save_terminal.call_args.args[0]
        self.assertEqual(saved['status'], 'completed')
        self.assertEqual(saved['assistant_message_id'], 99)

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
            )
        ))]

        self.assertEqual([event for event, _ in frames], ['start', 'error'])
        finalize.assert_called_once()
        self.assertEqual(store.save_terminal.call_args.args[0]['status'], 'failed')


class V2MessageStreamViewTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()

    def request(self, body=None):
        request = self.factory.post(
            '/api/consulting/v2/consultations/12/messages/stream/',
            body or browser_body(),
            format='json',
            HTTP_ACCEPT='text/event-stream',
            HTTP_IDEMPOTENCY_KEY='idem-test',
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
    def test_new_request_saves_state_and_returns_async_sse(
        self,
        get_session,
        store_class,
        create_user_message,
        _new_request_id,
        _build_payload,
        _make_state,
        proxy_stream,
    ):
        get_session.return_value = SimpleNamespace(id=12, status='in_progress')
        create_user_message.return_value = SimpleNamespace(id=41, content='은퇴 준비를 알려줘')
        store = store_class.return_value
        store.get_idempotency.return_value = None
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
        store.save_idempotency.assert_called_once()

    @patch('consulting.v2_views.proxy_v2_ai_stream')
    @patch('consulting.v2_views.make_processing_state', return_value=processing_state())
    @patch('consulting.v2_views.build_v2_ai_payload', return_value=ai_payload())
    @patch('consulting.v2_views.new_request_id', return_value='ai_req_v2_stream')
    @patch('consulting.v2_views.Message.objects.create')
    @patch('consulting.v2_views.RedisLifecycleStore')
    @patch('consulting.v2_views.get_owned_session')
    def test_error_review_uses_the_same_sse_endpoint_and_forwards_trigger(
        self,
        get_session,
        store_class,
        create_user_message,
        _new_request_id,
        build_payload,
        _make_state,
        proxy_stream,
    ):
        get_session.return_value = SimpleNamespace(id=12, status='in_progress')
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
