"""Exercise the installed SDK and LlamaIndex with synthetic transport responses."""

import asyncio
import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from llama_index.embeddings.google_genai import GoogleGenAIEmbedding

from core.embedding_usage import EmbeddingUsage, instrument_gemini_embedding


def response(tokens=7):
    payload = {"embeddings": [{"values": [0.1, 0.2]}]}
    if tokens is not None:
        payload["usageMetadata"] = {"promptTokenCount": tokens}
    return SimpleNamespace(body=json.dumps(payload), headers={})


class EmbeddingUsageTests(unittest.TestCase):
    def embedding(self):
        return GoogleGenAIEmbedding(model_name="gemini-embedding-001", api_key="synthetic-key")

    def test_actual_sdk_and_llamaindex_preserve_request_and_vectors(self):
        embedding = self.embedding()
        api = embedding._client._api_client
        api.request = Mock(return_value=response())
        before = embedding.get_text_embedding_batch(["synthetic input"])
        original_call = copy.deepcopy((api.request.call_args.args, api.request.call_args.kwargs))
        instrument_gemini_embedding(embedding)
        with EmbeddingUsage() as usage:
            after = embedding.get_text_embedding_batch(["synthetic input"])
        self.assertEqual(after, before)
        call = api.request.__wrapped__.call_args
        self.assertEqual((call.args, call.kwargs), original_call)
        self.assertEqual(usage.fields(), {
            "usage_status": "reported", "input_tokens": 7, "total_tokens": 7,
        })
        # The installed converter currently discards usageMetadata.
        self.assertNotIn("synthetic", repr(vars(usage)))

    def test_batches_sum_once_and_partial_usage_remains_unavailable(self):
        embedding = self.embedding()
        embedding.embed_batch_size = 1
        api = embedding._client._api_client
        api.request = Mock(side_effect=[response(7), response(5), response(None)])
        instrument_gemini_embedding(embedding)
        instrument_gemini_embedding(embedding)
        with EmbeddingUsage() as usage:
            embedding.get_text_embedding_batch(["a", "b", "c"])
        self.assertEqual(api.request.__wrapped__.call_count, 3)
        self.assertEqual(usage.fields(), {
            "usage_status": "unavailable", "input_tokens": 12, "total_tokens": 12,
        })

    def test_missing_invalid_and_zero_counts(self):
        for tokens in (None, -1, True, "7"):
            usage = EmbeddingUsage()
            usage.record(response(tokens))
            self.assertEqual(usage.fields(), {"usage_status": "unavailable"})
        usage = EmbeddingUsage()
        usage.record(response(0))
        self.assertEqual(usage.fields(), {
            "usage_status": "reported", "input_tokens": 0, "total_tokens": 0,
        })

    def test_pipeline_passes_usage_to_langfuse_without_query_or_vectors(self):
        from core.langfuse_exporter import _generation_args
        try:
            from core.model_boundary import bind_model_call_recorder
        except ImportError:
            from core.model_observation import bind_model_call_recorder
        from rag.pipeline.embeddings import LlamaIndexEmbeddingAdapter

        embedding = self.embedding()
        embedding._client._api_client.request = Mock(return_value=response(7))
        instrument_gemini_embedding(embedding)
        adapter = LlamaIndexEmbeddingAdapter(embedding, model_id="gemini-embedding-001", dimensions=2)
        events = []
        with bind_model_call_recorder(lambda **event: events.append(event)):
            self.assertEqual(adapter.embed_texts(["synthetic private query"]), [(0.1, 0.2)])
        self.assertEqual([event["status"] for event in events], ["started", "succeeded"])
        self.assertEqual(events[-1]["usage_status"], "reported")
        self.assertNotIn("synthetic private query", repr(events))
        self.assertIsNone(events[-1]["output"])
        exported = _generation_args(events[-1])
        self.assertEqual(exported["as_type"], "embedding")
        self.assertEqual(exported["usage_details"], {"input": 7, "total": 7})

    def test_transport_identity_exceptions_and_other_endpoints(self):
        embedding = self.embedding()
        api = embedding._client._api_client
        malformed = SimpleNamespace(body="invalid-json")
        error = RuntimeError("synthetic failure")
        api.request = Mock(side_effect=[malformed, response(99), error])
        instrument_gemini_embedding(embedding)
        with EmbeddingUsage() as usage:
            self.assertIs(api.request("post", "models/x:embedContent", {}, None), malformed)
            api.request("post", "models/x:generateContent", {}, None)
            with self.assertRaises(RuntimeError) as caught:
                api.request("post", "models/x:batchEmbedContents", {}, None)
        self.assertIs(caught.exception, error)
        self.assertEqual(usage.fields(), {"usage_status": "unavailable"})

    def test_async_sdk_and_concurrent_collectors(self):
        embedding = self.embedding()
        api = embedding._client._api_client

        async def fake_request(method, path, request_dict, http_options):
            await asyncio.sleep(0)
            text = request_dict["requests"][0]["content"]["parts"][0]["text"]
            return response(int(text))

        api.async_request = fake_request
        instrument_gemini_embedding(embedding)

        async def run(tokens):
            with EmbeddingUsage() as usage:
                vectors = await embedding.aget_text_embedding_batch([str(tokens)])
                self.assertEqual(vectors, [[0.1, 0.2]])
                return usage.fields()["input_tokens"]

        async def concurrent():
            return await asyncio.gather(run(7), run(11))

        self.assertEqual(asyncio.run(concurrent()), [7, 11])
