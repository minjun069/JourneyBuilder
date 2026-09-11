from __future__ import annotations

import json
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import httpx

from tests.security.service_tool_security_contract import (
    cases_for_owner,
    load_lifecycle_fixture,
    load_suite,
)
from transport.contracts import ChatAccepted


class F12ServiceAuthenticationContractTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from main import app

        self.app = app
        self.transport = AsyncMock()
        self.app.state.v2_transport = self.transport
        suite = load_suite()
        self.cases = {
            case["id"]: case for case in cases_for_owner(suite, "F12-T02")
        }
        self.lifecycle = load_lifecycle_fixture(suite)

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

    async def test_auth_001_blocks_before_schema_and_graph_scheduling(self):
        case = self.cases["F12-AUTH-001"]

        response = await self._post("/v2/chat", b"{}")

        self.assertEqual(response.status_code, case["expected"]["http_status"])
        self.assertEqual(
            response.json()["error"]["code"],
            case["expected"]["reason_code"],
        )
        self.transport.accept.assert_not_awaited()

    async def test_auth_003_accepts_the_frozen_lifecycle_request(self):
        case = self.cases["F12-AUTH-003"]
        fixture = self.lifecycle["cases"][case["source_fixture_case"]]
        request_id = fixture["request"]["body"]["request_id"]
        self.transport.accept.return_value = ChatAccepted(request_id=request_id)

        with patch("main.settings.ai_service_bearer_token", "f12-service-token"):
            response = await self._post(
                fixture["request"]["path"],
                json.dumps(fixture["request"]["body"]).encode(),
                authorization="Bearer f12-service-token",
            )

        self.assertEqual(response.status_code, case["expected"]["http_status"])
        self.assertEqual(response.json()["status"], "accepted")
        self.transport.accept.assert_awaited_once()

    async def test_retired_v1_service_paths_never_reach_a_graph(self):
        graph = AsyncMock()
        self.app.state.chat_graph = graph

        for path in ("/chat", "/chat/stream"):
            with self.subTest(path=path):
                response = await self._post(
                    path,
                    b'{"malformed":',
                    authorization="Bearer f12-service-token",
                )
                self.assertEqual(response.status_code, 410)
                self.assertEqual(
                    response.json()["error"]["code"],
                    "CONTRACT_VERSION_UNSUPPORTED",
                )

        graph.ainvoke.assert_not_awaited()
        self.transport.accept.assert_not_awaited()
