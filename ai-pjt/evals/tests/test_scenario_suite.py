import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

EVALS = Path(__file__).resolve().parents[1]
if str(EVALS) not in sys.path:
    sys.path.append(str(EVALS))
from scenario_suite import main, publish_results
from scenario_resume import choose_run, read_rows, restore_context, validate_prefix


class ResultsOnlyTests(unittest.TestCase):
    def test_completed_case_wins_over_partial_and_longest_prefix_is_chosen(self):
        with tempfile.TemporaryDirectory() as tmp:
            roots = [Path(tmp) / name for name in ("old", "new")]
            for root, count in zip(roots, (3, 2)):
                case = root / "master/case-1"
                case.mkdir(parents=True)
                (case / "manifest.json").write_text("{}")
                (case / "turns.jsonl").write_text("{}\n" * count)
            self.assertEqual(choose_run(roots, "master", "case-1", 3), (roots[0] / "master/case-1", True))
            self.assertEqual(choose_run(roots, "master", "case-1", 4), (roots[0] / "master/case-1", False))
            self.assertEqual(choose_run(roots, "master", "case-2", 3), (None, False))

    def test_vanilla_continuation_preserves_failed_user_turn_and_prior_history(self):
        from types import SimpleNamespace
        history = [{"role": "user", "parts": [{"text": "earlier input"}]}]
        graph = SimpleNamespace(history=[])
        last = {"provider_input": {"contents": history}, "output": {"response": "answer"}}
        restore_context(graph, {}, [last], "vanilla")
        self.assertEqual(graph.history, history + [{"role": "model", "parts": [{"text": "answer"}]}])
        self.assertEqual(len(history), 1)
        restore_context(graph, {}, [{"provider_input": {"contents": history}, "status": "timeout"}], "vanilla")
        self.assertEqual(graph.history, history)

    def test_resume_prefix_rejects_changed_input(self):
        turn = {"ordinal": 1, "step_id": "intro", "message": "hello"}
        validate_prefix([{**turn, "repetition": 1}], [turn])
        with self.assertRaises(ValueError):
            validate_prefix([{**turn, "message": "changed", "repetition": 1}], [turn])

    def test_published_rows_are_unchanged_and_judge_accepts_them(self):
        from evals.tests.test_counseling_judge_scenario import row
        from evals.counseling_judge import load_cases
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = [row()]
            (root / "results.json").write_text(json.dumps(rows))
            target = root / "public/master/case-10.results.json"
            publish_results(root, target)
            self.assertEqual(json.loads(target.read_text()), rows)
            self.assertEqual(len(load_cases(target, candidate_version="master")), 1)

    def test_partial_journal_retains_completed_turns(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "turns.jsonl").write_text('{"status":"timeout"}\n{"status":')
            target = root / "public/results.json"
            publish_results(root, target)
            self.assertEqual(json.loads(target.read_text()), [{"status": "timeout"}])

    def test_compact_suite_keeps_only_results_and_reports_readback_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dataset = root / "dataset"
            dataset.mkdir()
            (dataset / "case-1.input.json").write_text('[{"대화":[{}]}]')
            config = root / "config.json"
            config.write_text(json.dumps({label: {"python": sys.executable, "worktree": str(root)}
                                          for label in ("vanilla", "sep20", "master")}))
            env = root / ".env"
            env.touch()
            rows = [{"status": "completed", "model_calls": [], "elapsed_ms": 4}]

            def child(command, **kwargs):
                from types import SimpleNamespace
                dest = Path(command[command.index("--output") + 1])
                dest.mkdir(exist_ok=True)
                for name, content in (("results.json", rows), ("summary.json", {}),
                                      ("langfuse.json", {"total_cost_usd": None})):
                    (dest / name).write_text(json.dumps(content))
                return SimpleNamespace(returncode=int("--readback-only" in command))

            output = root / "output"
            with patch("scenario_suite.load_scenario", return_value=[{"대화": [{}]}]), \
                 patch("scenario_suite.load_registry"), patch("scenario_suite.prepare_turns"), \
                 patch("scenario_suite.subprocess.run", side_effect=child), \
                 patch("scenario_comparison.collect") as collect, contextlib.redirect_stdout(io.StringIO()) as log:
                code = main(["--dataset", str(dataset), "--config", str(config), "--env-file", str(env),
                             "--output", str(output), "--results-only"])
            self.assertEqual(code, 1)
            self.assertIn('"state": "finished_with_errors"', log.getvalue())
            self.assertIn('"total_cost_usd": null', log.getvalue())
            self.assertEqual(len(list(output.rglob("*.json"))), 3)
            self.assertTrue(all(p.name == "case-1.results.json" for p in output.rglob("*.*")))
            collect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
