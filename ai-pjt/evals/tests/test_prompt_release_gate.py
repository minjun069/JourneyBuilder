from __future__ import annotations

from copy import deepcopy
import unittest

from evals.prompt_release_gate import evaluate_prompt_candidate


def _report(release: str) -> dict:
    return {
        "corpus_id": "prompt-candidate-v1",
        "model_config": {
            "provider": "gemini",
            "name": "gemini-2.5-flash-lite",
            "temperature": 0.0,
        },
        "prompt_release": release,
        "prompt_manifest": {
            "release": release,
            "prompts": [{"id": "qa.answer", "version": "1.0.0"}],
        },
        "quality_metrics": {
            "planner_contract_pass_rate": 0.97,
            "critical_safety_pass_rate": 1.0,
            "grounded_answer_rate": 0.94,
            "form_action_exact_rate": 0.96,
            "error_review_false_positive_rate": 0.04,
            "average_model_calls": 4.0,
            "qa_normal_max_calls": 3,
            "input_direct_max_calls": 2,
            "input_review_max_calls": 3,
            "request_conditions_preserved_rate": 1.0,
            "question_coverage_rate": 1.0,
            "plain_language_pass_rate": 1.0,
        },
    }


class PromptReleaseGateTests(unittest.TestCase):
    def test_required_behavior_and_per_agent_call_budgets_are_enforced(self) -> None:
        candidate = _report("candidate")
        candidate["quality_metrics"].update({
            "qa_normal_max_calls": 4,
            "input_direct_max_calls": 3,
            "input_review_max_calls": 4,
            "request_conditions_preserved_rate": 0.9,
            "question_coverage_rate": 0.8,
            "plain_language_pass_rate": 0.7,
        })
        self.assertEqual(set(evaluate_prompt_candidate(_report("previous"), candidate)), {
            "qa_normal_max_calls_exceeded", "input_direct_max_calls_exceeded", "input_review_max_calls_exceeded",
            "request_conditions_preserved_rate_incomplete", "question_coverage_rate_incomplete", "plain_language_pass_rate_incomplete",
        })

    def test_non_regressing_new_release_passes(self) -> None:
        self.assertEqual(
            evaluate_prompt_candidate(_report("previous"), _report("candidate")),
            [],
        )

    def test_safety_quality_and_cost_regressions_fail_closed(self) -> None:
        baseline = _report("previous")
        candidate = deepcopy(_report("candidate"))
        candidate["quality_metrics"].update({
            "critical_safety_pass_rate": 0.99,
            "planner_contract_pass_rate": 0.94,
            "grounded_answer_rate": 0.90,
            "form_action_exact_rate": 0.95,
            "error_review_false_positive_rate": 0.05,
            "average_model_calls": 6.0,
        })

        self.assertEqual(
            set(evaluate_prompt_candidate(baseline, candidate)),
            {
                "critical_safety_regression",
                "planner_contract_pass_rate_below_threshold",
                "grounded_answer_regression",
                "form_action_regression",
                "error_review_false_positive_regression",
                "model_call_budget_regression",
            },
        )

    def test_missing_manifest_and_metrics_fail_closed(self) -> None:
        issues = evaluate_prompt_candidate({}, {})
        self.assertIn("baseline_prompt_release_missing", issues)
        self.assertIn("candidate_quality_metrics_missing", issues)

    def test_corpus_model_and_manifest_must_match_the_comparison(self) -> None:
        baseline = _report("previous")
        candidate = deepcopy(_report("candidate"))
        candidate["prompt_manifest"]["release"] = "wrong-release"
        self.assertEqual(
            evaluate_prompt_candidate(baseline, candidate),
            ["candidate_prompt_manifest_release_mismatch"],
        )

        candidate = deepcopy(_report("candidate"))
        candidate["corpus_id"] = "different-corpus"
        candidate["model_config"]["temperature"] = 0.2
        self.assertEqual(
            set(evaluate_prompt_candidate(baseline, candidate)),
            {
                "evaluation_corpus_mismatch",
                "model_config_mismatch",
            },
        )

    def test_non_numeric_or_out_of_range_metrics_fail_closed(self) -> None:
        baseline = _report("previous")
        candidate = deepcopy(_report("candidate"))
        candidate["quality_metrics"]["planner_contract_pass_rate"] = "0.99"
        self.assertIn(
            "candidate_planner_contract_pass_rate_invalid",
            evaluate_prompt_candidate(baseline, candidate),
        )

        candidate = deepcopy(_report("candidate"))
        candidate["quality_metrics"]["grounded_answer_rate"] = 1.1
        self.assertIn(
            "grounded_answer_rate_out_of_range",
            evaluate_prompt_candidate(baseline, candidate),
        )


if __name__ == "__main__":
    unittest.main()
