from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django  # noqa: E402

django.setup()

from consulting.ai_lifecycle import (  # noqa: E402
    ACTIVE_STATE_TTL_SECONDS,
    AI_EXECUTION_DEADLINE_SECONDS,
    CONTRACT_VERSION,
    TERMINAL_STATE_TTL_SECONDS,
    LifecycleError,
    bind_terminal_result,
    build_v2_ai_payload,
    require_idempotency_key,
    validate_browser_message,
    validate_terminal_payload,
)


FIXTURE = json.loads(
    (
        REPOSITORY_ROOT
        / 'contract-fixtures/ai-lifecycle/v2/lifecycle.json'
    ).read_text(encoding='utf-8')
)


class V2BackendProviderConsumerContractTests(unittest.TestCase):
    def assert_lifecycle_error(self, callable_, fixture_case):
        with self.assertRaises(LifecycleError) as captured:
            callable_()
        self.assertEqual(captured.exception.status_code, fixture_case['response']['status'])
        self.assertEqual(captured.exception.response_body(), fixture_case['response']['body'])

    def test_fixture_contains_every_required_case_category(self):
        categories = {case['category'] for case in FIXTURE['cases'].values()}
        self.assertLessEqual(set(FIXTURE['required_case_categories']), categories)

    def test_browser_validation_errors_match_frozen_responses(self):
        for case_name in ('browser_stream_missing_content', 'browser_stream_invalid_content_type'):
            with self.subTest(case=case_name):
                fixture_case = FIXTURE['cases'][case_name]
                self.assert_lifecycle_error(
                    lambda: validate_browser_message(fixture_case['request']['body']),
                    fixture_case,
                )

        missing_key = FIXTURE['cases']['browser_stream_missing_idempotency_key']
        self.assert_lifecycle_error(
            lambda: require_idempotency_key(SimpleNamespace(headers={})),
            missing_key,
        )

    @patch('consulting.ai_lifecycle.get_step_field_registry')
    @patch('consulting.ai_lifecycle.build_tool_context')
    @patch('consulting.ai_lifecycle.build_customer_info')
    def test_django_outbound_payload_matches_ai_provider_fixture(
        self,
        customer_info,
        tool_context,
        registry,
    ):
        fixture_body = FIXTURE['cases']['django_ai_stream']['request']['body']
        customer_info.return_value = fixture_body['customer_info']
        registry.return_value = fixture_body['field_registry']
        tool_context.return_value = fixture_body['tool_context']
        deadline = datetime.fromisoformat(fixture_body['deadline_at'].replace('Z', '+00:00'))

        actual = build_v2_ai_payload(
            request_id=fixture_body['request_id'],
            session=SimpleNamespace(id=fixture_body['session_id']),
            user=SimpleNamespace(id=3),
            message=fixture_body['message'],
            step_id=fixture_body['step_id'],
            form_data=fixture_body['customer_info']['current_form'],
            now=deadline - timedelta(seconds=AI_EXECUTION_DEADLINE_SECONDS),
        )

        self.assertEqual(actual, fixture_body)

    def test_terminal_shapes_and_errors_match_frozen_cases(self):
        completed = FIXTURE['cases']['terminal_completed_proposal']['terminal_payload']
        failed = FIXTURE['cases']['terminal_failed_llm_timeout']['terminal_payload']
        self.assertEqual(validate_terminal_payload(completed), completed)
        self.assertEqual(validate_terminal_payload(failed), failed)

        missing = FIXTURE['cases']['terminal_missing_status']
        with self.assertRaises(LifecycleError) as captured:
            validate_terminal_payload(missing['terminal_payload'])
        self.assertEqual(captured.exception.status_code, missing['expected_error']['status'])
        self.assertEqual(captured.exception.code, missing['expected_error']['code'])

        state = {
            'request_id': completed['request_id'],
            'session_id': 1,
            'thread_id': 'user-3-session-1',
            'status': 'processing',
        }
        mismatch = FIXTURE['cases']['terminal_binding_mismatch']
        with self.assertRaises(LifecycleError) as captured:
            bind_terminal_result(state, mismatch['terminal_payload'])
        self.assertEqual(captured.exception.status_code, mismatch['expected_error']['status'])
        self.assertEqual(captured.exception.code, mismatch['expected_error']['code'])

    def test_backend_timing_constants_match_the_shared_fixture(self):
        timing = FIXTURE['timing']
        self.assertEqual(CONTRACT_VERSION, FIXTURE['contract_version'])
        self.assertEqual(AI_EXECUTION_DEADLINE_SECONDS, timing['ai_execution_deadline_seconds'])
        self.assertEqual(ACTIVE_STATE_TTL_SECONDS, timing['active_state_ttl_seconds'])
        self.assertEqual(TERMINAL_STATE_TTL_SECONDS, timing['terminal_state_ttl_seconds'])
        self.assertEqual(TERMINAL_STATE_TTL_SECONDS, timing['idempotency_ttl_seconds'])


if __name__ == '__main__':
    unittest.main()
