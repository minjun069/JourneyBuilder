from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import httpx


class F12SSEServiceAuthenticationContractTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from main import app

        self.app = app

    async def _post(self, path, body, *, authorization=None):
        headers = {"Content-Type": "application/json"}
        if authorization is not None:
            headers["Authorization"] = authorization
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://ai",
        ) as client:
            return await client.post(path, content=body, headers=headers)

    async def test_stream_authentication_precedes_schema_validation(self):
        response = await self._post("/api/ai/v2/chat/stream", b"{}")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "SERVICE_UNAUTHORIZED")

    async def test_non_streaming_and_legacy_service_paths_are_absent(self):
        graph = AsyncMock()
        self.app.state.chat_graph = graph

        with patch("main.settings.ai_service_bearer_token", "f12-service-token"):
            for path in ("/v2/chat", "/chat", "/chat/stream"):
                with self.subTest(path=path):
                    response = await self._post(
                        path,
                        b"{}",
                        authorization="Bearer f12-service-token",
                    )
                    self.assertEqual(response.status_code, 404)

        graph.ainvoke.assert_not_awaited()
