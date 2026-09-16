from __future__ import annotations

import os
import unittest
from uuid import uuid4

from core.context import (
    ContextScope,
    ContextStateAPI,
    ConversationContext,
    ConversationTurn,
    ContextSummary,
    StructuredConversationState,
)
from core.context_store import (
    ContextCommitConflict,
    PostgresCommittedContextStore,
    committed_scope_key,
)


POSTGRES_TEST_DSN = os.environ.get("AI_CONTEXT_POSTGRES_TEST_DSN", "")


@unittest.skipUnless(POSTGRES_TEST_DSN, "PostgreSQL context integration is opt-in")
class PostgresCommittedContextTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        self._saver_manager = AsyncPostgresSaver.from_conn_string(POSTGRES_TEST_DSN)
        self.checkpointer = await self._saver_manager.__aenter__()
        await self.checkpointer.setup()
        self.store = PostgresCommittedContextStore(POSTGRES_TEST_DSN)
        await self.store.setup()
        self.api = ContextStateAPI(self.checkpointer, self.store)
        self.scope = ContextScope(2_000_000_000, f"integration-{uuid4().hex}")

    async def asyncTearDown(self) -> None:
        await self.api.delete_scope(self.scope)
        await self._saver_manager.__aexit__(None, None, None)

    @staticmethod
    def context(request_id: str) -> ConversationContext:
        return ConversationContext(turns=(ConversationTurn(
            turn_id=request_id,
            user_message=f"question-{request_id}",
            assistant_message=f"answer-{request_id}",
        ),))

    async def test_atomic_commit_replay_rollback_and_temporary_cleanup(self) -> None:
        first = self.context("request-1")
        second = self.context("request-2")
        await self.api.commit(
            self.scope,
            "request-1",
            "1" * 64,
            first,
            {"status": "completed", "output": {"response": "first"}},
        )
        await self.api.commit(
            self.scope,
            "request-2",
            "2" * 64,
            second,
            {"status": "completed", "output": {"response": "second"}},
        )
        memories = await self.api.retrieve_memories(
            self.scope,
            None,
            exclude_turn_ids=("request-2",),
            limit=5,
        )
        self.assertEqual([item.turn.turn_id for item in memories], ["request-1"])
        pending_embeddings = await self.api.pending_archive_embeddings(limit=10)
        archived_first = next(
            item for item in pending_embeddings if item.turn.turn_id == "request-1"
        )
        vector = (1.0, *(0.0 for _ in range(3_071)))
        await self.api.store_archive_embedding(archived_first.archive_id, vector)
        semantic = await self.api.retrieve_memories(
            self.scope,
            vector,
            exclude_turn_ids=("request-2",),
            limit=5,
        )
        self.assertEqual(semantic[0].turn.turn_id, "request-1")
        self.assertAlmostEqual(semantic[0].score, 1.0)

        summary_job = await self.api.claim_summary_job()
        self.assertIsNotNone(summary_job)
        before_failure = await self.api.read_context(self.scope)
        await self.api.fail_summary_job(summary_job, "provider_unavailable")
        self.assertEqual(await self.api.read_context(self.scope), before_failure)

        async with await self.store._connect(autocommit=True) as connection:
            await connection.execute(
                """
                UPDATE ai_context_summary_jobs
                SET next_attempt_at = NOW()
                WHERE scope_key = %s
                """,
                (committed_scope_key(self.scope),),
            )
        retried_job = await self.api.claim_summary_job()
        self.assertIsNotNone(retried_job)
        verified = ContextSummary(
            text="현재 목표: 테스트 목표.",
            estimated_units=12,
            summarized_through_sequence=retried_job.target_cursor,
        )
        applied = await self.api.complete_summary_job(
            retried_job,
            summary=verified,
            structured_state=StructuredConversationState(current_goal="테스트 목표"),
        )
        self.assertTrue(applied)
        self.assertEqual((await self.api.read_context(self.scope)).summary, verified)
        failed = {"status": "failed", "error": {"code": "LLM_TIMEOUT"}}
        await self.api.commit_terminal(
            self.scope,
            "request-3",
            "3" * 64,
            failed,
        )

        current = await self.api.read_context(self.scope)
        self.assertEqual(current.turns, second.turns)
        self.assertEqual(current.summary, verified)
        self.assertEqual(await self.api.read_previous_context(self.scope), first)
        self.assertEqual(
            await self.api.read_terminal(self.scope, "request-3", "3" * 64),
            failed,
        )
        with self.assertRaises(ContextCommitConflict):
            await self.api.read_terminal(self.scope, "request-3", "4" * 64)

        await self.api.begin_execution(self.scope, "request-4")
        await self.api.delete_execution(self.scope, "request-4")


if __name__ == "__main__":
    unittest.main()
