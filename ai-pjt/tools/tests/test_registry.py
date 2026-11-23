import asyncio
import json
import unittest
from pathlib import Path
from time import monotonic

from tools.catalog import build_default_registry, default_tool_definitions
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
            knowledge_search=knowledge_search,
            audit_sink=self.events.append,
        )

    async def test_schemas_are_filtered_by_agent(self):
        names = {
            item["name"]
            for item in self.registry.schemas_for("qa")
        }
        self.assertEqual(
            names,
            {"search_financial_knowledge", "get_approved_reference_value", "get_form_field_spec"},
        )
        schemas = self.registry.schemas_for("qa")
        self.assertTrue(all("input_schema" in item for item in schemas))
        self.assertTrue(all("function" not in item for item in schemas))

    async def test_tool_allowlists_use_only_shared_step_registry_agents(self):
        repository_root = Path(__file__).resolve().parents[3]
        step_registry = json.loads((
            repository_root
            / "contract-fixtures/ai-lifecycle/v2/step-registry.json"
        ).read_text(encoding="utf-8"))
        registered_agents = set(step_registry["agents"])
        definitions = default_tool_definitions()

        self.assertEqual(len(definitions), 3)
        for definition in definitions:
            with self.subTest(tool=definition.name):
                self.assertTrue(definition.allowed_agents)
                self.assertLessEqual(definition.allowed_agents, registered_agents)

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
            "get_form_field_spec",
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
        result = await self.registry.invoke("get_form_field_spec", {}, context)
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

    async def test_search_uses_injected_approved_retriever(self):
        result = await self.registry.invoke(
            "search_financial_knowledge",
            {"query": "연금 수령 시기", "top_k": 3},
            self.context,
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.data["results"][0]["document_id"], "pension-guide")

    async def test_approved_reference_value_uses_exact_policy_criteria(self):
        result = await self.registry.invoke(
            "get_approved_reference_value",
            {
                "reference_id": "debt_service_ratio_max",
                "age": 64,
                "period": "annual",
                "population": "retirewise_kr_households",
            },
            self.context,
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.data["reference"]["value"], 10)
        self.assertEqual(result.data["reference"]["unit"], "percent")
        self.assertEqual(result.data["reference"]["comparator"], "lt")
        self.assertEqual(
            result.data["reference"]["calculation_policy_version"],
            "2026-09-12.v1",
        )

    async def test_approved_reference_value_does_not_estimate_a_missing_match(self):
        result = await self.registry.invoke(
            "get_approved_reference_value",
            {
                "reference_id": "debt_service_ratio_max",
                "age": 64,
                "period": "monthly",
                "population": "retirewise_kr_households",
            },
            self.context,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, ToolErrorCode.NOT_FOUND)

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
            allowed_task_types=frozenset({"answer_question"}),
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
        result = await self.registry.invoke("get_form_field_spec", {}, context)
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
            allowed_task_types=frozenset({"answer_question"}),
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
                allowed_task_types=frozenset({"answer_question"}),
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
