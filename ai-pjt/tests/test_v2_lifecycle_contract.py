from __future__ import annotations

import json
from pathlib import Path
from unittest import TestCase

from pydantic import ValidationError

from transport.contracts import ChatV2Request, CompletedCallback, FailedCallback


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads(
    (
        REPOSITORY_ROOT
        / "contract-fixtures/ai-lifecycle/v2/lifecycle.json"
    ).read_text(encoding="utf-8")
)


class V2AISSEConsumerContractTests(TestCase):
    def test_request_model_consumes_the_frozen_sse_shape(self):
        request = FIXTURE["cases"]["django_ai_stream"]["request"]["body"]

        validated = ChatV2Request.model_validate(request).model_dump(mode="json")
        for key, value in request.items():
            with self.subTest(field=key):
                self.assertEqual(validated[key], value)

    def test_terminal_models_consume_completed_and_failed_sse_shapes(self):
        completed = FIXTURE["cases"]["terminal_completed_proposal"]["terminal_payload"]
        failed = FIXTURE["cases"]["terminal_failed_llm_timeout"]["terminal_payload"]

        self.assertEqual(
            CompletedCallback.model_validate(completed).model_dump(mode="json"),
            completed,
        )
        self.assertEqual(
            FailedCallback.model_validate(failed).model_dump(mode="json"),
            failed,
        )

    def test_request_model_rejects_missing_and_invalid_identity(self):
        for case_name in (
            "django_ai_stream_missing_request_id",
            "django_ai_stream_invalid_session_id_type",
        ):
            with self.subTest(case=case_name), self.assertRaises(ValidationError):
                ChatV2Request.model_validate(
                    FIXTURE["cases"][case_name]["request"]["body"]
                )

    def test_fixture_declares_non_streaming_routes_removed(self):
        removed = set(FIXTURE["removed_routes"])

        self.assertIn("POST /v2/chat", removed)
        self.assertIn("POST /api/v2/internal/ai/callback/", removed)
        self.assertIn(
            "GET /api/v2/consultations/{session_id}/ai-responses/{request_id}/",
            removed,
        )
