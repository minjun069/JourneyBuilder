from unittest import IsolatedAsyncioTestCase

import httpx

from main import app
class ChatRequestContractTests(IsolatedAsyncioTestCase):
    async def test_legacy_request_route_is_absent(self):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://ai",
        ) as client:
            response = await client.post(
                "/chat",
                content=b"not-json-and-must-not-be-parsed",
            )

        self.assertEqual(response.status_code, 404)
