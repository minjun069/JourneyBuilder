from __future__ import annotations

import importlib.util
import unittest

from tests.instrumentation import fake_instrumentation


LANGGRAPH_AVAILABLE = importlib.util.find_spec("langgraph") is not None


class FakePlanner:
    def __init__(self, plan: dict) -> None:
        self.plan = plan

    def __call__(self, **_: object) -> dict:
        return self.plan


class FakeAgentGraph:
    def __init__(self, role: str) -> None:
        self.role = role
        self.calls: list[dict] = []

    async def ainvoke(self, state: dict) -> dict:
        self.calls.append(state)
        if self.role == "input_assist":
            value = state["bound_inputs"]["value"]
            return {
                "output": {
                    "response": "조회 결과로 목표 생활비 변경안을 만들었습니다.",
                    "step_id": state["step_id"],
                    "action": {
                        "type": "form_change",
                        "target": "draft_form",
                        "user_requested": True,
                        "confidence": 1.0,
                        "changes": [{
                            "section": "financial_goals",
                            "path": "monthly_target_expense",
                            "operation": "set",
                            "value": value,
                            "unit": state["bound_inputs"]["unit"],
                            "reason": "승인된 조회 결과",
                        }],
                    },
                }
            }
        return {
            "output": {
                "response": f"{self.role} 응답",
                "step_id": state["step_id"],
                "action": None,
            }
        }


def all_agent_graphs() -> dict[str, FakeAgentGraph]:
    return {
        name: FakeAgentGraph(name)
        for name in (
            "qa",
            "input_assist",
            "error_review",
            "chart_interpret",
            "elicit",
        )
    }


FIELD_SPEC = {
    "step_id": "financial-goals",
    "section": "financial_goals",
    "path": "monthly_target_expense",
    "value_type": "integer",
    "unit": "KRW",
    "constraints": {"min": 0, "period": "month"},
    "cardinality": "single",
    "writable_by_ai": True,
    "nullable": False,
    "operations": ["set", "update"],
}


@unittest.skipUnless(LANGGRAPH_AVAILABLE, "LangGraph runtime image에서 실행")
class GraphExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_presentation_entry_bypasses_planner_and_routes_directly_to_chart(self) -> None:
        from graph.builder import build_graph

        def planner_must_not_run(**_: object) -> dict:
            raise AssertionError("presentation entry must bypass the planner")

        agents = all_agent_graphs()
        graph = build_graph(
            None,
            instrumentation=fake_instrumentation()[0],
            planner=planner_must_not_run,
            agent_graphs=agents,
        )
        result = await graph.ainvoke({
            "request_id": "presentation-entry-1",
            "message": "이 보고서의 핵심을 먼저 안내해 주세요.",
            "step_id": "lifetime-cashflow",
            "request_mode": "presentation_entry",
            "customer_info": {"saved_data": {}, "current_form": {}},
            "field_registry": [],
            "chart_payload": {"status": "guidance_only"},
        })

        self.assertEqual(result["route_plan"]["tasks"][0]["type"], "interpret_chart")
        self.assertEqual(len(agents["chart_interpret"].calls), 1)

    async def test_other_step_keeps_elicit_memory_out_of_planner_scope(self) -> None:
        from graph.builder import build_graph
        from graph.elicit_context import initial_elicit_contexts

        class CapturePlanner:
            def __init__(self) -> None:
                self.context: dict | None = None

            def __call__(
                self,
                *,
                message: str,
                step_id: str,
                context: dict,
            ) -> dict:
                self.context = context
                return {
                    "elicit_routing": {
                        "is_related": False,
                        "field": None,
                        "turn_kind": None,
                        "confidence_sufficient": False,
                    },
                    "tasks": [{
                        "id": "answer",
                        "type": "answer_question",
                        "description": message,
                        "depends_on": [],
                        "bindings": {},
                    }],
                }

        scope = {
            "schema_version": "1.0",
            "session_id": 12,
            "thread_id": "user-3-session-12",
        }
        mindset_state = {
            "step_id": "financial-mindset",
            "customer_info": {"saved_data": {}, "current_form": {}},
            "context_scope": scope,
        }
        contexts = initial_elicit_contexts(mindset_state)
        contexts["contexts"]["future_goals"].update({
            "open_question": "은퇴 후 어떤 삶을 원하시나요?",
            "status": "open",
            "updated_at": "2026-09-16T00:00:00Z",
        })
        planner = CapturePlanner()
        agents = all_agent_graphs()
        instrumentation, sink = fake_instrumentation()
        graph = build_graph(
            None,
            instrumentation=instrumentation,
            planner=planner,
            agent_graphs=agents,
        )

        result = await graph.ainvoke({
            "request_id": "turn-intro",
            "message": "이 서비스는 어떻게 쓰나요?",
            "step_id": "intro",
            "customer_info": {"saved_data": {}, "current_form": {}},
            "field_registry": [],
            "context_scope": scope,
            "elicit_contexts": contexts,
        })

        self.assertEqual(planner.context["elicit_context_index"], {})
        self.assertEqual(result["elicit_contexts"], contexts)
        self.assertEqual(result["output"]["response"], "qa 응답")
        plan_decisions = [
            event
            for event in sink.security_events
            if event.boundary == "plan_guardrail"
        ]
        self.assertEqual(len(plan_decisions), 1)
        self.assertEqual(plan_decisions[0].reason_code, "PLAN_GUARDRAIL_ALLOWED")

    async def test_planner_reconnects_field_context_after_intervening_qa(self) -> None:
        from graph.builder import build_graph
        from graph.elicit_context import initial_elicit_contexts

        class InterleavedPlanner:
            def __init__(self) -> None:
                self.calls: list[dict] = []

            def __call__(self, *, message: str, step_id: str, context: dict) -> dict:
                self.calls.append(context)
                if "계산" in message:
                    return {
                        "elicit_routing": {
                            "is_related": False,
                            "field": None,
                            "turn_kind": None,
                            "confidence_sufficient": False,
                        },
                        "tasks": [{
                            "id": "answer",
                            "type": "answer_question",
                            "description": message,
                            "depends_on": [],
                            "bindings": {},
                        }],
                    }
                return {
                    "elicit_routing": {
                        "is_related": True,
                        "field": "future_goals",
                        "turn_kind": "correction",
                        "confidence_sufficient": True,
                    },
                    "tasks": [{
                        "id": "correct",
                        "type": "collect_qualitative_info",
                        "description": message,
                        "depends_on": [],
                        "bindings": {},
                    }],
                }

        scope = {
            "schema_version": "1.0",
            "session_id": 12,
            "thread_id": "user-3-session-12",
        }
        base_state = {
            "step_id": "financial-mindset",
            "customer_info": {"saved_data": {}, "current_form": {}},
            "context_scope": scope,
        }
        contexts = initial_elicit_contexts(base_state)
        contexts["contexts"]["future_goals"].update({
            "open_question": "은퇴 후 어떤 삶을 원하시나요?",
            "status": "open",
            "updated_at": "2026-09-16T00:00:00Z",
        })
        planner = InterleavedPlanner()
        agents = all_agent_graphs()
        graph = build_graph(
            None,
            instrumentation=fake_instrumentation()[0],
            planner=planner,
            agent_graphs=agents,
        )

        qa_result = await graph.ainvoke({
            **base_state,
            "request_id": "turn-calc",
            "message": "필요 저축액 계산 방식은 뭐야?",
            "field_registry": [],
            "elicit_contexts": contexts,
        })
        self.assertEqual(len(agents["qa"].calls), 1)
        self.assertEqual(len(agents["elicit"].calls), 0)
        self.assertNotIn("ui_action", qa_result["output"])

        correction_result = await graph.ainvoke({
            **base_state,
            "request_id": "turn-correction",
            "message": "아까 말한 여행은 국내 여행 위주야.",
            "field_registry": [],
            "elicit_contexts": qa_result["elicit_contexts"],
        })

        self.assertEqual(len(agents["elicit"].calls), 1)
        self.assertEqual(
            agents["elicit"].calls[0]["elicit_routing"],
            {
                "is_related": True,
                "field": "future_goals",
                "turn_kind": "correction",
                "confidence_sufficient": True,
            },
        )
        self.assertEqual(
            planner.calls[0]["elicit_context_index"],
            planner.calls[1]["elicit_context_index"],
        )
        self.assertEqual(correction_result["output"]["response"], "elicit 응답")

    async def test_explanation_questions_do_not_fall_back_on_model_adapter_outage(
        self,
    ) -> None:
        from graph.builder import build_graph

        def unavailable_planner(**_kwargs: object) -> dict:
            raise RuntimeError("simulated planner outage")

        for index, message in enumerate((
            "단기 지향이면 어느정도 기간을 말하지?",
            "안정추구란 정확히 뭘 말하는거야?",
        )):
            with self.subTest(message=message):
                agents = all_agent_graphs()
                instrumentation, sink = fake_instrumentation()
                graph = build_graph(
                    None,
                    instrumentation=instrumentation,
                    planner=unavailable_planner,
                    agent_graphs=agents,
                )
                result = await graph.ainvoke({
                    "request_id": f"req-explanation-{index}",
                    "message": message,
                    "step_id": "basic-info",
                    "customer_info": {"saved_data": {}, "current_form": {}},
                    "field_registry": [],
                })

                self.assertEqual(result["output"]["response"], "qa 응답")
                self.assertEqual(result["route_plan"]["tasks"][0]["type"], "answer_question")
                self.assertEqual(
                    result["route_plan"]["tasks"][0]["route"]["executor"],
                    "qa",
                )
                self.assertEqual(len(agents["qa"].calls), 1)
                planner_terminal = next(
                    event
                    for event in sink.runtime_events
                    if event.stage.value == "planner" and event.status.value == "succeeded"
                )
                components = {
                    item["component"]
                    for item in planner_terminal.output["diagnostics"]
                }
                self.assertEqual(components, {"planner"})

    async def test_current_form_overrides_saved_fact_only_for_active_fields(self) -> None:
        from graph.builder import _flatten_customer_info

        facts = _flatten_customer_info(
            {
                "saved_data": {
                    "financial_goals": {
                        "monthly_target_expense": 2_000_000,
                    },
                    "basic_info": {"monthly_income": 3_000_000},
                },
                "current_form": {
                    "monthly_target_expense": 2_500_000,
                    "monthly_income": 9_999_999,
                },
            },
            field_registry=[FIELD_SPEC],
            step_id="financial-goals",
        )

        self.assertEqual(
            facts["financial_goals.monthly_target_expense"],
            2_500_000,
        )
        self.assertEqual(facts["basic_info.monthly_income"], 3_000_000)

    async def test_graph_passes_typed_tool_result_to_input_agent(self) -> None:
        from graph.builder import build_graph

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
            ]
        })
        agents = all_agent_graphs()

        async def lookup(_: dict) -> dict:
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

        graph = build_graph(
            None,
            instrumentation=fake_instrumentation()[0],
            planner=planner,
            agent_graphs=agents,
            tool_executors={"lookup_reference_value": lookup},
            response_composer=None,
        )
        result = await graph.ainvoke({
            "request_id": "req-1",
            "message": "평균 비용을 찾고 그 값으로 목표치를 채워줘.",
            "step_id": "financial-goals",
            "customer_info": {"saved_data": {}, "current_form": {}},
            "field_registry": [FIELD_SPEC],
        })

        self.assertEqual(result["route_plan"]["plan_id"], "plan-req-1")
        self.assertEqual(result["task_results"]["task-1"]["status"], "completed")
        self.assertEqual(result["task_results"]["task-2"]["status"], "completed")
        self.assertEqual(
            agents["input_assist"].calls[0]["bound_inputs"],
            {"value": 2_500_000, "unit": "KRW"},
        )
        self.assertEqual(result["output"]["action"]["changes"][0]["value"], 2_500_000)

    async def test_invalid_plan_returns_detailed_failure_without_agent_call(self) -> None:
        from graph.builder import build_graph

        agents = all_agent_graphs()
        graph = build_graph(
            None,
            instrumentation=fake_instrumentation()[0],
            planner=FakePlanner({"tasks": []}),
            agent_graphs=agents,
        )
        result = await graph.ainvoke({
            "request_id": "req-2",
            "message": "요청",
            "step_id": "intro",
            "customer_info": {"saved_data": {}, "current_form": {}},
            "field_registry": [],
        })

        self.assertTrue(result["plan_issues"])
        self.assertIn("실행 계획", result["output"]["response"])
        self.assertIn("다시 시도", result["output"]["response"])
        self.assertEqual(sum(len(agent.calls) for agent in agents.values()), 0)

    async def test_planner_instrumentation_includes_conversation_context(self) -> None:
        from graph.builder import build_graph
        from core.runtime_instrumentation import (
            RuntimeStage,
            RuntimeStatus,
            RuntimeTraceEvent,
            SecurityDecisionTraceEvent,
        )

        instrumentation, sink = fake_instrumentation()
        graph = build_graph(
            None,
            instrumentation=instrumentation,
            planner=FakePlanner({"tasks": []}),
            agent_graphs=all_agent_graphs(),
        )
        context = {
            "schema_version": "3.0",
            "summary": {},
            "structured_state": {
                "current_goal": None,
                "response_preferences": [],
                "decisions": [],
                "open_questions": [],
                "entities": [],
            },
            "turns": [{
                "turn_id": "previous-request",
                "user_message": "이전 질문",
                "assistant_message": "이전 답변",
            }],
        }
        await graph.ainvoke({
            "request_id": "req-context-event",
            "thread_id": "thread-context-event",
            "message": "요청",
            "step_id": "intro",
            "customer_info": {"saved_data": {}, "current_form": {}},
            "field_registry": [],
            "context_scope": {
                "schema_version": "3.0",
                "session_id": 1,
                "thread_id": "thread-context-event",
            },
            "conversation_context": context,
        })
        planner_start = next(
            event for event in sink.runtime_events
            if event.stage is RuntimeStage.PLANNER
            and event.status is RuntimeStatus.STARTED
        )
        self.assertEqual(planner_start.input["context"]["conversation"], context)
        events = list(sink.events)
        input_guardrail_index = next(
            index
            for index, event in enumerate(events)
            if isinstance(event, SecurityDecisionTraceEvent)
            and event.boundary == "input_guardrail"
        )
        planner_start_index = events.index(planner_start)
        validation_start_index = next(
            index
            for index, event in enumerate(events)
            if isinstance(event, RuntimeTraceEvent)
            and event.stage is RuntimeStage.PLAN_VALIDATION
            and event.status is RuntimeStatus.STARTED
        )
        validation_end_index = next(
            index
            for index, event in enumerate(events)
            if isinstance(event, RuntimeTraceEvent)
            and event.stage is RuntimeStage.PLAN_VALIDATION
            and event.status is not RuntimeStatus.STARTED
        )
        planner_end_index = next(
            index
            for index, event in enumerate(events)
            if isinstance(event, RuntimeTraceEvent)
            and event.stage is RuntimeStage.PLANNER
            and event.status is not RuntimeStatus.STARTED
        )
        self.assertLess(input_guardrail_index, planner_start_index)
        self.assertLess(planner_start_index, validation_start_index)
        self.assertLess(validation_start_index, validation_end_index)
        self.assertLess(validation_end_index, planner_end_index)

if __name__ == "__main__":
    unittest.main()
