from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import AIMessage

from core.context import ContextScope, ContextStateAPI
from core.context_store import InMemoryCommittedContextStore
from graph.builder import build_graph
from main import _process_v2_graph, app
from tests.test_graph_execution import (
    FIELD_SPEC,
    FakePlanner,
    all_agent_graphs,
)
from tests.instrumentation import fake_instrumentation
from transport.contracts import ChatV2Request, CompletedCallback
from transport.runtime import ExecutionBudget, utc_now


def make_request(request_id: str, session_id: int = 301) -> ChatV2Request:
    return ChatV2Request.model_validate({
        "contract_version": "2.0",
        "request_id": request_id,
        "payload_digest": "0" * 64,
        "session_id": session_id,
        "thread_id": "vertical-thread",
        "step_id": "basic-info",
        "message": "평균 소득을 찾아 월 소득 변경안을 만들어줘.",
        "deadline_at": datetime.now(timezone.utc) + timedelta(seconds=60),
        "customer_info": {
            "saved_data": {},
            "current_form": {"monthly_income": 2_000_000},
        },
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

    def install_graph(self, *, planner, tool_executors=None, agent_graphs=None):
        checkpointer = MemorySaver()
        context_api = ContextStateAPI(checkpointer, InMemoryCommittedContextStore())
        app.state.chat_graph = build_graph(
            checkpointer,
            instrumentation=fake_instrumentation()[0],
            planner=planner,
            agent_graphs=agent_graphs or all_agent_graphs(),
            tool_executors=tool_executors,
            response_composer=None,
        )
        app.state.context_api = context_api
        return context_api

    async def run_stream_execution(self, request):
        processor = AsyncMock(side_effect=_process_v2_graph)
        output = await processor(request, ExecutionBudget.from_request(request))
        terminal = CompletedCallback(
            request_id=request.request_id,
            session_id=request.session_id,
            thread_id=request.thread_id,
            completed_at=utc_now(),
            output=output,
        ).model_dump(mode="json")
        return processor, terminal

    async def test_context_plan_binding_aggregation_and_terminal_output(self):
        planner = FakePlanner({
            "tasks": [
                {
                    "id": "lookup",
                    "type": "lookup_reference_value",
                    "description": "40대 월평균 비용 조회",
                    "depends_on": [],
                    "bindings": {},
                    "arguments": {
                        "reference_id": "debt_service_ratio_max",
                        "age": 45,
                        "period": "annual",
                        "population": "retirewise_kr_households",
                    },
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
                    "schema_version": "1.0",
                    "producer_task_id": "task-1",
                    "reference_id": "monthly_target_expense",
                    "age": 45,
                    "value": 2_500_000,
                    "unit": "KRW",
                    "period": "monthly",
                    "population": "40대",
                    "comparator": "eq",
                    "source_id": "approved-statistics-2026",
                    "source_version": "1.0.0",
                    "as_of": "2026-06-30",
                    "calculation_policy_version": "2026-09-07.v1",
                },
            }

        context_api = self.install_graph(
            planner=planner,
            tool_executors={"lookup_reference_value": lookup},
        )
        request = make_request("ai_req_vertical_success")
        processor, first = await self.run_stream_execution(request)

        self.assertEqual(first["status"], "completed")
        self.assertEqual(first["output"]["action"]["changes"][0]["value"], 2_500_000)
        processor.assert_awaited_once()

        scope = ContextScope(request.session_id, request.thread_id)
        context = await context_api.read_context(scope)
        self.assertEqual(len(context.turns), 1)
        self.assertEqual(context.turns[0].turn_id, request.request_id)
        self.assertIn("변경안을 만들었습니다", context.turns[0].assistant_message)
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
                    "arguments": {
                        "reference_id": "debt_service_ratio_max",
                        "age": 45,
                        "period": "annual",
                        "population": "retirewise_kr_households",
                    },
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
        processor, terminal = await self.run_stream_execution(request)

        self.assertEqual(terminal["status"], "completed")
        self.assertIsNone(terminal["output"]["action"])
        self.assertIn("선행 작업", terminal["output"]["response"])
        self.assertIn("qa 응답", terminal["output"]["response"])
        processor.assert_awaited_once()


    async def test_household_reference_lookup_binds_exact_existing_row(self):
        from agents import input_assist
        from tools.catalog import build_default_registry
        from tools.graph_adapter import build_graph_tool_executors
        from tools.runtime import FunctionToolRuntime

        binding_fields = (
            "reference_id",
            "age",
            "value",
            "unit",
            "period",
            "population",
            "comparator",
            "source_id",
            "source_version",
            "as_of",
            "calculation_policy_version",
        )
        planner = FakePlanner({
            "tasks": [
                {
                    "id": "lookup",
                    "type": "lookup_reference_value",
                    "description": "45세 전국 일반가구 주거비 월평균 승인 기준 조회",
                    "depends_on": [],
                    "bindings": {},
                    "arguments": {
                        "reference_id": "household_monthly_expense_housing_avg",
                        "age": 45,
                        "period": "monthly",
                        "population": "kr_general_households_1plus",
                    },
                },
                {
                    "id": "fill",
                    "type": "propose_form_change",
                    "description": "사용자가 요청한 전국 가구 평균으로 기존 빈 주거비 항목을 채워줘",
                    "depends_on": ["lookup"],
                    "bindings": {
                        field: {"task_id": "lookup", "path": field}
                        for field in binding_fields
                    },
                },
            ],
        })
        agents = all_agent_graphs()
        agents["input_assist"] = input_assist.graph
        runtime = FunctionToolRuntime(build_default_registry())
        context_api = self.install_graph(
            planner=planner,
            tool_executors=build_graph_tool_executors(runtime),
            agent_graphs=agents,
        )
        request_payload = make_request("ai_req_f14_household_success").model_dump(
            mode="python"
        )
        request_payload["customer_info"] = {
            "saved_data": {},
            "current_form": {"goal_periods": [{
                "id": 51,
                "phase": "before",
                "from_age": 45,
                "to_age": 65,
                "items": [{
                    "id": 501,
                    "name": "주거비",
                    "amount": None,
                    "is_fixed": True,
                }],
            }]},
        }
        request_payload["step_id"] = "financial-goals"
        request = ChatV2Request.model_validate(request_payload)

        classify = SimpleNamespace(ainvoke=AsyncMock(return_value=input_assist.ClassifyResult(
            intent="fill_avg",
            user_requested_change=True,
        )))
        merge_model = SimpleNamespace(ainvoke=AsyncMock(return_value=AIMessage(
            content="승인된 주거비 기준으로 기존 빈 항목 변경안을 만들었습니다.",
        )))
        with patch.object(input_assist, "_classify_chain", classify), patch.object(
            input_assist, "llm", merge_model
        ):
            processor, first = await self.run_stream_execution(request)

        self.assertEqual(first["status"], "completed")
        change = first["output"]["action"]["changes"][0]
        self.assertEqual(change["section"], "goal_periods")
        self.assertEqual(change["path"], "items[].amount")
        self.assertEqual(change["parent_item_id"], 51)
        self.assertEqual(change["item_id"], 501)
        self.assertEqual(change["value"], 361_000)
        self.assertEqual(change["unit"], "KRW")
        self.assertEqual(
            change["reference"]["reference_id"],
            "household_monthly_expense_housing_avg",
        )
        self.assertEqual(
            change["reference"]["calculation_policy_version"],
            "2026-09-12.v1",
        )
        processor.assert_awaited_once()
