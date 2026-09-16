from __future__ import annotations

import unittest

from graph.elicit_context import (
    elicit_context_index,
    initial_elicit_contexts,
    valid_elicit_contexts,
)


class ElicitContextTests(unittest.TestCase):
    def state(self) -> dict:
        return {
            "step_id": "financial-mindset",
            "customer_info": {
                "current_form": {"values": "안정적인 생활"},
            },
            "context_scope": {
                "schema_version": "1.0",
                "session_id": 12,
                "thread_id": "user-3-session-12",
            },
        }

    def test_contexts_are_field_scoped_without_global_phase(self) -> None:
        state = self.state()
        store = initial_elicit_contexts(state)

        self.assertEqual(store["schema_version"], "2.0")
        self.assertNotIn("phase", store)
        self.assertNotIn("field_index", store)
        self.assertEqual(store["contexts"]["values"]["status"], "completed")
        self.assertEqual(
            store["contexts"]["future_goals"]["status"],
            "unstarted",
        )
        self.assertTrue(valid_elicit_contexts(state, store))

    def test_planner_index_preserves_confirmed_and_open_field_memory(self) -> None:
        state = self.state()
        store = initial_elicit_contexts(state)
        future = store["contexts"]["future_goals"]
        future.update({
            "open_question": "은퇴 후 어떤 삶을 원하시나요?",
            "status": "open",
            "updated_at": "2026-09-16T00:00:00Z",
        })

        index = elicit_context_index(store)
        by_field = {item["field"]: item for item in index["fields"]}

        self.assertEqual(by_field["values"]["confirmed_summary"], "안정적인 생활")
        self.assertEqual(by_field["future_goals"]["status"], "open")
        self.assertEqual(
            by_field["future_goals"]["open_question"],
            "은퇴 후 어떤 삶을 원하시나요?",
        )


if __name__ == "__main__":
    unittest.main()
