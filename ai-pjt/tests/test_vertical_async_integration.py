from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from langgraph.checkpoint.memory import MemorySaver

from core.context import ContextScope, ContextStateAPI
from graph.builder import build_graph
from main import _process_v2_graph, app
from tests.test_graph_execution import (
    FIELD_SPEC,
    FakeClassifier,
    FakePlanner,
    all_agent_graphs,
)
from transport.contracts import ChatV2Request
from transport.runtime import (
    AsyncChatTransport,
    InMemoryRequestCheckpointStore,
    request_digest,
)


def make_request(request_id: str, session_id: int = 301) -> ChatV2Request:
    return ChatV2Request.model_validate({
        "contract_version": "2.0",
        "request_id": request_id,
        "session_id": session_id,
        "thread_id": "vertical-thread",
        "step_id": "financial-goals",
        "message": "평균 비용을 찾아 목표 생활비 변경안을 만들어줘.",
        "deadline_at": datetime.now(timezone.utc) + timedelta(seconds=60),
        "customer_info": {
            "saved_data": {},
            "current_form": {"monthly_target_expense": 2_000_000},
        },
        "field_registry": [FIELD_SPEC],
        "chart_payload": None,
    })


class VerticalAsyncIntegrationTests(IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.previous_graph = getattr(app.state, "chat_graph", None)
        self.previous_context_api = getattr(app.state, "context_api", None)

    def tearDown(self) -> None:
        if self.previous_graph is None:
            del app.state.chat_graph
        else:
            app.state.chat_graph = self.previous_graph
        if self.previous_context_api is None:
            del app.state.context_api
        else:
            app.state.context_api = self.previous_context_api

    def install_graph(self, *, planner, tool_executors=None):
        checkpointer = MemorySaver()
        context_api = ContextStateAPI(checkpointer)
        app.state.chat_graph = build_graph(
            checkpointer,
            planner=planner,
            classifier=FakeClassifier(),
            agent_graphs=all_agent_graphs(),
            tool_executors=tool_executors,
        )
        app.state.context_api = context_api
        return context_api

    async def run_transport(self, request):
        store = InMemoryRequestCheckpointStore()
        processor = AsyncMock(side_effect=_process_v2_graph)
        callback = SimpleNamespace(send=AsyncMock())
        transport = AsyncChatTransport(
            checkpoint_store=store,
            processor=processor,
            callback_client=callback,
        )
        await store.claim(request, request_digest(request))
        first = await transport.run(request)
        return transport, processor, callback, first

    async def test_context_plan_binding_aggregation_and_terminal_replay(self):
        planner = FakePlanner({
            "tasks": [
                {
                    "id": "lookup",
                    "type": "lookup_reference_value",
                    "description": "40대 월평균 비용 조회",
                    "depends_on": [],
                    "bindings": {},
                },
                {
                    "id": "fill",
                    "type": "propose_form_change",
                    "description": "조회 결과로 목표 생활비 변경 제안",
                    "depends_on": ["lookup"],
                    "bindings": {
                        "value": {"task_id": "lookup", "path": "value"},
                        "unit": {"task_id": "lookup", "path": "unit"},
                    },
                },
            ],
        })

        async def lookup(_payload):
            return {
                "task_id": "task-1",
                "status": "completed",
                "artifact": {
                    "type": "reference_value",
                    "value": 2_500_000,
                    "unit": "KRW",
                    "period": "month",
                    "population": "40대",
                    "source_id": "approved-statistics-2026",
                    "as_of": "2026-06-30",
                },
            }

        context_api = self.install_graph(
            planner=planner,
            tool_executors={"lookup_reference_value": lookup},
        )
        request = make_request("ai_req_vertical_success")
        transport, processor, callback, first = await self.run_transport(request)
        replay = await transport.run(request)

        self.assertEqual(first, replay)
        self.assertEqual(first["status"], "completed")
        self.assertEqual(first["output"]["action"]["changes"][0]["value"], 2_500_000)
        processor.assert_awaited_once()
        self.assertEqual(callback.send.await_count, 2)
        self.assertEqual(callback.send.await_args_list[0], callback.send.await_args_list[1])

        scope = ContextScope(request.session_id, request.thread_id)
        context = await context_api.read_context(scope)
        self.assertEqual(len(context.turns), 1)
        self.assertEqual(context.turns[0].turn_id, request.request_id)
        self.assertIn("변경안을 만들었습니다", context.turns[0].assistant_message)
        values = await context_api.read_values(scope)
        self.assertEqual(values["task_results"]["task-1"]["status"], "completed")
        self.assertEqual(values["task_results"]["task-2"]["status"], "completed")
        self.assertEqual(values["execution_budget"]["task_limit"], 3)
        self.assertEqual(values["execution_budget"]["tasks_used"], 2)

        foreign = await context_api.read_context(
            ContextScope(request.session_id + 1, request.thread_id)
        )
        self.assertEqual(len(foreign.turns), 0)

    async def test_failed_dependency_is_blocked_while_independent_task_continues(self):
        planner = FakePlanner({
            "tasks": [
                {
                    "id": "lookup",
                    "type": "lookup_reference_value",
                    "description": "기준값 조회",
                    "depends_on": [],
                    "bindings": {},
                },
                {
                    "id": "fill",
                    "type": "propose_form_change",
                    "description": "조회값으로 변경 제안",
                    "depends_on": ["lookup"],
                    "bindings": {
                        "value": {"task_id": "lookup", "path": "value"},
                    },
                },
                {
                    "id": "explain",
                    "type": "answer_question",
                    "description": "상담 절차 설명",
                    "depends_on": [],
                    "bindings": {},
                },
            ],
        })

        async def unavailable_lookup(_payload):
            raise RuntimeError("fixture tool outage")

        context_api = self.install_graph(
            planner=planner,
            tool_executors={"lookup_reference_value": unavailable_lookup},
        )
        request = make_request("ai_req_vertical_partial")
        _transport, processor, callback, terminal = await self.run_transport(request)

        self.assertEqual(terminal["status"], "completed")
        self.assertIsNone(terminal["output"]["action"])
        self.assertIn("선행 작업", terminal["output"]["response"])
        self.assertIn("qa 응답", terminal["output"]["response"])
        processor.assert_awaited_once()
        callback.send.assert_awaited_once_with(terminal)

        values = await context_api.read_values(
            ContextScope(request.session_id, request.thread_id)
        )
        self.assertEqual(values["task_results"]["task-1"]["status"], "failed")
        self.assertEqual(values["task_results"]["task-2"]["status"], "blocked")
        self.assertEqual(values["task_results"]["task-2"]["blocked_by"], ["task-1"])
        self.assertEqual(values["task_results"]["task-3"]["status"], "completed")
        self.assertEqual(values["execution_budget"]["tasks_used"], 2)
