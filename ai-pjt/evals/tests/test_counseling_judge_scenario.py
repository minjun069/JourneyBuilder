from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from evals import counseling_judge as runner
from evals.counseling_judge_scenario import adapt_scenario_results


def row(ordinal=1, repetition=1, status="completed"):
    request_id = f"run-{repetition}-{ordinal}"
    return {
        "ordinal": ordinal, "repetition": repetition, "request_id": request_id,
        "step_id": "intro", "step_turn": ordinal, "process": "안내",
        "message": f"질문 {ordinal}", "report_supplied": False, "status": status,
        "graph_input": {
            "request_id": request_id, "thread_id": f"thread-{repetition}",
            "message": f"질문 {ordinal}", "customer_info": {"saved_data": {"age": 50}},
            "request_mode": "chat", "conversation_context": {"summary": {}, "turns": []},
        },
        "output": {"response": f"답변 {ordinal}", "action": None},
        "task_results": {"task-1": {"status": "succeeded"}}, "trace_id": request_id,
    }


def adapt(rows):
    return adapt_scenario_results(rows, manifest={"run_id": "run", "revision": {"commit": "abc"}},
                                  candidate_version="master", source_sha256="a" * 64)


class ScenarioJudgeTests(unittest.TestCase):
    def test_mapping_and_history_do_not_leak_future_or_other_repetitions(self):
        cases = adapt([row(1), row(2), row(1, 2), row(2, 2)])
        self.assertEqual(cases[0].request, "질문 1")
        self.assertEqual(cases[0].answer, "답변 1")
        self.assertEqual(cases[0].context.previous_dialogue, [])
        self.assertEqual([t.content for t in cases[1].context.previous_dialogue], ["질문 1", "답변 1"])
        self.assertEqual(cases[2].context.previous_dialogue, [])
        self.assertEqual(cases[3].source_metadata["repetition"], 2)
        self.assertNotIn("source_metadata", runner.judge_material(cases[0]))

    def test_actual_graph_customer_and_report_snapshot_are_used(self):
        value = row()
        value["customer_info"] = {"stale": True}
        value["report_supplied"] = True
        value["graph_input"]["customer_info"]["saved_data"]["report_snapshot"] = {"value": 10}
        case = adapt([value])[0]
        self.assertNotIn("stale", case.context.customer_info)
        self.assertEqual(case.context.diagnostic_results["report_snapshot"], {"value": 10})
        self.assertEqual(case.context.missing_information, [])

    def test_false_report_flag_is_known_absence_not_missing_evidence(self):
        case = adapt([row()])[0]
        self.assertFalse(case.context.diagnostic_results["report_supplied"])
        self.assertEqual(case.context.missing_information, [])
        value = row()
        value["report_supplied"] = True
        self.assertTrue(adapt([value])[0].context.missing_information)
        value["graph_input"]["chart_payload"] = {"actual_metric": 7}
        self.assertEqual(adapt([value])[0].context.missing_information, [])

    def test_task_failure_and_policy_blocked_responses_remain_assessable(self):
        for status in ("completed_with_task_failures", "completed_with_plan_issues", "policy_blocked"):
            with self.subTest(status=status):
                case = adapt([row(status=status)])[0]
                self.assertEqual(case.context.missing_information, [])
                self.assertEqual(case.context.execution_context["status"], status)

    def test_failed_execution_without_response_defers_without_provider(self):
        value = row(status="timeout")
        del value["output"]
        case = adapt([value])[0]
        judge = Mock()
        rubric = json.loads(runner.RUBRIC_PATH.read_text())
        result = runner.evaluate(case, judge, rubric)
        self.assertEqual(result["status"], "deferred")
        self.assertEqual(result["source"]["status"], "timeout")
        judge.generate.assert_not_called()

    def test_snapshot_retained_and_seeded_for_partial_results(self):
        value = row(9)
        value["graph_input"]["conversation_context"] = {
            "summary": {"text": "은퇴 예정"},
            "turns": [{"user_message": "예전 질문", "assistant_message": "예전 답변"}],
        }
        case = adapt([value])[0]
        self.assertEqual([t.content for t in case.context.previous_dialogue], ["예전 질문", "예전 답변"])
        self.assertEqual(case.context.execution_context["summary"], {"text": "은퇴 예정"})
        self.assertEqual(case.context.missing_information, [])

    def test_reconstruction_survives_snapshot_truncation(self):
        cases = adapt([row(1), row(2), row(3)])
        self.assertEqual(len(cases[-1].context.previous_dialogue), 4)
        self.assertEqual(cases[-1].context.execution_context["model_visible_dialogue"], [])

    def test_gap_remains_deferred_until_end_and_missing_success_is_invalid(self):
        cases = adapt([row(1), row(3), row(4)])
        self.assertTrue(cases[1].context.missing_information)
        self.assertTrue(cases[2].context.missing_information)
        value = row()
        del value["output"]
        with self.assertRaisesRegex(ValueError, "row 1"):
            adapt([value])

    def test_corrupt_or_duplicate_records_rejected(self):
        for values in ([row(), row()], [row(2), row(1)]):
            with self.assertRaises(ValueError):
                adapt(values)
        value = row()
        value["graph_input"]["message"] = "다른 질문"
        with self.assertRaisesRegex(ValueError, "row 1"):
            adapt([value])

    def test_load_array_detects_manifest_and_validate_only_never_calls(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "results.json"
            path.write_text(json.dumps([row(), row(2)], ensure_ascii=False, indent=2))
            manifest = path.with_name("manifest.json")
            manifest.write_text(json.dumps({"run_id": "run", "label": "master", "revision": {"commit": "abc"}}))
            cases = runner.load_cases(path)
            self.assertEqual(len(cases), 2)
            self.assertEqual(cases[0].candidate_version, "master")
            self.assertEqual(cases[0].source_metadata["revision"]["commit"], "abc")
            with patch.object(runner, "GeminiJudge") as client, patch("sys.stdout", new_callable=io.StringIO) as out:
                self.assertEqual(runner.main(["--input", str(path), "--validate-only"]), 0)
                self.assertEqual(json.loads(out.getvalue())["ready_for_judge"], 2)
                client.assert_not_called()
            manifest.unlink()
            with self.assertRaisesRegex(ValueError, "candidate-version"):
                runner.load_cases(path)
            self.assertEqual(runner.load_cases(path, candidate_version="sep20")[0].candidate_version, "sep20")

    def test_source_is_not_mutated_and_invalid_array_fails_preflight(self):
        value = [row()]
        original = deepcopy(value)
        adapt(value)
        self.assertEqual(value, original)
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "results.json"
            for content in ("[]", '[{"value": NaN}]', "[invalid"):
                path.write_text(content)
                with self.assertRaises(ValueError):
                    runner.load_cases(path, candidate_version="master")


if __name__ == "__main__":
    unittest.main()
