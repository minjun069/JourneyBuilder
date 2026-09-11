from unittest import IsolatedAsyncioTestCase

import httpx

from main import app
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

    async def test_legacy_request_is_rejected_without_callback_processing(self):
        request = ChatRequest(
            request_id='ai_req_test',
            session_id=12,
            thread_id='user-3-session-12',
            step_id='general',
            message='질문',
            customer_info={'saved_data': {}, 'current_form': {}},
            callback_url='http://backend:8000/api/ai/callback/',
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://ai",
        ) as client:
            response = await client.post(
                "/chat",
                json=request.model_dump(mode="json"),
            )

        self.assertEqual(response.status_code, 410)
        self.assertEqual(
            response.json()["error"]["code"],
            "CONTRACT_VERSION_UNSUPPORTED",
        )
