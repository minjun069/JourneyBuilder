"""Exercise actual ORM, report generation and simulation without a service DB."""

import json
from pathlib import Path
import subprocess
import sys
import unittest

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def generate(prefix=""):
    program = prefix + "\nfrom devtools.export_presentation_lab import export_bundle\n"
    program += "from django.core.serializers.json import DjangoJSONEncoder\nimport json\n"
    program += "print(json.dumps(export_bundle(), cls=DjangoJSONEncoder, allow_nan=False))\n"
    result = subprocess.run([sys.executable, "-c", program], cwd=BACKEND_ROOT,
                            check=True, capture_output=True, text=True, timeout=60)
    return json.loads(result.stdout)


class PresentationLabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = generate()
        cls.scenarios = {scenario["id"]: scenario for scenario in cls.bundle["scenarios"]}

    def reports(self, scenario):
        return {row["report_type"]: row["content"] for row in scenario["reports"]}

    def test_all_reports_share_the_same_input_and_policy(self):
        self.assertEqual(len(self.scenarios), 4)
        for scenario in self.scenarios.values():
            rows = scenario["reports"]
            self.assertEqual(len({row["report_type"] for row in rows}), 9)
            self.assertTrue(all(row["status"] == "ready" for row in rows))
            self.assertEqual(len({row["content"]["input_snapshot_digest"] for row in rows}), 1)
            self.assertEqual(len({row["content"]["calculation_policy_version"] for row in rows}), 1)

    def test_scenarios_exercise_negative_and_empty_values(self):
        self.assertLess(self.reports(self.scenarios["deficit"])["cash_flow"]["net_cash_flow"], 0)
        self.assertLess(self.reports(self.scenarios["high-debt"])["asset_statement"]["net_worth"], 0)
        self.assertEqual(self.reports(self.scenarios["no-assets"])["asset_statement"]["total_assets"], 0)
        self.assertEqual(self.scenarios["no-assets"]["simulation"]["error"]["code"], "INPUT_NOT_READY")
        for key in ("normal", "deficit", "high-debt"):
            self.assertTrue(self.scenarios[key]["simulation"]["success"])

    def test_fixed_inputs_produce_identical_results(self):
        self.assertEqual(generate(), self.bundle)

    def test_editing_an_input_changes_real_report_and_simulation(self):
        changed = generate('from devtools.presentation_inputs import BASE_INPUT\n'
                           'BASE_INPUT["financial_assets"][0]["current_value"] += 10000000')
        changed_normal = changed["scenarios"][0]
        before = self.reports(self.scenarios["normal"])
        after = self.reports(changed_normal)
        self.assertEqual(after["asset_statement"]["total_assets"] - before["asset_statement"]["total_assets"], 10_000_000)
        self.assertNotEqual(changed_normal["simulation"]["data"], self.scenarios["normal"]["simulation"]["data"])


if __name__ == "__main__":
    unittest.main()
