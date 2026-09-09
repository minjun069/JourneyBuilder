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
        'processing_deadline_at': '2026-09-08T00:01:30Z',
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
        AI_SERVICE_URL='http://ai-pjt:8001/chat',
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
            ('POST', 'http://ai-pjt:8001/v2/chat/stream'),
        )
        headers = client.stream.call_args.kwargs['headers']
        self.assertEqual(headers['Authorization'], 'Bearer service-token')
        self.assertEqual(headers['Accept'], 'text/event-stream')


class V2SSEProxyTests(SimpleTestCase):
    @patch('consulting.v2_sse.create_v2_assistant_message')
    @patch('consulting.v2_sse.stream_v2_ai_response')
    def test_done_saves_one_v2_message_and_terminal_state(self, upstream, create_message):
        upstream.return_value = async_items([
            'event: start',
            'data: {"contract_version":"2.0","request_id":"ai_req_v2_stream","session_id":12,"thread_id":"user-3-session-12"}',
            '',
            'event: delta',
            'data: {"contract_version":"2.0","request_id":"ai_req_v2_stream","content":"안녕"}',
            '',
            'event: done',
            'data: {"contract_version":"2.0","request_id":"ai_req_v2_stream","session_id":12,"thread_id":"user-3-session-12","status":"completed","completed_at":"2026-09-08T00:00:01Z","output":{"response":"안녕","step_id":"general","action":null}}',
            '',
        ])
        create_message.return_value = SimpleNamespace(
            id=99,
            role='assistant',
            content='안녕',
            extracted_data={'action': None},
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

        self.assertEqual([event for event, _ in frames], ['start', 'delta', 'done'])
        self.assertEqual(frames[-1][1]['message']['id'], 99)
        create_message.assert_called_once()
        store.save_terminal.assert_called_once()
        saved = store.save_terminal.call_args.args[0]
        self.assertEqual(saved['status'], 'completed')
        self.assertEqual(saved['assistant_message_id'], 99)

    @patch('consulting.v2_sse.create_v2_assistant_message')
    @patch('consulting.v2_sse.stream_v2_ai_response')
    def test_protocol_error_does_not_save_partial_message(self, upstream, create_message):
        upstream.return_value = async_items([
            'event: delta',
            'data: {"contract_version":"2.0","request_id":"another","content":"잘못된 응답"}',
            '',
        ])
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
        create_message.assert_not_called()
        self.assertEqual(store.save_terminal.call_args.args[0]['status'], 'failed')


class V2MessageStreamViewTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()

    def request(self):
        request = self.factory.post(
            '/api/v2/consultations/12/messages/stream/',
            browser_body(),
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

    @patch('consulting.v2_views.Message.objects.create')
    @patch('consulting.v2_views.RedisLifecycleStore')
    @patch('consulting.v2_views.get_owned_session')
    def test_processing_idempotency_replay_does_not_create_message(
        self,
        get_session,
        store_class,
        create_user_message,
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


def patch_digest():
    from .ai_lifecycle import canonical_digest, validate_browser_message

    return canonical_digest(validate_browser_message(browser_body()))
