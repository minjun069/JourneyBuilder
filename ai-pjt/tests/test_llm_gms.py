from __future__ import annotations

from types import SimpleNamespace
import json
import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from agents.error_review import ReviewResult
from agents.input_assist.model import ExtractResult


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

    def test_agent_json_responses_preserve_values_without_provider_schema_conversion(self) -> None:
        from core.json_response import json_response_chain

        values = [3200000, True, {"unit": "월", "count": 1}, [1, 2], None]
        payloads = (
            (ExtractResult, {"request": "change", "changes": [
                {"field_ref": f"f{index}", "item_ref": None, "operation": "set", "value": value}
                for index, value in enumerate(values)
            ], "questions": []}),
            (ReviewResult, {"items": [
                {"field_ref": f"f{index}", "reason": "입력 확인", "suggested_value": value}
                for index, value in enumerate(values)
            ]}),
        )
        for schema, payload in payloads:
            options = {}

            class FakeModel:
                def bind(self, **kwargs):
                    options.update(kwargs)
                    return RunnableLambda(lambda _: AIMessage(content=json.dumps(payload)))

            with patch("core.json_response.settings", SimpleNamespace(llm_provider="gemini")):
                result = json_response_chain(FakeModel(), schema).invoke([])
            self.assertEqual(options, {"response_mime_type": "application/json"})
            actual = [item.value for item in result.changes] if schema is ExtractResult else [item.suggested_value for item in result.items]
            self.assertEqual(actual, values)
            self.assertEqual([type(value) for value in actual], [type(value) for value in values])

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
