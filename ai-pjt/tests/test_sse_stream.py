"""Legacy streaming endpoints stay disabled after the v2 contract cutover."""

from __future__ import annotations

import json
from unittest import IsolatedAsyncioTestCase, TestCase

import httpx

from main import _encode_sse, app


def parse_sse_frame(frame: str) -> tuple[str, dict]:
    lines = frame.rstrip("\n").splitlines()
    event = lines[0].removeprefix("event: ")
    data = json.loads(lines[1].removeprefix("data: "))
    return event, data


class SSEEncodingTests(TestCase):
    def test_sse_frame_uses_named_event_and_json_data(self):
        frame = _encode_sse("delta", {"content": "은퇴\n준비"})

        self.assertTrue(frame.endswith("\n\n"))
        event, data = parse_sse_frame(frame)
        self.assertEqual(event, "delta")
        self.assertEqual(data, {"content": "은퇴\n준비"})


class LegacyEndpointTests(IsolatedAsyncioTestCase):
    async def test_legacy_chat_endpoints_do_not_parse_or_forward_payloads(self):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://ai",
        ) as client:
            for path in ("/chat", "/chat/stream"):
                with self.subTest(path=path):
                    response = await client.post(
                        path,
                        content=b"not-json-and-must-not-be-parsed",
                    )
                    self.assertEqual(response.status_code, 410)
                    self.assertEqual(
                        response.json()["error"]["code"],
                        "CONTRACT_VERSION_UNSUPPORTED",
                    )


if __name__ == "__main__":
    import unittest

    unittest.main()
