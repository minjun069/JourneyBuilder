import unittest

from core.context import (
    ContextScope,
    ContextStateAPI,
    ContextSummary,
    ConversationContext,
    ConversationTurn,
    StructuredConversationState,
)
from core.context_store import InMemoryCommittedContextStore
from core.context_summary import (
    ConversationMemoryManager,
    ModelSummaryGenerator,
    SummaryCandidate,
    SummaryClaim,
    SummaryValidationError,
    verify_summary_candidate,
)
from core.config import settings
from core.monitoring import OperationStage
from core.runtime_instrumentation import FakeInstrumentationSink, InstrumentationPort
from core.runtime_instrumentation_adapter import RuntimeInstrumentationAdapter


class FakeCheckpointer:
    async def adelete_thread(self, _thread_id):
        return None


class FakeEmbedding:
    async def aget_query_embedding(self, _text):
        return [1.0, 0.0]

    async def aget_text_embedding(self, _text):
        return [1.0, 0.0]


class FailingSummaryGenerator:
    async def generate(self, _job):
        raise RuntimeError("provider unavailable")


class GroundedSummaryGenerator:
    async def generate(self, _job):
        return SummaryCandidate(claims=[SummaryClaim(
            kind="goal",
            value="RAG 챗봇 구현",
            evidence_quote="RAG 챗봇 구현을 진행해줘",
        )])


class FakeSummaryRunnable:
    async def ainvoke(self, _messages):
        return SummaryCandidate(claims=[])


class FakeSummaryModel:
    def with_structured_output(self, _schema):
        return FakeSummaryRunnable()


class VerifiedSummaryTests(unittest.TestCase):
    def test_financial_claim_is_retained_with_its_source(self):
        from core.context import SummaryJob

        job = SummaryJob(
            scope=ContextScope(1, "thread"),
            base_cursor=0,
            target_cursor=1,
            attempts=0,
            existing_summary=None,
            existing_structured_state=StructuredConversationState(),
            source_turns=(ConversationTurn("r1", "월 소득을 기억해줘", "확인했습니다"),),
        )
        candidate = SummaryCandidate(claims=[SummaryClaim(
            kind="decision",
            value="월 소득",
            evidence_quote="월 소득을 기억해줘",
        )])
        summary, state = verify_summary_candidate(job, candidate)
        self.assertEqual(state.decisions, ("사용자: 월 소득",))
        self.assertIn("월 소득", summary.text)


class ConversationMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.scope = ContextScope(7, "thread-7")
        self.store = InMemoryCommittedContextStore()
        self.api = ContextStateAPI(FakeCheckpointer(), self.store)
        self.first = ConversationTurn(
            "req-1",
            "RAG 챗봇 구현을 진행해줘",
            "구현을 시작했습니다",
        )
        await self.api.commit(
            self.scope,
            "req-1",
            "1" * 64,
            ConversationContext(turns=(self.first,)),
            {"status": "completed"},
            self.first,
        )

    async def _evict_first(self, *, summary=None, state=None):
        second = ConversationTurn("req-2", "다음 질문", "다음 답변")
        context = ConversationContext(
            turns=(second,),
            summary=summary,
            structured_state=state or StructuredConversationState(),
        )
        await self.api.commit(
            self.scope,
            "req-2",
            "2" * 64,
            context,
            {"status": "completed"},
            second,
        )

    async def test_evicted_raw_turn_is_lossless_and_retrievable(self):
        await self._evict_first()
        memories = await self.api.retrieve_memories(
            self.scope,
            None,
            exclude_turn_ids=("req-2",),
            limit=5,
        )
        self.assertEqual(len(memories), 1)
        self.assertEqual(memories[0].turn, self.first)

    async def test_failed_async_summary_keeps_previous_verified_summary(self):
        previous = ContextSummary("현재 목표: 기존 목표.", 20, 0)
        state = StructuredConversationState(current_goal="기존 목표")
        await self._evict_first(summary=previous, state=state)
        manager = ConversationMemoryManager(
            self.api,
            embedding_model=FakeEmbedding(),
            summary_generator=FailingSummaryGenerator(),
        )
        await manager.run_once()
        current = await self.api.read_context(self.scope)
        self.assertEqual(current.summary, previous)
        self.assertEqual(current.structured_state, state)

    async def test_grounded_summary_advances_cursor_and_structured_state(self):
        await self._evict_first()
        manager = ConversationMemoryManager(
            self.api,
            embedding_model=FakeEmbedding(),
            summary_generator=GroundedSummaryGenerator(),
        )
        await manager.run_once()
        current = await self.api.read_context(self.scope)
        self.assertIsNotNone(current.summary)
        self.assertEqual(current.summary.summarized_through_sequence, 1)
        self.assertEqual(current.structured_state.current_goal, "사용자: RAG 챗봇 구현")

        third = ConversationTurn("req-3", "후속 질문", "후속 답변")
        stale_request_context = ConversationContext(turns=(third,))
        await self.api.commit(
            self.scope,
            "req-3",
            "3" * 64,
            stale_request_context,
            {"status": "completed"},
            third,
        )
        after_race = await self.api.read_context(self.scope)
        self.assertEqual(after_race.summary, current.summary)
        self.assertEqual(after_race.structured_state, current.structured_state)

    async def test_model_summary_emits_registered_prompt_metadata(self):
        await self._evict_first()
        job = await self.api.claim_summary_job()
        self.assertIsNotNone(job)
        sink = FakeInstrumentationSink()
        instrumentation = RuntimeInstrumentationAdapter(InstrumentationPort(sink))
        generator = ModelSummaryGenerator(
            FakeSummaryModel(),
            instrumentation=instrumentation,
        )
        previous_key = settings.masking_pseudonym_key
        settings.masking_pseudonym_key = "context-summary-test-masking-key-32bytes"
        try:
            await generator.generate(job)
        finally:
            settings.masking_pseudonym_key = previous_key

        self.assertEqual(len(sink.baseline_events), 1)
        event = sink.baseline_events[0]
        self.assertEqual(event.stage, OperationStage.MODEL)
        self.assertEqual(event.prompt_id, "summary.extract")
        self.assertEqual(event.prompt_release, settings.prompt_release)
        self.assertEqual(len(event.prompt_hash), 64)

    async def test_store_rejects_summary_with_unverified_cursor(self):
        await self._evict_first()
        job = await self.api.claim_summary_job()
        self.assertIsNotNone(job)
        with self.assertRaisesRegex(ValueError, "advance exactly"):
            await self.api.complete_summary_job(
                job,
                summary=ContextSummary(
                    text="현재 목표: 검증되지 않은 상태.",
                    estimated_units=20,
                    summarized_through_sequence=job.target_cursor + 1,
                ),
                structured_state=StructuredConversationState(
                    current_goal="검증되지 않은 상태"
                ),
            )


if __name__ == "__main__":
    unittest.main()
