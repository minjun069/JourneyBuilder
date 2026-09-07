import json
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

from fastapi import HTTPException

from main import _encode_sse, _stream_chat_events, _verify_internal_secret
from schemas.chat import ChatStreamRequest


def parse_sse_frame(frame: str) -> tuple[str, dict]:
    lines = frame.rstrip("\n").splitlines()
    event = lines[0].removeprefix("event: ")
    data = json.loads(lines[1].removeprefix("data: "))
    return event, data


def stream_request() -> ChatStreamRequest:
    return ChatStreamRequest(
        request_id="ai_req_stream_test",
        session_id=12,
        thread_id="user-3-session-12",
        step_id="general",
        message="은퇴 준비를 알려줘",
        customer_info={"saved_data": {}, "current_form": {}},
    )


class FakeStreamingGraph:
    async def astream_events(self, input_data, *, config, version):
        assert input_data["message"] == "은퇴 준비를 알려줘"
        assert config["configurable"]["thread_id"] == "user-3-session-12"
        assert version == "v2"
        yield {
            "event": "on_chat_model_stream",
            "metadata": {"langgraph_node": "router"},
            "data": {"chunk": SimpleNamespace(content="내부 라우팅 출력")},
        }
        for content in ("안녕", "하세요"):
            yield {
                "event": "on_chat_model_stream",
                "metadata": {"langgraph_node": "merge"},
                "data": {"chunk": SimpleNamespace(content=content)},
            }
        yield {
            "event": "on_chain_end",
            "parent_ids": [],
            "data": {
                "output": {
                    "output": {
                        "response": "안녕하세요",
                        "action": {"type": "form_change", "changes": []},
                    }
                }
            },
        }


class FakeNonStreamingGraph:
    async def astream_events(self, input_data, *, config, version):
        yield {
            "event": "on_chain_end",
            "parent_ids": [],
            "data": {"output": {"output": {"response": "한 번에 완성된 답변"}}},
        }


class FailingGraph:
    async def astream_events(self, input_data, *, config, version):
        yield {
            "event": "on_chat_model_stream",
            "metadata": {"langgraph_node": "merge"},
            "data": {"chunk": SimpleNamespace(content="일부")},
        }
        raise RuntimeError("외부에 노출하면 안 되는 오류")


class SSEContractTests(TestCase):
    def test_sse_frame_uses_named_event_and_json_data(self):
        frame = _encode_sse("delta", {"content": "은퇴\n준비"})

        self.assertTrue(frame.endswith("\n\n"))
        event, data = parse_sse_frame(frame)
        self.assertEqual(event, "delta")
        self.assertEqual(data, {"content": "은퇴\n준비"})

    @patch("main.settings.ai_callback_secret", "expected-secret")
    def test_internal_secret_is_required_when_configured(self):
        with self.assertRaises(HTTPException) as context:
            _verify_internal_secret("wrong-secret")

        self.assertEqual(context.exception.status_code, 403)


class SSEStreamTests(IsolatedAsyncioTestCase):
    @patch("main.get_callbacks", return_value=[])
    async def test_only_public_final_node_tokens_are_streamed(self, _callbacks):
        frames = [
            parse_sse_frame(frame)
            async for frame in _stream_chat_events(FakeStreamingGraph(), stream_request())
        ]

        self.assertEqual([event for event, _ in frames], ["start", "delta", "delta", "done"])
        self.assertEqual(
            [data["content"] for event, data in frames if event == "delta"],
            ["안녕", "하세요"],
        )
        self.assertNotIn("내부 라우팅 출력", str(frames))
        self.assertEqual(frames[-1][1]["output"]["response"], "안녕하세요")
        self.assertEqual(frames[-1][1]["output"]["action"]["type"], "form_change")

    @patch("main.get_callbacks", return_value=[])
    async def test_final_response_is_delta_fallback_for_non_streaming_model(self, _callbacks):
        frames = [
            parse_sse_frame(frame)
            async for frame in _stream_chat_events(FakeNonStreamingGraph(), stream_request())
        ]

        self.assertEqual([event for event, _ in frames], ["start", "delta", "done"])
        self.assertEqual(frames[1][1]["content"], "한 번에 완성된 답변")

    @patch("main.get_callbacks", return_value=[])
    async def test_stream_failure_returns_safe_error_event(self, _callbacks):
        frames = [
            parse_sse_frame(frame)
            async for frame in _stream_chat_events(FailingGraph(), stream_request())
        ]

        self.assertEqual([event for event, _ in frames], ["start", "delta", "error"])
        self.assertEqual(frames[-1][1]["code"], "AI_STREAM_FAILED")
        self.assertNotIn("외부에 노출하면 안 되는 오류", str(frames))
