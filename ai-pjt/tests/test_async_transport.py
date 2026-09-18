from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock
from unittest.mock import patch

import httpx

from core.monitoring import OperationStage, OperationStatus
from core.runtime_instrumentation import RuntimeStage, RuntimeStatus
from tests.instrumentation import fake_callback_masker, fake_instrumentation
from transport.contracts import (
    ChatV2Request,
    ServiceContractError,
    verify_service_authorization,
)
from transport.runtime import (
    AsyncChatTransport,
    CallbackClient,
    CallbackDeliveryContext,
    ExecutionBudget,
    InMemoryRequestCheckpointStore,
    request_digest,
)


def make_request(**changes) -> ChatV2Request:
    payload = {
        "contract_version": "2.0",
        "request_id": "ai_req_transport",
        "payload_digest": "0" * 64,
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


class FeatureControlContractTests(IsolatedAsyncioTestCase):
    def test_error_review_is_save_bound_without_a_trigger_field(self):
        review = make_request(request_mode="error_review")
        self.assertEqual(review.request_mode, "error_review")

        with self.assertRaises(ValueError):
            make_request(request_mode="error_review", review_trigger="save")

    def test_legacy_elicit_action_is_rejected(self):
        with self.assertRaises(ValueError):
            make_request(elicit_action="start")

    def test_presentation_entry_rejects_legacy_review_trigger(self):
        request = make_request(request_mode="presentation_entry")
        self.assertEqual(request.request_mode, "presentation_entry")

        with self.assertRaises(ValueError):
            make_request(request_mode="presentation_entry", review_trigger="save")


class CallbackClientTests(IsolatedAsyncioTestCase):
    async def test_retryable_failures_use_four_attempts_and_same_payload(self):
        received = []
        instrumentation, sink = fake_instrumentation()

        def handler(request):
            received.append((request.headers["Authorization"], request.content))
            status = 200 if len(received) == 4 else 503
            return httpx.Response(status, request=request)

        transport = httpx.MockTransport(handler)
        sleeper = AsyncMock()
        client = CallbackClient(
            url="http://backend/api/v2/internal/ai/callback/",
            bearer_token="test-token",
            instrumentation=instrumentation,
            sleeper=sleeper,
            jitter=lambda: 0,
            client_factory=lambda **kwargs: httpx.AsyncClient(
                transport=transport,
                **kwargs,
            ),
        )
        payload = {"request_id": "ai_req_transport", "status": "completed"}

        await client.send(
            payload,
            context=CallbackDeliveryContext.from_request(make_request()),
        )

        self.assertEqual(len(received), 4)
        self.assertEqual({body for _, body in received}, {received[0][1]})
        self.assertTrue(all(auth == "Bearer test-token" for auth, _ in received))
        self.assertEqual(
            [call.args[0] for call in sleeper.await_args_list],
            [1, 2, 4],
        )
        callback_events = [
            event for event in sink.baseline_events
            if event.stage is OperationStage.CALLBACK
        ]
        self.assertEqual(
            [event.status for event in callback_events],
            [
                OperationStatus.FAILED,
                OperationStatus.FAILED,
                OperationStatus.FAILED,
                OperationStatus.SUCCEEDED,
            ],
        )

    async def test_non_retryable_callback_response_stops_immediately(self):
        attempts = 0
        instrumentation, sink = fake_instrumentation()

        def handler(request):
            nonlocal attempts
            attempts += 1
            return httpx.Response(401, request=request)

        transport = httpx.MockTransport(handler)
        client = CallbackClient(
            url="http://backend/api/v2/internal/ai/callback/",
            bearer_token="test-token",
            instrumentation=instrumentation,
            sleeper=AsyncMock(),
            client_factory=lambda **kwargs: httpx.AsyncClient(
                transport=transport,
                **kwargs,
            ),
        )

        with self.assertRaises(httpx.HTTPStatusError):
            await client.send(
                {"status": "completed"},
                context=CallbackDeliveryContext.from_request(make_request()),
            )
        self.assertEqual(attempts, 1)
        self.assertEqual(sink.baseline_events[0].status, OperationStatus.FAILED)
        self.assertEqual(sink.baseline_events[0].error_code, "callback_http_401")

    def test_callback_client_rejects_missing_instrumentation(self):
        with self.assertRaises(TypeError):
            CallbackClient(
                url="http://backend/api/v2/internal/ai/callback/",
                bearer_token="test-token",
                instrumentation=None,  # type: ignore[arg-type]
            )


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
    def test_transport_rejects_missing_callback_masker(self):
        with self.assertRaises(TypeError):
            AsyncChatTransport(
                checkpoint_store=InMemoryRequestCheckpointStore(),
                processor=AsyncMock(),
                callback_client=AsyncMock(),
                callback_masker=None,  # type: ignore[arg-type]
                instrumentation=fake_instrumentation()[0],
            )

    def test_transport_rejects_missing_request_instrumentation(self):
        with self.assertRaises(TypeError):
            AsyncChatTransport(
                checkpoint_store=InMemoryRequestCheckpointStore(),
                processor=AsyncMock(),
                callback_client=AsyncMock(),
                callback_masker=fake_callback_masker(),
                instrumentation=None,  # type: ignore[arg-type]
            )

    async def test_accept_replay_schedules_graph_only_once(self):
        store = InMemoryRequestCheckpointStore()
        processor = AsyncMock(return_value={"response": "완료", "action": None})
        callback = AsyncMock()
        transport = AsyncChatTransport(
            checkpoint_store=store,
            processor=processor,
            callback_client=callback,
            callback_masker=fake_callback_masker(),
            instrumentation=fake_instrumentation()[0],
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
            callback_masker=fake_callback_masker(),
            instrumentation=fake_instrumentation()[0],
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

    async def test_callback_is_masked_before_checkpoint_and_delivery(self):
        store = InMemoryRequestCheckpointStore()
        processor = AsyncMock(return_value={
            "response": "연락처 010-9876-5432로 안내했습니다.",
            "step_id": "basic-info",
            "action": {
                "type": "form_change",
                "target": "draft_form",
                "user_requested": False,
                "confidence": 0.9,
                "changes": [{
                    "section": "basic_info",
                    "path": "monthly_income",
                    "operation": "set",
                    "value": 3_000_000,
                    "reason": "사용자 요청",
                }],
            },
        })
        callback = AsyncMock()
        instrumentation, sink = fake_instrumentation()
        transport = AsyncChatTransport(
            checkpoint_store=store,
            processor=processor,
            callback_client=callback,
            callback_masker=fake_callback_masker(),
            instrumentation=instrumentation,
        )
        request = make_request()
        await store.claim(request, request_digest(request))

        terminal = await transport.run(request)
        checkpoint = await store.get(request.request_id)

        self.assertNotIn("010-9876-5432", str(terminal))
        self.assertEqual(
            terminal["output"]["action"]["changes"][0]["value"],
            3_000_000,
        )
        self.assertEqual(checkpoint.terminal_payload, terminal)
        self.assertEqual(callback.send.await_args.args[0], terminal)
        self.assertEqual(
            callback.send.await_args.kwargs["context"].plan_id,
            f"plan-{request.request_id}",
        )
        validation_events = [
            event
            for event in sink.baseline_events
            if getattr(event, "stage", None) is OperationStage.OUTPUT_VALIDATION
        ]
        self.assertEqual(len(validation_events), 1)
        self.assertEqual(validation_events[0].status, OperationStatus.SUCCEEDED)
        output_guardrails = [
            event
            for event in sink.security_events
            if event.boundary == "output_guardrail"
        ]
        self.assertEqual(len(output_guardrails), 1)
        self.assertEqual(output_guardrails[0].decision.value, "allowed")

    async def test_expired_request_emits_safe_timeout_callback(self):
        store = InMemoryRequestCheckpointStore()
        processor = AsyncMock()
        callback = AsyncMock()
        instrumentation, sink = fake_instrumentation()
        transport = AsyncChatTransport(
            checkpoint_store=store,
            processor=processor,
            callback_client=callback,
            callback_masker=fake_callback_masker(),
            instrumentation=instrumentation,
        )
        request = make_request(
            deadline_at=datetime.now(timezone.utc) - timedelta(seconds=1)
        )
        await store.claim(request, request_digest(request))

        payload = await transport.run(request)

        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["error"]["code"], "LLM_TIMEOUT")
        processor.assert_not_awaited()
        request_events = [
            event for event in sink.runtime_events
            if event.stage is RuntimeStage.REQUEST_PROCESSING
        ]
        self.assertEqual(
            [event.status for event in request_events],
            [RuntimeStatus.STARTED, RuntimeStatus.FAILED],
        )
        self.assertEqual(request_events[-1].reason_code, "request_timeout")
