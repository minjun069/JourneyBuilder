from __future__ import annotations

import unittest

from tools.contracts import ToolErrorCode
from tools.runtime import (
    RuntimeToolRequest,
    ToolCallBudget,
    build_production_tool_runtime,
)


def runtime_payload() -> dict:
    return {
        "request_id": "req-search",
        "task": {
            "id": "task-search",
            "type": "answer_question",
            "step_id": "intro",
            "executor_kind": "agent",
            "produces": "text_response",
            "route": {
                "executor_kind": "agent",
                "executor": "qa",
            },
        },
        "step_id": "intro",
        "tool_call_budget": ToolCallBudget(limit=1),
        "tool_audit_sink": lambda _event: None,
    }


class KnowledgeSearchBindingTests(unittest.IsolatedAsyncioTestCase):
    async def test_production_signature_preserves_machine_provenance(self) -> None:
        calls = []

        async def approved_search(query: str, top_k: int) -> list[dict]:
            calls.append((query, top_k))
            return [{
                "source_id": "approved-source",
                "version": "1.0.0",
                "chunk_id": "chunk-1",
                "source_locators": ["https://example.test/source"],
                "text": "승인된 근거",
                "score": 0.8,
                "rank": 1,
            }]

        runtime = build_production_tool_runtime(knowledge_search=approved_search)
        result = await runtime.invoke(
            RuntimeToolRequest(
                tool_name="search_financial_knowledge",
                arguments={"query": "연금 수령 시기", "top_k": 3},
                agent="qa",
            ),
            runtime_payload(),
        )

        self.assertTrue(result.ok)
        self.assertEqual(calls, [("연금 수령 시기", 3)])
        self.assertEqual(result.data["results"][0]["source_id"], "approved-source")
        self.assertEqual(result.data["results"][0]["chunk_id"], "chunk-1")

    async def test_missing_or_failing_retriever_is_a_tool_error(self) -> None:
        async def unavailable_search(_query: str, _top_k: int) -> list[dict]:
            raise RuntimeError("index unavailable")

        runtime = build_production_tool_runtime(knowledge_search=unavailable_search)
        result = await runtime.invoke(
            RuntimeToolRequest(
                tool_name="search_financial_knowledge",
                arguments={"query": "연금"},
                agent="qa",
            ),
            runtime_payload(),
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, ToolErrorCode.DEPENDENCY_UNAVAILABLE)
        self.assertTrue(result.error.retryable)

    async def test_registry_rejects_out_of_range_top_k_before_search(self) -> None:
        calls = 0

        async def approved_search(_query: str, _top_k: int) -> list[dict]:
            nonlocal calls
            calls += 1
            return []

        runtime = build_production_tool_runtime(knowledge_search=approved_search)
        result = await runtime.invoke(
            RuntimeToolRequest(
                tool_name="search_financial_knowledge",
                arguments={"query": "연금", "top_k": 11},
                agent="qa",
            ),
            runtime_payload(),
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, ToolErrorCode.INVALID_ARGUMENTS)
        self.assertEqual(calls, 0)


if __name__ == "__main__":
    unittest.main()
