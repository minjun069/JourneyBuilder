import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

import httpx

from main import app, _stream_v2_chat_events
from transport.contracts import ChatV2Request


def parse_frame(frame: str) -> tuple[str, dict]:
    lines = frame.rstrip("\n").splitlines()
    return (
        lines[0].removeprefix("event: "),
        json.loads(lines[1].removeprefix("data: ")),
    )


def v2_request() -> ChatV2Request:
    return ChatV2Request(
        contract_version="2.0",
        request_id="ai_req_v2_stream",
        session_id=12,
        thread_id="user-3-session-12",
        step_id="general",
        message="은퇴 준비를 알려줘",
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=60),
        customer_info={"saved_data": {}, "current_form": {}},
        field_registry=[],
        chart_payload=None,
    )


class FakeContextAPI:
    async def read_context(self, scope):
        return {}

    def state_channels(self, scope, context):
        return {"context_scope": {"session_id": scope.session_id}}

    def config_for(self, scope):
        return {"configurable": {"thread_id": scope.thread_id}}


class FakeV2Graph:
    async def astream_events(self, input_data, *, config, version):
        assert input_data["request_id"] == "ai_req_v2_stream"
        assert "execution_budget" in input_data
        assert config["configurable"]["thread_id"] == "user-3-session-12"
        assert version == "v2"
        yield {
            "event": "on_chat_model_stream",
            "metadata": {"langgraph_node": "router"},
            "data": {"chunk": SimpleNamespace(content="내부 출력")},
        }
        yield {
            "event": "on_chat_model_stream",
            "metadata": {"langgraph_node": "merge"},
            "data": {"chunk": SimpleNamespace(content="안녕하세요")},
        }
        yield {
            "event": "on_chain_end",
            "parent_ids": [],
            "data": {
                "output": {
                    "output": {
                        "response": "안녕하세요",
                        "step_id": "general",
                        "action": None,
                    }
                }
            },
        }


class FailingV2Graph:
    async def astream_events(self, input_data, *, config, version):
        yield {
            "event": "on_chat_model_stream",
            "metadata": {"langgraph_node": "merge"},
            "data": {"chunk": SimpleNamespace(content="일부")},
        }
        raise RuntimeError("외부에 노출하면 안 되는 오류")


class V2SSEStreamTests(IsolatedAsyncioTestCase):
    @patch("main.get_callbacks", return_value=[])
    async def test_stream_uses_v2_identity_and_callback_compatible_done(self, _callbacks):
        frames = [
            parse_frame(frame)
            async for frame in _stream_v2_chat_events(
                FakeV2Graph(),
                FakeContextAPI(),
                v2_request(),
            )
        ]

        self.assertEqual([name for name, _ in frames], ["start", "delta", "done"])
        for _, data in frames:
            self.assertEqual(data["contract_version"], "2.0")
            self.assertEqual(data["request_id"], "ai_req_v2_stream")
        self.assertNotIn("내부 출력", str(frames))
        self.assertEqual(frames[-1][1]["status"], "completed")
        self.assertEqual(frames[-1][1]["output"]["response"], "안녕하세요")
        self.assertIn("completed_at", frames[-1][1])

    @patch("main.get_callbacks", return_value=[])
    async def test_failure_returns_safe_v2_terminal_error(self, _callbacks):
        frames = [
            parse_frame(frame)
            async for frame in _stream_v2_chat_events(
                FailingV2Graph(),
                FakeContextAPI(),
                v2_request(),
            )
        ]

        self.assertEqual([name for name, _ in frames], ["start", "delta", "error"])
        terminal = frames[-1][1]
        self.assertEqual(terminal["status"], "failed")
        self.assertEqual(terminal["error"]["code"], "INTERNAL_ERROR")
        self.assertNotIn("외부에 노출하면 안 되는 오류", str(frames))


class V2SSEEndpointTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        app.state.chat_graph = FakeV2Graph()
        app.state.context_api = FakeContextAPI()

    async def _post(self, payload, authorization=None):
        headers = {"Accept": "text/event-stream"}
        if authorization is not None:
            headers["Authorization"] = authorization
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://ai",
        ) as client:
            return await client.post("/v2/chat/stream", json=payload, headers=headers)

    async def test_authentication_precedes_request_validation(self):
        response = await self._post({})

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "SERVICE_UNAUTHORIZED")

    @patch("main.get_callbacks", return_value=[])
    async def test_valid_request_returns_v2_event_stream(self, _callbacks):
        with patch("main.settings.ai_service_bearer_token", "test-token"):
            response = await self._post(
                v2_request().model_dump(mode="json"),
                authorization="Bearer test-token",
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/event-stream", response.headers["content-type"])
        self.assertEqual(response.headers["cache-control"], "no-cache")
        self.assertEqual(response.headers["x-accel-buffering"], "no")
        self.assertIn("event: delta", response.text)
        self.assertIn('"contract_version":"2.0"', response.text)


if __name__ == "__main__":
    import unittest

    unittest.main()
