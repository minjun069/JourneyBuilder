from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock
from unittest.mock import patch

import httpx

from transport.contracts import (
    ChatAccepted,
    ChatV2Request,
    ServiceContractError,
    verify_service_authorization,
)
from transport.runtime import (
    AsyncChatTransport,
    CallbackClient,
    ExecutionBudget,
    InMemoryRequestCheckpointStore,
    request_digest,
)


def make_request(**changes) -> ChatV2Request:
    payload = {
        "contract_version": "2.0",
        "request_id": "ai_req_transport",
        "session_id": 1,
        "thread_id": "user-3-session-1",
        "step_id": "basic-info",
        "message": "월급은 세후 300만 원이에요.",
        "deadline_at": datetime.now(timezone.utc) + timedelta(seconds=60),
        "customer_info": {"saved_data": {}, "current_form": {}},
        "field_registry": [],
        "chart_payload": None,
    }
    payload.update(changes)
    return ChatV2Request.model_validate(payload)


class ExecutionBudgetTests(IsolatedAsyncioTestCase):
    def test_budget_uses_one_outer_deadline_and_three_task_limit(self):
        deadline = datetime.now(timezone.utc) + timedelta(seconds=30)
        budget = ExecutionBudget(
            deadline_at=deadline,
            started_at_monotonic=0,
        )

        for _ in range(3):
            budget = budget.consume_task()

        self.assertEqual(budget.deadline_at, deadline)
        self.assertEqual(budget.tasks_used, 3)
        with self.assertRaises(RuntimeError):
            budget.consume_task()


class ServiceAuthorizationTests(IsolatedAsyncioTestCase):
    def test_bearer_is_required_and_empty_token_never_bypasses_auth(self):
        for supplied, expected in (
            (None, "test-token"),
            ("Bearer wrong", "test-token"),
            ("Bearer ", ""),
        ):
            with self.subTest(supplied=supplied, expected=expected):
                with self.assertRaises(ServiceContractError) as raised:
                    verify_service_authorization(supplied, expected)
                self.assertEqual(raised.exception.code, "SERVICE_UNAUTHORIZED")

        verify_service_authorization("Bearer test-token", "test-token")


class CallbackClientTests(IsolatedAsyncioTestCase):
    async def test_retryable_failures_use_four_attempts_and_same_payload(self):
        received = []

        def handler(request):
            received.append((request.headers["Authorization"], request.content))
            status = 200 if len(received) == 4 else 503
            return httpx.Response(status, request=request)

        transport = httpx.MockTransport(handler)
        sleeper = AsyncMock()
        client = CallbackClient(
            url="http://backend/api/v2/internal/ai/callback/",
            bearer_token="test-token",
            sleeper=sleeper,
            jitter=lambda: 0,
            client_factory=lambda **kwargs: httpx.AsyncClient(
                transport=transport,
                **kwargs,
            ),
        )
        payload = {"request_id": "ai_req_transport", "status": "completed"}

        await client.send(payload)

        self.assertEqual(len(received), 4)
        self.assertEqual({body for _, body in received}, {received[0][1]})
        self.assertTrue(all(auth == "Bearer test-token" for auth, _ in received))
        self.assertEqual(
            [call.args[0] for call in sleeper.await_args_list],
            [1, 2, 4],
        )

    async def test_non_retryable_callback_response_stops_immediately(self):
        attempts = 0

        def handler(request):
            nonlocal attempts
            attempts += 1
            return httpx.Response(401, request=request)

        transport = httpx.MockTransport(handler)
        client = CallbackClient(
            url="http://backend/api/v2/internal/ai/callback/",
            bearer_token="test-token",
            sleeper=AsyncMock(),
            client_factory=lambda **kwargs: httpx.AsyncClient(
                transport=transport,
                **kwargs,
            ),
        )

        with self.assertRaises(httpx.HTTPStatusError):
            await client.send({"status": "completed"})
        self.assertEqual(attempts, 1)


class RequestCheckpointTests(IsolatedAsyncioTestCase):
    async def test_identical_request_is_reused_but_changed_payload_conflicts(self):
        store = InMemoryRequestCheckpointStore()
        request = make_request()

        first = await store.claim(request, request_digest(request))
        replay = await store.claim(request, request_digest(request))

        self.assertTrue(first.created)
        self.assertFalse(replay.created)
        changed = make_request(message="다른 요청")
        with self.assertRaises(ServiceContractError) as raised:
            await store.claim(changed, request_digest(changed))
        self.assertEqual(raised.exception.code, "REQUEST_ID_CONFLICT")

    async def test_terminal_payload_is_immutable(self):
        store = InMemoryRequestCheckpointStore()
        request = make_request()
        await store.claim(request, request_digest(request))
        await store.save_terminal(
            request.request_id,
            {"status": "completed", "output": {"response": "ok"}},
        )

        with self.assertRaises(RuntimeError):
            await store.save_terminal(
                request.request_id,
                {"status": "failed", "error": {"code": "INTERNAL_ERROR"}},
            )


class AsyncChatTransportTests(IsolatedAsyncioTestCase):
    async def test_accept_replay_schedules_graph_only_once(self):
        store = InMemoryRequestCheckpointStore()
        processor = AsyncMock(return_value={"response": "완료", "action": None})
        callback = AsyncMock()
        transport = AsyncChatTransport(
            checkpoint_store=store,
            processor=processor,
            callback_client=callback,
        )
        scheduled = []
        request = make_request()

        await transport.accept(request, lambda function, argument: scheduled.append((function, argument)))
        await transport.accept(request, lambda function, argument: scheduled.append((function, argument)))

        self.assertEqual(len(scheduled), 1)
        await scheduled[0][0](scheduled[0][1])
        processor.assert_awaited_once()

    async def test_callback_retry_entry_reuses_saved_terminal_result(self):
        store = InMemoryRequestCheckpointStore()
        processor = AsyncMock(return_value={"response": "완료", "action": None})
        callback = AsyncMock()
        transport = AsyncChatTransport(
            checkpoint_store=store,
            processor=processor,
            callback_client=callback,
        )
        request = make_request()
        await store.claim(request, request_digest(request))

        first = await transport.run(request)
        second = await transport.run(request)

        self.assertEqual(first, second)
        processor.assert_awaited_once()
        self.assertEqual(callback.send.await_count, 2)
        checkpoint = await store.get(request.request_id)
        self.assertTrue(checkpoint.callback_delivered)

    async def test_expired_request_emits_safe_timeout_callback(self):
        store = InMemoryRequestCheckpointStore()
        processor = AsyncMock()
        callback = AsyncMock()
        transport = AsyncChatTransport(
            checkpoint_store=store,
            processor=processor,
            callback_client=callback,
        )
        request = make_request(
            deadline_at=datetime.now(timezone.utc) - timedelta(seconds=1)
        )
        await store.claim(request, request_digest(request))

        payload = await transport.run(request)

        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["error"]["code"], "LLM_TIMEOUT")
        processor.assert_not_awaited()


class V2EndpointTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from main import app

        self.app = app
        self.transport = AsyncMock()
        self.transport.accept.return_value = ChatAccepted(
            request_id="ai_req_transport"
        )
        self.app.state.v2_transport = self.transport

    async def _post(self, payload, *, authorization=None):
        headers = {}
        if authorization is not None:
            headers["Authorization"] = authorization
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://ai",
        ) as client:
            return await client.post("/v2/chat", json=payload, headers=headers)

    async def test_authentication_fails_before_empty_body_schema(self):
        response = await self._post({})

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "SERVICE_UNAUTHORIZED")
        self.transport.accept.assert_not_awaited()

    async def test_valid_v2_request_returns_accepted_contract(self):
        request = make_request()
        with patch("main.settings.ai_service_bearer_token", "test-token"):
            response = await self._post(
                request.model_dump(mode="json"),
                authorization="Bearer test-token",
            )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            response.json(),
            {
                "contract_version": "2.0",
                "request_id": "ai_req_transport",
                "status": "accepted",
            },
        )
        self.transport.accept.assert_awaited_once()

    async def test_strict_session_type_is_rejected(self):
        payload = make_request().model_dump(mode="json")
        payload["session_id"] = "one"
        with patch("main.settings.ai_service_bearer_token", "test-token"):
            response = await self._post(
                payload,
                authorization="Bearer test-token",
            )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "REQUEST_SCHEMA_INVALID")
