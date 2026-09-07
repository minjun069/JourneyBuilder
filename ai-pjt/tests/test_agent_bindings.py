from __future__ import annotations

import importlib.util
import os
import unittest


AGENT_RUNTIME_AVAILABLE = all(
    importlib.util.find_spec(module) is not None
    for module in ("langchain_core", "langgraph")
)


def configure_disposable_runtime() -> None:
    os.environ.setdefault("LLM_PROVIDER", "gemini")
    os.environ.setdefault("EMBEDDING_PROVIDER", "gemini")
    os.environ.setdefault("GOOGLE_API_KEY", "f03-disposable-provider-key")
    os.environ.setdefault(
        "DATABASE_URL",
        "postgresql://f03:f03-disposable-db@postgres/f03",
    )
    os.environ.setdefault("AI_SERVICE_BEARER_TOKEN", "f03-disposable-bearer")


@unittest.skipUnless(AGENT_RUNTIME_AVAILABLE, "Agent runtime image에서 실행")
class InputAssistBindingTests(unittest.IsolatedAsyncioTestCase):
    async def test_bound_value_bypasses_model_resolution_and_review(self) -> None:
        configure_disposable_runtime()
        from agents import input_assist

        state = {
            "identification": [{
                "section": "financial_goals",
                "path": "monthly_target_expense",
                "operation": "set",
                "excerpt": "그 값으로 목표치를 채워줘",
            }],
            "cur_index": 0,
            "classification": "fill_avg",
            "validation_reason": "",
            "field_registry": [{
                "section": "financial_goals",
                "path": "monthly_target_expense",
                "unit": "KRW",
            }],
            "bound_inputs": {
                "value": 2_500_000,
                "unit": "KRW",
                "source_id": "approved-statistics-2026",
            },
            "partial_results": [],
        }

        resolved = await input_assist._resolve(state)
        state.update(resolved)
        reviewed = await input_assist._error_check_2(state)

        self.assertEqual(resolved["resolution"], 2_500_000)
        self.assertEqual(reviewed["partial_results"][0]["resolution"], 2_500_000)
        self.assertEqual(reviewed["partial_results"][0]["bound_unit"], "KRW")

    async def test_fill_avg_without_binding_returns_no_generated_value(self) -> None:
        configure_disposable_runtime()
        from agents import input_assist

        state = {
            "identification": [{
                "section": "financial_goals",
                "path": "monthly_target_expense",
                "operation": "set",
                "excerpt": "평균값으로 채워줘",
            }],
            "cur_index": 0,
            "classification": "fill_avg",
            "validation_reason": "",
            "field_registry": [],
            "bound_inputs": {},
            "partial_results": [],
        }

        resolved = await input_assist._resolve(state)
        state.update(resolved)
        reviewed = await input_assist._error_check_2(state)

        self.assertIsNone(resolved["resolution"])
        self.assertFalse(reviewed["partial_results"][0]["success"])
        self.assertIn("평균값을 생성하지 않음", reviewed["partial_results"][0]["reason"])


@unittest.skipUnless(AGENT_RUNTIME_AVAILABLE, "Agent runtime image에서 실행")
class QABindingTests(unittest.IsolatedAsyncioTestCase):
    async def test_bound_reference_skips_a_second_retrieval_decision(self) -> None:
        configure_disposable_runtime()
        from agents import qa

        decision = await qa._decide({
            "bound_inputs": {
                "value": 2_500_000,
                "unit": "KRW",
                "source_id": "approved-statistics-2026",
            }
        })
        self.assertEqual(decision, {"need_retrieval": False})


if __name__ == "__main__":
    unittest.main()
