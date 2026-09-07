from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


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
    PROCESSING_DEADLINE_SECONDS,
    TERMINAL_STATE_TTL_SECONDS,
    LifecycleError,
    bind_terminal_callback,
    build_v2_ai_payload,
    decide_change,
    expire_if_due,
    require_idempotency_key,
    validate_browser_message,
    validate_terminal_callback,
    v2_success,
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
        for case_name in ('browser_message_missing_content', 'browser_message_invalid_content_type'):
            with self.subTest(case=case_name):
                fixture_case = FIXTURE['cases'][case_name]
                self.assert_lifecycle_error(
                    lambda: validate_browser_message(fixture_case['request']['body']),
                    fixture_case,
                )

        missing_key = FIXTURE['cases']['browser_message_missing_idempotency_key']
        self.assert_lifecycle_error(
            lambda: require_idempotency_key(SimpleNamespace(headers={})),
            missing_key,
        )

    @patch('consulting.ai_lifecycle.get_step_field_registry')
    @patch('consulting.ai_lifecycle.build_customer_info')
    def test_django_outbound_payload_matches_ai_provider_fixture(self, customer_info, registry):
        fixture_body = FIXTURE['cases']['django_ai_accepted']['request']['body']
        customer_info.return_value = fixture_body['customer_info']
        registry.return_value = fixture_body['field_registry']
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

    def test_callback_shapes_and_errors_match_frozen_cases(self):
        completed = FIXTURE['cases']['callback_completed_proposal']['request']['body']
        failed = FIXTURE['cases']['callback_failed_llm_timeout']['request']['body']
        self.assertEqual(validate_terminal_callback(completed), completed)
        self.assertEqual(validate_terminal_callback(failed), failed)

        missing = FIXTURE['cases']['callback_missing_status']
        self.assert_lifecycle_error(
            lambda: validate_terminal_callback(missing['request']['body']),
            missing,
        )

        state = {
            'request_id': completed['request_id'],
            'session_id': 1,
            'thread_id': 'user-3-session-1',
            'status': 'processing',
        }
        mismatch = FIXTURE['cases']['callback_binding_mismatch']
        self.assert_lifecycle_error(
            lambda: bind_terminal_callback(state, mismatch['request']['body']),
            mismatch,
        )

    def test_processing_expiry_matches_polling_fixture(self):
        fixture_response = FIXTURE['cases']['polling_expired']['response']['body']
        deadline = datetime(2026, 9, 2, 6, 1, 30, tzinfo=timezone.utc)
        state = {
            'request_id': fixture_response['data']['request_id'],
            'status': 'processing',
            'processing_deadline_at': deadline.isoformat(),
        }

        expired, changed = expire_if_due(state, now=deadline)

        self.assertTrue(changed)
        self.assertEqual(
            v2_success({
                'request_id': expired['request_id'],
                'status': expired['status'],
                'error': expired['error'],
            }),
            fixture_response,
        )

    def test_individual_decisions_match_both_frozen_responses(self):
        action = deepcopy(
            FIXTURE['cases']['polling_completed_proposal']['response']['body']
            ['data']['message']['extracted_data']['action']
        )
        message = SimpleNamespace(extracted_data={'action': action}, save=Mock())

        approved = decide_change(
            message,
            change_id='chg_fixture_income',
            decision='approved',
        )
        rejected = decide_change(
            message,
            change_id='chg_fixture_job',
            decision='rejected',
        )

        self.assertEqual(
            v2_success(approved),
            FIXTURE['cases']['change_approve_first']['response']['body'],
        )
        self.assertEqual(
            v2_success(rejected),
            FIXTURE['cases']['change_reject_second']['response']['body'],
        )

    def test_backend_timing_constants_match_the_shared_fixture(self):
        timing = FIXTURE['timing']
        self.assertEqual(CONTRACT_VERSION, FIXTURE['contract_version'])
        self.assertEqual(AI_EXECUTION_DEADLINE_SECONDS, timing['ai_execution_deadline_seconds'])
        self.assertEqual(PROCESSING_DEADLINE_SECONDS, timing['processing_deadline_seconds'])
        self.assertEqual(ACTIVE_STATE_TTL_SECONDS, timing['active_state_ttl_seconds'])
        self.assertEqual(TERMINAL_STATE_TTL_SECONDS, timing['terminal_state_ttl_seconds'])
        self.assertEqual(TERMINAL_STATE_TTL_SECONDS, timing['idempotency_ttl_seconds'])


if __name__ == '__main__':
    unittest.main()
