import asyncio
import json
from copy import deepcopy
from hashlib import sha256
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch

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


class ProgressV2Graph:
    async def astream_events(self, input_data, *, config, version):
        for node in ("plan", "execute"):
            yield {
                "event": "on_chain_start",
                "metadata": {"langgraph_node": node},
                "data": {},
            }
        await asyncio.sleep(0.02)
        for node in ("validate", "persist_context"):
            yield {
                "event": "on_chain_start",
                "metadata": {"langgraph_node": node},
                "data": {},
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
                    },
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
    @patch("transport.chat_service.get_callbacks", return_value=[])
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
    async def test_opt_in_stream_emits_public_stages_and_heartbeats(self, _callbacks):
        frames = [
            parse_frame(frame)
            async for frame in _stream_v2_chat_events(
                ProgressV2Graph(),
                FakeContextAPI(),
                v2_request(),
                fake_callback_masker(),
                progress_events=True,
                heartbeat_interval_seconds=0.005,
            )
        ]

        self.assertEqual(frames[0][0], "start")
        self.assertEqual(frames[-1][0], "done")
        progress = [data for event, data in frames if event == "progress"]
        self.assertEqual(
            [data["phase"] for data in progress],
            ["preparing", "planning", "executing", "validating", "saving"],
        )
        nonterminal = [
            data for event, data in frames if event in {"progress", "heartbeat"}
        ]
        self.assertEqual(
            [data["event_seq"] for data in nonterminal],
            list(range(1, len(nonterminal) + 1)),
        )
        self.assertTrue(any(event == "heartbeat" for event, _ in frames))
        self.assertNotIn("percent", str(nonterminal))
        self.assertNotIn("은퇴 준비를 알려줘", str(nonterminal))

    @patch("main.get_callbacks", return_value=[])
    async def test_terminal_replay_does_not_replay_progress(self, _callbacks):
        context = FakeContextAPI()
        context.terminal = {
            "contract_version": "2.0",
            "request_id": "ai_req_v2_stream",
            "session_id": 12,
            "thread_id": "user-3-session-12",
            "status": "completed",
            "completed_at": "2026-09-17T00:00:00Z",
            "output": {"response": "재생 응답"},
        }

        frames = [
            parse_frame(frame)
            async for frame in _stream_v2_chat_events(
                FakeV2Graph(),
                context,
                v2_request(),
                fake_callback_masker(),
                progress_events=True,
            )
        ]

        self.assertEqual([event for event, _ in frames], ["start", "done"])

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

    @patch("transport.chat_service.get_callbacks", return_value=[])
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

    @patch("transport.chat_service.get_callbacks", return_value=[])
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

    @patch("transport.chat_service.get_callbacks", return_value=[])
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

    @patch("transport.chat_service.get_callbacks", return_value=[])
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

    async def _post(
        self,
        payload,
        authorization=None,
        *,
        sign=True,
        progress_events_version=None,
    ):
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
        if progress_events_version is not None:
            headers["X-AI-Progress-Events"] = progress_events_version
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://ai",
        ) as client:
            return await client.post("/api/ai/v2/chat/stream", json=payload, headers=headers)

    async def test_authentication_precedes_request_validation(self):
        response = await self._post({})

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "SERVICE_UNAUTHORIZED")

    @patch("transport.chat_service.get_callbacks", return_value=[])
    async def test_browser_mindset_forms_reach_real_graph_without_model_calls(self, _callbacks):
        from agents.elicit import service
        from agents.elicit.builder import build_graph as build_elicit
        from core.mindset_elicitation import opening_question
        from graph.builder import build_graph
        from tests.test_graph_execution import all_agent_graphs

        fixture = json.loads((Path(__file__).resolve().parents[2]
                              / "contract-fixtures/ai-lifecycle/v2/mindset-elicitation.json").read_text())
        planner = Mock(side_effect=AssertionError("start must not call planner"))
        composer = AsyncMock(side_effect=AssertionError("start must not call composer"))
        agents = all_agent_graphs()
        agents["elicit"] = build_elicit()
        app.state.chat_graph = build_graph(
            None, planner=planner, response_composer=composer, agent_graphs=agents,
            instrumentation=fake_instrumentation()[0],
        )
        for case in fixture["form_cases"]:
            with self.subTest(case=case["id"]):
                app.state.context_api = FakeContextAPI()
                app.state.context_api.read_context = AsyncMock(return_value=deepcopy(EMPTY_CONTEXT))
                payload = v2_request().model_dump(mode="json")
                payload.update(
                    step_id=fixture["step_id"], request_mode=fixture["request_mode"],
                    elicitation_state=fixture["request_cases"][0]["state"],
                    customer_info={"saved_data": {}, "current_form": case["current_form"]},
                )
                with patch("main.settings.ai_service_bearer_token", "test-token"), \
                     patch.object(service, "collect_answer", new_callable=AsyncMock) as model:
                    response = await self._post(payload, authorization="Bearer test-token")
                    model.assert_not_awaited()
                self.assertEqual(response.status_code, 200, response.text)
                events = [parse_frame(frame) for frame in response.text.strip().split("\n\n")]
                self.assertEqual([event for event, _ in events], ["start", "done"])
                output = events[-1][1]["output"]
                self.assertEqual(output["response"], opening_question("values", payload["customer_info"]))
                self.assertEqual(output["elicitation"]["summary"], case["summary"])
                self.assertEqual(output["elicitation"]["answer_count"], 0)
                self.assertIsNone(output["action"])
        planner.assert_not_called()
        composer.assert_not_awaited()

    async def test_model_level_mindset_errors_return_422_instead_of_crashing(self):
        for value in (42, False, [], {}):
            with self.subTest(value=value):
                payload = v2_request().model_dump(mode="json")
                payload.update(
                    step_id="financial-mindset", request_mode="mindset_elicitation",
                    elicitation_state={"event": "start", "field": "values",
                                       "answer_count": 0, "completed_fields": []},
                    customer_info={"saved_data": {}, "current_form": {"values": value}},
                )
                with patch("main.settings.ai_service_bearer_token", "test-token"):
                    response = await self._post(payload, authorization="Bearer test-token")
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["error"]["code"], "REQUEST_SCHEMA_INVALID")
                self.assertNotIn("current mindset summary", response.text)

    @patch("transport.chat_service.get_callbacks", return_value=[])
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

    @patch("main.get_callbacks", return_value=[])
    async def test_progress_capability_is_explicitly_negotiated(self, _callbacks):
        app.state.chat_graph = ProgressV2Graph()
        with patch("main.settings.ai_service_bearer_token", "test-token"):
            response = await self._post(
                v2_request().model_dump(mode="json"),
                authorization="Bearer test-token",
                progress_events_version="v1",
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("event: progress", response.text)
        self.assertNotIn("percent", response.text)

    async def test_unknown_progress_capability_is_rejected(self):
        with patch("main.settings.ai_service_bearer_token", "test-token"):
            response = await self._post(
                v2_request().model_dump(mode="json"),
                authorization="Bearer test-token",
                progress_events_version="v2",
            )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "REQUEST_SCHEMA_INVALID")

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
