from __future__ import annotations

import importlib.util
import os
import unittest
from unittest.mock import AsyncMock, patch


AGENT_RUNTIME_AVAILABLE = all(
    importlib.util.find_spec(module) is not None
    for module in ("langchain_core", "langgraph")
)


def configure_disposable_runtime() -> None:
    os.environ.setdefault("LLM_PROVIDER", "gemini")
    os.environ.setdefault("EMBEDDING_PROVIDER", "gemini")
    os.environ.setdefault("GOOGLE_API_KEY", "f03-disposable-provider-key")
    os.environ.setdefault("DATABASE_URL", "postgresql://f03:f03-disposable-db@postgres/f03")
    os.environ.setdefault("AI_SERVICE_BEARER_TOKEN", "f03-disposable-bearer")
    os.environ.setdefault("AI_MASKING_PSEUDONYM_KEY", "f03-disposable-masking-key-at-least-32-bytes")


@unittest.skipUnless(AGENT_RUNTIME_AVAILABLE, "Agent runtime image에서 실행")
class InputAssistBindingTests(unittest.TestCase):
    def state(self, bindings):
        configure_disposable_runtime()
        from agents.input_assist import service

        state = {
            "step_id": "financial-goals", "message": "승인된 가구 평균값으로 채워줘",
            "customer_info": {"current_form": {"goal_periods": [{
                "id": 10, "phase": "before", "from_age": 40, "to_age": 60,
                "items": [{"id": 11, "name": "생활비", "amount": None}],
            }]}},
            "task_input": {"bindings": bindings},
            "extraction": {"request": "fill_defaults", "changes": [], "questions": []},
            "questions": [],
        }
        state["field_refs"], state["item_refs"], _ = service._form_context(state)
        return state

    def test_approved_bound_value_bypasses_model_resolution_and_review(self):
        state = self.state({"reference_id": "approved-reference", "value": 2500000, "unit": "KRW"})
        from agents.input_assist import service

        proposal = {
            "section": "goal_periods", "path": "items[].amount", "operation": "update",
            "item_id": 11, "parent_item_id": 10, "policy_value": 2500000,
        }
        with patch.object(service, "build_bound_reference_proposals", return_value=[proposal]) as build:
            result = service._validate(state)
        self.assertEqual(result["candidates"][0]["change"]["value"], 2500000)
        self.assertEqual(result["candidates"][0]["change"]["unit"], "KRW")
        self.assertEqual(result["review_candidates"], [])
        self.assertEqual(build.call_args.kwargs["bindings"], state["task_input"]["bindings"])
        self.assertTrue(build.call_args.kwargs["user_requested_change"])

    def test_unapproved_reference_does_not_become_a_generated_value(self):
        state = self.state({"reference_id": "unapproved", "value": 9999999, "unit": "KRW"})
        from agents.input_assist import service

        result = service._validate(state)
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["review_candidates"], [])
        self.assertIn("연결할 수 없어", result["questions"][0])


@unittest.skipUnless(AGENT_RUNTIME_AVAILABLE, "Agent runtime image에서 실행")
class QABindingTests(unittest.IsolatedAsyncioTestCase):
    async def test_bound_reference_still_allows_explanation_retrieval(self):
        configure_disposable_runtime()
        from agents.qa import service as qa

        state = {
            "questions": [{"question": "이 기준값의 의미와 배경을 설명해줘", "need_retrieval": True, "query": "기준값 의미와 배경"}],
            "task_input": {"bindings": {"value": 2500000, "unit": "KRW", "source_id": "approved-statistics-2026"}},
        }
        with patch.object(qa, "_search", AsyncMock(return_value="승인 기준값은 가구 통계입니다.")) as search:
            result = await qa._retrieve(state)
        search.assert_awaited_once_with("기준값 의미와 배경")
        self.assertFalse(result["official_only"])
        self.assertEqual(result["evidence"][0]["reference"], "승인 기준값은 가구 통계입니다.")


if __name__ == "__main__":
    unittest.main()
