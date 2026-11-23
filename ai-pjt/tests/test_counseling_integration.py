"""Candidate retrieval and the real executor boundary with a mocked provider.

The response fixtures establish wiring and safety, not real model counseling quality.
"""

from __future__ import annotations

from contextlib import ExitStack, asynccontextmanager
from functools import partial
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

os.environ.setdefault("LLM_PROVIDER", "gemini")
os.environ.setdefault("EMBEDDING_PROVIDER", "gemini")
os.environ.setdefault("GOOGLE_API_KEY", "counseling-integration-disposable-key")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("AI_SERVICE_BEARER_TOKEN", "counseling-integration-bearer")
os.environ.setdefault("AI_MASKING_PSEUDONYM_KEY", "counseling-integration-mask-key-32bytes")

from core.masking import MaskingSink
from core.observability_masking import FakeSerializedInstrumentationSink, build_masked_runtime_instrumentation
from graph.aggregation import aggregate_task_results, compose_aggregate_response
from graph.executor import execute_route_plan
from rag.counseling_corpus import DEFAULT_MANIFEST, REPOSITORY_ROOT, load_candidate
from rag.counseling_retriever import (
    CounselingCardChunker, CounselingRetriever, CounselingTopic, get_counseling_retriever,
)
from rag.pipeline.embeddings import DeterministicHashEmbedding
from rag.pipeline.indexing import COUNSELING_CANDIDATE_COLLECTION, IndexPipeline
from rag.pipeline.models import IndexTarget
from rag.pipeline.store import JsonVectorStore
from tests.instrumentation import bound_fake_instrumentation


ROOT = Path(__file__).resolve().parents[2]
BASE_ANSWER = "월 상환액은 900,000원입니다. 현재 입력에 따른 결과입니다."
EXPLANATION = "납부일과 생활비가 필요한 시점을 함께 보면 매달 부담이 생기는 구간을 이해하기 쉽습니다."
MASKING_KEY = b"counseling-integration-mask-key-32bytes"


def task(identity="answer", task_type="answer_question"):
    executor = "input_assist" if task_type == "propose_form_change" else "qa"
    return {
        "id": identity, "type": task_type, "description": "고객이 요청한 항목을 처리한다.",
        "depends_on": [], "bindings": {}, "executor_kind": "agent",
        "produces": "form_change" if executor == "input_assist" else "text_response",
        "route": {"task_id": identity, "executor_kind": "agent", "executor": executor,
                  "reason_code": "intent_match"},
    }


async def answer_task(payload):
    return {"task_id": payload["task"]["id"], "status": "completed", "response": BASE_ANSWER,
            "artifact": {"type": "text_response", "text": BASE_ANSWER}}


async def form_task(payload):
    return {
        "task_id": payload["task"]["id"], "status": "completed", "response": "월소득 변경안을 만들었습니다.",
        "artifact": {"type": "form_change", "target": "draft_form", "user_requested": True,
                     "changes": [{"section": "basic_info", "path": "monthly_income", "operation": "set",
                                  "value": 3_500_000, "unit": "KRW", "reason": "고객의 명시적 변경 요청"}]},
    }


class RecordingRetriever:
    def __init__(self, actual):
        self.actual = actual
        self.calls = []

    def retrieve(self, topic):
        self.calls.append(topic)
        return self.actual.retrieve(topic)


class RecordingModel:
    def __init__(self, response, *, failure=False):
        self.response = response
        self.failure = failure
        self.calls = []

    def with_structured_output(self, schema):
        async def invoke(messages):
            self.calls.append((schema.__name__, [message.content for message in messages]))
            if self.failure:
                raise RuntimeError("synthetic provider unavailable")
            return schema(response=self.response)
        return SimpleNamespace(ainvoke=invoke)


class CounselingIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        get_counseling_retriever.cache_clear()
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        corpus = load_candidate(DEFAULT_MANIFEST, repository_root=REPOSITORY_ROOT)
        store = JsonVectorStore(Path(self.temporary.name))
        embedding = DeterministicHashEmbedding(2048)
        pipeline = IndexPipeline(
            store=store, chunker=CounselingCardChunker(), embedding=embedding,
            pending_candidate_sources={
                document.metadata.source_id: (document.metadata.version, document.metadata.sha256)
                for document in corpus.documents
            },
        )
        manifest = pipeline.build(
            list(corpus.documents),
            IndexTarget(COUNSELING_CANDIDATE_COLLECTION, "integration-v1",
                        operational=True, artifact_kind="experiment"),
        )
        store.activate(COUNSELING_CANDIDATE_COLLECTION, manifest.physical_collection)
        self.actual = CounselingRetriever(store=store, embedding=embedding, minimum_dense_score=0.0)
        self.retriever = RecordingRetriever(self.actual)

    def tearDown(self):
        get_counseling_retriever.cache_clear()

    async def execute(self, *, composer=None, tasks=None, question="상환액을 내고 나면 생활비가 빠듯해요.",
                      instrumentation=None, request_id="counseling-integration"):
        return await execute_route_plan(
            plan={"tasks": tasks or [task()]}, step_id="basic-info", customer_info={},
            agent_executors={"qa": answer_task, "input_assist": form_task},
            tool_executors={}, request_id=request_id,
            instrumentation=instrumentation or bound_fake_instrumentation(
                request_id=request_id, thread_id="counseling-thread", step_id="basic-info")[0],
            response_composer=composer,
            question=question, conversation_context={},
        )

    def composer(self):
        return partial(compose_aggregate_response, counseling_retriever=self.retriever)

    async def test_retrieval_precedes_one_full_rewrite_and_preserves_action(self):
        tasks = [task(), task("change", "propose_form_change")]
        baseline = await self.execute(tasks=tasks)
        original = baseline.output["response"]
        revised = original.replace("현재 입력에 따른 결과입니다.", EXPLANATION + " 현재 입력에 따른 결과입니다.")
        model = RecordingModel(revised)
        with patch("core.llm.llm", model):
            result = await self.execute(tasks=tasks, composer=self.composer())
        self.assertEqual(result.output["response"], revised)
        self.assertEqual({k: v for k, v in result.output.items() if k != "response"},
                         {k: v for k, v in baseline.output.items() if k != "response"})
        self.assertEqual(result.output["action"]["changes"][0]["value"], 3_500_000)
        self.assertEqual(self.retriever.calls, [CounselingTopic.DEBT])
        self.assertEqual([name for name, _ in model.calls], ["AggregatorModelOutput"])
        rendered = json.dumps(model.calls, ensure_ascii=False)
        self.assertIn(self.actual.retrieve(CounselingTopic.DEBT)[0].text[:40], rendered)

    async def test_failure_preserves_verified_answer(self):
        model = RecordingModel("", failure=True)
        with patch("core.llm.llm", model):
            result = await self.execute(composer=self.composer())
        self.assertEqual(result.output["response"], BASE_ANSWER)
        self.assertEqual(len(model.calls), 1)

    async def test_candidate_search_is_read_only_and_keeps_pending_status(self):
        paths = [ROOT / "ai-pjt/rag/data/counseling-candidate.json", ROOT / "ai-pjt/rag/data/catalog.json"]
        manifest = json.loads(paths[0].read_text(encoding="utf-8"))
        paths.extend(ROOT / document["path"] for document in manifest["documents"])
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        model = RecordingModel(BASE_ANSWER)
        with ExitStack() as stack:
            active_write = stack.enter_context(patch("rag.pipeline.store.PostgresIndexStore.activate_many", side_effect=AssertionError("index mutation")))
            index_write = stack.enter_context(patch("rag.pipeline.store.PostgresIndexStore.upsert", side_effect=AssertionError("index mutation")))
            stack.enter_context(patch("core.llm.llm", model))
            await self.execute(composer=self.composer())
        self.assertEqual(before, {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths})
        self.assertEqual(manifest["acceptance"]["status"], "pending")
        active_write.assert_not_called()
        index_write.assert_not_called()

    async def test_private_query_never_reaches_search_trace_or_model(self):
        downstream = FakeSerializedInstrumentationSink()
        adapter, _ = build_masked_runtime_instrumentation(
            pseudonym_key=MASKING_KEY, sink=MaskingSink.TRACE_EXPORT, downstream=downstream)
        question = "상환 부담이 걱정돼요. 연락처 leak@example.invalid, 010-2345-6789, 대출 987654321원"
        model = RecordingModel(BASE_ANSWER)
        with patch("core.llm.llm", model):
            await self.execute(composer=self.composer(), question=question,
                               request_id="private-request",
                               instrumentation=adapter.bind(request_id="private-request", thread_id="private-thread", step_id="basic-info"))
        self.assertEqual(self.retriever.calls, [CounselingTopic.DEBT])
        provider = json.dumps(model.calls, ensure_ascii=False)
        trace = json.dumps(downstream.payloads, ensure_ascii=False)
        for forbidden in ("leak@example.invalid", "010-2345-6789"):
            self.assertNotIn(forbidden, provider)
            self.assertNotIn(forbidden, trace)
        self.assertNotIn("987654321", trace)
        searches = [event for event in downstream.payloads if event.get("operation_name") == "counseling.search"]
        self.assertEqual(len(searches), 1)
        self.assertGreater(searches[0]["result_count"], 0)
        self.assertFalse(any(key in searches[0] for key in ("query", "excerpt", "text", "evidence")))


class CounselingStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_lifespan_wires_one_composer_and_checks_index_only_when_enabled(self):
        import main

        @asynccontextmanager
        async def checkpointer():
            yield object()

        for enabled in (False, True):
            with self.subTest(enabled=enabled), ExitStack() as stack:
                settings = SimpleNamespace(
                    validate_runtime=Mock(), langfuse_enabled=False, masking_pseudonym_key_bytes=MASKING_KEY,
                    context_memory_top_k=5, context_embedding_batch_size=8,
                    counseling_rag_enabled=enabled, counseling_rag_timeout_seconds=7.0,
                    plan_validation_enabled=enabled,
                )
                store = SimpleNamespace(setup=AsyncMock(), cleanup_closed_contexts=AsyncMock())
                api = SimpleNamespace(cleanup_expired_executions=AsyncMock())
                exporter = SimpleNamespace(shutdown=Mock())
                stack.enter_context(patch.object(main, "settings", settings))
                stack.enter_context(patch.object(main, "get_callbacks", return_value=[]))
                stack.enter_context(patch.object(main, "get_checkpointer", checkpointer))
                stack.enter_context(patch.object(main, "build_committed_context_store", return_value=store))
                stack.enter_context(patch.object(main, "build_production_tool_runtime", return_value=object()))
                stack.enter_context(patch.object(main, "build_graph_tool_executors", return_value={}))
                stack.enter_context(patch.object(main, "build_masked_runtime_instrumentation", return_value=bound_fake_instrumentation(request_id="r", thread_id="t", step_id="s")))
                stack.enter_context(patch.object(main, "StructuredLogInstrumentationExporter", return_value=exporter))
                stack.enter_context(patch.object(main, "build_context_state_api", return_value=api))
                stack.enter_context(patch.object(main, "ConversationMemoryManager", return_value=object()))
                stack.enter_context(patch.object(main, "ModelSummaryGenerator", return_value=object()))
                stack.enter_context(patch.object(main, "_context_cleanup_loop", AsyncMock()))
                stack.enter_context(patch.object(main, "_context_memory_loop", AsyncMock()))
                factory = stack.enter_context(patch("rag.counseling_retriever.get_counseling_retriever"))
                candidate_retriever = object()
                factory.return_value = candidate_retriever
                build = stack.enter_context(patch.object(main, "build_graph", return_value=object()))
                app = SimpleNamespace(state=SimpleNamespace())
                async with main.lifespan(app):
                    self.assertEqual(build.call_args.kwargs["plan_validation_enabled"], enabled)
                    composer = build.call_args.kwargs["response_composer"]
                    if enabled:
                        self.assertIs(composer.func, compose_aggregate_response)
                        self.assertEqual(composer.keywords["timeout_seconds"], 7.0)
                        self.assertIs(composer.keywords["counseling_retriever"], candidate_retriever)
                        factory.assert_called_once_with()
                    else:
                        self.assertIs(composer, compose_aggregate_response)
                        factory.assert_not_called()
                settings.validate_runtime.assert_called_once()
                exporter.shutdown.assert_called_once()


if __name__ == "__main__":
    unittest.main()
