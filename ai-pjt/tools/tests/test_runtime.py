from __future__ import annotations

import unittest

from tools.catalog import build_default_registry
from tools.contracts import ToolErrorCode
from tools.runtime import FunctionToolRuntime, RuntimeToolRequest, ToolCallBudget


class FunctionToolRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def payload(self, budget: ToolCallBudget) -> dict:
        return {
            "request_id": "req-1",
            "task": {
                "id": "task-1",
                "type": "answer_question",
                "step_id": "basic-info",
                "executor_kind": "agent",
                "produces": "text_response",
                "route": {
                    "executor_kind": "agent",
                    "executor": "qa",
                },
            },
            "step_id": "basic-info",
            "tool_call_budget": budget,
            "tool_audit_sink": self.events.append,
        }

    def setUp(self) -> None:
        self.events = []

    async def test_runtime_injects_ids_source_snapshot_and_budget(self) -> None:
        payload = self.payload(ToolCallBudget(limit=1))
        runtime = FunctionToolRuntime(build_default_registry())

        result = await runtime.invoke(
            RuntimeToolRequest(
                tool_name="get_form_field_spec",
                arguments={},
                agent="qa",
            ),
            payload,
        )

        self.assertTrue(result.ok)
        self.assertEqual(payload["tool_call_budget"].used, 1)
        self.assertEqual(self.events[0].request_id, "req-1")
        self.assertFalse(hasattr(self.events[0], "plan_id"))
        self.assertEqual(self.events[0].task_id, "task-1")
        self.assertEqual(self.events[0].call_source, "graph_task")
        self.assertEqual(self.events[0].decision, "allowed")

    async def test_budget_exhaustion_fails_before_registry_invocation(self) -> None:
        runtime = FunctionToolRuntime(build_default_registry())
        result = await runtime.invoke(
            RuntimeToolRequest(
                tool_name="get_form_field_spec",
                arguments={},
                agent="qa",
            ),
            self.payload(ToolCallBudget(limit=0)),
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, ToolErrorCode.BUDGET_EXCEEDED)

    async def test_claimed_agent_mismatch_is_denied_before_budget(self) -> None:
        payload = self.payload(ToolCallBudget(limit=1))
        payload["task"] = {
            "id": "task-1",
            "type": "lookup_reference_value",
            "step_id": "basic-info",
            "executor_kind": "tool",
            "produces": "reference_value",
            "capability": "lookup_reference_value",
            "tool_name": "get_approved_reference_value",
            "authorized_agent": "qa",
            "route": {
                "executor_kind": "tool",
                "executor": "lookup_reference_value",
            },
        }
        runtime = FunctionToolRuntime(build_default_registry())

        result = await runtime.invoke(
            RuntimeToolRequest(
                tool_name="get_approved_reference_value",
                arguments={
                    "reference_id": "debt_service_ratio_max",
                    "age": 45,
                    "period": "annual",
                    "population": "retirewise_kr_households",
                },
                agent="chart_interpret",
            ),
            payload,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, ToolErrorCode.CONTEXT_MISMATCH)
        self.assertEqual(payload["tool_call_budget"].used, 0)
        self.assertEqual(self.events[-1].decision, "denied")

    async def test_missing_trusted_task_binding_fails_closed(self) -> None:
        payload = self.payload(ToolCallBudget())
        payload.pop("request_id")
        runtime = FunctionToolRuntime(build_default_registry())

        result = await runtime.invoke(
            RuntimeToolRequest(
                tool_name="get_form_field_spec",
                arguments={},
                agent="qa",
            ),
            payload,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, ToolErrorCode.INTERNAL_ERROR)


if __name__ == "__main__":
    unittest.main()
