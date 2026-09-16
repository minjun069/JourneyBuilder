import unittest

from core.context import ContextScope, ContextStateAPI, ConversationContext, ConversationTurn
from core.context_store import InMemoryCommittedContextStore


class FakeCheckpointer:
    async def adelete_thread(self, _thread_id):
        return None


class ContextIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_session_and_thread_form_the_committed_context_boundary(self):
        store = InMemoryCommittedContextStore()
        api = ContextStateAPI(FakeCheckpointer(), store)
        target = ContextScope(1, "thread-a")
        other_session = ContextScope(2, "thread-a")
        other_thread = ContextScope(1, "thread-b")
        context = ConversationContext(
            turns=(ConversationTurn("req-1", "질문", "답변"),)
        )
        await api.commit(target, "req-1", "a" * 64, context, {"status": "completed"})
        self.assertEqual(await api.read_context(target), context)
        self.assertEqual(await api.read_context(other_session), ConversationContext())
        self.assertEqual(await api.read_context(other_thread), ConversationContext())

    async def test_request_scoped_configs_do_not_share_langgraph_threads(self):
        api = ContextStateAPI(FakeCheckpointer(), InMemoryCommittedContextStore())
        scope = ContextScope(1, "thread-a")
        first = api.config_for(scope, "req-1")["configurable"]["thread_id"]
        second = api.config_for(scope, "req-2")["configurable"]["thread_id"]
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
