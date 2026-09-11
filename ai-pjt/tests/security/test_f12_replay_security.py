from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

import httpx

from tests.instrumentation import fake_callback_masker, fake_instrumentation
from tests.security.service_tool_security_contract import cases_for_owner, load_suite
from transport.contracts import ChatV2Request, ServiceContractError
from transport.runtime import (
    AsyncChatTransport,
    CallbackClient,
    CallbackDeliveryContext,
    InMemoryRequestCheckpointStore,
)


def _request(**changes):
    payload = {
        "contract_version": "2.0",
        "request_id": "F12-SYNTHETIC-REQUEST-RPL-001",
        "session_id": 12,
        "thread_id": "user-3-session-12",
        "step_id": "basic-info",
        "message": "합성 요청입니다.",
        "deadline_at": datetime.now(timezone.utc) + timedelta(seconds=60),
        "customer_info": {"saved_data": {}, "current_form": {}},
        "field_registry": [],
        "chart_payload": None,
    }
    payload.update(changes)
    return ChatV2Request.model_validate(payload)


class F12AIReplaySecurityTests(IsolatedAsyncioTestCase):
    def setUp(self):
        suite = load_suite()
        self.cases = {
            case["id"]: case for case in cases_for_owner(suite, "F12-T03")
        }

    async def test_rpl_001_and_002_schedule_one_graph_and_preserve_the_claim(self):
        replay_case = self.cases["F12-RPL-001"]
        conflict_case = self.cases["F12-RPL-002"]
        processor = AsyncMock(return_value={"response": "완료", "action": None})
        callback = AsyncMock()
        transport = AsyncChatTransport(
            checkpoint_store=InMemoryRequestCheckpointStore(),
            processor=processor,
            callback_client=callback,
            callback_masker=fake_callback_masker(),
        )
        scheduled = []
        request = _request()

        first = await transport.accept(
            request,
            lambda function, argument: scheduled.append((function, argument)),
        )
        replay = await transport.accept(
            request,
            lambda function, argument: scheduled.append((function, argument)),
        )
        with self.assertRaises(ServiceContractError) as raised:
            await transport.accept(
                _request(message="digest가 다른 합성 요청입니다."),
                lambda function, argument: scheduled.append((function, argument)),
            )

        self.assertEqual(first, replay)
        self.assertEqual(len(scheduled), 1)
        self.assertEqual(
            raised.exception.code,
            conflict_case["expected"]["reason_code"],
        )
        await scheduled[0][0](scheduled[0][1])
        processor.assert_awaited_once()
        self.assertEqual(replay_case["expected"]["http_status"], 202)

    async def test_rpl_005_retries_one_frozen_terminal_payload(self):
        case = self.cases["F12-RPL-005"]
        received = []
        source_payload = {
            "request_id": "F12-SYNTHETIC-REQUEST-RPL-005",
            "status": "completed",
            "output": {"response": "확정 응답"},
        }

        def handler(request):
            received.append(request.content)
            status = 200 if len(received) == case["input"]["attempts"] else 503
            return httpx.Response(status, request=request)

        async def mutate_source_during_backoff(_delay):
            source_payload["output"]["response"] = "재생성된 응답"

        client = CallbackClient(
            url="http://backend/api/v2/internal/ai/callback/",
            bearer_token="f12-service-token",
            instrumentation=fake_instrumentation()[0],
            sleeper=mutate_source_during_backoff,
            jitter=lambda: 0,
            client_factory=lambda **kwargs: httpx.AsyncClient(
                transport=httpx.MockTransport(handler),
                **kwargs,
            ),
        )

        await client.send(
            source_payload,
            context=CallbackDeliveryContext.from_request(
                _request(request_id=source_payload["request_id"])
            ),
        )

        self.assertEqual(len(received), case["input"]["attempts"])
        self.assertEqual(len(set(received)), 1)
        self.assertIn("확정 응답".encode(), received[0])
        self.assertNotIn("재생성된 응답".encode(), received[0])
