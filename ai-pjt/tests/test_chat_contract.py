from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from schemas.chat import ChatRequest


class ChatRequestContractTests(IsolatedAsyncioTestCase):
    def test_request_requires_session_and_callback_routing(self):
        request = ChatRequest(
            request_id='ai_req_test',
            session_id=12,
            thread_id='user-3-session-12',
            step_id='basic-info',
            message='은퇴 준비 상태를 알려줘',
            customer_info={'saved_data': {}, 'current_form': {}},
            callback_url='http://backend:8000/api/ai/callback/',
        )

        self.assertEqual(request.session_id, 12)
        self.assertEqual(request.callback_url, 'http://backend:8000/api/ai/callback/')

    async def test_processing_failure_is_reported_by_callback(self):
        from main import _process_and_callback

        graph = AsyncMock()
        graph.ainvoke.side_effect = RuntimeError('sensitive internal failure')
        request = ChatRequest(
            request_id='ai_req_test',
            session_id=12,
            thread_id='user-3-session-12',
            step_id='general',
            message='질문',
            customer_info={'saved_data': {}, 'current_form': {}},
            callback_url='http://backend:8000/api/ai/callback/',
        )

        with patch('main._send_callback', new=AsyncMock()) as send_callback:
            await _process_and_callback(graph, request)

        payload = send_callback.await_args.args[1]
        self.assertEqual(payload['status'], 'failed')
        self.assertEqual(payload['error_code'], 'AI_PROCESSING_FAILED')
        self.assertNotIn('sensitive internal failure', str(payload))
