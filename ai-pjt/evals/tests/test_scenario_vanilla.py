import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

EVALS = Path(__file__).resolve().parents[1]
sys.path.append(str(EVALS))
from scenario_inputs import prepare_turns, load_registry
from scenario_vanilla import VanillaConversation, raw_turns, provider_usage, user_text


class VanillaTests(unittest.TestCase):
    def test_raw_values_and_full_history_without_system_instruction(self):
        requests, events = [], []
        class Client:
            def __init__(self, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def post(self, url, headers, json):
                requests.append(deepcopy(json))
                data = {"candidates": [{"content": {"parts": [{"text": "답변"}]}, "finishReason": "STOP"}],
                        "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 2, "totalTokenCount": 7}}
                return SimpleNamespace(raise_for_status=lambda: None, json=lambda: data)
        observed = SimpleNamespace(record_operation=lambda **kw: events.append(kw))
        instrumentation = SimpleNamespace(bind=lambda **kw: observed)
        settings = SimpleNamespace(llm_temperature=0, gemini_base_url="https://example.invalid", llm_model="gemini-2.5-flash-lite", gemini_api_key="test")
        case = [{"step_id": "basic-info", "프로세스": "기본", "대화": [
            {"사용자": "교사입니다", "입력값": {"직업": "교사", "월 소득(원)": 0}},
            {"사용자": "앞서 말씀드린 내용을 기억하나요?", "입력값": {}}]}]
        async def run():
            chat = VanillaConversation(instrumentation, settings)
            for turn in raw_turns(case):
                await chat.ainvoke({**turn, "request_id": str(turn["ordinal"]), "thread_id": "test"})
            self.assertEqual(VanillaConversation(instrumentation, settings).history, [])
        with patch("httpx.AsyncClient", Client):
            asyncio.run(run())
        self.assertEqual([len(p["contents"]) for p in requests], [1, 3])
        self.assertEqual(set(requests[0]), {"contents", "generationConfig"})
        sent = requests[0]["contents"][0]["parts"][0]["text"]
        self.assertTrue(sent.startswith("교사입니다\n\n입력값:\n"))
        self.assertEqual(json.loads(sent.split("입력값:\n", 1)[1]), case[0]["대화"][0]["입력값"])
        self.assertEqual(requests[1]["contents"][1]["role"], "model")
        self.assertEqual(len(events), 4)

    def test_missing_usage_is_not_zero(self):
        self.assertEqual(provider_usage({}), {"usage_status": "unavailable"})

    def test_source_choices_and_cross_step_clear_preserved(self):
        data = [{"step_id": "basic-info", "프로세스": "기본", "대화": [{"사용자": "교사", "입력값": {"직업": "교사"}}]},
                {"step_id": "general", "프로세스": "대화", "대화": [{"사용자": "상환했습니다", "입력값": {"부채": []}}]}]
        registry = load_registry(EVALS.parents[1])
        with self.assertRaises(ValueError): prepare_turns(data, registry)
        turns = prepare_turns(data, registry, True)
        self.assertEqual(turns[0]["customer_info"]["saved_data"]["basic_info"]["job"], "교사")
        self.assertTrue(turns[0]["mapping_notes"])
        self.assertEqual(turns[1]["customer_info"]["saved_data"]["liabilities"], [])


if __name__ == "__main__":
    unittest.main()
