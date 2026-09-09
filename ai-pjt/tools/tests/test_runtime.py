from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from tools.catalog import build_default_registry
from tools.contracts import ToolErrorCode
from tools.runtime import FunctionToolRuntime, RuntimeToolRequest, ToolCallBudget


class FunctionToolRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def payload(self, budget: ToolCallBudget) -> dict:
        return {
            "request_id": "req-1",
            "plan_id": "plan-req-1",
            "task": {"id": "task-1"},
            "step_id": "basic-info",
            "deadline_at": (
                datetime.now(timezone.utc) + timedelta(seconds=30)
            ).isoformat(),
            "tool_context": {
                "field_specs": [],
                "dashboard_metrics": {},
                "retirement_scenarios": {},
                "available_reports": [],
                "section_completion": {"basic_info": True},
            },
            "tool_call_budget": budget,
        }

    async def test_runtime_injects_ids_source_snapshot_and_budget(self) -> None:
        events = []
        payload = self.payload(ToolCallBudget(limit=1))
        payload["tool_audit_sink"] = events.append
        runtime = FunctionToolRuntime(build_default_registry())

        result = await runtime.invoke(
            RuntimeToolRequest(
                tool_name="get_step_completion_status",
                arguments={},
                agent="qa",
            ),
            payload,
        )

        self.assertTrue(result.ok)
        self.assertEqual(payload["tool_call_budget"].used, 1)
        self.assertEqual(events[0].request_id, "req-1")
        self.assertEqual(events[0].plan_id, "plan-req-1")
        self.assertEqual(events[0].task_id, "task-1")
        self.assertEqual(events[0].call_source, "graph_task")

    async def test_budget_exhaustion_fails_before_registry_invocation(self) -> None:
        runtime = FunctionToolRuntime(build_default_registry())
        result = await runtime.invoke(
            RuntimeToolRequest(
                tool_name="get_step_completion_status",
                arguments={},
                agent="qa",
            ),
            self.payload(ToolCallBudget(limit=0)),
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, ToolErrorCode.BUDGET_EXCEEDED)

    async def test_missing_trusted_task_binding_fails_closed(self) -> None:
        payload = self.payload(ToolCallBudget())
        payload.pop("plan_id")
        runtime = FunctionToolRuntime(build_default_registry())

        result = await runtime.invoke(
            RuntimeToolRequest(
                tool_name="get_step_completion_status",
                arguments={},
                agent="qa",
            ),
            payload,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, ToolErrorCode.INTERNAL_ERROR)


if __name__ == "__main__":
    unittest.main()
