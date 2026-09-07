import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from .sse import SSEProtocolError, encode_sse, iter_sse_events
from .ai_services import stream_ai_response
from .views import MessageStreamView, _proxy_ai_stream


def parse_frame(frame):
    lines = frame.rstrip('\n').splitlines()
    return lines[0].removeprefix('event: '), json.loads(lines[1].removeprefix('data: '))


def ai_payload():
    return {
        'request_id': 'ai_req_stream_test',
        'session_id': 12,
        'thread_id': 'user-3-session-12',
        'step_id': 'general',
        'message': '은퇴 준비를 알려줘',
        'customer_info': {'saved_data': {}, 'current_form': {}},
        'field_registry': [],
        'callback_url': 'http://backend:8000/api/ai/callback/',
    }


class SSEParserTests(SimpleTestCase):
    def test_parser_ignores_comments_and_decodes_json_events(self):
        events = list(iter_sse_events([
            ': heartbeat',
            '',
            'event: delta',
            'data: {"content":"은퇴"}',
            '',
        ]))

        self.assertEqual(events, [('delta', {'content': '은퇴'})])

    def test_parser_rejects_non_object_data(self):
        with self.assertRaises(SSEProtocolError):
            list(iter_sse_events(['event: delta', 'data: ["invalid"]', '']))


class AIStreamClientTests(SimpleTestCase):
    @override_settings(
        AI_SERVICE_STREAM_URL='http://ai-pjt:8001/chat/stream',
        AI_SERVICE_TIMEOUT_SECONDS=5,
        AI_STREAM_READ_TIMEOUT_SECONDS=120,
        AI_CALLBACK_SECRET='internal-secret',
    )
    @patch('httpx.stream')
    def test_stream_client_uses_stream_url_and_excludes_callback(self, httpx_stream):
        response = MagicMock()
        response.iter_lines.return_value = iter(['event: done', 'data: {}', ''])
        httpx_stream.return_value.__enter__.return_value = response

        lines = list(stream_ai_response(ai_payload()))

        self.assertEqual(lines, ['event: done', 'data: {}', ''])
        response.raise_for_status.assert_called_once_with()
        kwargs = httpx_stream.call_args.kwargs
        self.assertEqual(httpx_stream.call_args.args[:2], ('POST', 'http://ai-pjt:8001/chat/stream'))
        self.assertNotIn('callback_url', kwargs['json'])
        self.assertEqual(kwargs['headers']['Accept'], 'text/event-stream')
        self.assertEqual(kwargs['headers']['X-Internal-Secret'], 'internal-secret')
        self.assertEqual(kwargs['timeout'].connect, 5)
        self.assertEqual(kwargs['timeout'].read, 120)


class SSEProxyTests(SimpleTestCase):
    @patch('consulting.views.create_assistant_message_from_callback')
    @patch('consulting.views.stream_ai_response')
    def test_proxy_streams_delta_and_saves_one_completed_message(
        self,
        stream_ai_response,
        create_message,
    ):
        stream_ai_response.return_value = iter([
            'event: start',
            'data: {"request_id":"ai_req_stream_test","session_id":12}',
            '',
            'event: delta',
            'data: {"request_id":"ai_req_stream_test","content":"안녕"}',
            '',
            'event: done',
            'data: {"request_id":"ai_req_stream_test","session_id":12,"thread_id":"user-3-session-12","output":{"response":"안녕","action":null}}',
            '',
        ])
        create_message.return_value = SimpleNamespace(
            id=99,
            extracted_data={
                'action': {
                    'type': 'form_change',
                    'target': 'draft_form',
                    'step_id': 'general',
                    'changes': [],
                },
            },
        )

        frames = [
            parse_frame(frame)
            for frame in _proxy_ai_stream(
                SimpleNamespace(id=12),
                ai_payload(),
                'general',
                41,
            )
        ]

        self.assertEqual([event for event, _ in frames], ['start', 'delta', 'done'])
        self.assertEqual(frames[0][1]['user_message_id'], 41)
        self.assertEqual(frames[1][1]['content'], '안녕')
        self.assertEqual(frames[2][1]['message_id'], 99)
        self.assertEqual(frames[2][1]['output']['action']['step_id'], 'general')
        create_message.assert_called_once()
        self.assertEqual(create_message.call_args.kwargs['step_id'], 'general')

    @patch('consulting.views.create_assistant_message_from_callback')
    @patch('consulting.views.stream_ai_response')
    def test_proxy_rejects_mismatched_request_identity(
        self,
        stream_ai_response,
        create_message,
    ):
        stream_ai_response.return_value = iter([
            'event: delta',
            'data: {"request_id":"another_request","content":"잘못된 응답"}',
            '',
        ])

        frames = [
            parse_frame(frame)
            for frame in _proxy_ai_stream(
                SimpleNamespace(id=12),
                ai_payload(),
                'general',
                41,
            )
        ]

        self.assertEqual([event for event, _ in frames], ['start', 'error'])
        self.assertNotIn('another_request', str(frames))
        create_message.assert_not_called()

    @patch('consulting.views.create_assistant_message_from_callback')
    @patch('consulting.views.stream_ai_response')
    def test_proxy_does_not_save_partial_message_when_upstream_fails(
        self,
        stream_ai_response,
        create_message,
    ):
        stream_ai_response.return_value = iter([
            'event: delta',
            'data: {"request_id":"ai_req_stream_test","content":"일부"}',
            '',
            'event: error',
            'data: {"request_id":"ai_req_stream_test","code":"AI_STREAM_FAILED","message":"internal detail"}',
            '',
        ])

        frames = [
            parse_frame(frame)
            for frame in _proxy_ai_stream(
                SimpleNamespace(id=12),
                ai_payload(),
                'general',
                41,
            )
        ]

        self.assertEqual([event for event, _ in frames], ['start', 'delta', 'error'])
        self.assertNotIn('internal detail', str(frames))
        create_message.assert_not_called()


class MessageStreamViewTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()

    @patch('consulting.views._proxy_ai_stream')
    @patch('consulting.views.build_ai_request_payload')
    @patch('consulting.views.generate_request_id', return_value='ai_req_stream_test')
    @patch('consulting.views.Message.objects.create')
    @patch('consulting.views.get_session_or_404')
    def test_post_validates_session_saves_user_message_and_returns_sse(
        self,
        get_session,
        create_user_message,
        _generate_request_id,
        build_payload,
        proxy_stream,
    ):
        session = SimpleNamespace(id=12, status='in_progress')
        get_session.return_value = session
        create_user_message.return_value = SimpleNamespace(id=41, content='은퇴 준비를 알려줘')
        build_payload.return_value = ai_payload()
        proxy_stream.return_value = iter([encode_sse('start', {'request_id': 'ai_req_stream_test'})])
        user = SimpleNamespace(id=3, is_authenticated=True)
        request = self.factory.post(
            '/messages/stream/',
            {'content': '은퇴 준비를 알려줘', 'step_id': 'general', 'form_data': {}},
            format='json',
        )
        force_authenticate(request, user=user)

        response = MessageStreamView.as_view()(request, session_id=12)
        body = b''.join(response.streaming_content).decode('utf-8')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response['Content-Type'].startswith('text/event-stream'))
        self.assertEqual(response['Cache-Control'], 'no-cache')
        self.assertEqual(response['X-Accel-Buffering'], 'no')
        self.assertIn('event: start', body)
        create_user_message.assert_called_once()
        build_payload.assert_called_once()
        proxy_stream.assert_called_once_with(session, build_payload.return_value, 'general', 41)
