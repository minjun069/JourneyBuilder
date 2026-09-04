import asyncio
import unittest
from time import monotonic

from tools.catalog import build_default_registry
from tools.contracts import (
    FunctionToolDefinition,
    ToolErrorCode,
    ToolExecutionContext,
)
from tools.registry import FunctionToolRegistry


FIELD_SPEC = {
    "step_id": "basic-info",
    "section": "basic_info",
    "path": "monthly_income",
    "value_type": "integer",
    "unit": "KRW",
    "constraints": {"min": 0, "period": "month"},
    "cardinality": "single",
    "sensitivity": "financial_sensitive",
    "writable_by_ai": True,
    "nullable": True,
    "label": "세후 월 소득",
    "operations": ["set", "update"],
}


async def knowledge_search(query, top_k):
    return [
        {
            "document_id": "pension-guide",
            "version": "2026-01",
            "excerpt": query,
            "score": 0.9,
        }
    ][:top_k]


class FunctionToolRegistryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.events = []
        self.registry = build_default_registry()
        self.context = ToolExecutionContext(
            request_id="ai_req_test",
            step_id="basic-info",
            agent="qa",
            field_registry=(FIELD_SPEC,),
            dashboard_metrics={
                "net_worth": {
                    "metric_id": "net_worth",
                    "value": 120_000_000,
                    "unit": "KRW",
                    "source_report": "asset_statement",
                    "report_version": 2,
                    "generated_at": "2026-09-04T00:00:00Z",
                    "status": "ready",
                }
            },
            retirement_scenarios={
                "baseline": {
                    "scenario_id": "baseline",
                    "status": "ready",
                    "version": 1,
                    "generated_at": "2026-09-04T00:00:00Z",
                    "assumptions": {"retirement_age": 60},
                    "metrics": {"shortfall": 10_000_000},
                }
            },
            available_reports=(
                {
                    "report_type": "asset_statement",
                    "version": 2,
                    "status": "ready",
                    "generated_at": "2026-09-04T00:00:00Z",
                },
            ),
            section_completion={"basic_info": True, "financial_assets": False},
            knowledge_search=knowledge_search,
            audit_sink=self.events.append,
        )

    async def test_schemas_are_filtered_by_agent(self):
        names = {
            item["name"]
            for item in self.registry.schemas_for("chart_interpret")
        }
        self.assertEqual(
            names,
            {"get_dashboard_metric", "get_retirement_scenario", "list_available_reports"},
        )
        schemas = self.registry.schemas_for("chart_interpret")
        self.assertTrue(all("input_schema" in item for item in schemas))
        self.assertTrue(all("function" not in item for item in schemas))

    async def test_unknown_tool_returns_structured_error(self):
        result = await self.registry.invoke("unknown", {}, self.context)
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, ToolErrorCode.UNKNOWN_TOOL)

    async def test_trusted_context_identifiers_are_not_model_arguments(self):
        result = await self.registry.invoke(
            "get_form_field_spec",
            {"field_path": "monthly_income", "session_id": 99},
            self.context,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, ToolErrorCode.INVALID_ARGUMENTS)

    async def test_non_object_arguments_return_structured_error(self):
        result = await self.registry.invoke(  # type: ignore[arg-type]
            "get_step_completion_status",
            [],
            self.context,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, ToolErrorCode.INVALID_ARGUMENTS)

    async def test_forbidden_agent_is_rejected_before_execution(self):
        context = ToolExecutionContext(
            request_id="ai_req_test",
            step_id="basic-info",
            agent="fallback",
            audit_sink=self.events.append,
        )
        result = await self.registry.invoke("get_dashboard_metric", {"metric_id": "x"}, context)
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, ToolErrorCode.FORBIDDEN)

    async def test_get_form_field_spec_is_scoped_to_current_step(self):
        result = await self.registry.invoke(
            "get_form_field_spec",
            {"field_path": "monthly_income"},
            self.context,
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.data["step_id"], "basic-info")
        self.assertEqual(result.data["fields"][0]["path"], "monthly_income")

    async def test_get_dashboard_metric_returns_only_ready_report_metric(self):
        result = await self.registry.invoke(
            "get_dashboard_metric",
            {"metric_id": "net_worth", "report_version": 2},
            self.context,
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.data["metric"]["unit"], "KRW")

    async def test_get_retirement_scenario_fails_explicitly_when_missing(self):
        result = await self.registry.invoke(
            "get_retirement_scenario",
            {"scenario_id": "not-saved"},
            self.context,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, ToolErrorCode.NOT_FOUND)

    async def test_list_reports_and_completion_do_not_return_financial_values(self):
        reports = await self.registry.invoke("list_available_reports", {}, self.context)
        completion = await self.registry.invoke("get_step_completion_status", {}, self.context)
        self.assertTrue(reports.ok)
        self.assertEqual(reports.data["reports"][0]["status"], "ready")
        self.assertTrue(completion.ok)
        self.assertEqual(completion.data["progress_pct"], 50)
        self.assertNotIn("value", completion.data)

    async def test_search_uses_injected_approved_retriever(self):
        result = await self.registry.invoke(
            "search_financial_knowledge",
            {"query": "연금 수령 시기", "top_k": 3},
            self.context,
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.data["results"][0]["document_id"], "pension-guide")

    async def test_timeout_is_returned_as_structured_error(self):
        async def slow_handler(_, __):
            await asyncio.sleep(0.05)
            return {"done": True}

        registry = FunctionToolRegistry()
        registry.register(FunctionToolDefinition(
            name="slow_tool",
            description="timeout test",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            output_schema={
                "type": "object",
                "properties": {"done": {"type": "boolean"}},
                "required": ["done"],
                "additionalProperties": False,
            },
            allowed_agents=frozenset({"qa"}),
            handler=slow_handler,
            timeout_seconds=0.001,
        ))
        result = await registry.invoke("slow_tool", {}, self.context)
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, ToolErrorCode.DEADLINE_EXCEEDED)

    async def test_expired_request_deadline_skips_handler(self):
        context = ToolExecutionContext(
            request_id="ai_req_expired",
            step_id="intro",
            agent="qa",
            deadline_at_monotonic=monotonic() - 1,
        )
        result = await self.registry.invoke("list_available_reports", {}, context)
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, ToolErrorCode.DEADLINE_EXCEEDED)

    async def test_invalid_handler_result_returns_structured_error(self):
        async def invalid_handler(_, __):
            return {"done": "not-a-boolean"}

        registry = FunctionToolRegistry()
        registry.register(FunctionToolDefinition(
            name="invalid_result_tool",
            description="invalid result test",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            output_schema={
                "type": "object",
                "properties": {"done": {"type": "boolean"}},
                "required": ["done"],
                "additionalProperties": False,
            },
            allowed_agents=frozenset({"qa"}),
            handler=invalid_handler,
        ))
        result = await registry.invoke("invalid_result_tool", {}, self.context)
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, ToolErrorCode.INVALID_RESULT)

    async def test_sync_handler_is_rejected_so_timeout_remains_enforceable(self):
        def sync_handler(_, __):
            return {"done": True}

        registry = FunctionToolRegistry()
        with self.assertRaises(TypeError):
            registry.register(FunctionToolDefinition(
                name="sync_tool",
                description="sync handler test",
                input_schema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                output_schema={
                    "type": "object",
                    "properties": {"done": {"type": "boolean"}},
                    "required": ["done"],
                    "additionalProperties": False,
                },
                allowed_agents=frozenset({"qa"}),
                handler=sync_handler,
            ))

    async def test_audit_event_excludes_raw_arguments_and_results(self):
        await self.registry.invoke(
            "search_financial_knowledge",
            {"query": "민감한 검색 원문", "top_k": 1},
            self.context,
        )
        event = self.events[-1].to_dict()
        self.assertNotIn("민감한 검색 원문", repr(event))
        self.assertNotIn("arguments", event)
        self.assertNotIn("result", event)


if __name__ == "__main__":
    unittest.main()
