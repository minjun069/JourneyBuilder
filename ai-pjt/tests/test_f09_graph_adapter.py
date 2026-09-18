from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from graph.builder import build_graph
from tests.instrumentation import fake_instrumentation
from tests.test_graph_execution import FakePlanner, all_agent_graphs
from tools.catalog import build_default_registry
from tools.graph_adapter import build_graph_tool_executors
from tools.runtime import FunctionToolRuntime


def _graph():
    runtime = FunctionToolRuntime(build_default_registry())
    return build_graph_tool_executors(runtime)


def _state(message: str) -> dict:
    return {
        "request_id": "req-f09-adapter",
        "message": message,
        "step_id": "basic-info",
        "customer_info": {"saved_data": {}, "current_form": {}},
    }


def _reference_plan(*, period: str = "annual") -> dict:
    return {
        "tasks": [
            {
                "id": "reference",
                "type": "lookup_reference_value",
                "description": "45세 부채상환비율 승인 기준 조회",
                "depends_on": [],
                "bindings": {},
                "arguments": {
                    "reference_id": "debt_service_ratio_max",
                    "age": 45,
                    "period": period,
                    "population": "retirewise_kr_households",
                },
            },
            {
                "id": "explain",
                "type": "answer_question",
                "description": "승인 기준과 출처 설명",
                "depends_on": ["reference"],
                "bindings": {
                    "value": {"task_id": "reference", "path": "value"},
                    "unit": {"task_id": "reference", "path": "unit"},
                    "period": {"task_id": "reference", "path": "period"},
                    "population": {"task_id": "reference", "path": "population"},
                    "source_id": {"task_id": "reference", "path": "source_id"},
                    "as_of": {"task_id": "reference", "path": "as_of"},
                    "calculation_policy_version": {
                        "task_id": "reference",
                        "path": "calculation_policy_version",
                    },
                },
            },
        ]
    }


class F09GraphAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_approved_reference_value_keeps_policy_provenance_in_binding(self):
        planner = FakePlanner(_reference_plan())
        agents = all_agent_graphs()
        graph = build_graph(
            None,
            instrumentation=fake_instrumentation()[0],
            planner=planner,
            agent_graphs=agents,
            tool_executors=_graph(),
            response_composer=None,
        )

        result = await graph.ainvoke(_state("45세 부채상환 기준을 알려줘"))

        artifact = result["task_results"]["task-1"]["artifact"]
        self.assertEqual(artifact["value"], 25)
        self.assertEqual(artifact["source_version"], "1.1.0")
        self.assertEqual(artifact["calculation_policy_version"], "2026-09-12.v1")
        bound = agents["qa"].calls[0]["task_input"]["bindings"]
        self.assertEqual(bound["value"], 25)
        self.assertEqual(bound["unit"], "percent")
        self.assertEqual(bound["source_id"], "retirewise-household-financial-indicator-policy")
        self.assertEqual(bound["as_of"], "2026-09-07")
        self.assertEqual(bound["calculation_policy_version"], "2026-09-12.v1")

    async def test_missing_approved_reference_blocks_dependent_task(self):
        agents = all_agent_graphs()
        graph = build_graph(
            None,
            instrumentation=fake_instrumentation()[0],
            planner=FakePlanner(_reference_plan(period="monthly")),
            agent_graphs=agents,
            tool_executors=_graph(),
            response_composer=None,
        )

        result = await graph.ainvoke(_state("45세 부채상환 월 기준을 알려줘"))

        self.assertEqual(
            result["task_results"]["task-1"]["reason_code"],
            "tool_resource_not_found",
        )
        self.assertEqual(result["task_results"]["task-2"]["status"], "blocked")
        self.assertEqual(agents["qa"].calls, [])

    async def test_reference_provider_cannot_substitute_a_different_requested_id(self):
        runtime = SimpleNamespace(invoke=AsyncMock(return_value=SimpleNamespace(
            ok=True,
            data={"reference": {
                "reference_id": "household_monthly_expense_food_living_avg",
                "age": 45,
                "value": 449_000,
                "unit": "KRW",
                "period": "monthly",
                "population": "kr_general_households_1plus",
                "comparator": "eq",
                "source_id": "kostat-household-expenses-2025",
                "source_version": "2025",
                "as_of": "2025-12-31",
                "calculation_policy_version": "2026-09-12.v1",
            }},
        )))
        executor = build_graph_tool_executors(runtime)["lookup_reference_value"]
        result = await executor({
            "task": {
                "id": "task-1",
                "type": "lookup_reference_value",
                "tool_name": "get_approved_reference_value",
                "authorized_agent": "qa",
                "arguments": {
                    "reference_id": "household_monthly_expense_housing_avg",
                    "age": 45,
                    "period": "monthly",
                    "population": "kr_general_households_1plus",
                },
            },
        })

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason_code"], "reference_contract_mismatch")
        self.assertNotIn("artifact", result)


if __name__ == "__main__":
    unittest.main()
