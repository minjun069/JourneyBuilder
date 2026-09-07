from __future__ import annotations

import importlib.util
import unittest


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
                    "value": 2_500_000,
                    "unit": "KRW",
                    "period": "month",
                    "population": "40대",
                    "source_id": "approved-statistics-2026",
                    "as_of": "2026-06-30",
                },
            }

        graph = build_graph(
            None,
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

    async def test_invalid_plan_never_calls_an_agent(self) -> None:
        from graph.builder import SAFE_PLAN_FAILURE_RESPONSE, build_graph

        agents = all_agent_graphs()
        graph = build_graph(
            None,
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
        self.assertEqual(result["output"]["response"], SAFE_PLAN_FAILURE_RESPONSE)
        self.assertEqual(sum(len(agent.calls) for agent in agents.values()), 0)


if __name__ == "__main__":
    unittest.main()
