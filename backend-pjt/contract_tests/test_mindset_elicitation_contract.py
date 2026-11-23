"""Provider/consumer agreement for the single shared mindset fixture."""

import json
from pathlib import Path
import unittest

from consulting.elicitation_contract import (
    FIELDS, MODE, START_CONTENT, STEP, validate_output, validate_request,
)


class MindsetElicitationContractTests(unittest.TestCase):
    def test_shared_request_and_result_cases(self):
        fixture = json.loads((
            Path(__file__).resolve().parents[2]
            / 'contract-fixtures/ai-lifecycle/v2/mindset-elicitation.json'
        ).read_text(encoding='utf-8'))
        self.assertEqual(fixture['fields'], list(FIELDS))
        self.assertEqual(fixture['request_mode'], MODE)
        self.assertEqual(fixture['step_id'], STEP)
        self.assertEqual(fixture['max_answer_count'], 3)
        for case in fixture['request_cases']:
            with self.subTest(case=case['id']):
                def check():
                    return validate_request(
                        case['state'], request_mode=MODE, step_id=STEP,
                        content=START_CONTENT,
                    )
                if case['valid']:
                    self.assertEqual(check(), case['state'])
                else:
                    with self.assertRaises(ValueError):
                        check()
        for case in fixture['result_cases']:
            with self.subTest(case=case['id']):
                output = {'step_id': STEP, 'elicitation': case['result']}
                if case['valid']:
                    validate_output(output)
                else:
                    with self.assertRaises(ValueError):
                        validate_output(output)
