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
        self.assertEqual(self.contract["ai_endpoint"], "/v2/chat/stream")

    def test_done_and_error_are_callback_compatible_terminal_payloads(self) -> None:
        done = self.contract["ai_events"][-1]
        error = self.contract["error_event"]
        self.assertEqual(done["event"], "done")
        self.assertEqual(done["data"]["status"], "completed")
        self.assertEqual(error["event"], "error")
        self.assertEqual(error["data"]["status"], "failed")
        self.assertIn("completed_at", done["data"])
        self.assertIn("completed_at", error["data"])


if __name__ == "__main__":
    unittest.main()
