import json
from pathlib import Path
import sys
import unittest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))

from consulting.form_actions import normalize_form_action  # noqa: E402


class FormActionPolicyContractTests(unittest.TestCase):
    def action(self, *, requested):
        return normalize_form_action({
            'action': {
                'type': 'form_change',
                'target': 'draft_form',
                'user_requested': requested,
                'changes': [{
                    'section': 'basic_info',
                    'path': 'monthly_income',
                    'operation': 'set',
                    'value': 3000000,
                }],
            },
        }, 'basic-info')

    def test_user_request_never_bypasses_approval(self):
        for requested in (True, False):
            with self.subTest(requested=requested):
                self.assertEqual(
                    self.action(requested=requested)['mode'],
                    'proposal',
                )

    def test_every_fixture_change_starts_pending_and_has_a_frontend_decision(self):
        fixture_path = (
            REPOSITORY_ROOT
            / 'contract-fixtures/ai-lifecycle/v2/form-actions.json'
        )
        fixture = json.loads(fixture_path.read_text(encoding='utf-8'))

        for case_name, case in fixture['cases'].items():
            with self.subTest(case=case_name):
                action = case['django_normalized_action']
                self.assertEqual(action['mode'], 'proposal')
                self.assertEqual(action['status'], 'pending')
                self.assertTrue(all(
                    change['decision_status'] == 'pending'
                    for change in action['changes']
                ))
                self.assertEqual(
                    {change['change_id'] for change in action['changes']},
                    {decision['change_id'] for decision in case['frontend_decisions']},
                )

    def test_backend_normalizer_matches_each_fixture_before_server_ids_are_added(self):
        fixture_path = (
            REPOSITORY_ROOT
            / 'contract-fixtures/ai-lifecycle/v2/form-actions.json'
        )
        fixture = json.loads(fixture_path.read_text(encoding='utf-8'))

        for case_name, case in fixture['cases'].items():
            with self.subTest(case=case_name):
                actual = normalize_form_action(
                    {'action': case['ai_output_action']},
                    case['step_id'],
                )
                expected = dict(case['django_normalized_action'])
                expected.pop('action_id')
                expected.pop('status')
                for change in expected['changes']:
                    change.pop('change_id')
                    change.pop('decision_status')

                self.assertEqual(actual, expected)


if __name__ == '__main__':
    unittest.main()
