"""Collection regressions: no provider calls or external export."""

import asyncio
import unittest
from unittest.mock import patch

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableLambda
from langchain_core.outputs import ChatResult, ChatGeneration

from core.model_measurement import ModelMeasurement
from core.model_boundary import ModelPurpose, secure_invoke, secure_ainvoke, secure_system_prompt
try:
    from core.model_boundary import bind_model_call_recorder
except ImportError:
    from core.model_observation import bind_model_call_recorder
from core.langfuse_exporter import LangfuseInstrumentationExporter
from core.masking import MaskingSink
from core.observability_masking import build_masked_runtime_instrumentation


class Observation:
    def __init__(self, owner, **values):
        self.owner, self.values, self.ended = owner, values, False
        owner.observations.append(self)

    def start_observation(self, **values):
        return Observation(self.owner, **values)

    def update(self, **values):
        self.values.update(values)

    def end(self):
        self.ended = True


class Client:
    def __init__(self):
        self.observations = []

    def start_observation(self, **values):
        return Observation(self, **values)

    def shutdown(self):
        pass


def response(tokens=20):
    return AIMessage(content="정상 응답", usage_metadata={
        "input_tokens": tokens, "output_tokens": 8, "total_tokens": tokens + 8,
        "input_token_details": {"cache_read": 3},
        "output_token_details": {"reasoning": 2},
    })


def messages():
    return [SystemMessage(content=secure_system_prompt("질문에 답하세요.")),
            HumanMessage(content="안녕하세요")]


class MeasurementTests(unittest.TestCase):
    def setUp(self):
        patcher = patch("core.config.settings.masking_pseudonym_key", "measurement-synthetic-key-32-bytes")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_usage_survives_parser_without_changing_output(self):
        model = FakeMessagesListChatModel(responses=[response()])
        chain = model | RunnableLambda(lambda message: {"answer": message.content})
        with ModelMeasurement() as measurement:
            result = chain.invoke("hello")
        self.assertEqual(result, {"answer": "정상 응답"})
        self.assertEqual(measurement.fields(result)["input_tokens"], 20)
        self.assertEqual(measurement.fields(result)["cached_input_tokens"], 3)
        self.assertEqual(measurement.fields(result)["reasoning_output_tokens"], 2)

    def test_unknown_usage_is_not_zero_and_context_is_reset(self):
        from core.model_measurement import _collector
        with ModelMeasurement() as measurement:
            pass
        self.assertIsNone(_collector.get())
        self.assertEqual(measurement.fields()["usage_status"], "unavailable")
        self.assertNotIn("input_tokens", measurement.fields())

    def test_generation_opens_before_inference_and_finishes_with_usage(self):
        client = Client()
        exporter = LangfuseInstrumentationExporter(client)
        adapter, sink = build_masked_runtime_instrumentation(
            pseudonym_key=b"measurement-synthetic-key-32-bytes",
            downstream=exporter, sink=MaskingSink.TRACE_EXPORT,
        )
        import inspect
        binding = dict(request_id="request-test", thread_id="thread-test", step_id="basic-info")
        if "plan_id" in inspect.signature(adapter.bind).parameters:
            binding["plan_id"] = "plan-test"
        observed = adapter.bind(**binding)

        def invoke(_):
            generations = [o for o in client.observations if o.values.get("as_type") == "generation"]
            self.assertEqual(len(generations), 1)
            self.assertFalse(generations[0].ended)
            return response()

        with patch("core.config.settings.masking_pseudonym_key", "measurement-synthetic-key-32-bytes"):
            with bind_model_call_recorder(lambda **event: observed.record_model_call(task_id="task_measurement", **event)):
                result = secure_invoke(RunnableLambda(invoke), messages(), purpose=ModelPurpose.QA)
        self.assertEqual(result.content, "정상 응답")
        generations = [o for o in client.observations if o.values.get("as_type") == "generation"]
        self.assertEqual(len(generations), 1)
        self.assertTrue(generations[0].ended)
        steps = [o for o in client.observations if "agent_step" in o.values.get("name", "")]
        self.assertEqual(len(steps), 1)
        self.assertTrue(steps[0].ended)
        self.assertEqual(generations[0].values["usage_details"], {
            "input": 17, "output": 6,
            "input_cached_tokens": 3, "output_reasoning_tokens": 2, "total": 28,
        })
        self.assertEqual(sink.dropped_exports, 0)

    def test_recorder_failure_does_not_change_inference(self):
        answer = response()
        def broken(**_):
            raise RuntimeError("synthetic exporter failure")
        with bind_model_call_recorder(broken):
            result = secure_invoke(RunnableLambda(lambda _: answer), messages(), purpose=ModelPurpose.QA)
        self.assertIs(result, answer)

    def test_concurrent_calls_keep_usage_separate(self):
        class AsyncModel(FakeMessagesListChatModel):
            async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
                await asyncio.sleep(0)
                return ChatResult(generations=[ChatGeneration(message=self.responses[0])])
        async def run(tokens):
            model = AsyncModel(responses=[response(tokens)])
            with ModelMeasurement() as measurement:
                await model.ainvoke("hello")
                await asyncio.sleep(0)
            return measurement.fields()["input_tokens"]
        async def run_all():
            return await asyncio.gather(run(10), run(30))
        self.assertEqual(asyncio.run(run_all()), [10, 30])

    def test_cancellation_preserves_exception_and_closes_call(self):
        events = []
        async def cancel(_):
            raise asyncio.CancelledError()
        async def run():
            with bind_model_call_recorder(lambda **event: events.append(event)):
                await secure_ainvoke(RunnableLambda(cancel), messages(), purpose=ModelPurpose.QA)
        with patch("core.config.settings.masking_pseudonym_key", "measurement-synthetic-key-32-bytes"):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(run())
        self.assertEqual([event["status"] for event in events], ["started", "failed"])
        self.assertEqual(events[0]["call_id"], events[1]["call_id"])

    def test_embedding_preserves_vectors_and_marks_missing_usage(self):
        from rag.pipeline.embeddings import LlamaIndexEmbeddingAdapter
        class Embedding:
            def get_text_embedding_batch(self, texts):
                self.texts = texts
                return [[1.0, 2.0]]
        delegate = Embedding()
        adapter = LlamaIndexEmbeddingAdapter(delegate, model_id="gemini-embedding-2", dimensions=2)
        events = []
        with bind_model_call_recorder(lambda **event: events.append(event)):
            vectors = adapter.embed_texts(["synthetic query"])
        self.assertEqual(vectors, [(1.0, 2.0)])
        self.assertEqual(delegate.texts, ["synthetic query"])
        self.assertEqual([event["status"] for event in events], ["started", "succeeded"])
        self.assertTrue(all(event["usage_status"] == "unavailable" for event in events))
        self.assertNotIn("synthetic query", repr(events))

    def test_zero_usage_is_reported_not_missing(self):
        message = AIMessage(content="", usage_metadata={"input_tokens":0,"output_tokens":0,"total_tokens":0})
        fields = ModelMeasurement().fields(message)
        self.assertEqual(fields["usage_status"], "reported")
        self.assertEqual(fields["total_tokens"], 0)

    def test_background_summary_finishes_its_synthetic_root(self):
        from core.langfuse_exporter import _generation_args
        self.assertEqual(_generation_args({"input_tokens":0,"output_tokens":0,"total_tokens":0})["usage_details"],
                         {"input":0,"output":0,"total":0})
        client = Client()
        exporter = LangfuseInstrumentationExporter(client)
        payload = dict(contract_version="1.0", event_family="operation", masking_status="applied",
            trace_id="trace_" + "a" * 24, attempt_id="attempt_synthetic", occurred_at_epoch_ms=1000,
            stage="model", agent="summary", operation_name="summary.extract", model_name="synthetic-model",
            call_id="call_" + "b" * 32, status="started", duration_ms=0)
        exporter.emit(payload)
        self.assertFalse(client.observations[0].ended)
        exporter.emit({**payload, "status":"succeeded", "duration_ms":50, "output":{"summary":"synthetic"}})
        self.assertTrue(client.observations[0].ended)
        self.assertTrue(client.observations[1].ended)
