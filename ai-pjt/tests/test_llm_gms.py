from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agents.error_review import ReviewResult
from agents.input_assist import ResolveResult


class GMSAsyncAdapterTests(unittest.IsolatedAsyncioTestCase):
    def test_gms_proxy_disables_incompatible_async_streaming_client(self) -> None:
        from core.llm import _create_llm

        captured: dict[str, object] = {}

        class FakeGoogleModel:
            def __init__(self, **kwargs: object) -> None:
                captured.update(kwargs)

        fake_settings = SimpleNamespace(
            llm_provider="gemini",
            llm_model="gemini-test",
            llm_temperature=0.0,
            gemini_api_key="test-key",
            gemini_base_url="https://gms.example.com/gemini",
        )
        with (
            patch(
                "langchain_google_genai.ChatGoogleGenerativeAI",
                FakeGoogleModel,
            ),
            patch("core.llm.settings", fake_settings),
        ):
            _create_llm()

        self.assertIs(captured["disable_streaming"], True)
        self.assertIs(captured["streaming"], True)
        self.assertEqual(captured["temperature"], 0.0)
        self.assertEqual(captured["transport"], "rest")

    def test_agent_tool_schemas_do_not_expose_unbounded_any_value_unions(self) -> None:
        resolve_schema = ResolveResult.model_json_schema()
        review_schema = ReviewResult.model_json_schema()

        self.assertEqual(resolve_schema["properties"]["value_json"]["type"], "string")
        review_item = review_schema["$defs"]["ReviewItem"]["properties"]
        self.assertEqual(review_item["current_value_json"]["type"], "string")
        self.assertNotIn("anyOf", review_item["current_value_json"])

    async def test_async_generation_uses_sync_rest_client_in_worker_thread(self) -> None:
        from core.llm import _generate_with_sync_rest_client

        model = SimpleNamespace(_generate=lambda messages, stop, manager, **kwargs: {
            "messages": messages,
            "stop": stop,
            "manager": manager,
            "kwargs": kwargs,
        })
        with patch("core.llm.asyncio.to_thread", return_value={"ok": True}) as to_thread:
            result = await _generate_with_sync_rest_client(
                model,
                ["message"],
                ["stop"],
                tools=["tool"],
            )

        self.assertEqual(result, {"ok": True})
        to_thread.assert_called_once_with(
            model._generate,
            ["message"],
            ["stop"],
            None,
            tools=["tool"],
        )


if __name__ == "__main__":
    unittest.main()
