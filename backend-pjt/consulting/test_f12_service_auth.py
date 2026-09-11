from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from rest_framework.test import APIRequestFactory

from .v2_views import RetiredAICallbackView, V2CallbackView


SUITE_PATH = (
    Path(__file__).resolve().parents[2]
    / "ai-pjt/tests/security/service-tool-security-cases.json"
)


def _load_t02_cases():
    with SUITE_PATH.open(encoding="utf-8") as suite_file:
        suite = json.load(suite_file)
    return {
        case["id"]: case
        for case in suite["cases"]
        if case["downstream_owner"] == "F12-T02"
    }


class F12CallbackAuthenticationContractTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.cases = _load_t02_cases()

    @override_settings(AI_SERVICE_BEARER_TOKEN="f12-service-token")
    @patch("consulting.v2_views.RedisLifecycleStore")
    @patch("consulting.v2_views.validate_terminal_callback")
    def test_auth_002_blocks_before_schema_or_state_access(
        self,
        validate_callback,
        lifecycle_store,
    ):
        case = self.cases["F12-AUTH-002"]
        request = self.factory.post(
            "/api/v2/internal/ai/callback/",
            {"request_id": case["input"]["request_id"]},
            format="json",
            HTTP_AUTHORIZATION="Bearer wrong-token",
        )

        response = V2CallbackView.as_view()(request)

        self.assertEqual(response.status_code, case["expected"]["http_status"])
        self.assertEqual(
            response.data["error"]["code"],
            case["expected"]["reason_code"],
        )
        validate_callback.assert_not_called()
        lifecycle_store.assert_not_called()

    def test_legacy_callback_path_is_unconditionally_retired(self):
        request = self.factory.post(
            "/api/ai/callback/",
            {"request_id": "F12-SYNTHETIC-LEGACY-CALLBACK"},
            format="json",
            HTTP_X_INTERNAL_SECRET="formerly-valid-secret",
        )

        response = RetiredAICallbackView.as_view()(request)

        self.assertEqual(response.status_code, 410)
        self.assertEqual(
            response.data["error"]["code"],
            "CONTRACT_VERSION_UNSUPPORTED",
        )
