"""Plain Gemini REST chat: original turns, all prior replies, no system prompt/tools.

Only observability crosses the project's masker. The model receives the authored
scenario values unchanged, as explicitly requested for the vanilla comparison.
"""
import asyncio
from copy import deepcopy
import json
from time import perf_counter
from uuid import uuid4


def raw_turns(data):
    turns = []
    for step in data:
        for number, item in enumerate(step["대화"], 1):
            raw = {"step_id": step["step_id"], "프로세스": step["프로세스"], **deepcopy(item)}
            turns.append({"ordinal": len(turns) + 1, "step_id": step["step_id"],
                          "process": step["프로세스"], "step_turn": number,
                          "message": item["사용자"], "input_delta": deepcopy(item["입력값"]),
                          "raw_turn": raw, "customer_info": {}, "field_registry": [], "mapping_notes": []})
    return turns


def provider_usage(data):
    usage = data.get("usageMetadata", {})
    mapping = {"promptTokenCount": "input_tokens", "candidatesTokenCount": "output_tokens",
               "totalTokenCount": "total_tokens", "cachedContentTokenCount": "cached_input_tokens",
               "thoughtsTokenCount": "reasoning_output_tokens"}
    values = {target: usage[key] for key, target in mapping.items()
              if isinstance(usage.get(key), int) and not isinstance(usage[key], bool)}
    values["usage_status"] = "reported" if {"input_tokens", "output_tokens"} <= values.keys() else "unavailable"
    return values


def user_text(raw):
    # A normal chat message, with the authored form values pasted underneath.
    # Sending the entire transport JSON encourages JSON continuation instead of
    # a conversational reply; step IDs are retained locally, not instructions.
    return raw["사용자"] + "\n\n입력값:\n" + json.dumps(raw["입력값"], ensure_ascii=False)


class VanillaConversation:
    def __init__(self, instrumentation, settings):
        self.instrumentation, self.settings = instrumentation, settings
        self.history = []

    async def ainvoke(self, state, config=None):
        import httpx
        from core.monitoring import OperationStage, OperationStatus
        raw = state["raw_turn"]
        self.history.append({"role": "user", "parts": [{"text": user_text(raw)}]})
        payload = {"contents": deepcopy(self.history),
                   "generationConfig": {"temperature": self.settings.llm_temperature}}
        state["provider_input"] = deepcopy(payload)
        observed = self.instrumentation.bind(request_id=state["request_id"], thread_id=state["thread_id"], step_id=state["step_id"])
        endpoint = (self.settings.gemini_base_url or "https://generativelanguage.googleapis.com").rstrip("/")
        url = f"{endpoint}/v1beta/models/{self.settings.llm_model}:generateContent"
        for attempt in range(3):
            call_id = "call_" + uuid4().hex
            common = dict(stage=OperationStage.MODEL, operation_name="vanilla.generate", agent="vanilla",
                          model_provider="gemini", model_name=self.settings.llm_model,
                          model_temperature=self.settings.llm_temperature, call_id=call_id,
                          input=payload["contents"])
            observed.record_operation(**common, status=OperationStatus.STARTED, duration_ms=0)
            started = perf_counter()
            data = {}
            try:
                async with httpx.AsyncClient(timeout=150) as client:
                    response = await client.post(url, headers={"x-goog-api-key": self.settings.gemini_api_key}, json=payload)
                response.raise_for_status()
                data = response.json()
                candidates = data.get("candidates", [])
                content = candidates[0].get("content", {}) if candidates else {}
                text = "".join(p.get("text", "") for p in content.get("parts", []) if not p.get("thought"))
                if not text.strip():
                    raise ValueError("provider returned no answer")
                observed.record_operation(**common, status=OperationStatus.SUCCEEDED,
                                          duration_ms=int((perf_counter()-started)*1000), output=text, **provider_usage(data))
                self.history.append({"role": "model", "parts": [{"text": text}]})
                return {"output": {"response": text}, "conversation_context": {},
                        "provider_input": payload, "finish_reason": candidates[0].get("finishReason")}
            except BaseException as error:
                observed.record_operation(**common, status=OperationStatus.FAILED,
                                          duration_ms=int((perf_counter()-started)*1000), error_code=type(error).__name__,
                                          **provider_usage(data))
                retryable = isinstance(error, httpx.HTTPStatusError) and error.response.status_code in {429, 503}
                if retryable and attempt < 2:
                    await asyncio.sleep(3 * (attempt + 1))
                    continue
                raise


def build_vanilla_runtime(collector):
    from core.config import settings
    from core.masking import MaskingSink
    from core.observability_masking import build_masked_runtime_instrumentation
    adapter, sink = build_masked_runtime_instrumentation(
        pseudonym_key=settings.masking_pseudonym_key_bytes, downstream=collector, sink=MaskingSink.TRACE_EXPORT)
    return VanillaConversation(adapter, settings), adapter, sink, {}
