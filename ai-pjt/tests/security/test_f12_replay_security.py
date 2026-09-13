from unittest import TestCase

from tests.security.service_tool_security_contract import cases_for_owner, load_suite


class F12SSEReplaySecurityTests(TestCase):
    def setUp(self):
        suite = load_suite()
        self.cases = {
            case["id"]: case for case in cases_for_owner(suite, "F12-T03")
        }

    def test_browser_idempotency_replay_owns_graph_non_reexecution(self):
        replay = self.cases["F12-RPL-001"]
        conflict = self.cases["F12-RPL-002"]

        self.assertEqual(replay["boundary"], "browser_sse")
        self.assertEqual(replay["expected"]["http_status"], 200)
        self.assertIn(
            "no_graph_reexecution",
            replay["expected"]["required_effects"],
        )
        self.assertEqual(
            conflict["expected"]["reason_code"],
            "IDEMPOTENCY_CONFLICT",
        )

    def test_terminal_receipt_is_the_only_completed_result_replay_source(self):
        replay = self.cases["F12-RPL-005"]

        self.assertEqual(replay["boundary"], "browser_sse")
        self.assertTrue(replay["input"]["redis_state_missing"])
        self.assertTrue(replay["input"]["terminal_receipt_exists"])
        self.assertEqual(
            replay["expected"]["reason_code"],
            "TERMINAL_RECEIPT_REPLAY",
        )
        self.assertIn(
            "no_graph_reexecution",
            replay["expected"]["required_effects"],
        )
