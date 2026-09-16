from __future__ import annotations

import os
import unittest
from uuid import uuid4

from core.context import (
    ContextScope,
    ContextStateAPI,
    ConversationContext,
    ConversationTurn,
)
from core.context_store import ContextCommitConflict, PostgresCommittedContextStore


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
        failed = {"status": "failed", "error": {"code": "LLM_TIMEOUT"}}
        await self.api.commit_terminal(
            self.scope,
            "request-3",
            "3" * 64,
            failed,
        )

        self.assertEqual(await self.api.read_context(self.scope), second)
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
