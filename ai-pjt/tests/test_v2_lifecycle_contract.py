from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase

import httpx
from pydantic import ValidationError

from core.config import settings
from main import app
from transport.contracts import (
    ChatAccepted,
    ChatV2Request,
    CompletedCallback,
    FailedCallback,
)
from transport.runtime import ACTIVE_TTL, TERMINAL_TTL, CallbackClient


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads(
    (
        REPOSITORY_ROOT
        / "contract-fixtures/ai-lifecycle/v2/lifecycle.json"
    ).read_text(encoding="utf-8")
)


class V2AIProviderContractTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.previous_transport = getattr(app.state, "v2_transport", None)
        self.previous_token = settings.ai_service_bearer_token
        settings.ai_service_bearer_token = "fixture-service-token"

        async def accept(request, _schedule):
            return ChatAccepted(request_id=request.request_id)

        app.state.v2_transport = SimpleNamespace(accept=accept)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://ai.test",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        settings.ai_service_bearer_token = self.previous_token
        if self.previous_transport is None:
            del app.state.v2_transport
        else:
            app.state.v2_transport = self.previous_transport

    def authorization(self, fixture_case):
        headers = dict(fixture_case["request"].get("headers", {}))
        if headers.get("Authorization") == "Bearer <AI_SERVICE_BEARER_TOKEN>":
            headers["Authorization"] = "Bearer fixture-service-token"
        return headers

    async def test_accepts_the_frozen_django_request_and_response(self):
        fixture_case = FIXTURE["cases"]["django_ai_accepted"]

        response = await self.client.post(
            fixture_case["request"]["path"],
            headers=self.authorization(fixture_case),
            json=fixture_case["request"]["body"],
        )

        self.assertEqual(response.status_code, fixture_case["response"]["status"])
        self.assertEqual(response.json(), fixture_case["response"]["body"])

    async def test_schema_and_version_errors_match_the_frozen_cases(self):
        for case_name in (
            "django_ai_missing_request_id",
            "django_ai_invalid_session_id_type",
            "contract_version_unsupported",
        ):
            with self.subTest(case=case_name):
                fixture_case = FIXTURE["cases"][case_name]
                response = await self.client.post(
                    fixture_case["request"]["path"],
                    headers=self.authorization(fixture_case),
                    json=fixture_case["request"]["body"],
                )
                self.assertEqual(
                    (response.status_code, response.json()),
                    (
                        fixture_case["response"]["status"],
                        fixture_case["response"]["body"],
                    ),
                )

    async def test_service_authentication_precedes_schema_validation(self):
        fixture_case = FIXTURE["cases"]["service_unauthorized"]

        response = await self.client.post(
            fixture_case["request"]["path"],
            json=fixture_case["request"]["body"],
        )

        self.assertEqual(response.status_code, fixture_case["response"]["status"])
        self.assertEqual(response.json(), fixture_case["response"]["body"])


class V2AICallbackConsumerContractTests(TestCase):
    def test_terminal_callback_models_consume_both_frozen_shapes(self):
        completed = FIXTURE["cases"]["callback_completed_proposal"]["request"]["body"]
        failed = FIXTURE["cases"]["callback_failed_llm_timeout"]["request"]["body"]

        self.assertEqual(
            CompletedCallback.model_validate(completed).model_dump(mode="json"),
            completed,
        )
        self.assertEqual(
            FailedCallback.model_validate(failed).model_dump(mode="json"),
            failed,
        )

    def test_request_model_rejects_the_frozen_invalid_type(self):
        invalid = FIXTURE["cases"]["django_ai_invalid_session_id_type"]["request"]["body"]
        with self.assertRaises(ValidationError):
            ChatV2Request.model_validate(invalid)

    def test_runtime_ttls_and_callback_policy_match_fixture_timing(self):
        timing = FIXTURE["timing"]
        callback = CallbackClient(
            url="http://backend.test/api/v2/internal/ai/callback/",
            bearer_token="fixture-token",
        )

        self.assertEqual(ACTIVE_TTL, timedelta(seconds=timing["active_state_ttl_seconds"]))
        self.assertEqual(TERMINAL_TTL, timedelta(seconds=timing["terminal_state_ttl_seconds"]))
        self.assertEqual(callback._timeout_seconds, timing["callback_timeout_seconds"])
        self.assertEqual(callback._max_attempts, timing["callback_max_attempts"])
