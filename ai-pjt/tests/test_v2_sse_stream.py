import json
from hashlib import sha256
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

import httpx

from main import app, _stream_v2_chat_events
from core.model_boundary import ModelOutputBlockedError
from core.runtime_instrumentation import RuntimeStage, RuntimeStatus
from tests.instrumentation import fake_instrumentation, fake_terminal_masker
from transport.contracts import ChatV2Request


EMPTY_CONTEXT = {
    "schema_version": "3.0",
    "summary": {},
    "structured_state": {
        "current_goal": None,
        "response_preferences": [],
        "decisions": [],
        "open_questions": [],
        "entities": [],
    },
    "turns": [],
}


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
        payload_digest="0" * 64,
        session_id=12,
        thread_id="user-3-session-12",
        step_id="general",
        message="은퇴 준비를 알려줘",
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=60),
        customer_info={"saved_data": {}, "current_form": {}},
        chart_payload=None,
    )


def error_review_request() -> ChatV2Request:
    return ChatV2Request(
        **{
            **v2_request().model_dump(),
            "step_id": "basic-info",
            "request_mode": "error_review",
            "message": "현재 입력값을 검토해 주세요.",
        }
    )


class FakeContextAPI:
    def __init__(self):
        self.terminal = None

    async def read_context(self, scope):
        return {}

    async def read_terminal(self, scope, request_id, payload_digest):
        return self.terminal

    async def begin_execution(self, scope, request_id):
        return None

    async def delete_execution(self, scope, request_id):
        return None

    async def commit(
        self,
        scope,
        request_id,
        payload_digest,
        context,
        terminal,
        completed_turn=None,
    ):
        self.terminal = dict(terminal)
        return self.terminal

    async def commit_terminal(self, scope, request_id, payload_digest, terminal):
        self.terminal = dict(terminal)
        return self.terminal

    def state_channels(self, scope, context, retrieved_memories=()):
        return {
            "conversation_context": context,
            "retrieved_memories": list(retrieved_memories),
        }

    def config_for(self, scope, request_id):
        return {"configurable": {"thread_id": scope.thread_id}}


class FakeV2Graph:
    async def astream_events(self, input_data, *, config, version):
        assert input_data["request_id"] == "ai_req_v2_stream"
        assert "execution_budget" not in input_data
        assert config["configurable"]["thread_id"] == "user-3-session-12"
        assert version == "v2"
        yield {
            "event": "on_chat_model_stream",
            "metadata": {"langgraph_node": "planner"},
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
                    "conversation_context": EMPTY_CONTEXT,
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


class PolicyBlockedV2Graph:
    async def astream_events(self, input_data, *, config, version):
        raise ModelOutputBlockedError("민감정보가 포함된 모델 응답")
        yield  # pragma: no cover - keep this an async generator


class EmptyResponseV2Graph:
    async def astream_events(self, input_data, *, config, version):
        yield {
            "event": "on_chain_end",
            "parent_ids": [],
            "data": {
                "output": {
                    "conversation_context": EMPTY_CONTEXT,
                    "output": {
                        "response": "",
                        "step_id": "general",
                        "action": None,
                    }
                }
            },
        }


class FakeErrorReviewGraph:
    async def astream_events(self, input_data, *, config, version):
        assert input_data["request_mode"] == "error_review"
        assert "review_trigger" not in input_data
        yield {
            "event": "on_chain_end",
            "parent_ids": [],
            "data": {
                "output": {
                    "conversation_context": EMPTY_CONTEXT,
                    "output": {
                        "response": "입력값에서 확인이 필요한 항목 1개를 찾았습니다.",
                        "step_id": "basic-info",
                        "action": None,
                        "review": {
                            "type": "error_review",
                            "manual": {
                                "document_id": "interface-basic-info",
                                "version": "1.0.0",
                            },
                            "items": [],
                        },
                    }
                }
            },
        }


class V2SSEStreamTests(IsolatedAsyncioTestCase):
    @patch("main.get_callbacks", return_value=[])
    async def test_stream_uses_v2_identity_and_terminal_done(self, _callbacks):
        frames = [
            parse_frame(frame)
            async for frame in _stream_v2_chat_events(
                FakeV2Graph(),
                FakeContextAPI(),
                v2_request(),
                fake_terminal_masker(),
            )
        ]

        self.assertEqual([name for name, _ in frames], ["start", "done"])
        for _, data in frames:
            self.assertEqual(data["contract_version"], "2.0")
            self.assertEqual(data["request_id"], "ai_req_v2_stream")
        self.assertNotIn("내부 출력", str(frames))
        self.assertEqual(frames[-1][1]["status"], "completed")
        self.assertEqual(frames[-1][1]["output"]["response"], "안녕하세요")
        self.assertIn("completed_at", frames[-1][1])

    @patch("main.get_callbacks", return_value=[])
    async def test_stream_emits_one_request_root_lifecycle_with_io(self, _callbacks):
        instrumentation, sink = fake_instrumentation()

        frames = [
            parse_frame(frame)
            async for frame in _stream_v2_chat_events(
                FakeV2Graph(),
                FakeContextAPI(),
                v2_request(),
                fake_terminal_masker(),
                instrumentation=instrumentation,
            )
        ]

        self.assertEqual([name for name, _ in frames], ["start", "done"])
        request_events = [
            event
            for event in sink.runtime_events
            if event.stage is RuntimeStage.REQUEST_PROCESSING
        ]
        self.assertEqual(
            [event.status for event in request_events],
            [RuntimeStatus.STARTED, RuntimeStatus.SUCCEEDED],
        )
        self.assertEqual(request_events[0].input["message"], "은퇴 준비를 알려줘")
        self.assertEqual(request_events[1].output["response"], "안녕하세요")

    @patch("main.get_callbacks", return_value=[])
    async def test_failure_returns_safe_v2_terminal_error(self, _callbacks):
        frames = [
            parse_frame(frame)
            async for frame in _stream_v2_chat_events(
                FailingV2Graph(),
                FakeContextAPI(),
                v2_request(),
                fake_terminal_masker(),
            )
        ]

        self.assertEqual([name for name, _ in frames], ["start", "error"])
        terminal = frames[-1][1]
        self.assertEqual(terminal["status"], "failed")
        self.assertEqual(terminal["error"]["code"], "INTERNAL_ERROR")
        self.assertNotIn("외부에 노출하면 안 되는 오류", str(frames))

    @patch("main.get_callbacks", return_value=[])
    async def test_policy_block_returns_policy_terminal_error(self, _callbacks):
        frames = [
            parse_frame(frame)
            async for frame in _stream_v2_chat_events(
                PolicyBlockedV2Graph(),
                FakeContextAPI(),
                v2_request(),
                fake_terminal_masker(),
            )
        ]

        self.assertEqual([name for name, _ in frames], ["start", "error"])
        terminal = frames[-1][1]
        self.assertEqual(terminal["status"], "failed")
        self.assertEqual(terminal["error"]["code"], "POLICY_BLOCKED")
        self.assertNotIn("민감정보가 포함된 모델 응답", str(frames))

    @patch("main.get_callbacks", return_value=[])
    async def test_empty_response_returns_error_instead_of_done(self, _callbacks):
        frames = [
            parse_frame(frame)
            async for frame in _stream_v2_chat_events(
                EmptyResponseV2Graph(),
                FakeContextAPI(),
                v2_request(),
                fake_terminal_masker(),
            )
        ]

        self.assertEqual([name for name, _ in frames], ["start", "error"])
        self.assertNotIn("event: done", str(frames))

    @patch("main.get_callbacks", return_value=[])
    async def test_error_review_is_preserved_in_sse_done(self, _callbacks):
        frames = [
            parse_frame(frame)
            async for frame in _stream_v2_chat_events(
                FakeErrorReviewGraph(),
                FakeContextAPI(),
                error_review_request(),
                fake_terminal_masker(),
            )
        ]

        self.assertEqual([name for name, _ in frames], ["start", "done"])
        review = frames[-1][1]["output"]["review"]
        self.assertEqual(review["type"], "error_review")
        self.assertNotIn("trigger", review)


class V2SSEEndpointTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        app.state.chat_graph = FakeV2Graph()
        app.state.context_api = FakeContextAPI()
        app.state.terminal_masker = fake_terminal_masker()
        app.state.instrumentation = fake_instrumentation()[0]

    async def _post(self, payload, authorization=None, *, sign=True):
        if sign and isinstance(payload, dict) and payload:
            unsigned = {key: value for key, value in payload.items() if key != "payload_digest"}
            payload = {
                **unsigned,
                "payload_digest": sha256(json.dumps(
                    unsigned,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()).hexdigest(),
            }
        headers = {"Accept": "text/event-stream"}
        if authorization is not None:
            headers["Authorization"] = authorization
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://ai",
        ) as client:
            return await client.post("/api/ai/v2/chat/stream", json=payload, headers=headers)

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
        self.assertNotIn("event: delta", response.text)
        self.assertIn("event: done", response.text)
        self.assertIn('"contract_version":"2.0"', response.text)

    async def test_payload_digest_mismatch_is_rejected_before_graph_execution(self):
        with patch("main.settings.ai_service_bearer_token", "test-token"):
            response = await self._post(
                v2_request().model_dump(mode="json"),
                authorization="Bearer test-token",
                sign=False,
            )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "PAYLOAD_DIGEST_MISMATCH")


if __name__ == "__main__":
    import unittest

    unittest.main()
