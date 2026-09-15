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


class FakeClassifier:
    def __call__(self, *, task: dict, candidates: tuple[str, ...]) -> dict:
        agent = {
            "answer_question": "qa",
            "answer_dashboard_metric": "qa",
            "propose_form_change": "input_assist",
        }.get(task["type"], "fallback")
        return {
            "status": "matched",
            "agent": agent if agent in candidates else "fallback",
        }


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
            "fallback",
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
    async def test_explanation_questions_do_not_fall_back_on_model_adapter_outage(
        self,
    ) -> None:
        from graph.builder import build_graph

        def unavailable_planner(**_kwargs: object) -> dict:
            raise RuntimeError("simulated planner outage")

        def unavailable_classifier(**_kwargs: object) -> dict:
            raise RuntimeError("simulated classifier outage")

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
                    classifier=unavailable_classifier,
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
                self.assertEqual(len(agents["fallback"].calls), 0)
                planner_terminal = next(
                    event
                    for event in sink.runtime_events
                    if event.stage.value == "planner" and event.status.value == "succeeded"
                )
                components = {
                    item["component"]
                    for item in planner_terminal.output["diagnostics"]
                }
                self.assertEqual(components, {"planner", "router_classifier"})

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
            classifier=FakeClassifier(),
            agent_graphs=agents,
            tool_executors={"lookup_reference_value": lookup},
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

    async def test_invalid_plan_calls_only_sanitized_fallback(self) -> None:
        from graph.builder import build_graph

        agents = all_agent_graphs()
        graph = build_graph(
            None,
            instrumentation=fake_instrumentation()[0],
            planner=FakePlanner({"tasks": []}),
            classifier=FakeClassifier(),
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
        self.assertEqual(result["output"]["response"], "fallback 응답")
        self.assertEqual(len(agents["fallback"].calls), 1)
        self.assertEqual(agents["fallback"].calls[0]["customer_info"], {})
        self.assertEqual(
            sum(len(agent.calls) for name, agent in agents.items() if name != "fallback"),
            0,
        )

    async def test_invalid_plan_reaches_compiled_fallback_graph(self) -> None:
        from agents import fallback
        from graph.builder import build_graph

        agents = all_agent_graphs()
        agents["fallback"] = fallback.graph
        graph = build_graph(
            None,
            instrumentation=fake_instrumentation()[0],
            planner=FakePlanner({"tasks": []}),
            classifier=FakeClassifier(),
            agent_graphs=agents,
        )

        result = await graph.ainvoke({
            "request_id": "req-compiled-fallback",
            "message": "요청",
            "step_id": "intro",
            "customer_info": {"saved_data": {}, "current_form": {}},
            "field_registry": [],
        })

        self.assertTrue(result["plan_issues"])
        self.assertEqual(
            result["output"]["response"],
            fallback.MESSAGES["plan_invalid"],
        )

if __name__ == "__main__":
    unittest.main()
