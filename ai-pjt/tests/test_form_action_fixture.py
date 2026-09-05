import json
from pathlib import Path
import unittest


class FormActionProducerContractTests(unittest.TestCase):
    def test_ai_output_has_no_approval_or_server_owned_ids(self):
        fixture_path = (
            Path(__file__).resolve().parents[2]
            / 'docs/contracts/fixtures/ai-lifecycle/v2/form-actions.json'
        )
        fixture = json.loads(fixture_path.read_text(encoding='utf-8'))

        for case_name, case in fixture['cases'].items():
            with self.subTest(case=case_name):
                action = case['ai_output_action']
                self.assertEqual(action['target'], 'draft_form')
                for key in (
                    'action_id',
                    'change_id',
                    'mode',
                    'status',
                    'decision_status',
                ):
                    self.assertNotIn(key, action)
                for change in action['changes']:
                    self.assertNotIn('change_id', change)
                    self.assertNotIn('decision_status', change)


if __name__ == '__main__':
    unittest.main()
