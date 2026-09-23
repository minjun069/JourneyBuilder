from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "contract-fixtures" / "ai-lifecycle" / "v2" / "sse.json"


class AISSEV2ConsumerContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_ai_request_uses_the_frozen_v2_shape(self) -> None:
        request = self.contract["ai_request"]
        self.assertEqual(request["contract_version"], "2.0")
        self.assertEqual(request["request_id"], "ai_req_fixture")
        self.assertIn("deadline_at", request)
        self.assertNotIn("callback_url", request)

    def test_ai_stream_requires_service_authorization(self) -> None:
        self.assertIn("Authorization", self.contract["required_service_headers"])
        self.assertEqual(self.contract["ai_endpoint"], "/api/ai/v2/chat/stream")

    def test_progress_events_are_opt_in_and_content_free(self) -> None:
        policy = self.contract["progress_policy"]
        self.assertEqual(
            self.contract["optional_service_headers"]["X-AI-Progress-Events"],
            "v1",
        )
        self.assertEqual(policy["events"], ["progress", "heartbeat"])
        self.assertEqual(policy["heartbeat_seconds"], 15)
        self.assertFalse(policy["includes_percentage"])
        self.assertFalse(policy["includes_content"])

    def test_all_frontend_conversation_flows_use_sse(self) -> None:
        policy = self.contract["channel_policy"]
        self.assertEqual(policy["general_chat"], "sse")
        self.assertEqual(set(policy["elicit"].values()), {"sse"})
        self.assertEqual(set(policy["error_review"].values()), {"sse"})
        self.assertEqual(policy["change_decision"], {
            "owner": "frontend",
            "transport": "none",
            "persists_on_server": False,
        })
        self.assertEqual(policy["frontend_polling_flows"], [])

    def test_done_and_error_are_terminal_payloads(self) -> None:
        done = self.contract["ai_events"][-1]
        error = self.contract["error_event"]
        self.assertEqual(done["event"], "done")
        self.assertEqual(done["data"]["status"], "completed")
        self.assertEqual(error["event"], "error")
        self.assertEqual(error["data"]["status"], "failed")
        self.assertIn("completed_at", done["data"])
        self.assertIn("completed_at", error["data"])

    def test_non_streaming_transports_are_disabled(self) -> None:
        policy = self.contract["transport_policy"]
        self.assertEqual(policy["browser_to_django"], "sse_only")
        self.assertEqual(policy["django_to_ai"], "sse_only")
        self.assertFalse(policy["non_streaming_accept"])
        self.assertFalse(policy["polling_api"])
        self.assertFalse(policy["http_callback"])
        self.assertEqual(
            self.contract["idempotency"]["same_key_completed"]["source"],
            "terminal_receipt",
        )


if __name__ == "__main__":
    unittest.main()
