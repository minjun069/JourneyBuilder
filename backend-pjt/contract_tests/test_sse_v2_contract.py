from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "contract-fixtures" / "ai-lifecycle" / "v2" / "sse.json"


class SSEV2ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_browser_and_ai_endpoints_are_v2_streams(self) -> None:
        self.assertTrue(
            self.contract["browser_endpoint"].startswith("/api/consulting/v2/")
        )
        self.assertEqual(self.contract["ai_endpoint"], "/api/ai/v2/chat/stream")
        self.assertEqual(self.contract["content_type"], "text/event-stream")

    def test_event_state_machine_has_one_terminal_event(self) -> None:
        allowed = set(self.contract["allowed_events"])
        terminal = set(self.contract["terminal_events"])
        self.assertEqual(
            allowed,
            {"start", "progress", "heartbeat", "done", "error"},
        )
        self.assertEqual(terminal, {"done", "error"})
        names = [item["event"] for item in self.contract["browser_events"]]
        self.assertEqual(names, ["start", "progress", "heartbeat", "done"])

    def test_every_fixture_event_keeps_version_and_request_identity(self) -> None:
        for boundary in ("ai_events", "browser_events"):
            for event in self.contract[boundary]:
                self.assertEqual(event["data"]["contract_version"], "2.0")
                self.assertEqual(event["data"]["request_id"], "ai_req_fixture")

    def test_browser_contract_requires_auth_and_idempotency(self) -> None:
        headers = set(self.contract["required_browser_headers"])
        self.assertIn("Authorization", headers)
        self.assertIn("Idempotency-Key", headers)

    def test_progress_events_are_opt_in_transient_metadata(self) -> None:
        policy = self.contract["progress_policy"]
        self.assertEqual(
            self.contract["optional_browser_headers"]["X-AI-Progress-Events"],
            "v1",
        )
        self.assertFalse(policy["stored"])
        self.assertFalse(policy["replayed"])
        self.assertFalse(policy["includes_percentage"])
        self.assertFalse(policy["includes_content"])

    def test_error_review_uses_sse_and_frontend_polling_is_empty(self) -> None:
        policy = self.contract["channel_policy"]
        self.assertEqual(policy["error_review"], {
            "save": "sse",
        })
        self.assertEqual(policy["change_decision"], {
            "owner": "frontend",
            "transport": "none",
            "persists_on_server": False,
        })
        self.assertEqual(policy["frontend_polling_flows"], [])
        self.assertEqual(self.contract["timeouts_seconds"]["stream_read_idle"], 120)
        self.assertEqual(self.contract["timeouts_seconds"]["processing_expiry"], 180)


if __name__ == "__main__":
    unittest.main()
