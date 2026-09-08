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
        self.assertTrue(self.contract["browser_endpoint"].startswith("/api/v2/"))
        self.assertEqual(self.contract["ai_endpoint"], "/v2/chat/stream")
        self.assertEqual(self.contract["content_type"], "text/event-stream")

    def test_event_state_machine_has_one_terminal_event(self) -> None:
        allowed = set(self.contract["allowed_events"])
        terminal = set(self.contract["terminal_events"])
        self.assertEqual(allowed, {"start", "delta", "done", "error"})
        self.assertEqual(terminal, {"done", "error"})
        names = [item["event"] for item in self.contract["browser_events"]]
        self.assertEqual(names, ["start", "delta", "done"])

    def test_every_fixture_event_keeps_version_and_request_identity(self) -> None:
        for boundary in ("ai_events", "browser_events"):
            for event in self.contract[boundary]:
                self.assertEqual(event["data"]["contract_version"], "2.0")
                self.assertEqual(event["data"]["request_id"], "ai_req_fixture")

    def test_browser_contract_requires_auth_and_idempotency(self) -> None:
        headers = set(self.contract["required_browser_headers"])
        self.assertIn("Authorization", headers)
        self.assertIn("Idempotency-Key", headers)


if __name__ == "__main__":
    unittest.main()
