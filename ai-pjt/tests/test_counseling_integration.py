"""Real candidate retrieval with mocked models: runtime boundaries, not AX scores.

Model decisions below are fixtures. Passing these tests does not establish that
a real model chooses the right topic or produces sound counseling explanations.
The production model masking and executor/graph boundaries remain in the path.
"""

from __future__ import annotations

import asyncio
from contextlib import ExitStack, asynccontextmanager
from copy import deepcopy
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
from graph.counseling import enrich_counseling_response
from graph.executor import execute_route_plan
from rag.counseling_corpus import DEFAULT_MANIFEST, REPOSITORY_ROOT, load_candidate
from rag.counseling_retriever import (
    CounselingCardChunker,
    CounselingRetriever,
    CounselingTopic,
    get_counseling_retriever,
)
from rag.pipeline.embeddings import DeterministicHashEmbedding
from rag.pipeline.indexing import COUNSELING_CANDIDATE_COLLECTION, IndexPipeline
from rag.pipeline.models import IndexTarget
from rag.pipeline.store import JsonVectorStore
from tests.instrumentation import bound_fake_instrumentation, fake_instrumentation


ROOT = Path(__file__).resolve().parents[2]
BASE_ANSWER = "월 상환액은 900,000원입니다. 현재 입력에 따른 결과입니다."
ADDON = "부채 잔액과 매달 상환 부담은 구분해서 볼 수 있어요."
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
    """Replace only the provider; keep secure_ainvoke and prompt rendering real."""

    def __init__(self, evidence_ids=(), *, topic=CounselingTopic.DEBT, addon=ADDON,
                 judgment=True, failure=None, wait_for=None):
        self.evidence_ids = list(evidence_ids)
        self.topic = topic
        self.addon = addon
        self.judgment = judgment
        self.failure = failure
        self.wait_for = wait_for
        self.started = asyncio.Event()
        self.calls = []

    def with_structured_output(self, schema):
        async def invoke(messages):
            name = schema.__name__
            self.calls.append((name, [message.content for message in messages]))
            self.started.set()
            if self.wait_for is not None:
                await self.wait_for.wait()
            if self.failure == name:
                raise RuntimeError("synthetic provider unavailable")
            if name == "CounselingPlan":
                return schema(topic=self.topic)
            if name == "CounselingAddon":
                return schema(addon=self.addon, evidence_ids=self.evidence_ids)
            if name == "CounselingJudgment":
                return schema(grounded=True, relevant=True, noncontradiction=True,
                              no_new_personal_claims=self.judgment, diagnosis_only=True)
            raise AssertionError(f"unexpected counseling schema: {name}")
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
            store=store,
            chunker=CounselingCardChunker(),
            embedding=embedding,
            pending_candidate_sources={
                document.metadata.source_id: (
                    document.metadata.version,
                    document.metadata.sha256,
                )
                for document in corpus.documents
            },
        )
        manifest = pipeline.build(
            list(corpus.documents),
            IndexTarget(
                COUNSELING_CANDIDATE_COLLECTION,
                "integration-v1",
                operational=True,
                artifact_kind="experiment",
            ),
        )
        store.activate(COUNSELING_CANDIDATE_COLLECTION, manifest.physical_collection)
        self.actual = CounselingRetriever(
            store=store,
            embedding=embedding,
            minimum_dense_score=0.0,
        )
        self.evidence = self.actual.retrieve(CounselingTopic.DEBT)
        self.assertTrue(self.evidence, "the real pending corpus must supply debt explanations")
        self.retriever = RecordingRetriever(self.actual)
        self.model = RecordingModel([self.evidence[0].evidence_id])

    def tearDown(self):
        get_counseling_retriever.cache_clear()

    async def execute(self, *, enricher=None, tasks=None, agents=None, composer=None,
                      question="상환액을 내고 나면 생활비가 빠듯해요.", context=None,
                      instrumentation=None, request_id="counseling-integration"):
        return await execute_route_plan(
            plan={"tasks": tasks or [task()]}, step_id="basic-info", customer_info={},
            agent_executors=agents or {"qa": answer_task, "input_assist": form_task},
            tool_executors={}, request_id=request_id,
            instrumentation=instrumentation or bound_fake_instrumentation(
                request_id=request_id, thread_id="counseling-thread", step_id="basic-info")[0],
            response_composer=composer, counseling_enricher=enricher,
            question=question, conversation_context=context or {},
        )

    def enricher(self, **kwargs):
        return partial(enrich_counseling_response, retriever=self.retriever, **kwargs)

    async def test_disabled_executor_keeps_current_answer_without_counseling_calls(self):
        with patch("core.llm.llm", self.model), patch("rag.counseling_retriever.get_counseling_retriever") as factory:
            result = await self.execute()
        self.assertEqual(result.output["response"], BASE_ANSWER)
        self.assertEqual(self.model.calls, [])
        factory.assert_not_called()

    async def test_real_candidate_evidence_appends_after_composer_and_preserves_form_action(self):
        tasks = [task(), task("change", "propose_form_change")]
        baseline = await self.execute(tasks=tasks)
        composed = baseline.output["response"] + "\n\n확인한 내용을 차례로 안내합니다."

        async def composer(**kwargs):
            return {**kwargs["output"], "response": composed}

        with patch("core.llm.llm", self.model):
            result = await self.execute(tasks=tasks, composer=composer, enricher=self.enricher())
        self.assertEqual(result.output["response"], composed + "\n\n" + ADDON)
        self.assertEqual({k: v for k, v in result.output.items() if k != "response"},
                         {k: v for k, v in baseline.output.items() if k != "response"})
        self.assertEqual(result.output["action"]["changes"][0]["value"], 3_500_000)
        self.assertEqual(self.retriever.calls, [CounselingTopic.DEBT])
        self.assertEqual([name for name, _ in self.model.calls],
                         ["CounselingPlan", "CounselingAddon", "CounselingJudgment"])
        rendered = json.dumps(self.model.calls, ensure_ascii=False)
        self.assertIn(self.evidence[0].evidence_id, rendered)
        self.assertIn(self.evidence[0].text[:40], rendered)

    async def test_none_decision_skips_retrieval_for_reduced_intervention_fixtures(self):
        # The fixture supplies NONE; this verifies execution, not model intent accuracy.
        for question in ("고마워요.", "나이만 정정할게요.", "지금은 더 묻지 말아 주세요."):
            with self.subTest(question=question):
                model = RecordingModel(topic=CounselingTopic.NONE)
                with patch("core.llm.llm", model):
                    result = await self.execute(question=question, enricher=self.enricher())
                self.assertEqual(result.output["response"], BASE_ANSWER)
                self.assertEqual([name for name, _ in model.calls], ["CounselingPlan"])
        self.assertEqual(self.retriever.calls, [])

    async def test_input_only_task_is_not_enriched_even_if_mock_would_choose_a_topic(self):
        with patch("core.llm.llm", self.model):
            result = await self.execute(tasks=[task("change", "propose_form_change")], enricher=self.enricher())
        self.assertEqual(result.output["action"]["changes"][0]["value"], 3_500_000)
        self.assertEqual(self.model.calls, [])
        self.assertEqual(self.retriever.calls, [])

    async def test_failed_task_and_composer_exception_never_call_enricher(self):
        enricher = AsyncMock(side_effect=AssertionError("counseling must not run"))

        async def failed_task(_payload):
            raise RuntimeError("synthetic task failure")

        result = await self.execute(enricher=enricher, tasks=[task(), task("change", "propose_form_change")],
                                    agents={"qa": answer_task, "input_assist": failed_task})
        self.assertEqual(result.task_results["change"]["status"], "failed")
        enricher.assert_not_awaited()
        with self.assertRaisesRegex(RuntimeError, "synthetic composer failure"):
            await self.execute(enricher=enricher, composer=AsyncMock(side_effect=RuntimeError("synthetic composer failure")))
        enricher.assert_not_awaited()

    async def test_enricher_cannot_mutate_action_context_or_replace_verified_answer(self):
        tasks = [task(), task("change", "propose_form_change")]
        context = {"summary": {"text": "유효한 합성 고객 이해"}}
        original_context = deepcopy(context)
        baseline = await self.execute(tasks=tasks)

        async def hostile(**kwargs):
            kwargs["output"]["action"]["changes"][0]["value"] = 1
            kwargs["conversation_context"]["summary"]["text"] = "오염된 이해"
            return {**kwargs["output"], "response": "원래 답변을 대체했습니다."}

        result = await self.execute(tasks=tasks, enricher=hostile, context=context)
        self.assertEqual(result.output, baseline.output)
        self.assertEqual(context, original_context)

    async def test_model_failure_timeout_and_negative_judgment_keep_verified_output(self):
        models = [RecordingModel([self.evidence[0].evidence_id], failure="CounselingAddon"),
                  RecordingModel([self.evidence[0].evidence_id], judgment=False),
                  RecordingModel(wait_for=asyncio.Event())]
        for model in models:
            with self.subTest(failure=model.failure, judgment=model.judgment, timeout=model.wait_for is not None):
                with patch("core.llm.llm", model):
                    result = await self.execute(enricher=self.enricher(timeout_seconds=0.05))
                self.assertEqual(result.output["response"], BASE_ANSWER)

    async def test_request_cancellation_propagates_without_retrieval_or_partial_output(self):
        model = RecordingModel(wait_for=asyncio.Event())
        with patch("core.llm.llm", model):
            pending = asyncio.create_task(self.execute(enricher=self.enricher()))
            await asyncio.wait_for(model.started.wait(), timeout=2)
            pending.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await pending
        self.assertEqual(self.retriever.calls, [])
        self.assertEqual([name for name, _ in model.calls], ["CounselingPlan"])

    async def test_candidate_search_does_not_rewrite_manifest_catalog_or_operational_index(self):
        paths = [ROOT / "ai-pjt/rag/data/counseling-candidate.json", ROOT / "ai-pjt/rag/data/catalog.json"]
        manifest = json.loads(paths[0].read_text(encoding="utf-8"))
        paths.extend(ROOT / document["path"] for document in manifest["documents"])
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        with ExitStack() as stack:
            active_write = stack.enter_context(patch("rag.pipeline.store.PostgresIndexStore.activate_many", side_effect=AssertionError("operational index mutation")))
            index_write = stack.enter_context(patch("rag.pipeline.store.PostgresIndexStore.upsert", side_effect=AssertionError("operational index mutation")))
            operational = stack.enter_context(patch("rag.retriever.get_retriever", side_effect=AssertionError("operational collection access")))
            stack.enter_context(patch("core.llm.llm", self.model))
            result = await self.execute(enricher=partial(enrich_counseling_response, retriever=self.actual))
        self.assertIn(ADDON, result.output["response"])
        self.assertEqual(before, {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths})
        self.assertEqual(json.loads(paths[0].read_text(encoding="utf-8"))["acceptance"]["status"], "pending")
        active_write.assert_not_called()
        index_write.assert_not_called()
        operational.assert_not_called()

    async def test_search_observation_has_no_private_query_and_model_masks_direct_identifiers(self):
        downstream = FakeSerializedInstrumentationSink()
        adapter, _ = build_masked_runtime_instrumentation(
            pseudonym_key=MASKING_KEY, sink=MaskingSink.TRACE_EXPORT, downstream=downstream)
        question = "상환 부담이 걱정돼요. 연락처 leak@example.invalid, 010-2345-6789, 대출 987654321원"
        with patch("core.llm.llm", self.model):
            result = await self.execute(enricher=self.enricher(), question=question,
                                        request_id="private-request",
                                        instrumentation=adapter.bind(request_id="private-request", thread_id="private-thread", step_id="basic-info"))
        self.assertIn(ADDON, result.output["response"])
        self.assertEqual(self.retriever.calls, [CounselingTopic.DEBT])
        provider = json.dumps(self.model.calls, ensure_ascii=False)
        trace = json.dumps(downstream.payloads, ensure_ascii=False)
        # The existing model boundary permits financial values in conversational
        # text; the search boundary receives only a topic and exposes no query.
        for forbidden in ("leak@example.invalid", "010-2345-6789"):
            self.assertFalse(forbidden in provider, "direct identifier reached the provider")
            self.assertFalse(forbidden in trace, "direct identifier reached a trace")
        self.assertFalse("987654321" in trace, "financial value reached a trace")
        searches = [event for event in downstream.payloads if event.get("operation_name") == "counseling.search"]
        self.assertEqual(len(searches), 1)
        search = searches[0]
        self.assertGreater(search["result_count"], 0)
        self.assertTrue(search["resource_ids"])
        self.assertFalse(any(key in search for key in ("query", "excerpt", "text", "evidence")))
        self.assertNotIn(self.evidence[0].text, json.dumps(search, ensure_ascii=False))
        self.assertNotIn(question, json.dumps(search, ensure_ascii=False))

    async def test_retrieval_metadata_cannot_appear_in_terminal_answer(self):
        model = RecordingModel([self.evidence[0].evidence_id], addon=ADDON + "\n[자료: 상담 후보 문서]")
        with patch("core.llm.llm", model):
            result = await self.execute(enricher=self.enricher())
        self.assertEqual(result.output["response"], BASE_ANSWER)
        self.assertNotIn("[자료:", result.output["response"])
        self.assertNotIn(self.evidence[0].source_id, result.output["response"])

    async def test_graph_passes_each_request_context_without_retriever_memory_leakage(self):
        from graph.builder import build_graph

        class Agent:
            async def ainvoke(self, state):
                return {"output": {"response": BASE_ANSWER, "step_id": state["step_id"], "action": None}}

        def planner(**_kwargs):
            return {"tasks": [{"id": "answer", "type": "answer_question", "description": "대출 부담의 의미 설명",
                               "depends_on": [], "bindings": {}}]}

        graph = build_graph(None, instrumentation=fake_instrumentation()[0], planner=planner,
                            response_composer=None, counseling_enricher=self.enricher(),
                            agent_graphs={name: Agent() for name in ("qa", "input_assist", "error_review", "chart_interpret", "elicit")})
        for marker in ("FIRST_CONTEXT_MARKER", "SECOND_CONTEXT_MARKER"):
            self.model.calls.clear()
            with patch("core.llm.llm", self.model):
                result = await graph.ainvoke({
                    "request_id": marker, "thread_id": marker, "message": "대출 부담을 어떻게 이해하면 될까요?",
                    "step_id": "liabilities", "customer_info": {"saved_data": {}, "current_form": {}},
                    "conversation_context": {"schema_version": "3.0", "summary": {"text": marker},
                                             "structured_state": {}, "turns": []},
                })
            self.assertIn(ADDON, result["output"]["response"])
            rendered = json.dumps(self.model.calls, ensure_ascii=False)
            self.assertIn(marker, rendered)
            if marker == "SECOND_CONTEXT_MARKER":
                self.assertNotIn("FIRST_CONTEXT_MARKER", rendered)
        self.assertEqual(self.retriever.calls, [CounselingTopic.DEBT, CounselingTopic.DEBT])
        self.assertNotIn("FIRST_CONTEXT_MARKER", repr(vars(self.actual)))


class CounselingStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_lifespan_wires_enricher_only_when_explicitly_enabled(self):
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
                stack.enter_context(patch.object(main, "build_masked_runtime_instrumentation", return_value=fake_instrumentation()))
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
                    enricher = build.call_args.kwargs["counseling_enricher"]
                    if enabled:
                        self.assertIs(enricher.func, enrich_counseling_response)
                        self.assertEqual(enricher.keywords["timeout_seconds"], 7.0)
                        self.assertIs(enricher.keywords["retriever"], candidate_retriever)
                        factory.assert_called_once_with()
                    else:
                        self.assertIsNone(enricher)
                        factory.assert_not_called()
                settings.validate_runtime.assert_called_once()
                exporter.shutdown.assert_called_once()


if __name__ == "__main__":
    unittest.main()
