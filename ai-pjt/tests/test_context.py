import unittest

from core.context import (
    CONTEXT_SCHEMA_VERSION,
    ContextBudgetPolicy,
    ContextScope,
    ContextStateAPI,
    ConversationContext,
    ConversationTurn,
    advance_context,
    build_context_window,
    checkpoint_thread_key,
    resolve_confirmed_facts,
)
from core.context_store import ContextCommitConflict, InMemoryCommittedContextStore


def turn(turn_id: str, text: str = "내용") -> ConversationTurn:
    return ConversationTurn(turn_id, f"질문 {text}", f"답변 {text}")


class FakeCheckpointer:
    def __init__(self):
        self.deleted = []

    async def adelete_thread(self, thread_id):
        self.deleted.append(thread_id)


class ContextSchemaTests(unittest.TestCase):
    def test_state_contains_only_layered_non_financial_memory_and_turns(self):
        context = ConversationContext(turns=(turn("req-1"),))
        state = context.to_state()
        self.assertEqual(state["schema_version"], CONTEXT_SCHEMA_VERSION)
        self.assertEqual(
            set(state),
            {"schema_version", "summary", "structured_state", "turns"},
        )
        self.assertNotIn("fact_revisions", state)
        self.assertNotIn("turn_digests", state)
        self.assertNotIn("source_turn_ids", state["summary"])
        self.assertEqual(ConversationContext.from_state(state), context)

    def test_request_checkpoint_key_is_opaque_and_request_scoped(self):
        scope = ContextScope(3, "user-7-session-3")
        first = checkpoint_thread_key(scope, "req-1")
        self.assertEqual(first, checkpoint_thread_key(scope, "req-1"))
        self.assertNotEqual(first, checkpoint_thread_key(scope, "req-2"))
        self.assertNotIn(scope.thread_id, first)

    def test_backend_snapshot_is_the_only_source_of_financial_facts(self):
        facts = resolve_confirmed_facts(
            active_step_fields=("income.monthly",),
            saved_data={"income.monthly": 100, "asset.cash": 200},
            current_form={"income.monthly": 300, "asset.cash": 999},
        )
        self.assertEqual(dict(facts), {"income.monthly": 300, "asset.cash": 200})

    def test_context_window_uses_current_snapshot_not_historical_turns(self):
        context = ConversationContext(turns=(turn("req-1", "과거 소득 100"),))
        window = build_context_window(
            context=context,
            current_confirmed_facts={"income.monthly": 500},
        )
        self.assertEqual(dict(window.confirmed_facts), {"income.monthly": 500})

    def test_overflow_keeps_verified_summary_unchanged_and_evicts_recent_copy(self):
        context = ConversationContext()
        policy = ContextBudgetPolicy(
            max_model_input_units=120,
            max_summary_units=40,
            max_structured_units=0,
            max_memory_units=0,
        )
        for index in range(12):
            context = advance_context(
                context=context,
                new_turn=turn(f"req-{index}", "긴내용" * 15),
                policy=policy,
            )
        self.assertIsNone(context.summary)
        self.assertLess(len(context.turns), 12)


class CommittedContextTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.scope = ContextScope(9, "thread-9")
        self.store = InMemoryCommittedContextStore()
        self.checkpointer = FakeCheckpointer()
        self.api = ContextStateAPI(self.checkpointer, self.store)

    async def test_commit_replaces_current_and_keeps_one_previous(self):
        first = ConversationContext(turns=(turn("req-1"),))
        second = ConversationContext(turns=(turn("req-1"), turn("req-2")))
        await self.api.commit(self.scope, "req-1", "1" * 64, first, {"status": "completed"})
        await self.api.commit(self.scope, "req-2", "2" * 64, second, {"status": "completed"})
        self.assertEqual(await self.api.read_context(self.scope), second)
        self.assertEqual(await self.api.read_previous_context(self.scope), first)

    async def test_exact_request_replays_terminal_without_second_commit(self):
        context = ConversationContext(turns=(turn("req-1"),))
        terminal = {"status": "completed", "output": {"response": "ok"}}
        first = await self.api.commit(self.scope, "req-1", "a" * 64, context, terminal)
        replay = await self.api.commit(
            self.scope,
            "req-1",
            "a" * 64,
            ConversationContext(turns=(turn("should-not-win"),)),
            {"status": "completed", "output": {"response": "different"}},
        )
        self.assertEqual(replay, first)
        self.assertEqual(
            await self.api.read_terminal(self.scope, "req-1", "a" * 64),
            terminal,
        )

    async def test_same_request_with_another_payload_is_rejected(self):
        await self.api.commit(
            self.scope,
            "req-1",
            "a" * 64,
            ConversationContext(turns=(turn("req-1"),)),
            {"status": "completed"},
        )
        with self.assertRaises(ContextCommitConflict):
            await self.api.read_terminal(self.scope, "req-1", "b" * 64)

    async def test_failed_terminal_replays_without_advancing_context(self):
        current = ConversationContext(turns=(turn("req-1"),))
        await self.api.commit(
            self.scope, "req-1", "a" * 64, current, {"status": "completed"}
        )
        failed = {"status": "failed", "error": {"code": "LLM_TIMEOUT"}}
        await self.api.commit_terminal(self.scope, "req-2", "b" * 64, failed)

        self.assertEqual(await self.api.read_context(self.scope), current)
        self.assertIsNone(await self.api.read_previous_context(self.scope))
        self.assertEqual(
            await self.api.read_terminal(self.scope, "req-2", "b" * 64),
            failed,
        )

    async def test_temporary_checkpoint_is_deleted_after_execution(self):
        await self.api.begin_execution(self.scope, "req-1")
        await self.api.delete_execution(self.scope, "req-1")
        self.assertEqual(
            self.checkpointer.deleted,
            [checkpoint_thread_key(self.scope, "req-1")],
        )

    async def test_delete_scope_does_not_depend_on_langgraph_history(self):
        await self.api.commit(
            self.scope,
            "req-1",
            "a" * 64,
            ConversationContext(turns=(turn("req-1"),)),
            {"status": "completed"},
        )
        await self.api.delete_scope(self.scope)
        self.assertEqual(await self.api.read_context(self.scope), ConversationContext())


if __name__ == "__main__":
    unittest.main()
