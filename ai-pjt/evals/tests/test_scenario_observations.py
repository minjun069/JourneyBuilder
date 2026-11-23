from copy import deepcopy
import hashlib
from pathlib import Path
import sys
import unittest

sys.path.append(str(Path(__file__).resolve().parents[1]))
from scenario_observations import audit_trace, reconcile_calls


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.turn = {"status": "completed", "model_calls": [{"call_id": "c1", "input_tokens": 10,
                     "output_tokens": 2, "usage_status": "reported"}]}
        self.observation = {"id": "terminal", "type": "GENERATION", "metadata": {"call_id": "c1"},
                            "endTime": "2026-09-30", "usageDetails": {"input": 10, "output": 2},
                            "costDetails": {"total": 0.2}}
        self.trace = {"output": {}, "totalCost": 0.4, "observations": [self.observation]}

    def test_duplicate_shadow_excluded_from_cost(self):
        shadow = {**self.observation, "id": "shadow", "usageDetails": {}, "endTime": None}
        self.trace["observations"].insert(0, shadow)
        result = audit_trace(self.turn, self.trace)
        self.assertTrue(result["complete"])
        self.assertEqual(result["duplicate_observations"], 1)
        self.assertEqual(result["total_cost_usd"], 0.2)
        self.assertEqual(result["measurements"][0]["id"], "terminal")

    def test_equal_count_with_wrong_identity_is_incomplete(self):
        self.observation["metadata"]["call_id"] = "other"
        result = audit_trace(self.turn, self.trace)
        self.assertFalse(result["complete"])
        self.assertEqual(result["missing_call_ids"], ["c1"])

    def test_mismatched_usage_and_unknown_price_are_not_valid_costs(self):
        self.observation["usageDetails"]["input"] = 11
        self.assertFalse(audit_trace(self.turn, self.trace)["complete"])
        self.observation["usageDetails"]["input"] = 10
        self.observation["costDetails"] = {}
        self.observation["calculatedTotalCost"] = 0
        self.assertIsNone(audit_trace(self.turn, self.trace)["total_cost_usd"])

    def test_failed_request_can_be_observed_without_output_or_cost(self):
        self.turn["status"] = "error"
        self.turn["model_calls"][0]["usage_status"] = "unavailable"
        self.trace["output"] = None
        self.trace["observations"].append({"name": "retirewise.request", "level": "ERROR"})
        result = audit_trace(self.turn, self.trace)
        self.assertTrue(result["complete"])
        self.assertIsNone(result["total_cost_usd"])

    def test_late_event_belongs_to_original_trace_and_reconcile_is_idempotent(self):
        trace_id = lambda s: hashlib.sha256(("retirewise:" + s).encode()).hexdigest()[:32]
        rows = [{"ordinal": i, "trace_id": trace_id(str(i)), "model_calls": []} for i in (1, 2)]
        events = [{"stage": "model", "trace_id": "1", "call_id": "a", "status": "started"},
                  {"stage": "model", "trace_id": "2", "call_id": "b", "status": "started"},
                  {"stage": "model", "trace_id": "1", "call_id": "a", "status": "succeeded", "input_tokens": 3}]
        self.assertEqual(len(reconcile_calls(rows, events)), 2)
        self.assertEqual(rows[0]["model_calls"][0]["input_tokens"], 3)
        self.assertEqual(rows[1]["model_calls"][0]["status"], "unfinished")
        self.assertNotIn("input_tokens", rows[1]["model_calls"][0])
        before = deepcopy(rows)
        self.assertEqual(reconcile_calls(rows, events), [])
        self.assertEqual(before, rows)


if __name__ == "__main__":
    unittest.main()
