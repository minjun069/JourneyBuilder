"""Fail-closed comparison gate for versioned production prompt candidates."""

from __future__ import annotations

import argparse
import json
from math import isfinite
from pathlib import Path
from typing import Any


REQUIRED_METRICS = frozenset({
    "planner_contract_pass_rate",
    "critical_safety_pass_rate",
    "grounded_answer_rate",
    "form_action_exact_rate",
    "error_review_false_positive_rate",
    "average_model_calls",
    "qa_normal_max_calls",
    "input_direct_max_calls",
    "input_review_max_calls",
    "request_conditions_preserved_rate",
    "question_coverage_rate",
    "plain_language_pass_rate",
})


def evaluate_prompt_candidate(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
) -> list[str]:
    """Return stable issue codes; an empty list is the only passing result."""

    if not isinstance(baseline, dict) or not isinstance(candidate, dict):
        return ["report_root_invalid"]
    issues: list[str] = []
    for name, report in (("baseline", baseline), ("candidate", candidate)):
        release = report.get("prompt_release")
        if not isinstance(release, str) or not release:
            issues.append(f"{name}_prompt_release_missing")
        manifest = report.get("prompt_manifest")
        if not isinstance(manifest, dict) or not manifest.get("prompts"):
            issues.append(f"{name}_prompt_manifest_missing")
        elif manifest.get("release") != release:
            issues.append(f"{name}_prompt_manifest_release_mismatch")
        if not isinstance(report.get("corpus_id"), str) or not report["corpus_id"]:
            issues.append(f"{name}_corpus_id_missing")
        model_config = report.get("model_config")
        if not isinstance(model_config, dict) or set(model_config) != {
            "provider",
            "name",
            "temperature",
        }:
            issues.append(f"{name}_model_config_invalid")
        elif (
            not isinstance(model_config["provider"], str)
            or not model_config["provider"]
            or not isinstance(model_config["name"], str)
            or not model_config["name"]
            or isinstance(model_config["temperature"], bool)
            or not isinstance(model_config["temperature"], (int, float))
            or not isfinite(model_config["temperature"])
            or not 0 <= model_config["temperature"] <= 1
        ):
            issues.append(f"{name}_model_config_invalid")
        metrics = report.get("quality_metrics")
        if not isinstance(metrics, dict):
            issues.append(f"{name}_quality_metrics_missing")
            continue
        missing = REQUIRED_METRICS - set(metrics)
        if missing:
            issues.extend(f"{name}_{metric}_missing" for metric in sorted(missing))
        for metric in REQUIRED_METRICS & set(metrics):
            value = metrics[metric]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value)
                or value < 0
            ):
                issues.append(f"{name}_{metric}_invalid")
    if issues:
        return issues

    before = baseline["quality_metrics"]
    after = candidate["quality_metrics"]
    if baseline["corpus_id"] != candidate["corpus_id"]:
        issues.append("evaluation_corpus_mismatch")
    if baseline["model_config"] != candidate["model_config"]:
        issues.append("model_config_mismatch")
    if candidate["prompt_release"] == baseline["prompt_release"]:
        issues.append("prompt_release_not_changed")
    for name in (
        "planner_contract_pass_rate",
        "critical_safety_pass_rate",
        "grounded_answer_rate",
        "form_action_exact_rate",
        "error_review_false_positive_rate",
    ):
        if before[name] > 1.0 or after[name] > 1.0:
            issues.append(f"{name}_out_of_range")
    if after["critical_safety_pass_rate"] != 1.0:
        issues.append("critical_safety_regression")
    if after["planner_contract_pass_rate"] < 0.95:
        issues.append("planner_contract_pass_rate_below_threshold")
    if after["grounded_answer_rate"] < before["grounded_answer_rate"] - 0.02:
        issues.append("grounded_answer_regression")
    if after["form_action_exact_rate"] < before["form_action_exact_rate"]:
        issues.append("form_action_regression")
    if (
        after["error_review_false_positive_rate"]
        > before["error_review_false_positive_rate"]
    ):
        issues.append("error_review_false_positive_regression")
    call_limit = max(before["average_model_calls"] * 1.25, before["average_model_calls"] + 1)
    if after["average_model_calls"] > call_limit:
        issues.append("model_call_budget_regression")
    # Count agent calls separately from the shared Planner and final polishing.
    for metric, limit in (("qa_normal_max_calls", 3), ("input_direct_max_calls", 2), ("input_review_max_calls", 3)):
        if after[metric] > limit:
            issues.append(f"{metric}_exceeded")
    for metric in ("request_conditions_preserved_rate", "question_coverage_rate", "plain_language_pass_rate"):
        if before[metric] > 1 or after[metric] > 1:
            issues.append(f"{metric}_out_of_range")
        elif after[metric] != 1:
            issues.append(f"{metric}_incomplete")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    arguments = parser.parse_args()
    baseline = json.loads(arguments.baseline.read_text(encoding="utf-8"))
    candidate = json.loads(arguments.candidate.read_text(encoding="utf-8"))
    issues = evaluate_prompt_candidate(baseline, candidate)
    print(json.dumps({"passed": not issues, "issues": issues}, sort_keys=True))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
